"""Test-runner detection and summary parsing, shared by bin/tcount and hooks/run_ledger.py.

A count is reported only when a runner SUMMARY is found; exit status alone is never a count
(rtk-wrapped runners exit 0 after a spawn failure; a pipe reports the last status).
Parses pytest, vitest, jest, go test, playwright, cargo, node --test, mocha, dotnet test
(summed over projects) and the rtk one-line forms (rtk 0.41.0).

Map, in file order: command detection (detect_runner, tcount_runs, core_argv), the worker
cap (cap_workers), summary parsing (the _parse_* functions), tcount reports, parse_counts.
See the design notes
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from shell.words import (Unreadable, read_script, substitution_spans, token_spans,  # the shared reader
                         words as shell_words)

MAX_FAILING_IDS = 20

# ---------------------------------------------------------------------------
# Command detection
# ---------------------------------------------------------------------------

# One shell segment is a test run when it matches any of these (after stripping
# leading VAR=value assignments and wrappers such as `rtk`, `npx`, `uv run`).
# A runner word is ended by a blank, the end of the segment, or a shell word end (_WORD_END),
# never by a dot or a word character: `pytest.ini` runs nothing; `(cd web && pytest)`,
# `pytest>out.log` and `bash -c "cd web && pytest"` are runs. Case arms are removed first (_segments).
_WORD_END = r"""(?=[\s)<>&;|"'`]|$)"""
_RUNNER_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # bare `tcount`, a path to it, or `python3 <path>/tcount` (quoted paths may hold spaces)
    ("tcount", re.compile(r"""^(?:python3?\s+)?(?:'[^']*/tcount'|"[^"]*/tcount"|(?:\S*/)?tcount)""" + _WORD_END)),
    # run-tests runs each suite through tcount and prints its `log:` lines (not --status)
    ("tcount", re.compile(r"""^(?:python3?\s+)?(?:'[^']*/run-tests'|"[^"]*/run-tests"|(?:\S*/)?run-tests)"""
                          + _WORD_END + r"(?!\s*--status\b)")),
    # bare pytest, or `python -m pytest` with python bare or by path (sys.executable; a quoted
    # path may hold spaces): a run not known to be pytest is read by every parser
    ("pytest", re.compile(r"""^(?:(?:'[^']*/python3?(?:\.\d+)?'|"[^"]*/python3?(?:\.\d+)?"|(?:\S*/)?python3?(?:\.\d+)?)"""
                          r"""\s+-m\s+)?pytest""" + _WORD_END)),
    ("vitest", re.compile(r"^vitest" + _WORD_END)),
    ("jest", re.compile(r"^jest" + _WORD_END)),
    ("go", re.compile(r"^go\s+test\b")),
    ("cargo", re.compile(r"^cargo\s+(?:test|nextest)\b")),
    ("playwright", re.compile(r"^playwright\s+test\b")),
    ("node", re.compile(r"^node(?=\s).*\s--test\b")),
    ("mocha", re.compile(r"^mocha" + _WORD_END)),
    # `dotnet test` / `dotnet vstest`, bare or by path (a quoted path may hold spaces)
    ("dotnet", re.compile(r"""^(?:'[^']*/dotnet'|"[^"]*/dotnet"|(?:\S*/)?dotnet)\s+(?:test|vstest)\b""")),
    ("npm", re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b")),
)
# nice, env and time are matched with their options and by path (`/usr/bin/nice -19`,
# `env -i`, `time -p`). An assignment may be quoted: shlex.join quotes one that holds a space.
# `env-get FILE KEY[,KEY...] --` runs the command after its `--` (only its full form).
_ENV_GET = (r"(?:\S*/)?env-get\s+(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)"
            r"\s+(?:[A-Z_][A-Z0-9_,]*|'[A-Z_][A-Z0-9_,]*'|\"[A-Z_][A-Z0-9_,]*\")\s+(?:--|'--'|\"--\")")
_WRAPPERS = re.compile(
    r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S*|'[A-Za-z_][A-Za-z0-9_]*=[^']*')\s+)*"     # env assignments
    r"(?:(?:" + _ENV_GET + r"|rtk(?:\s+proxy)?|npx|pnpm\s+exec|pnpm\s+dlx|yarn\s+dlx|bunx|uv\s+run|poetry\s+run"
    r"|(?:\S*/)?nice(?:\s+(?:-n\s*-?\d+|-\d+|--adjustment(?:=|\s+)-?\d+))?"
    r"|(?:\S*/)?time(?:\s+-[plh]+)*"
    r"|(?:\S*/)?env(?:\s+(?:-i|--ignore-environment|-u\s*\S+|--unset=\S+))*"
    r")\s+)*"
)
# A shell keyword in front of a command (`do tcount ...`, `then pytest`, `! pytest`, `{ tcount x; }`).
_KEYWORDS = re.compile(r"^(?:(?:do|then|else|elif|if|while|until|!|\{)\s+)+")


def _strip_wrappers(segment: str) -> str:
    segment = segment.strip().lstrip("(").strip()
    previous = None
    while previous != segment:
        previous = segment
        segment = _KEYWORDS.sub("", segment, count=1)
        segment = _WRAPPERS.sub("", segment, count=1).strip()
    return segment


