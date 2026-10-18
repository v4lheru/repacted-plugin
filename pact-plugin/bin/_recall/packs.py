"""Skill-pack recall fragments: the T1 triggers an installed pack ships in recall.d/,
merged after core's recall/triggers.json.

Location: pact-plugin/bin/_recall/packs.py. Used by _recall/skills.py (all_triggers,
t1), _recall/merge.py (pointer_line), _recall/dispatch.py (the once-a-session note, pack pulls)
and bin/_doctor_state.py (check_packs). The format a pack author writes: reference/recall-packs.md.

Discovery: <config dir>/plugins/installed_plugins.json, the harness's own registry (its cache
dir also keeps orphaned versions, so the cache is never globbed). An entry counts when its scope
is user, or project/local with a projectPath that is the session's project, and no
enabledPlugins sets it false; a plugin with no recall.d is not a pack. Core (name `repacted`)
is never one, and an ASCII name only differing from another in case is a conflict. A `--plugin-dir` load is
not in the registry, so it adds no fragments (a known limit).
Merge: core first, unchanged; each pack's entries are ADDED under `<pack>:<skill>`, packs in
name order. Nothing is replaced: a key claimed twice is dropped everywhere and reported as a
conflict; a malformed fragment is skipped whole and reported as a problem.
A fragment is third-party input read on every tool event: size, count and length caps; strict
UTF-8; NaN/Infinity and duplicate keys refused; every path resolved inside the pack root and
read only as a regular file; ASCII names only; the pattern budget spent on raw list lengths
before anything compiles; regexes limited to one ambiguity point (one repeat or optional part, or
one alternation) with nothing nested in it, matched against a bounded cue (SUBJECT_MAX characters,
bounded()) (Python's re cannot be interrupted, and two adjacent repeats or a chain of `?` take
seconds on a short command). A matched skill's SKILL.md is read only up to SKILL_MD_MAX bytes.
"""
from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from _kb import datawrap as W            # bin/ is on sys.path (_recall/__init__.py)

try:                                     # 3.11+ moved the parser; sre_parse warns there
    from re import _parser as _sre
except ImportError:                      # pragma: no cover - Python 3.9 and 3.10
    import sre_parse as _sre             # type: ignore[no-redef]

CORE_NAME = "repacted"
REGISTRY = Path("plugins") / "installed_plugins.json"
FRAGMENT_DIR = "recall.d"
SKILL_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}", re.ASCII)          # the harness's skill names
PACK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)     # plugin names (some use capitals)
SPEC_KEYS = frozenset(("why", "commands", "hosts", "globs"))
TOP_KEYS = frozenset(("version", "about", "skills"))
HARNESS_FILE_MAX = 4 * 1024 * 1024       # registry and settings files: the harness's own
FRAGMENT_MAX = 64 * 1024
FRAGMENT_DEPTH = 8                       # the format needs 4: top, skills, one skill, a list
FRAGMENTS_MAX = 16                       # per pack
PACKS_MAX = 16
SKILLS_MAX = 64                          # per pack
PATTERNS_MAX = 16                        # per list
PACK_PATTERNS_MAX = 256                  # over all packs
PATTERN_LEN = 200
SKILL_MD_MAX = 256 * 1024                # a larger SKILL.md is withheld, never read in part
WHY_LEN = 300
SUBJECT_MAX = 1024                       # a pack regex sees at most this much of a command
HOST_MAX = 253                           # ... of a URL host (the DNS limit)
HOSTS_MAX = 16                           # ... and this many hosts
_REPEATS = tuple(getattr(_sre, n) for n in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
                 if hasattr(_sre, n))
_BACKREFS = tuple(getattr(_sre, n) for n in ("GROUPREF", "GROUPREF_EXISTS", "GROUPREF_IGNORE",
                                             "GROUPREF_LOC_IGNORE", "GROUPREF_UNI_IGNORE")
                  if hasattr(_sre, n))
_JSON_TOKEN = re.compile(r'["\\\[\]{}]')     # what _depth reads: quotes, escapes, brackets
_cache: dict = {}


