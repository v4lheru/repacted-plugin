"""
Location: pact-plugin/hooks/shared/evidence_check.py
Summary: The slim evidence check `repact accept` runs on a dispatched task: is there a passing
         tcount run whose input tree id equals the worktree's tree as it is now? Plus exact
         acceptance-criterion matching and, for a fix or a feature, a red run first.
         It answers with granular statuses, never one blurred "done":
           inputs_changed    the worktree's tree differs from dispatch.base_tree
           inputs_committed  HEAD holds every input file as it is on disk (what
                             was tested is what ships)
           run_recorded      a passing run of the task since dispatch (required when changed)
           inputs_unchanged  a passing run on the current tree (required when changed)
           checks_passed     each pinned AC has a passing run on the current tree that selects
                             the same tests as its command (acceptance.same_selection)
           red_first         fix and feature only: a failing run, before the first passing run
                             on the current tree or on that tree with the fix reverted, naming
                             a test file that passing run covers (a dotnet --filter names the
                             .cs files declaring its class; worker and build flags never narrow)
           needs_human       no tree id (not git, git failed, over content_tree.CHANGED_MAX
                             changed files), no dispatch time, or a config change with no run
         A run is a ledger row with a tree id (tcount stamps it before the run), counted, exit
         0, nothing failed, at least one passed, stamped at or after dispatch.dispatched_at, run
         inside the worktree or on a tree the task held (base_tree or now), and it loaded what
         the tree id covers (`excluded`): a readable command whose targets and
         root/config options lie inside the git root of its directory, no PYTEST_ADDOPTS,
         NODE_OPTIONS or VSTest* variable, and every PYTHONPATH entry inside that root. Tree equality is the whole
         freshness rule: the tree id covers every input file (content_tree.is_input_path),
         named in produced or not, and a commit does not change it.
Used by: shared/ops/evidence.py (the adapter accept and submit call).
Rule ids (shared/rules.py): evidence.no_run, evidence.stale_run, evidence.selection_mismatch,
evidence.red_first_missing, evidence.needs_human, evidence.uncommitted. Known limits: a hand-
written ledger row fakes a run; red_first shows a red run existed, not that the intended
assertion failed, so it says "red run recorded", never "regression proven"; the tree id covers
the git root's input files, not what a run imports from outside it (an editable install of
another checkout, a site-packages copy), nor a test that reads an excluded file (*.md).
"""
from __future__ import annotations

import os
import re
import shlex
from typing import Optional

from shared import acceptance as AC
from shared import content_tree as ct
from shared.check_modes import use_mode
from shared.ledger import _record_time, _run_end, read_runs
from shared.plugin_paths import ledger_path
from shared.runcounts import detect_runner

NO_RUN = "evidence.no_run"
STALE_RUN = "evidence.stale_run"
SELECTION_MISMATCH = "evidence.selection_mismatch"
RED_FIRST = "evidence.red_first_missing"
NEEDS_HUMAN = "evidence.needs_human"
UNCOMMITTED = "evidence.uncommitted"
RULE_IDS = (NO_RUN, STALE_RUN, SELECTION_MISMATCH, RED_FIRST, NEEDS_HUMAN, UNCOMMITTED)
KINDS = ("fix", "feature", "refactor", "config", "docs")
RED_FIRST_KINDS = ("fix", "feature")      # a missing or unknown kind is judged as feature
TREE_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
FILES_SHOWN = 5
TCOUNT_HINT = "run `nice -n 19 tcount <test command>` in the worktree after the last edit"
COMMIT_HINT = "commit your work, then rerun tcount"
# Options whose value is a directory or file the runner loads from; `-C dir` or
# `--prefix dir` as two words leave the directory a target, which is checked the same way.
ROOT_OPTIONS = ("-c", "--rootdir", "--confcutdir", "--config", "--prefix", "--dir", "--directory", "--cwd")
INI_PATH_KEYS = ("pythonpath", "rootdir", "testpaths")   # an ini key whose value is a loaded path
BARRED_ENV = ("PYTEST_ADDOPTS", "NODE_OPTIONS")   # a run under either (or a VSTest* one) selects other tests
NOTES_SHOWN = 5
uncommitted_inputs = ct.uncommitted_inputs        # a module name the tests can stand in for