def command_lines(command: str) -> list[str]:
    """The lines of a Bash command as the shell runs them, one command line each. A newline ends
    a line unless it is inside quotes; a backslash-newline joins two lines; a comment is dropped,
    and so is the body of a here-document, which is data (a script written through `cat <<EOF`
    that holds a line `pytest -q` runs nothing). shell.words.read_script reads it; what it
    cannot (Unreadable) is taken line by line as written. 1.3.5 field report: a `tcount ...`
    line under an `export ...` line was no segment's start, so its 13 runs were never attributed."""
    text = (command or "").replace("\r\n", "\n")
    try:
        script = read_script(text)[0]
    except Unreadable:
        return text.split("\n")
    spans = token_spans(script, "\n")
    if spans is None:
        return script.split("\n")
    lines, at = [], 0
    for start, end, _token in spans:
        if script[start] == "\n":
            lines.append(script[at:start])
            at = end
    lines.append(script[at:])
    return lines


# `case WORD in`, anywhere in a line (after `then`, inside `$(`), and what ends one of its arms.
_CASE_IN = r"""(?<![\w-])case\s+(?:"[^"]*"|'[^']*'|\$\([^)]*\)|\S+)\s+in(?=\s|$)"""
_ESAC = r"(?<![\w-])esac(?![\w-])"
_CASE_EVENT = re.compile(f"{_CASE_IN}|;;&?|;&|{_ESAC}")
_AT_ESAC = re.compile(rf"\s*{_ESAC}")
# The pattern that opens an arm: `pytest)`, `(unit)`, `tcount|run-tests)`, `"a b"|*)`.
_ARM_WORD = r"""(?:"[^"]*"|'[^']*'|[^\s()|;&"']+)"""
_ARM = re.compile(rf"\s*\(?\s*{_ARM_WORD}(?:\s*\|\s*{_ARM_WORD})*\s*\)")


def _segments(command: str) -> list[str]:
    """Every command segment: each command line (command_lines) split on &&, ||, ; and |, then
    the segments of each $(...) or `...` it runs (`seen="$(cd w && pytest)"` runs pytest).

    The pattern that opens an arm of a `case` is not a command and is blanked first: the arm
    `pytest)` or `tcount|run-tests)` runs nothing, and what follows the pattern on its line is
    read as any other command (`unit) (cd web && pytest) ;;` runs pytest). A pattern is looked
    for only where the shell expects one: after `case WORD in` and after the `;;` of an arm,
    until `esac`. The arm scan is not quote-aware."""
    segments: list[str] = []
    cases, arm = 0, False           # open `case`s; whether a pattern is expected next
    for line in command_lines(command):
        kept, at = [], 0
        while True:
            if arm and line[at:].strip() and not _AT_ESAC.match(line, at):
                pattern = _ARM.match(line, at)
                if pattern:
                    kept.append(" " * (pattern.end() - at))
                    at = pattern.end()
                arm = False
            event = _CASE_EVENT.search(line, at)
            if event is None:
                kept.append(line[at:])
                break
            kept.append(line[at:event.end()])
            at, word = event.end(), event.group()
            if word.startswith("case"):
                cases, arm = cases + 1, True
            elif word == "esac":
                cases, arm = max(0, cases - 1), False
            elif cases:
                arm = True
        segments.extend(_split_segments("".join(kept)))
        try:
            inner = substitution_spans("".join(kept))
        except Unreadable:
            inner = []
        for _start, _end, command in inner:
            segments.extend(_segments(command))
    return segments


def _split_segments(line: str) -> list[str]:
    """The text between the control operators of one command line (`&&`, `||`, `;`, `|`,
    `&`), read with the shared tokeniser so an operator inside quotes splits nothing; a line
    whose quoting cannot be read is one segment."""
    spans = token_spans(line, "|;&")
    if spans is None:
        return [line.strip()]
    segments, at = [], 0
    for start, end, _token in spans:
        if line[start] in "|;&":
            segments.append(line[at:start].strip())
            at = end
    segments.append(line[at:].strip())
    return segments


def detect_runner(command: str) -> Optional[str]:
    """Name of the test runner a shell command invokes, or None when it is not a test run.

    The command is read line by line (command_lines) and each line is split on &&, ||, ; and |;
    the first segment that is a test run wins. "tcount" is returned for a tcount invocation.
    """
    if not command:
        return None
    for segment in _segments(command):
        core = _strip_wrappers(segment)
        # `pnpm vitest` / `yarn jest`: a package manager running a runner binary.
        core = re.sub(r"^(?:pnpm|yarn|bun)\s+(?=(?:vitest|jest|mocha|playwright)\b)", "", core)
        for name, pattern in _RUNNER_PATTERNS:
            if pattern.search(core):
                return name
    return None


def runs_tcount(command: str) -> bool:
    """True when ANY segment of a shell command runs tcount or run-tests. detect_runner names the
    first test run only, so `pytest --co -q; tcount pytest` reads as pytest there; the run-ledger
    hook asks this first, because a tcount report is read by its own rules (tcount_lines)."""
    return tcount_runs(command) > 0