class FragmentError(ValueError):
    """A fragment (or one entry of it) that is not loaded; the message is the doctor's reason."""


class Report:
    """What the merge found: the packs that ship a recall.d, conflicts and problems (one line
    each), and how many triggers were added."""

    def __init__(self) -> None:
        self.packs: list[str] = []
        self.conflicts: list[str] = []
        self.problems: list[str] = []
        self.triggers = 0

    def lines(self) -> list[str]:
        return [f"conflict: {c}" for c in self.conflicts] + [f"skipped: {p}" for p in self.problems]


# ─── untrusted reads ──────────────────────────────────────────────────────────────────────

def _refuse_constant(name: str):
    raise FragmentError(f"{name} is not a JSON value")


def _no_duplicates(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise FragmentError("a key appears twice")
    return dict(pairs)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _depth(text: str) -> int:
    """The deepest [ / { nesting of a JSON text, strings left out: json's own recursion limit
    differs between Pythons (3.14 parses 20000 levels), so the depth rule is checked here.
    One pass over the structural characters with the in-string and escape state (a regex
    over the strings restarts at every quote of a long run of escaped ones: 8 s on 64 KB)."""
    depth = deepest = 0
    in_string, escaped_at = False, -1
    for match in _JSON_TOKEN.finditer(text):
        at, ch = match.start(), match.group()
        if at == escaped_at:                 # the character a backslash escapes
            continue
        if in_string:
            if ch == "\\":
                escaped_at = at + 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def read_json(path: Path, limit: int, root: Optional[str] = None, max_depth: Optional[int] = None):
    """The parsed JSON of a regular file of at most `limit` bytes, resolved inside `root` when
    given and nested at most `max_depth` deep when given. Raises FragmentError with the reason."""
    real = os.path.realpath(path)
    if root is not None and not _inside(real, root):
        raise FragmentError("resolves outside the pack")
    try:
        info = os.stat(real)
        if not stat.S_ISREG(info.st_mode):
            raise FragmentError("not a regular file")
        if info.st_size > limit:
            raise FragmentError(f"larger than {limit} bytes")
        with open(real, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise FragmentError(f"unreadable ({exc.__class__.__name__})") from None
    if len(data) > limit:
        raise FragmentError(f"larger than {limit} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FragmentError("not UTF-8") from None
    if max_depth is not None and _depth(text) > max_depth:
        raise FragmentError("nested too deeply")
    try:
        return json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise FragmentError("nested too deeply") from None
    except FragmentError:
        raise
    except ValueError as exc:
        raise FragmentError(f"not JSON ({str(exc)[:60]})") from None


def _harness_json(path: Path) -> dict:
    try:
        value = read_json(path, HARNESS_FILE_MAX)
    except FragmentError:
        return {}
    return value if isinstance(value, dict) else {}


# ─── discovery ────────────────────────────────────────────────────────────────────────────

def _settings_files(config_dir: Path, cwd: Optional[str]) -> list[Path]:
    """User, then project, then local settings: a later file that names a plugin decides."""
    files = [config_dir / "settings.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    return files


def _disabled(config_dir: Path, cwd: Optional[str]) -> set:
    state: dict = {}
    for path in _settings_files(config_dir, cwd):
        enabled = _harness_json(path).get("enabledPlugins")
        if isinstance(enabled, dict):
            state.update({k: v for k, v in enabled.items() if isinstance(k, str)})
    return {key for key, value in state.items() if value is False}


def _in_scope(entry: dict, cwd: Optional[str]) -> bool:
    scope = entry.get("scope")
    if scope == "user":
        return True
    project = entry.get("projectPath")
    if scope not in ("project", "local") or not cwd or not isinstance(project, str) or not project:
        return False
    return _inside(os.path.realpath(cwd), os.path.realpath(project))


def discover(config_dir: Path, cwd: Optional[str], report: Report) -> list[tuple[str, str]]:
    """[(pack name, real install root)] of the installed, enabled, non-core packs, by name."""
    plugins = _harness_json(config_dir / REGISTRY).get("plugins")
    if not isinstance(plugins, dict):
        return []
    disabled = _disabled(config_dir, cwd)
    roots: dict[str, set] = {}
    for key, entries in plugins.items():
        if not isinstance(key, str) or key in disabled or not isinstance(entries, list):
            continue
        name = key.partition("@")[0]
        if name == CORE_NAME:
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not _in_scope(entry, cwd):
                continue
            path = entry.get("installPath")         # a plugin with no recall.d is not a pack
            if isinstance(path, str) and path and os.path.lexists(Path(path) / FRAGMENT_DIR):
                roots.setdefault(name, set()).add(os.path.realpath(path))
    folded: dict[str, list] = {}
    for name in roots:
        folded.setdefault(name.casefold(), []).append(name)
    found = []
    for name in sorted(roots):
        if not PACK_RE.fullmatch(name) or name.casefold() == CORE_NAME:
            report.problems.append(f"pack {ascii(name)[:70]}: not an ASCII plugin name other than core's")
        elif len(roots[name]) > 1 or len(folded[name.casefold()]) > 1:
            count = max(len(roots[name]), len(folded[name.casefold()]))
            report.conflicts.append(f"pack {name}: installed {count} times; none loaded")
        else:
            found.append((name, next(iter(roots[name]))))
    if len(found) > PACKS_MAX:
        report.problems.append(f"{len(found) - PACKS_MAX} pack(s) past the first {PACKS_MAX} not read")
    return found[:PACKS_MAX]


# ─── one fragment ─────────────────────────────────────────────────────────────────────────

def _check_text(value, what: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise FragmentError(f"{what} is not a string of at most {limit} characters")
    if any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\t\n" for ch in value):
        raise FragmentError(f"{what} holds a control or format character")
    return value


def _repeat_problem(items, in_repeat: bool, points: list) -> Optional[str]:
    """Why the parsed pattern could backtrack badly, or None; appends each ambiguity point (a
    repeat whose low and high differ, `?` included, or an alternation) to `points`."""
    for op, arg in items:
        if op in _BACKREFS:
            return "a back-reference"
        if op in _REPEATS:
            low, high, body = arg
            if high > 1 and in_repeat:
                return "a repeat inside a repeat"
            if low < high:
                points.append(op)
            children = [(body, in_repeat or high > 1)]
        elif op == _sre.BRANCH:
            if in_repeat:
                return "an alternation inside a repeat"
            if len(arg[1]) > 1:
                points.append(op)
            children = [(branch, in_repeat) for branch in arg[1]]
        elif op == _sre.SUBPATTERN:
            children = [(arg[-1], in_repeat)]
        elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
            children = [(arg[1], in_repeat)]
        elif op == getattr(_sre, "ATOMIC_GROUP", None):
            children = [(arg, in_repeat)]
        else:
            continue
        for child, nested in children:
            why = _repeat_problem(child, nested, points)
            if why:
                return why
    return None


def compile_pattern(pattern) -> "re.Pattern":
    """A pack regex compiled, or FragmentError: at most one ambiguity point over the whole
    pattern (one `*`, `+`, `?` or `{m,n}` with m < n, or one alternation), nothing repeated
    inside a repeat, no alternation inside it, no back-reference. Each point multiplies the
    ways a subject can match: 28 chained `?` stall a search on an 80-character command."""
    pattern = _check_text(pattern, "a pattern", PATTERN_LEN)
    try:
        parsed = _sre.parse(pattern)
    except (re.error, RecursionError, OverflowError) as exc:
        raise FragmentError(f"pattern {ascii(pattern)[:40]} does not compile ({exc})") from None
    points: list = []
    why = _repeat_problem(list(parsed), False, points)
    if not why and len(points) > 1:
        why = "more than one repeat or alternation"
    if why:
        raise FragmentError(f"pattern {ascii(pattern)[:40]} has {why}")
    return re.compile(pattern)


def _raw_lists(spec: dict) -> dict:
    """{field: raw list} of a spec's pattern fields, each checked to be a short list."""
    lists = {}
    for field in ("commands", "hosts", "globs"):
        values = spec.get(field, [])
        if not isinstance(values, list) or len(values) > PATTERNS_MAX:
            raise FragmentError(f"{field} is not a list of at most {PATTERNS_MAX}")
        lists[field] = values
    return lists


def _patterns(values: list, field: str) -> list:
    if field == "globs":
        return [_check_text(g, "a glob", PATTERN_LEN) for g in values]
    return [compile_pattern(p) for p in values]


def _skill_name(key, pack: str) -> str:
    """The bare skill name of a fragment key: `<skill>` or `<this pack>:<skill>`."""
    if not isinstance(key, str):
        raise FragmentError("a skill key is not a string")
    prefix, sep, bare = key.rpartition(":")
    if sep and prefix != pack:
        raise FragmentError(f"key {ascii(key)[:70]} names a skill outside pack {pack}")
    if not SKILL_RE.fullmatch(bare):
        raise FragmentError(f"key {ascii(key)[:70]} is not a lowercase ASCII skill name")
    return bare


def _file_label(pack: str, path: Path) -> str:
    """`<pack>/<file name>` fit for a report line: the name is the pack author's, so ascii()."""
    return f"{pack}/{ascii(path.name)[1:-1][:70]}"


def load_fragment(path: Path, pack: str, root: str,
                  budget: Optional[list] = None) -> tuple[dict, list[str], list[str]]:
    """({bare skill: compiled spec}, conflicts, skipped) of one fragment. A malformed file raises
    FragmentError; an entry naming a skill outside the pack is dropped as a conflict.
    `budget` is a one-item list of the patterns left over all packs: each entry spends its raw
    list lengths before any of its patterns compiles, and an entry past it is skipped (so a
    full pack cannot make every tool event compile thousands of patterns)."""
    data = read_json(path, FRAGMENT_MAX, root, FRAGMENT_DEPTH)
    if not isinstance(data, dict) or set(data) - TOP_KEYS or data.get("version") != 1 \
            or isinstance(data.get("version"), bool) or not isinstance(data.get("skills"), dict):
        raise FragmentError('not {"version": 1, "skills": {...}}')
    if len(data["skills"]) > SKILLS_MAX:
        raise FragmentError(f"more than {SKILLS_MAX} skills")
    entries, conflicts, skipped = {}, [], []
    for key, spec in data["skills"].items():
        try:
            bare = _skill_name(key, pack)
        except FragmentError as exc:
            if ":" in str(key):
                conflicts.append(f"{_file_label(pack, path)}: {exc}")
                continue
            raise
        if not isinstance(spec, dict) or set(spec) - SPEC_KEYS:
            raise FragmentError(f"{bare}: not an object of {', '.join(sorted(SPEC_KEYS))}")
        lists = _raw_lists(spec)
        if budget is not None:
            size = sum(len(values) for values in lists.values())
            if size > budget[0]:
                skipped.append(f"{pack}:{bare}: past the {PACK_PATTERNS_MAX}-pattern limit over all packs")
                continue
            budget[0] -= size
        skill_md = os.path.realpath(Path(root) / "skills" / bare / "SKILL.md")
        if not _inside(skill_md, root) or not os.path.isfile(skill_md):
            raise FragmentError(f"{bare}: the pack has no skills/{bare}/SKILL.md")
        compiled = {"why": _check_text(spec.get("why", ""), "why", WHY_LEN),
                    **{field: _patterns(values, field) for field, values in lists.items()},
                    "pack": pack, "skill": bare, "skill_md": skill_md}
        if not (compiled["commands"] or compiled["hosts"] or compiled["globs"]):
            raise FragmentError(f"{bare}: no pattern")
        entries[bare] = compiled
    return entries, conflicts, skipped


def _fragments(pack: str, root: str, report: Report) -> list[Path]:
    folder = Path(root) / FRAGMENT_DIR
    if not os.path.lexists(folder):
        return []
    real = os.path.realpath(folder)
    if not _inside(real, root) or not os.path.isdir(real):
        report.problems.append(f"{pack}/{FRAGMENT_DIR}: not a folder inside the pack")
        return []
    try:
        names = sorted(n for n in os.listdir(real) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        report.problems.append(f"{pack}/{FRAGMENT_DIR}: unreadable")
        return []
    if len(names) > FRAGMENTS_MAX:
        report.problems.append(f"{pack}/{FRAGMENT_DIR}: {len(names) - FRAGMENTS_MAX} file(s) past "
                               f"the first {FRAGMENTS_MAX} not read")
    return [Path(real) / n for n in names[:FRAGMENTS_MAX]]


# ─── the merge ────────────────────────────────────────────────────────────────────────────

def _signature(config_dir: Path, cwd: Optional[str], roots: Iterable[str]) -> tuple:
    def sig(path) -> tuple:
        try:
            st = os.stat(path)
            return (str(path), st.st_mtime_ns, st.st_size)
        except OSError:
            return (str(path),)
    parts = [sig(config_dir / REGISTRY)] + [sig(p) for p in _settings_files(config_dir, cwd)]
    for root in roots:
        folder = Path(root) / FRAGMENT_DIR
        parts.append(sig(folder))
        try:
            parts += [sig(folder / n) for n in sorted(os.listdir(folder))]
        except OSError:
            pass
    return (str(config_dir), cwd, tuple(parts))


def merge(core: dict, config_dir: Path, cwd: Optional[str] = None) -> tuple[dict, Report]:
    """(triggers, report): `core` itself when no pack adds a trigger, else a new dict holding
    core's entries first, unchanged, then every pack entry under `<pack>:<skill>`."""
    cached = _cache.get("last")
    if cached and cached[0] is core and cached[1] == _signature(config_dir, cwd, cached[2]):
        return cached[3], cached[4]
    report = Report()
    packs = discover(config_dir, cwd, report)
    claims: dict[str, list] = {}
    raw_budget = [PACK_PATTERNS_MAX]
    for pack, root in packs:
        report.packs.append(pack)
        for path in _fragments(pack, root, report):
            try:
                entries, conflicts, skipped = load_fragment(path, pack, root, raw_budget)
            except FragmentError as exc:
                report.problems.append(f"{pack}/{FRAGMENT_DIR}/{ascii(path.name)[1:-1][:70]}: {exc}")
                continue
            report.conflicts += conflicts
            report.problems += skipped
            for bare, spec in entries.items():
                claims.setdefault(f"{pack}:{bare}", []).append((_file_label(pack, path), spec))
    merged = dict(core)
    for key in sorted(claims):
        owners = claims[key]
        if key in core or len(owners) > 1:
            where = ", ".join(["core"] * (key in core) + [o for o, _ in owners])
            report.conflicts.append(f"{key}: claimed by {where}; dropped")
            continue
        merged[key] = owners[0][1]
        report.triggers += 1
    result = merged if report.triggers else core
    _cache["last"] = (core, _signature(config_dir, cwd, [r for _, r in packs]), [r for _, r in packs],
                      result, report)
    return result, report


# ─── what leaves ──────────────────────────────────────────────────────────────────────────

def bounded(cue: dict) -> dict:
    """The cue a pack's patterns see: command, hosts and path cut to the caps above."""
    return {"command": cue["command"][:SUBJECT_MAX], "path": cue["path"][:SUBJECT_MAX],
            "hosts": [host[:HOST_MAX] for host in cue["hosts"][:HOSTS_MAX]]}


def skill_text(spec: dict) -> Optional[str]:
    """A matched pack skill's SKILL.md text ("" when unreadable), or None when it is larger
    than SKILL_MD_MAX: the caller withholds it whole, since a cut read could miss a credential."""
    try:
        with open(spec["skill_md"], "rb") as handle:
            data = handle.read(SKILL_MD_MAX + 1)
    except OSError:
        return ""
    if len(data) > SKILL_MD_MAX:
        return None
    return data.decode("utf-8", errors="replace")


def data_line(cand: dict) -> str:
    """A pack's `why` as one data element, the way KB text is shown (datawrap.element)."""
    return W.element(f"pack/{cand['pack']}", f"skills/{cand['skill']}/SKILL.md", "pack",
                     cand.get("summary", ""))