class Verdict:
    """The statuses, the problems (rule, line) that refuse accept, notes, and the runs and tree
    the acceptance record keeps."""

    def __init__(self, tree: Optional[str]):
        self.tree, self.statuses, self.problems, self.notes, self.runs = tree, {}, [], [], []

    def status(self, name: str, state: str, line: str) -> None:
        self.statuses[name] = {"state": state, "line": line}

    def refuse(self, name: str, rule: str, line: str) -> None:
        self.status(name, "fail", line)
        self.problems.append({"rule": rule, "line": line})

    def as_dict(self) -> dict:
        return {"tree": self.tree, "statuses": self.statuses, "problems": self.problems,
                "notes": self.notes, "runs": self.runs}


def _int(run: dict, key: str) -> int:
    value = run.get(key)
    return value if type(value) is int else 0


def is_red(run: dict) -> bool:
    return bool(run.get("count_parsed")) and _int(run, "failed") + _int(run, "errors") > 0


def run_tree(run: dict) -> Optional[str]:
    """The content tree id the run saw: tcount's own (run_tree on a joined hook row), or None."""
    tree = run.get("run_tree") or run.get("tree")
    return tree if isinstance(tree, str) and TREE_ID.fullmatch(tree) else None


def _where(run: dict) -> Optional[str]:
    place = run.get("run_cwd") or run.get("cwd")
    return os.path.realpath(place) if isinstance(place, str) and os.path.isabs(place) else None


def _inside(path: Optional[str], root: str) -> bool:
    return path is not None and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def task_runs(runs: list, dispatch: dict, now: str, since: float, notes: Optional[list] = None) -> list:
    """The task's runs, oldest first: a tree id, stamped at or after dispatch, run in the worktree
    or on a tree the task held (its base tree or the current one), and not `excluded`. Each
    excluded run adds a line to `notes`, when given."""
    root = os.path.realpath(str(dispatch.get("worktree")))
    held = {now, dispatch.get("base_tree")}
    picked = [run for run in runs if isinstance(run, dict) and run_tree(run)
              and (_record_time(run) or 0.0) >= since
              and (_inside(_where(run), root) or run_tree(run) in held)]
    roots: dict = {}
    kept = []
    for run in picked:
        why = excluded(run, roots)
        if not why:
            kept.append(run)
        elif notes is not None and len(notes) < NOTES_SHOWN:
            notes.append(f"run {AC.stem(run)} does not count: {why}")
    return sorted(kept, key=lambda run: _run_end(run) or 0.0)


def run_env(run: dict) -> dict:
    """The selecting variables (acceptance.selecting_env) tcount recorded for the run (run_env on a joined hook row)."""
    env = run.get("run_env") or run.get("env")
    return {k: v for k, v in env.items() if isinstance(v, str)} if isinstance(env, dict) else {}


def _loaded_paths(picked: tuple) -> list:
    """The paths a run's selection loads from: its targets (less a ::node part), the values of
    ROOT_OPTIONS, an attached `-cFILE` included, and each path an -o/--override-ini of an
    INI_PATH_KEYS key names (`-o pythonpath=a b`, `-orootdir=x`)."""
    paths = [target.split("::", 1)[0] for target in picked[2]]
    for option, value in picked[1]:
        if option in ROOT_OPTIONS:
            paths.append(value)
        elif option.startswith("-c") and not option.startswith("--") and len(option) > 2:
            paths.append(option[2:])
        if option in ("-o", "--override-ini") or (option.startswith("-o") and not option.startswith("--")):
            key, _, listed = (value if option in ("-o", "--override-ini") else option[2:]).partition("=")
            if key.strip() in INI_PATH_KEYS:
                paths.extend(listed.split())
    return [path for path in paths if path]