def tcount_runs(command: str) -> int:
    """How many segments of a shell command run tcount or run-tests: the most tcount runs the
    call can have started by its own text (a loop or a script starts more; the run-ledger hook
    adopts no more unseen rows than this)."""
    count = 0
    for segment in _segments(command or ""):
        core = _strip_wrappers(segment)
        if any(name == "tcount" and pattern.search(core) for name, pattern in _RUNNER_PATTERNS):
            count += 1
    return count


def runner_from_argv(argv: list[str]) -> Optional[str]:
    """detect_runner for an argv list (tcount's own command)."""
    return detect_runner(shlex.join(argv)) if argv else None


def core_argv(argv: list[str]) -> list[str]:
    """argv without its leading wrappers (`nice -n 19`, `env VAR=x`, `time -p`, `rtk`): the
    command itself, whose name and flags are the run's. argv as given when nothing is left."""
    core = shell_words(_strip_wrappers(shlex.join(argv))) or []
    return core or list(argv)


def command_name(argv: list[str]) -> str:
    """The program an argv runs, for naming a run no pattern knows: `nice -n 19 ./gate.sh` is
    'gate.sh', never 'nice' (a log named after the wrapper says nothing about the run)."""
    program = core_argv(argv)[0]
    return os.path.basename(program) or program


# ---------------------------------------------------------------------------
# Worker cap (tcount)
# ---------------------------------------------------------------------------

MAX_WORKERS = 2
# The cap is decided on argv tokens, never on a shell string: tcount runs no shell, so an argv
# that holds one of these as a token is not a command line this module can read.
_SHELL_TOKENS = frozenset({"&&", "||", "|", ";", "&", "(", ")", "{", "}", "!",
                           "do", "then", "else", "elif", "if", "while", "until"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_LAUNCHERS = frozenset({"nice", "time", "npx", "pnpm", "yarn", "bunx", "uv", "poetry", "rtk"})
_PYTHON = re.compile(r"^python3?(?:\.\d+)?$")
_JS_RUNNERS = ("vitest", "jest")
_JS_MANAGERS = ("pnpm", "yarn", "bun")          # `pnpm vitest`, as detect_runner reads it
# A flag by which the command sets its own worker count: such a command is left alone.
# vitest's -w is --watch; jest's is --maxWorkers.
_OWN_WORKER_FLAG = {
    "vitest": re.compile(r"^--(?:maxWorkers|max-workers|poolOptions\..+|fileParallelism|file-parallelism"
                         r"|no-file-parallelism|no-fileParallelism|singleThread|single-thread|no-threads)(?:=.*)?$"),
    "jest": re.compile(r"^(?:--maxWorkers|--max-workers|--runInBand|-i|-w\S*)(?:=.*)?$"),
    "dotnet": re.compile(r"^(?:(?:--?|/)(?:m|maxcpucount)(?::.*)?|--max-parallel-test-modules(?:=.*)?)$", re.I),
}
_XDIST_ATTACHED = re.compile(r"^(?:(-n)(=?)|(--numprocesses)(=))(.+)$", re.S)


def _wrapper_prefix(argv: list[str]) -> Optional[int]:
    """How many leading tokens of argv are wrappers (core_argv's reading, checked token by
    token), or None when argv is not a plain command: a shell token anywhere, a prefix that
    core_argv did not cut at a token, or an assignment that no `env` in front of it takes."""
    if not argv or any(token in _SHELL_TOKENS for token in argv):
        return None
    core = core_argv(argv)
    start = len(argv) - len(core)
    if start < 0 or argv[start:] != core:
        return None
    after_env = False
    for token in argv[:start]:
        name = os.path.basename(token)
        if _ASSIGNMENT.match(token):
            if not after_env:
                return None
        elif name == "env":
            after_env = True
        elif name in _LAUNCHERS:
            after_env = False
    return start


def runs_under_nice(argv: list[str]) -> bool:
    """True when argv already runs its command under `nice` (a wrapper in front of it)."""
    start = _wrapper_prefix(argv)
    return bool(start) and any(os.path.basename(token) == "nice" for token in argv[:start])


def _over_cap(value: str) -> bool:
    """A pytest-xdist worker count above the cap: `auto`, `logical`, or a number read as xdist
    reads it (int(): "+8", " 8", "8_0" and digits of any script are numbers)."""
    if value.strip().lower() in ("auto", "logical"):
        return True
    try:
        return int(value) > MAX_WORKERS
    except ValueError:
        return False


def _add_worker_flag(argv: list[str], at: int, runner: str, flag: str) -> tuple[list[str], Optional[str]]:
    """argv with flag among the runner's arguments (argv[at:]): in front of a `--` of its own,
    else last. Unchanged when an argument already sets the worker count."""
    args = argv[at:]
    if any(_OWN_WORKER_FLAG[runner].match(arg) for arg in args):
        return argv, None
    end = at + args.index("--") if "--" in args else len(argv)
    return [*argv[:end], flag, *argv[end:]], f"added {flag}"


def _cap_xdist(argv: list[str], at: int) -> tuple[list[str], Optional[str]]:
    """argv with every pytest-xdist worker count above the cap (argv[at:], up to a `--`) set to
    the cap. A pytest run with no such count is serial already and is left alone."""
    end = at + argv[at:].index("--") if "--" in argv[at:] else len(argv)
    capped, lowered, index = list(argv), [], at
    while index < end:
        arg = argv[index]
        if arg in ("-n", "--numprocesses"):
            if index + 1 < end and _over_cap(argv[index + 1]):
                capped[index + 1] = str(MAX_WORKERS)
                lowered.append(arg)
            index += 2
            continue
        attached = _XDIST_ATTACHED.match(arg)
        if attached and _over_cap(attached.group(5)):
            flag, sign = attached.group(1) or attached.group(3), attached.group(2) or attached.group(4) or ""
            capped[index] = f"{flag}{sign}{MAX_WORKERS}"
            lowered.append(flag)
        index += 1
    if not lowered:
        return argv, None
    return capped, f"lowered {lowered[0]} to {MAX_WORKERS}"


def cap_workers(argv: list[str]) -> tuple[list[str], Optional[str]]:
    """(the argv to run, what was changed) for tcount's command; (argv, None) when nothing is.

    vitest and jest get `--maxWorkers=2`, `dotnet test` and `dotnet build` get `-m:2`, each only
    when the command sets no worker count of its own (_OWN_WORKER_FLAG). pytest is serial unless
    it carries pytest-xdist's `-n`, so it is changed only when that count is above 2 or `auto`.
    The runner is the program behind the wrappers core_argv sees through, read as a token: a
    runner word inside a quoted argument (`bash -c "npx vitest run"`, one argv token), a shell
    operator, or a runner this function does not know leaves argv as it is.
    """
    argv = list(argv)
    start = _wrapper_prefix(argv)
    if start is None:
        return argv, None
    core = argv[start:]
    name, first = os.path.basename(core[0]), 1
    if name in _JS_MANAGERS and len(core) > 1 and core[1] in _JS_RUNNERS:
        name, first = core[1], 2
    if name in _JS_RUNNERS:
        return _add_worker_flag(argv, start + first, name, f"--maxWorkers={MAX_WORKERS}")
    if name == "dotnet" and len(core) > 1 and core[1] in ("test", "build"):
        if any(arg.lower().endswith((".dll", ".exe")) for arg in core[2:]):
            return argv, None       # `dotnet test A.dll` is vstest: it takes no MSBuild switch
        return _add_worker_flag(argv, start + 2, "dotnet", f"-m:{MAX_WORKERS}")
    if name in ("pytest", "py.test"):
        return _cap_xdist(argv, start + 1)
    if _PYTHON.match(name) and core[1:3] == ["-m", "pytest"]:
        return _cap_xdist(argv, start + 3)
    return argv, None


# ---------------------------------------------------------------------------
# Summary parsing
# ---------------------------------------------------------------------------


@dataclass
class Counts:
    """Parsed result of one run. collected counts tests that ran or were skipped."""
    runner: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    collected: Optional[int] = None
    failing_ids: list[str] = field(default_factory=list)
    projects: Optional[int] = None      # per-project summaries summed into this count (dotnet)

    def finish(self) -> "Counts":
        if self.collected is None:
            self.collected = self.passed + self.failed + self.errors + self.skipped
        return self

    def as_ledger_fields(self) -> dict:
        return {"runner": self.runner, "collected": self.collected, "passed": self.passed,
                "failed": self.failed, "errors": self.errors, "skipped": self.skipped}


# word -> Counts attribute; None means "seen, not counted" (deselected, warnings, ...)
_WORD_BUCKETS = {
    "passed": "passed", "passing": "passed", "pass": "passed", "xpassed": "passed",
    "failed": "failed", "failing": "failed", "fail": "failed", "failures": "failed",
    "error": "errors", "errors": "errors", "interrupted": "errors",
    "skipped": "skipped", "skip": "skipped", "pending": "skipped", "todo": "skipped",
    "xfailed": "skipped", "ignored": "skipped", "did not run": "skipped",
    "flaky": "passed", "cancelled": "errors",
    "deselected": None, "warning": None, "warnings": None, "rerun": None,
    "measured": None, "filtered out": None, "total": None, "snapshots": None,
    "subtests": None,
}
_PAIR = re.compile(r"(\d+)\s+(did not run|filtered out|[a-z]+)")


def _add_pairs(counts: Counts, text: str) -> bool:
    """Add every '<n> <word>' pair in text to counts. True when at least one counted."""
    found = False
    for number, word in _PAIR.findall(text):
        if word in _WORD_BUCKETS:
            found = True
            bucket = _WORD_BUCKETS[word]
            if bucket:
                setattr(counts, bucket, getattr(counts, bucket) + int(number))
    return found


def _clean(text: str) -> list[str]:
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    return [ansi.sub("", line.rstrip("\r")) for line in text.splitlines()]


def _parse_jest(lines: list[str]) -> Optional[Counts]:
    for line in reversed(lines):
        match = re.match(r"^\s*Tests:\s+(.*?)(\d+)\s+total", line)
        if match:
            counts = Counts("jest")
            _add_pairs(counts, match.group(1))
            counts.collected = int(match.group(2))
            return counts
    return None


def _parse_vitest(lines: list[str]) -> Optional[Counts]:
    for line in reversed(lines):
        match = re.match(r"^\s*Tests\s{2,}(.+?)\s*\((\d+)\)\s*$", line)
        if match:
            counts = Counts("vitest")
            _add_pairs(counts, match.group(1).replace("|", ","))
            counts.collected = int(match.group(2))
            return counts
        if re.match(r"^\s*No test files found", line):
            return Counts("vitest", collected=0)
    return None


def _parse_node(lines: list[str]) -> Optional[Counts]:
    stats: dict[str, int] = {}
    for line in lines:
        match = re.match(r"^\s*(?:ℹ|#)\s+(tests|pass|fail|skipped|todo|cancelled)\s+(\d+)\s*$", line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    if "tests" not in stats:
        return None
    return Counts("node", passed=stats.get("pass", 0), failed=stats.get("fail", 0),
                  errors=stats.get("cancelled", 0),
                  skipped=stats.get("skipped", 0) + stats.get("todo", 0),
                  collected=stats["tests"])


def _parse_cargo(lines: list[str]) -> Optional[Counts]:
    counts, found = Counts("cargo"), False
    pattern = re.compile(r"test result: \w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored")
    for line in lines:
        match = pattern.search(line)
        if match:
            found = True
            counts.passed += int(match.group(1))
            counts.failed += int(match.group(2))
            counts.skipped += int(match.group(3))
    return counts if found else None


def _parse_mocha(lines: list[str]) -> Optional[Counts]:
    counts, found = Counts("mocha"), False
    for line in lines:
        match = re.match(r"^\s+(\d+)\s+(passing|failing|pending)\b", line)
        if match:
            found = True
            _add_pairs(counts, f"{match.group(1)} {match.group(2)}")
    return counts if found else None


def _parse_playwright(lines: list[str]) -> Optional[Counts]:
    counts, found = Counts("playwright"), False
    pattern = re.compile(r"^\s+(\d+)\s+(passed|failed|flaky|skipped|interrupted|did not run)(?:\s+\(.*\))?\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            found = True
            _add_pairs(counts, f"{match.group(1)} {match.group(2)}")
    return counts if found else None


GO_RUNNERS = ("go", "go-packages")
_GO_BUCKETS = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped",
               "pass": "passed", "fail": "failed", "skip": "skipped"}
# `go test` without -v names no passing test, only its packages: `ok  \tpkg\t0.01s` (or
# `(cached)`), `FAIL\tpkg\t0.02s` (or `[build failed]`), `?   \tpkg\t[no test files]`.
_GO_PACKAGE = re.compile(r"^(ok|FAIL|\?)\s+(\S+)\s+(?:[\d.]+s\b|\(cached\)|\[(?:no test files|build failed|"
                         r"setup failed)\])")


def _parse_go_json(lines: list[str]) -> Optional[Counts]:
    """`go test -json`: one test2json event per line; a pass, fail or skip event that names a
    Test is one test result (a package's own event names none)."""
    counts, found = Counts("go"), False
    for line in lines:
        if not line.startswith('{"'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        bucket = _GO_BUCKETS.get(event.get("Action")) if isinstance(event, dict) else None
        if bucket and isinstance(event.get("Test"), str) and event["Test"]:
            found = True
            setattr(counts, bucket, getattr(counts, bucket) + 1)
            if bucket == "failed" and len(counts.failing_ids) < MAX_FAILING_IDS:
                counts.failing_ids.append(event["Test"])
    return counts if found else None


def _parse_go(lines: list[str]) -> Optional[Counts]:
    """go test: -json events, else the verbose `--- PASS/FAIL/SKIP` test lines, else (no -v)
    the package lines, counted as packages under the runner name go-packages so that a count
    of packages never reads as a count of tests."""
    json_counts = _parse_go_json(lines)
    if json_counts is not None:
        return json_counts
    counts, packages, found, verbose = Counts("go"), Counts("go-packages"), False, False
    failed: list[str] = []
    for line in lines:
        verbose = verbose or line.startswith("=== RUN")
        match = re.match(r"^\s*--- (PASS|FAIL|SKIP): ", line)
        if match:
            found = True
            bucket = _GO_BUCKETS[match.group(1)]
            setattr(counts, bucket, getattr(counts, bucket) + 1)
            name = line.split(":", 1)[1].split()[:1]
            if bucket == "failed" and name and name[0] not in failed and len(failed) < MAX_FAILING_IDS:
                failed.append(name[0])      # the test, never the package's own FAIL line
            continue
        package = _GO_PACKAGE.match(line)
        if package:
            bucket = {"ok": "passed", "FAIL": "failed", "?": "skipped"}[package.group(1)]
            setattr(packages, bucket, getattr(packages, bucket) + 1)
    counts.failing_ids, packages.failing_ids = failed, list(failed)
    if found and (verbose or counts.passed or counts.skipped):
        return counts
    if packages.passed or packages.failed or packages.skipped:
        return packages
    return counts if found else None


# Any "<n> <words>" list before " in <t>s": an allowlist of words here turned every plugin-added
# category (pytest-subtests' "8 subtests passed") into NO TEST COUNT. _add_pairs tallies only
# the known words; subtest counts are sub-results and are not tallied (the runner's exit code
# still carries a subtest failure).
_PYTEST_SUMMARY = re.compile(r"^=*\s*(\d+ [a-z][a-z ]*(?:, \d+ [a-z][a-z ]*)*) in [\d.]+s\b")


def _parse_pytest(lines: list[str]) -> Optional[Counts]:
    for line in reversed(lines):
        if re.match(r"^=*\s*no tests ran\b", line):
            return Counts("pytest", collected=0)
        match = _PYTEST_SUMMARY.match(line)
        if match:
            counts = Counts("pytest")
            if _add_pairs(counts, match.group(1)):   # "12 files in 3s" is not a test summary
                return counts
    return None


def _parse_pytest_all(lines: list[str]) -> Optional[Counts]:
    """Every pytest summary of a wrapper's log (`sh -c 'pytest a; pytest b'`, `make test`),
    summed: reading only the last let a red first suite vanish from the count (aud-bugs #25).
    projects says how many were summed."""
    total, runs = Counts("pytest", collected=0), 0
    for line in lines:
        match = _PYTEST_SUMMARY.match(line)
        part = Counts("pytest")
        if match and _add_pairs(part, match.group(1)):
            runs += 1
        elif re.match(r"^=*\s*no tests ran\b", line):
            runs += 1
        else:
            continue
        part.finish()
        for name in ("passed", "failed", "errors", "skipped", "collected"):
            setattr(total, name, getattr(total, name) + getattr(part, name))
    if not runs:
        return None
    total.projects = runs
    return total


# rtk's compressed one-liners, e.g. "Pytest: 3 passed, 1 failed", "Pytest: No tests collected".
_RTK_LINE = re.compile(r"^(Pytest|Go test|Tests|Vitest|Jest|Playwright|cargo test|Cargo test)\s*:\s*(.*)$")
_RTK_RUNNER = {"pytest": "pytest", "go test": "go", "tests": "tests", "vitest": "vitest",
               "jest": "jest", "playwright": "playwright", "cargo test": "cargo"}


def _parse_rtk(lines: list[str]) -> Optional[Counts]:
    for line in lines:
        match = _RTK_LINE.match(line.strip())
        if not match:
            continue
        counts = Counts(_RTK_RUNNER[match.group(1).lower()])
        if re.search(r"\bno tests (?:collected|found|ran)\b", match.group(2), re.I):
            counts.collected = 0
            return counts
        if _add_pairs(counts, match.group(2).lower()):
            return counts
    return None


# dotnet test (VSTest console logger), captured 2026-10-01 from field logs (VSTest 18.7.0, net8.0).
# Minimal verbosity (the default) prints one line per test assembly:
#   Failed!  - Failed:    39, Passed:    39, Skipped:     0, Total:    78, Duration: 15 s - A.Tests.dll (net8.0)
# Normal verbosity prints one block per test assembly:
#   Total tests: 53 / "     Passed: 51" / "     Failed: 2" / " Total time: 1,1287 Seconds"
# A run over several projects prints one of these per project and no total of its own, so the
# count is their sum. Both forms are anchored at column 0 and matched whole: the logger indents
# a test's own output, so a summary a test prints is not counted, and a line that does not fit
# (a localised CLI, thousands separators, a cut-off line) is no count rather than a wrong one.
_DOTNET_LINE = re.compile(r"^(?:Passed|Failed|Skipped)!\s+-\s+Failed:\s+(\d+), Passed:\s+(\d+), "
                          r"Skipped:\s+(\d+), Total:\s+(\d+), Duration: [^-]* - (\S.*)$")
_DOTNET_TOTAL = re.compile(r"^Total tests: (\d+)\s*$")
_DOTNET_PART = re.compile(r"^\s+(Passed|Failed|Skipped): (\d+)\s*$")
_DOTNET_TIME = re.compile(r"^\s*Total time: ")
_DOTNET_NO_TESTS = re.compile(r"^No test (?:is available in|matches the given testcase filter)\b")
# rtk 0.41.0 keeps only this line of a passing run. Its "fail dotnet test: ..." line is not
# read: it reports a build error as "1 failed" and a normal-verbosity run as "0 passed".
_DOTNET_RTK_OK = re.compile(r"^ok dotnet test: (\d+) tests? passed\b")


def _dotnet_blocks(lines: list[str]) -> Optional[list[tuple[int, int, int, int]]]:
    """(failed, passed, skipped, total) per normal-verbosity block, or None when a block is
    malformed (a part named twice, parts above the total, no closing "Total time:" line), as
    the interleaved blocks of parallel projects are: their parts cannot be told apart."""
    blocks, index = [], 0
    while index < len(lines):
        total = _DOTNET_TOTAL.match(lines[index])
        index += 1
        if not total:
            continue
        parts: dict[str, int] = {}
        while index < len(lines) and (part := _DOTNET_PART.match(lines[index])):
            if part.group(1) in parts:
                return None
            parts[part.group(1)] = int(part.group(2))
            index += 1
        closed = index < len(lines) and _DOTNET_TIME.match(lines[index])
        if not closed or sum(parts.values()) > int(total.group(1)):
            return None
        blocks.append((parts.get("Failed", 0), parts.get("Passed", 0), parts.get("Skipped", 0),
                       int(total.group(1))))
    return blocks


def _parse_dotnet(lines: list[str]) -> Optional[Counts]:
    by_assembly: dict[str, tuple[int, int, int, int]] = {}
    for line in lines:
        match = _DOTNET_LINE.match(line)
        if not match:
            continue
        failed, passed, skipped, total = (int(group) for group in match.groups()[:4])
        if failed + passed + skipped > total:
            return None
        # One assembly counts once. Printed twice (a retry loop, or a line that copies a real
        # one), the lower pass count stands: the sum would claim tests that do not exist.
        seen = by_assembly.get(match.group(5))
        if seen is None or (passed, -failed) < (seen[1], -seen[0]):
            by_assembly[match.group(5)] = (failed, passed, skipped, total)
    blocks = _dotnet_blocks(lines)
    if blocks is None:
        return None
    summaries = [*by_assembly.values(), *blocks]
    if not summaries:
        for line in lines:
            match = _DOTNET_RTK_OK.match(line)
            if match:
                return Counts("dotnet", passed=int(match.group(1)))
        if any(_DOTNET_NO_TESTS.match(line) for line in lines):
            return Counts("dotnet", collected=0)
        return None
    counts = Counts("dotnet", collected=0, projects=len(summaries))
    for failed, passed, skipped, total in summaries:
        counts.failed += failed
        counts.passed += passed
        counts.skipped += skipped
        counts.collected += total
    return counts


def format_tcount_line(counts: Counts, shown_exit: str) -> str:
    """tcount's counts line; _parse_tcount reads it back (the hook records tcount runs too)."""
    return (f"{counts.runner}: collected {counts.collected} · passed {counts.passed} · "
            f"failed {counts.failed} · errors {counts.errors} · skipped {counts.skipped} · "
            f"exit {shown_exit}")


_TCOUNT_COUNTS = (r"([\w.-]+): collected (\d+) · passed (\d+) · failed (\d+) · "
                  r"errors (\d+) · skipped (\d+) · exit (\S+)$")
_TCOUNT_LINE = re.compile("^" + _TCOUNT_COUNTS)
# The same line as a Bash call shows it: after `grep -n` (a "12:" prefix), or in run-tests'
# closing summary ("run-tests: plugin     pytest: collected ...").
_TCOUNT_SHOWN = re.compile(r"^(?:\d+[:-])?(?:run-tests: \S+\s+)?" + _TCOUNT_COUNTS)
_TCOUNT_LOG_SHOWN = re.compile(r"^(?:\d+[:-])?log: (\S.*)$")


def _parse_tcount(lines: list[str]) -> Optional[Counts]:
    for line in lines:
        match = _TCOUNT_LINE.match(line)
        if match:
            return _tcount_counts(match)
    return None


def _parse_tcount_all(lines: list[str]) -> Optional[Counts]:
    """Every tcount counts line of a wrapper's log (a script that runs tcount twice), summed;
    projects says how many. Read only after every runner's own summary found nothing."""
    found = [_tcount_counts(match) for match in map(_TCOUNT_LINE.match, lines) if match]
    if not found:
        return None
    total = Counts(found[0].runner if len({c.runner for c in found}) == 1 else "tests", collected=0)
    for part in found:
        for name in ("passed", "failed", "errors", "skipped", "collected"):
            setattr(total, name, getattr(total, name) + getattr(part, name))
    total.projects = len(found)
    return total


def _tcount_counts(match: re.Match) -> Counts:
    collected, passed, failed, errors, skipped = (int(g) for g in match.groups()[1:6])
    return Counts(match.group(1), passed=passed, failed=failed, errors=errors,
                  skipped=skipped, collected=collected)


def tcount_reports(text: str) -> list[tuple[Optional[Counts], Optional[int], Optional[str]]]:
    """(counts, exit code, log path) for every tcount report a Bash output shows, in order.

    A report is a counts line and the `log:` line that closes it; a command that runs several
    suites (run-tests' two) shows one report each, and each is a run. Counts with no log line
    after them (a report read back through grep, run-tests' closing summary) come with log None;
    a log line with no counts line (NO TEST COUNT) comes with counts None. The exit code is the
    one the line states (None for "spawn-failed"): the suite's own, not the shell's.
    Read by the run-ledger hook only; tcount parses a runner's log with parse_counts.
    """
    reports: list[tuple[Optional[Counts], Optional[int], Optional[str]]] = []
    pending: Optional[tuple[Counts, Optional[int]]] = None
    for line in _clean(text or ""):
        counts_line, log_line = _TCOUNT_SHOWN.match(line), _TCOUNT_LOG_SHOWN.match(line)
        if counts_line:
            if pending:
                reports.append((*pending, None))
            shown_exit = counts_line.group(7)
            pending = (_tcount_counts(counts_line), int(shown_exit) if shown_exit.isdigit() else None)
        elif log_line:
            reports.append((*(pending or (None, None)), log_line.group(1).strip()))
            pending = None
    if pending:
        reports.append((*pending, None))
    return reports


# Order matters: tcount's own line, specific multi-line formats, rtk one-liners, then pytest.
_PARSERS = (_parse_tcount, _parse_dotnet, _parse_jest, _parse_vitest, _parse_node, _parse_cargo,
            _parse_playwright, _parse_mocha, _parse_go, _parse_rtk, _parse_pytest)
# A dotnet log carries the tests' own output, so a run known to be dotnet is read by the dotnet
# parser only: a line a test printed ("12 passed in 0.5s") is never another runner's summary.
# The reverse holds too: a run known to be another runner is never read as dotnet, nor as a
# tcount report: a tcount counts line in a pytest log is something a test printed, and the first
# such line used to win over the real summary below it.
_OTHER_RUNNERS = frozenset(name for name, _ in _RUNNER_PATTERNS) - {"dotnet", "tcount", "npm"}
# A wrapper (`sh -c ...`, `make test`, `npm test`, a script; any hint that names no runner) is
# read by every runner's parser BEFORE a tcount line, and its pytest and tcount summaries are
# summed: an echoed tcount-shaped line must not win over the real summary below it.
_WRAPPER_PARSERS = (_parse_dotnet, _parse_jest, _parse_vitest, _parse_node, _parse_cargo,
                    _parse_playwright, _parse_mocha, _parse_go, _parse_rtk, _parse_pytest_all,
                    _parse_tcount_all)


def _parsers_for(runner_hint: Optional[str]) -> tuple:
    if runner_hint == "dotnet":
        return (_parse_dotnet,)
    if runner_hint in _OTHER_RUNNERS:
        return tuple(parser for parser in _PARSERS if parser not in (_parse_dotnet, _parse_tcount))
    if runner_hint in (None, "tcount"):
        return _PARSERS
    return _WRAPPER_PARSERS

_FAILING_ID_PATTERNS = (
    re.compile(r"^(?:FAILED|ERROR)\s+(\S+::.+?)(?:\s+-\s.*)?$"),        # pytest (ids may hold spaces)
    re.compile(r"^\s*\d+\.\s+\[FAIL\]\s+(.+)$"),                      # rtk pytest
    re.compile(r"^\s*--- FAIL: (\S+)"),                               # go
    re.compile(r"^test (\S+) \.\.\. FAILED$"),                        # cargo
    re.compile(r"^\s*✖\s+(.+?)(?:\s+\([\d.]+m?s\))?$"),               # node spec
    re.compile(r"^not ok \d+ - (.+)$"),                               # node TAP
    re.compile(r"^\s*(?:×|✕)\s+(.+?)(?:\s+\(?\d+(?:\.\d+)?\s?m?s\)?)?$"),  # vitest / jest
    re.compile(r"^\s*●\s+(.+)$"),                                     # jest detail header
    re.compile(r"^\s*FAIL\s+(\S+.*)$"),                               # vitest/jest file line
    re.compile(r"^\s+\d+\)\s+(.+)$"),                                 # mocha / playwright
    re.compile(r"^  Failed (\S.*?)(?:\s+\[[^\]]*\])?$"),                # dotnet test
)
_NOT_AN_ID = re.compile(r"^(?:failing tests:?|Console|\d+ (?:passing|failing))$")


def failing_ids(lines: list[str]) -> list[str]:
    """Up to MAX_FAILING_IDS distinct failing test ids, in output order."""
    ids: list[str] = []
    for line in lines:
        for pattern in _FAILING_ID_PATTERNS:
            match = pattern.match(line)
            if match:
                test_id = match.group(1).strip()
                if test_id and not _NOT_AN_ID.match(test_id) and test_id not in ids:
                    ids.append(test_id)
                break
        if len(ids) >= MAX_FAILING_IDS:
            break
    return ids


def parse_counts(text: str, runner_hint: Optional[str] = None) -> Optional[Counts]:
    """Counts from runner output, or None when no runner summary is present.

    None is the honest answer for "no count": callers must report it as NOT RUN,
    never as a pass. runner_hint (from detect_runner) names the runner when the
    summary line itself is generic (rtk's "Tests: ..."), and keeps a dotnet log and
    another runner's log from being read by each other's parser (_parsers_for).
    """
    if not text:
        return None
    lines = _clean(text)
    for parser in _parsers_for(runner_hint):
        counts = parser(lines)
        if counts is not None:
            if counts.runner == "tests" and runner_hint:
                counts.runner = runner_hint
            counts.finish()
            # go names its failing tests itself: a package's own FAIL line is no test id
            if (counts.failed or counts.errors) and not counts.failing_ids and counts.runner not in GO_RUNNERS:
                counts.failing_ids = failing_ids(lines)
            return counts
    return None