def excluded(run: dict, roots: dict) -> str:
    """Why a run counts for nothing, "" when it counts: its command cannot be read
    (acceptance.selection: a wrapper that changes what runs), it ran under PYTEST_ADDOPTS,
    NODE_OPTIONS or a VSTest* variable, or it loads a target, a root or config option value or a PYTHONPATH entry from
    outside the git root of its directory (the tree id covers that root only). `roots` caches
    the git root per directory."""
    picked = AC.selection(_command(run))
    if picked is None:
        return "its command cannot be read as a test selection (a wrapper option that changes what runs)"
    env = run_env(run)
    barred = [name for name in BARRED_ENV if name in env] + sorted(n for n in env if AC.VSTEST_ENV.match(n))
    if barred:
        return f"it ran with {' and '.join(barred)} set, which changes the tests it selects"
    where = _where(run)
    if where not in roots:
        top = ct.work_root(where) if where else None
        roots[where] = os.path.realpath(top) if top else None
    top = roots[where]
    if top is None:
        return "no git repository holds the directory it ran in"
    entries = env["PYTHONPATH"].split(os.pathsep) if "PYTHONPATH" in env else []
    for path in _loaded_paths(picked) + [entry or "." for entry in entries]:
        if not _inside(os.path.realpath(os.path.join(where, os.path.expanduser(path))), top):
            return f"it loads {path[:120]} from outside its repository {top}"
    return ""


def _command(run: dict) -> str:
    return str(run.get("run_cmd") or run.get("cmd") or "")


def _shown(files: list) -> str:
    more = f" and {len(files) - FILES_SHOWN} more" if len(files) > FILES_SHOWN else ""
    return ", ".join(files[:FILES_SHOWN]) + more


def _changed_since(worktree: str, tree: Optional[str], now: str) -> str:
    files = ct.code_diff(worktree, tree, now) if tree else None
    if files is None:
        return "git could not compare the run's tree with the current one"
    return _shown(files) if files else ("no input file differs between the two tree ids (one was "
                                        "taken by an older tcount or in another checkout): rerun tcount")


# ---------------------------------------------------------------------------
# Which tests a run names (red_first)
# ---------------------------------------------------------------------------

def _norm(path: str) -> str:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/") or path


# Flags that set workers, resources or build settings, never which tests run: a green that carries
# them selects what it would select without them. The *_VALUE spellings take the next
# word as their value.
_JS_RESOURCE = re.compile(r"--(?:maxWorkers|max-workers|minWorkers|min-workers|pool)(?:=.*)?\Z", re.S)
_JS_RESOURCE_VALUE = ("--maxWorkers", "--max-workers", "--minWorkers", "--min-workers", "--pool")
_JS_RESOURCE_WORD = re.compile(r"[A-Za-z0-9%]+\Z")   # a worker count or pool name; else the flag stays
_JS_RUNNERS = ("vitest", "jest")
_DOTNET_RESOURCE = re.compile(r"(?:-{1,2}|/)(?:m|maxcpucount)(?::\d*)?\Z|--no-build\Z|--no-restore\Z|--nologo\Z"
                              r"|(?:-c|--configuration|-v|--verbosity|-l|--logger)[:=].+\Z", re.I | re.S)
_DOTNET_RESOURCE_VALUE = ("-c", "--configuration", "-v", "--verbosity", "-l", "--logger")
# `-p:Name=Value` sets an MSBuild property; a VSTest* one (VSTestTestCaseFilter) selects tests.
_DOTNET_PROPERTY = re.compile(r"(?:-{1,2}|/)(?:p|property):(.*)\Z", re.I | re.S)
_VSTEST_PROPERTY = re.compile(r"(?:^|[;,])\s*VSTest", re.I)
_DOTNET_FILTER = re.compile(r"--filter[:=](.*)\Z", re.I | re.S)
_DOTNET_PROJECT = re.compile(r".+\.(?:csproj|fsproj|vbproj|sln|slnf|slnx)\Z", re.I)
# One filter term, `Property op value` or a bare value (FullyQualifiedName~value). Grouping,
# negation and escapes are not read: such a filter names no file and selects nothing known.
_FILTER_TERM = re.compile(r"\s*(?:([A-Za-z]+)\s*(=|~)\s*)?([A-Za-z0-9_.+]+)\s*\Z")
# Name is the test method's name (MSTest, NUnit), so it names no class.
_CLASS_PROPERTIES = {"fullyqualifiedname": "FullyQualifiedName", "classname": "ClassName"}
# A type declaration at a line start, after attributes and modifiers (a comment never matches).
_CS_TYPE = re.compile(r"^[ \t]*(?:\[[^\]\n]*\][ \t]*)*(?:(?:public|internal|private|protected|sealed|static"
                      r"|abstract|partial|file|unsafe|new|readonly|ref)\s+)*(?:class|record(?:\s+class)?|struct)"
                      r"\s+@?([A-Za-z_][A-Za-z0-9_]*)", re.M)
_SKIP_DIRS = frozenset(("bin", "obj", ".git", "node_modules"))
CS_FILES_MAX = 5000                      # .cs files read per project directory, at most
CS_BYTES_MAX = 1 << 20                   # bytes read per .cs file


class Target(tuple):
    """A dotnet red's test: (file, class, tail, property, op, group, value). `tail` is the filter
    value after the class segment (a method or its prefix), `group` the canonical text of an
    `&` group (empty for a single term), `value` the term's whole value. Shown as
    `file::Class.tail`."""
    __slots__ = ()

    def __new__(cls, file, klass, tail, prop, op, group, value=""):
        return tuple.__new__(cls, (file, klass, tail, prop, op, group, value))

    file = property(lambda self: self[0])
    klass = property(lambda self: self[1])
    tail = property(lambda self: self[2])
    prop = property(lambda self: self[3])
    op = property(lambda self: self[4])
    group = property(lambda self: self[5])
    value = property(lambda self: self[6])

    def __str__(self) -> str:
        return f"{self.file}::{self.klass}" + (f".{self.tail}" if self.tail else "")


def _js_words(words: list) -> list:
    """A vitest or jest command as `<runner> <args>`: the package-manager words in front, a
    `run` after vitest and the resource flags (with their values) dropped. A flag whose next
    word is not a plain count or name (an option, a path) is kept with it, so the run reads as
    narrowed."""
    runner = next((i for i, word in enumerate(words[:4]) if os.path.basename(word) in _JS_RUNNERS), None)
    if runner is None:
        return words
    rest, kept = iter(words[runner + 1:]), [os.path.basename(words[runner])]
    for index, word in enumerate(rest):
        if index == 0 and kept[0] == "vitest" and word == "run":
            continue
        if word in _JS_RESOURCE_VALUE:
            value = next(rest, None)
            if value is None or not _JS_RESOURCE_WORD.match(value):
                kept += [word] + ([value] if value is not None else [])
        elif not _JS_RESOURCE.match(word):
            kept.append(word)
    return kept


def _picked(run: dict) -> Optional[tuple]:
    """acceptance.selection of the run's command with resource flags set aside (vitest, jest)."""
    words = AC.test_words(_command(run))
    if words is None:
        return None
    if detect_runner(shlex.join(words)) in _JS_RUNNERS:
        return AC.selection(shlex.join(_js_words(words)))
    return AC.selection(_command(run))


def _dotnet(run: dict) -> Optional[dict]:
    """A `dotnet test` run read as {projects, filter, narrowed}, or None when the run is not
    one or is a vstest run of a built assembly. `projects` are the absolute project
    directories (the run's directory when none is named); `filter` is the --filter text or
    None; `narrowed` is True when an option other than a resource flag (or a VSTest*
    property, anything after `--`, or a resource flag whose value is missing or starts with
    `-`) may select fewer tests."""
    words = AC.test_words(_command(run))
    where = _where(run)
    if not words or where is None or detect_runner(shlex.join(words)) != "dotnet" or words[1:2] != ["test"]:
        return None
    projects, expr, narrowed, rest = [], None, False, iter(words[2:])
    for word in rest:
        attached = _DOTNET_FILTER.match(word)
        prop = _DOTNET_PROPERTY.match(word)
        if word == "--":
            narrowed = True
            break
        if word.lower().endswith((".dll", ".exe")):
            return None
        if word == "--filter":
            expr = next(rest, "")
        elif attached:
            expr = attached.group(1)
        elif prop:
            narrowed = narrowed or bool(_VSTEST_PROPERTY.search(prop.group(1)))
        elif word in _DOTNET_RESOURCE_VALUE:
            narrowed = narrowed or next(rest, "-").startswith("-")
        elif _DOTNET_RESOURCE.match(word):
            continue
        elif word.startswith("-") or (word.startswith("/") and not os.path.exists(word)):
            narrowed = True
        else:
            path = os.path.realpath(os.path.join(where, word))
            projects.append(os.path.dirname(path) if _DOTNET_PROJECT.match(word) else path)
    return {"projects": projects or [where], "filter": expr, "narrowed": narrowed}


def _classes(folder: str, cache: dict) -> dict:
    """{class name: sorted .cs files declaring it} under `folder` (bin, obj and hidden folders
    skipped; at most CS_FILES_MAX files), cached per folder in `cache`."""
    if folder in cache:
        return cache[folder]
    found: dict = {}
    seen = 0
    for top, dirs, files in os.walk(folder):
        if seen >= CS_FILES_MAX:
            break
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".cs") or seen >= CS_FILES_MAX:
                continue
            seen += 1
            path = os.path.join(top, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read(CS_BYTES_MAX)
            except OSError:
                continue
            for klass in set(_CS_TYPE.findall(text)):
                found.setdefault(klass, []).append(path)
    cache[folder] = found
    return found


def _filter_groups(expr: str) -> Optional[list]:
    """The --filter text as groups (split on |) of (property, op, value) terms (split on &), or
    None when a term cannot be read (grouping, negation, an escape, odd quoting, empty)."""
    if not expr or any(mark in expr for mark in "()!\\"):
        return None
    groups = []
    for part in expr.split("|"):
        terms = []
        for text in part.split("&"):
            match = _FILTER_TERM.fullmatch(text)
            if not match:
                return None
            name, op, value = match.groups()
            terms.append((name or "FullyQualifiedName", op or "~", value))
        groups.append(terms)
    return groups


def _term_targets(term: tuple, group: str, projects: list, cache: dict) -> list:
    """The Targets a filter term names: the .cs files under the projects that declare the class
    its value's last class segment names (every file of a partial class). [] when the property
    is not one that names a class, or no segment is a declared class."""
    name, op, value = term
    prop = _CLASS_PROPERTIES.get(name.lower())
    if prop is None:
        return []
    segments = [part for part in re.split(r"[.+]", value) if part]
    found = []
    for project in projects:
        declared = _classes(project, cache)
        hits = [index for index, part in enumerate(segments) if part in declared]
        if hits:
            klass = segments[hits[-1]]
            tail = ".".join(segments[hits[-1] + 1:])
            found += [Target(file, klass, tail, prop, op, group, value) for file in declared[klass]]
    return found


def _group_targets(terms: list, projects: list, cache: dict) -> list:
    group = "&".join(sorted(f"{name.lower()}{op}{value}" for name, op, value in terms)) if len(terms) > 1 else ""
    found = []
    for term in terms:
        found += _term_targets(term, group, projects, cache)
    return found


def _dotnet_targets(reading: dict, cache: dict) -> list:
    groups = _filter_groups(reading["filter"]) if reading["filter"] is not None else None
    found = []
    for terms in groups or []:
        found += _group_targets(terms, reading["projects"], cache)
    return found


def test_files(run: dict, nodes: bool = False) -> list:
    """The file part of each target the run's command names (a node id loses its ::part, unless
    `nodes`)."""
    picked = _picked(run)
    found = []
    for target in (picked[2] if picked else []):
        file, mark, node = target.partition("::")
        if target:
            found.append(_norm(file) + (mark + node if nodes else ""))
    return found


def red_targets(run: dict, cache: Optional[dict] = None) -> list:
    """What a red run names: for `dotnet test`, a Target per .cs file its --filter maps to (no
    filter names nothing: the whole project is no test file, and a narrowed run names nothing:
    what else selected its tests is not read); otherwise test_files with node ids."""
    reading = _dotnet(run)
    if reading is not None:
        return [] if reading["narrowed"] else _dotnet_targets(reading, {} if cache is None else cache)
    words = AC.test_words(_command(run))
    if words and detect_runner(shlex.join(words)) == "dotnet":
        return []                       # a vstest run of a built assembly names no .cs file
    return test_files(run, nodes=True)


def _same_file(word: str, file: str) -> bool:
    return word == file or word.endswith("/" + file) or file.endswith("/" + word)


def _selects_at_least(green: Target, red: Target) -> bool:
    """The green term selects every test the red term selects. `P~g` contains: any test whose
    P contains the red's value r (`P~r`) or is r (`P=r`) contains g when g is part of r; and a
    test of class C has a FullyQualifiedName that holds C, so `FullyQualifiedName~g` covers a
    ClassName red the same way. `P=v` covers only the same `P=v`, and `ClassName=C` also an
    exact `FullyQualifiedName=C.Method`."""
    if green.op == "~":
        return green.value in red.value and (green.prop == red.prop
                                             or (green.prop, red.prop) == ("FullyQualifiedName", "ClassName"))
    if (green.prop, red.op, green.value) == (red.prop, "=", red.value):
        return True
    method = red.value[len(green.value) + 1:]
    return (green.prop, red.prop, red.op) == ("ClassName", "FullyQualifiedName", "=") \
        and red.value.startswith(green.value + ".") and bool(method) and "." not in method


def _covers_dotnet(run: dict, red: Target, cache: dict) -> bool:
    """A passing dotnet run covers a red Target: it is not narrowed, a project it ran holds the
    red's file, and it has no filter, or a filter group on the red's class file that selects at
    least the red's tests (a single term: _selects_at_least; an `&` group: the same group)."""
    reading = _dotnet(run)
    if reading is None or reading["narrowed"] or not any(_inside(red.file, p) for p in reading["projects"]):
        return False
    if reading["filter"] is None:
        return True
    for terms in _filter_groups(reading["filter"]) or []:
        for green in _group_targets(terms, reading["projects"], cache):
            if green.file != red.file or green.klass != red.klass:
                continue
            if (green.group == red.group) if green.group else _selects_at_least(green, red):
                return True
    return False


def covers(run: dict, red, root: str, cache: Optional[dict] = None) -> bool:
    """The passing run ran the red target `red` (a file, a file::node id, or a dotnet Target)
    and selects nothing narrower: no option such as -k, -m or --deselect (resource flags such
    as --maxWorkers are set aside). A run that names the file, or a directory above it, covers
    it only with no ::node id among its targets; a node id covers only the same node id. No
    target at all covers it only when the run's directory is the worktree root `root` (the
    whole suite)."""
    if isinstance(red, Target):
        return _covers_dotnet(run, red, {} if cache is None else cache)
    picked = _picked(run)
    if picked is None or picked[1]:
        return False
    file, _, node = red.partition("::")
    whole = not any("::" in target for target in picked[2])
    targets = test_files(run, nodes=True)
    if not targets:
        return _where(run) == root
    where = _where(run)
    for target in targets:
        path, _, its_node = target.partition("::")
        if its_node:
            if node == its_node and _same_file(path, file):
                return True
        elif not whole:
            continue
        elif _same_file(path, file) or (file + "/").startswith(path + "/"):
            return True
        elif where and os.path.isabs(path) and os.path.join(where, file).startswith(path + "/"):
            return True
    return False


def _fix_reverted(worktree: str, red_tree: Optional[str], base, now: str) -> bool:
    """The red ran on the accepted inputs less (part of) the fix: a parent commit
    or a temporary revert commit. Every non-test input file that differs from the current tree
    changed since dispatch and is as it was at dispatch; at least one such file differs."""
    if not red_tree or red_tree == now or not (isinstance(base, str) and TREE_ID.fullmatch(base)):
        return False
    fix, to_now, to_base = (ct.code_diff(worktree, a, b) for a, b in ((base, now), (red_tree, now), (red_tree, base)))
    if fix is None or to_now is None or to_base is None:
        return False
    reverted = {path for path in to_now if not ct.is_test_path(path)}
    return bool(reverted) and reverted <= set(fix) and not reverted & set(to_base)


def _red_first(verdict: Verdict, kind: str, ours: list, on_now: list, root: str, place: dict,
               base=None) -> None:
    """A red run counts when it ended before the first green (its end stamp, then its ledger
    position `place` by id when the one-second stamps tie), or, whenever it ran, when its tree
    is the accepted one with the fix reverted (_fix_reverted). A red with no readable end never
    counts; a green with none comes first. Either way a green on the current inputs must cover
    a test the red names."""
    def order(run: dict) -> tuple:
        return (_run_end(run) or 0.0, place.get(id(run), 0))

    reds = [run for run in ours if is_red(run)]
    if kind not in RED_FIRST_KINDS:
        recorded = " (a red run is recorded)" if reds else ""
        verdict.status("red_first", "not_required", f"red-first is not required for a {kind} task{recorded}")
        return
    if not on_now:
        verdict.refuse("red_first", RED_FIRST, f"a {kind} task needs a failing tcount run of its test "
                       "before a passing run on the current inputs, and no passing run is on them")
        return
    first_green = min(order(run) for run in on_now)
    cache: dict = {}
    for red in reds:
        if _run_end(red) is None:
            continue
        if order(red) < first_green:
            how = "before the passing run"
        elif _fix_reverted(root, run_tree(red), base, verdict.tree):
            how = "on the inputs with the fix reverted"
        else:
            continue
        for target in red_targets(red, cache):
            if any(covers(green, target, root, cache) for green in on_now):
                verdict.status("red_first", "pass", f"red run recorded for {target} {how}")
                return
    verdict.refuse("red_first", RED_FIRST, f"a {kind} task needs a failing tcount run since dispatch, "
                   "naming a test file a passing run on the current inputs covers, and run before "
                   "that green or on the inputs with the fix reverted. A red after the green on these "
                   "same inputs does not count. If the green came first: make a temporary revert "
                   "commit of the fix (test files kept), run the named test with tcount and see it "
                   "fail, then restore the fix. Only if that cannot be done, ask the lead for a "
                   "waiver (`repact accept <id> --waive \"<reason naming that red run>\"`; the waiver "
                   "does not check the run, and unattended it parks the task for the owner)")


# ---------------------------------------------------------------------------
# Acceptance criteria (checks_passed)
# ---------------------------------------------------------------------------

def _checks(verdict: Verdict, dispatch: dict, greens: list, on_now: list) -> None:
    pin, why = AC.read_pin(dispatch)
    if why:
        verdict.refuse("checks_passed", NEEDS_HUMAN, f"the dispatch record's {why}")
        return
    if not pin:
        verdict.status("checks_passed", "not_required", "no acceptance criterion is pinned for this task")
        return
    lines = []
    for ident, command in pin.items():
        hits = [run for run in on_now if AC.same_selection(run, command)]
        older = [run for run in greens if AC.same_selection(run, command)]
        if hits:
            lines.append(f"{ident}: run {AC.stem(hits[-1])} passed on the current inputs")
        elif older:
            verdict.problems.append({"rule": STALE_RUN, "line": (
                f"{ident}: the input files changed after its run {AC.stem(older[-1])}: "
                f"{_changed_since(str(dispatch.get('worktree')), run_tree(older[-1]), verdict.tree)}")})
        elif on_now:
            verdict.problems.append({"rule": SELECTION_MISMATCH, "line": (
                f"{ident}: no passing run on the current inputs selects the tests of `{command[:100]}`; "
                f"the newest ran `{_command(on_now[-1])[:100]}`")})
        else:
            verdict.problems.append({"rule": NO_RUN, "line": (
                f"{ident}: no passing tcount run of `{command[:100]}` on the current inputs")})
    failed = len(pin) - len(lines)
    state = "fail" if failed else "pass"
    verdict.status("checks_passed", state, f"{len(lines)} of {len(pin)} pinned criteria passed"
                   + ("" if failed else ": " + "; ".join(lines))[:600])


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _inputs(verdict: Verdict, dispatch: dict, kind: str, greens: list, on_now: list) -> None:
    worktree = str(dispatch.get("worktree"))
    if not greens:
        if kind == "config":
            verdict.refuse("run_recorded", NEEDS_HUMAN, "a config change with no passing tcount run: "
                           "a human checks it (a schema check, a build or a smoke run), or " + TCOUNT_HINT)
        else:
            verdict.refuse("run_recorded", NO_RUN, "no passing tcount run since dispatch in the "
                           f"worktree or on its current inputs: {TCOUNT_HINT}")
        verdict.status("inputs_unchanged", "fail", "no passing run to compare with the current inputs")
        return
    verdict.status("run_recorded", "pass", f"{len(greens)} passing tcount run(s) since dispatch")
    if on_now:
        verdict.status("inputs_unchanged", "pass", f"run {AC.stem(on_now[-1])} tested the current inputs")
        return
    newest = greens[-1]
    verdict.refuse("inputs_unchanged", STALE_RUN, f"the input files changed after the run "
                   f"{AC.stem(newest)}: {_changed_since(worktree, run_tree(newest), verdict.tree)}; "
                   + TCOUNT_HINT)


def _committed(verdict: Verdict, worktree: str) -> None:
    """inputs_committed: accept closes what HEAD holds, so every input file must
    be committed; a staged, unstaged, untracked or deleted one refuses."""
    files = uncommitted_inputs(worktree)
    if files is None:
        verdict.refuse("inputs_committed", UNCOMMITTED, "git could not list the worktree's uncommitted "
                       f"files: {COMMIT_HINT}")
    elif files:
        verdict.refuse("inputs_committed", UNCOMMITTED, f"input files are not committed ({_shown(files)}), "
                       f"so the branch would not ship what was tested: {COMMIT_HINT}")
    else:
        verdict.status("inputs_committed", "pass", "every input file is committed")


def _pythonpath_notes(verdict: Verdict, on_now: list) -> None:
    for run in on_now[-NOTES_SHOWN:]:
        path = run_env(run).get("PYTHONPATH")
        if path is not None:
            verdict.notes.append(f"run {AC.stem(run)} ran with PYTHONPATH={path[:200]}")


def check(task: dict, runs: Optional[list] = None) -> dict:
    """The verdict on a dispatched task (Verdict.as_dict). `runs` defaults to this machine's
    ledger (shared.ledger.read_runs). Never raises on a malformed task: what it cannot read is a
    needs_human problem, never a pass."""
    meta = task.get("metadata") if isinstance(task, dict) and isinstance(task.get("metadata"), dict) else {}
    dispatch = meta.get("dispatch") if isinstance(meta.get("dispatch"), dict) else {}
    kind = dispatch.get("kind") if dispatch.get("kind") in KINDS else "feature"
    worktree = dispatch.get("worktree")
    now = ct.content_tree(worktree) if isinstance(worktree, str) and os.path.isabs(worktree) else None
    verdict = Verdict(now)
    since = _record_time({"ts": dispatch.get("dispatched_at")})
    if now is None or since is None:
        why = ("the dispatch record has no readable dispatched_at" if now is not None else
               f"the input tree of {worktree!r} has no id (not a git worktree, git failed, or more "
               f"than {ct.CHANGED_MAX} changed input files), so no run can be matched to it")
        verdict.refuse("needs_human", NEEDS_HUMAN, why)
        return _moded(verdict).as_dict()
    if runs is None:
        runs = read_runs(ledger_path(create_dir=False))[0]
    ours = task_runs(runs, dispatch, now, since, verdict.notes)
    greens = [run for run in ours if AC.run_passed(run)]
    on_now = [run for run in greens if run_tree(run) == now]
    verdict.runs = [AC.stem(run) for run in on_now][-20:]
    _pythonpath_notes(verdict, on_now)
    base = dispatch.get("base_tree")
    if base == now:
        verdict.status("inputs_changed", "no", "no input file changed since dispatch")
        line = "no input file changed since dispatch, so no run is required (nothing was verified)"
        verdict.status("run_recorded", "not_required", line)
        verdict.status("inputs_unchanged", "not_required", line)
    else:
        changed = _changed_since(worktree, base if isinstance(base, str) and TREE_ID.fullmatch(base)
                                 else None, now) if base else "the dispatch record has no base tree"
        verdict.status("inputs_changed", "yes", f"changed since dispatch: {changed}")
        _inputs(verdict, dispatch, kind, greens, on_now)
    _committed(verdict, worktree)
    _checks(verdict, dispatch, greens, on_now)
    place = {id(run): run["run_seq"] if type(run.get("run_seq")) is int else seq
             for seq, run in enumerate(runs) if isinstance(run, dict)}
    _red_first(verdict, kind, ours, on_now, os.path.realpath(worktree), place, base)
    return _moded(verdict).as_dict()


def _moded(verdict: Verdict) -> Verdict:
    """checks.json handoff_evidence below 'gate' turns every problem into a note."""
    if verdict.problems and use_mode("handoff_evidence") != "gate":
        verdict.notes += [f"{p['line']} [rule: {p['rule']}] (handoff_evidence is not 'gate')"
                          for p in verdict.problems]
        verdict.problems = []
    return verdict
