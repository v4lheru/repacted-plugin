"""
Location: pact-plugin/hooks/shared/acceptance.py
Summary: Acceptance criteria against the run ledger: read docs/<feature>/acceptance.md, find the
         ledger runs an AC's HANDOFF citation or command names, and say whether a run passed.
         Reads the ledger through the evidence package's readers (read_runs, _run_key, RUN_STEM),
         unchanged; the AC id grammar and dispositions live in shared/ref_dispositions.py.
Used by: shared/evidence_check.py (a done AC's cited runs, for `repact accept`), shared/ops/dispatch.py (the pin), bin/_front/criteria.py (`repact inspect --acceptance`, the one-screen view).
Both judge a bound AC with `judge`: the newest cited run must pass, run the AC's
command, inside the tree holding the acceptance file, and end after the task's claim. The lead pins
each AC's command at dispatch in metadata.acceptance_pin ({AC id: command}, printed by
`repact inspect --acceptance <file> --pin`); a file line that disagrees with the pin is refused (`pin_problem`),
since the coder's checkout holds the file. "Runs the AC's command" means the same test
selection (`selection`): the same runner, targets and options, compared exactly once the
wrappers (WRAPPERS) and the output-only options (OUTPUT_ONLY) are set aside.

acceptance.md: one criterion per line, `AC-<n>: <criterion> `<command that proves it>``, a list
marker or bold around the id allowed. The command is the line's last backtick span.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Optional

from shared import ref_dispositions as RD
from shared.ledger import RUN_STEM, _record_time, _run_end, _run_key, read_runs
from shared.plugin_paths import ledger_path
from shared.runcounts import detect_runner
from shell.words import words as _shell_words  # the one reader every guard shares

LINE = re.compile(r"\s*(?:[-*+]\s+)?(?:\*\*)?(AC-\S*?)(?:\*\*)?\s*[:.)]\s+(.*)")
CODE = re.compile(r"`([^`\n]+)`")
MAX_TEXT = 1 << 20
# The wrappers `_unwrap` sets aside run the command after them as it is: `tcount`, `rtk [proxy]`,
# `nice [-n N]`, `env NAME=value`, `uv run [uv options]`, `python[3[.N]] -m`. `uv run --with PKG`
# and `--no-project` only set up the environment and are set aside too (this repo's own test
# recipe). A wrapper option that moves what runs or where (another directory or
# project, an editable or requirements install, an env file; an env option, or an env
# assignment of a SELECTING_ENV variable) makes the command unreadable: selection() gives None
# and the run counts for nothing.
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")
_UV_VALUE = ("-p", "--python", "--extra", "--group", "--with")
_UV_CHANGES = re.compile(r"--(?:directory|project|with-editable|with-requirements|env-file)(?:=.*)?\Z")
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=.*", re.DOTALL)
# Environment variables that change which tests run or what they import: tcount
# records them, and a run under the first two never counts (shared/evidence_check.py).
SELECTING_ENV = ("PYTEST_ADDOPTS", "NODE_OPTIONS", "PYTHONPATH")
# Options that change what a run prints or how soon it stops, never which tests it selects; the
# second list takes a value as the next word (`--tb short`). Every other option must match.
OUTPUT_ONLY = re.compile(r"-[qvsxl]+\Z|--(?:quiet|verbose|no-header|exitfirst|showlocals)\Z"
                         r"|--(?:tb|color|durations|maxfail|numprocesses)=")
OUTPUT_VALUE = ("--tb", "--color", "--durations", "--maxfail", "--numprocesses", "-n")
# pytest's report option: `-rfE` or `-r fE`. For node and mocha `-r` loads a module.
_PYTEST_REPORT = re.compile(r"-r[fEsxXpPaAN]+\Z")
_PYTEST = ("pytest", "py.test")
_SHELLS = ("sh", "bash", "zsh", "dash", "ksh")
_INLINE_RUNNERS = ("node", "bun", "deno")    # detect_runner can name these with inline code given
_INLINE_FLAGS = ("-e", "--eval", "-p", "--print")
# Selection options whose value is the next word: `-k expr` is one (option, value) pair.
VALUE_OPTIONS = ("-k", "-m", "-c", "-p", "-o", "--deselect", "--ignore", "--ignore-glob", "--rootdir",
                 "--confcutdir", "--override-ini", "--exclude", "-t", "--testNamePattern", "--config",
                 "-run", "-tags", "-r", "--require", "--import")
PIN_KEY = "acceptance_pin"
PIN_RULE = "acceptance.pin"
PIN_MARK = "the lead's pin"   # in every pin refusal line, so the reason gains PIN_THROUGH
PIN_THROUGH = ("The lead reviews the change to the acceptance file and re-pins it: `repact inspect --pin --acceptance "
               "<acceptance.md>` prints the {AC id: command} map for metadata.acceptance_pin.")


def parse_criteria(text: str) -> tuple[list[dict], list[str]]:
    """([{id, line, criterion, command}], [ignored id-like starts]) in file order. A line whose id
    is not an AC id (AC-0, a fullwidth digit) is ignored and named; a duplicate is kept and flagged
    by the caller."""
    found, ignored = [], []
    for number, raw in enumerate(text[:MAX_TEXT].splitlines(), 1):
        match = LINE.match(raw)
        if not match:
            continue
        ident, rest = match.groups()
        if not RD.is_ac(ident):
            ignored.append(ident[:20])
            continue
        spans = CODE.findall(rest)
        found.append({"id": ident, "line": number, "command": spans[-1].strip() if spans else "",
                      "criterion": CODE.sub("", rest).strip()})
    return found, ignored


def _unwrap(words: list[str]) -> Optional[list[str]]:
    """The words less the leading wrappers (tcount, rtk, nice, env, uv run, python -m), or None
    when a wrapper changes what runs (see _UV_CHANGES and SELECTING_ENV)."""
    while words:
        head = os.path.basename(words[0])
        if head == "tcount":
            words = words[1:]
        elif head == "rtk":
            words = words[2:] if words[1:2] == ["proxy"] else words[1:]
        elif head == "nice":
            words = words[3:] if words[1:2] == ["-n"] else words[2:] if re.fullmatch(
                r"-n?-?\d+", "".join(words[1:2])) else words[1:]
        elif head == "env":
            words = words[1:]
            while words and (assigned := _ASSIGNMENT.match(words[0])):
                if assigned.group(1) in SELECTING_ENV:
                    return None
                words = words[1:]
            if words and words[0].startswith("-"):
                return None
        elif head == "uv" and words[1:2] == ["run"]:
            words = words[2:]
            while words and words[0].startswith("-"):
                if _UV_CHANGES.match(words[0]):
                    return None
                words = words[2:] if words[0] in _UV_VALUE else words[1:]
        elif _PYTHON.match(head) and words[1:2] == ["-m"]:
            words = words[2:]
        else:
            return words
    return words


def _runs_tests(words: list[str]) -> bool:
    """The first word is a test runner (runcounts.detect_runner, read by its base name, or py.test)
    and no word hands a shell or an interpreter code inline (`sh -c`, `node -e`): what such a run
    does is not in its words."""
    head = os.path.basename(words[0])
    if head in _SHELLS:
        return False
    if head in _INLINE_RUNNERS and any(word.split("=", 1)[0] in _INLINE_FLAGS for word in words[1:]):
        return False
    return head in _PYTEST or detect_runner(shlex.join([head, *words[1:]])) is not None


def selection(command: str) -> Optional[tuple]:
    """(runner, sorted (option, value) pairs, targets) of a test command, or None when it cannot be
    split (an open quote) or names nothing. Wrappers and OUTPUT_ONLY options are set aside; a
    target loses a leading `./` and a trailing `/`."""
    split = _shell_words(command)
    if split is None:
        return None
    words = _unwrap(split)
    if not words or not _runs_tests(words):
        return None
    pytest = os.path.basename(words[0]) in _PYTEST
    options, targets, rest = [], [], iter(words[1:])
    for word in rest:
        if word in OUTPUT_VALUE or (pytest and word == "-r"):
            next(rest, None)
        elif OUTPUT_ONLY.match(word) or (pytest and _PYTEST_REPORT.match(word)):
            continue
        elif word in VALUE_OPTIONS:
            options.append((word, next(rest, "")))
        elif word.startswith("-"):
            name, _, value = word.partition("=") if word.startswith("--") else (word, "", "")
            options.append((name, value))
        else:
            word = word[2:] if word.startswith("./") else word
            targets.append(word.rstrip("/") or word)
    return os.path.basename(words[0]), sorted(options), targets


def ledger_runs(path: Optional[os.PathLike] = None) -> Optional[list[dict]]:
    """Every run in the ledger (read_runs), or None when the ledger is absent or unreadable."""
    target = Path(path) if path else ledger_path(create_dir=False)
    if not target.is_file() or not os.access(target, os.R_OK):
        return None
    return read_runs(target)[0]


def runs_for(citation: str, runs: list[dict]) -> list[dict]:
    """The ledger runs a cited log stem names; a short stem (no date) matches on clock and pid."""
    match = RUN_STEM.search(citation)
    if not match:
        return []
    date, clock, pid = match.groups()
    return [run for run in runs if (key := _run_key(run))
            and key[1] == clock and key[2] == pid and (date is None or key[0] == date)]


def _count(record: dict, key: str) -> Optional[int]:
    value = record.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_passed(record: dict) -> bool:
    """A counted run with exit 0, no failure or error, and at least one test passed."""
    counts = [_count(record, key) for key in ("passed", "failed", "errors", "exit_code")]
    return bool(record.get("count_parsed")) and None not in counts and counts[0] > 0 \
        and counts[1] == counts[2] == counts[3] == 0


def _command(record: dict) -> str:
    return str(record.get("run_cmd") or record.get("cmd") or "")


def same_selection(record: dict, command: str) -> bool:
    """The run selected the tests `command` selects: the same runner and options, and its targets
    pair one to one with the command's (a relative target also matches an absolute path ending in
    `/<target>`)."""
    want, got = selection(command), selection(_command(record))
    if not want or not got or want[:2] != got[:2] or len(want[2]) != len(got[2]):
        return False
    left = list(got[2])
    for target in want[2]:
        hit = next((word for word in left if word == target or (os.path.isabs(word) and not os.path.isabs(target)
                                                               and word.endswith("/" + target))), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def _inside(record: dict, root: Optional[str]) -> bool:
    """The run's directory (tcount's own cwd, else the hook's) lies in `root`; any when root is None."""
    if root is None:
        return True
    cwd = record.get("run_cwd") or record.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return False
    real = os.path.realpath(cwd)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def runs_of(command: str, runs: list[dict], root: Optional[str]) -> list[dict]:
    """The runs, oldest first, of the same selection as `command` (same_selection), run inside
    `root` when one is given."""
    picked = [run for run in runs if same_selection(run, command) and _inside(run, root)]
    return sorted(picked, key=lambda run: _record_time(run) or 0.0)


def judge(ids: list[str], runs: list[dict], command: str, root: Optional[str],
          since: Optional[float]) -> tuple[Optional[dict], str]:
    """(the newest cited run, "" when it closes the AC, else why not) for an AC bound to its
    acceptance line. A cited id the ledger lacks gives (None, why). Only the newest cited run is
    judged, so an older passing run cannot stand beside a newer failing one. `since` None skips
    the claim check (the caller notes it)."""
    absent = [run_id for run_id in ids if not runs_for(run_id, runs)]
    if absent:
        return None, f"run {', '.join(absent[:3])} is not in this machine's run ledger"
    hits = [run for run_id in ids for run in runs_for(run_id, runs)]
    if not hits:
        return None, "no cited run"
    newest = max(hits, key=lambda run: _run_end(run) or 0.0)
    label = stem(newest)
    if not run_passed(newest):
        return newest, f"newest cited run {label} did not pass"
    if not same_selection(newest, command):
        return newest, (f"newest cited run {label} does not run the AC command `{command[:120]}`: it ran "
                        f"`{_command(newest)[:120]}`, another test selection")
    if not _inside(newest, root):
        return newest, f"newest cited run {label} ran outside the task's tree {root}"
    end = _run_end(newest)
    if since is not None and (end is None or end <= since):
        return newest, f"newest cited run {label} ended before the task was claimed"
    return newest, ""


def read_criteria(path: Path) -> Optional[dict]:
    """{AC id: item} of an acceptance file (first line per id wins), or None when it cannot be read."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_TEXT:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    items: dict = {}
    for item in parse_criteria(text)[0]:
        items.setdefault(item["id"], item)
    return items


def make_pin(items: dict) -> dict:
    """The pin of an acceptance file's criteria: {AC id: command}."""
    return {ident: item["command"] for ident, item in items.items()}


def read_pin(metadata) -> tuple[Optional[dict], str]:
    """(the lead's pin, "") from a task's stored metadata; (None, "") with no pin; ({}, why) when
    the value is not an {AC id: command} map, which the caller refuses."""
    value = metadata.get(PIN_KEY) if isinstance(metadata, dict) else None
    if value is None:
        return None, ""
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(command, str)
                                              for key, command in value.items()):
        return {}, f"metadata.acceptance_pin, {PIN_MARK}, is not an {{AC id: command}} map"
    return value, ""


def pin_problem(ident: str, item: Optional[dict], pin: dict) -> str:
    """Why the acceptance file's line for `ident` disagrees with the lead's pin, else ""."""
    if ident not in pin:
        return f"not in {PIN_MARK} (metadata.acceptance_pin)"
    if item is None:
        return f"gone from the acceptance file, though {PIN_MARK} holds it"
    if item["command"].split() != pin[ident].split():
        return (f"its command in the acceptance file is `{item['command'][:80]}`, {PIN_MARK} says "
                f"`{pin[ident][:80]}`")
    return ""


def resolve_file(named, cwd) -> Optional[Path]:
    """metadata.acceptance as a path: absolute as given, else relative to the repository holding
    `cwd` (the writer's checkout). None when it is not a usable string or no base is known."""
    if not isinstance(named, str) or not named.strip() or len(named) > 4096 or "\x00" in named:
        return None
    path = Path(named.strip()).expanduser()
    if not path.is_absolute():
        base = project_root(Path(cwd)) if isinstance(cwd, str) and os.path.isabs(cwd) else None
        if base is None:
            return None
        path = Path(base) / path
    return Path(os.path.realpath(path))


def project_root(path: Path) -> Optional[str]:
    """The nearest directory at or above `path` holding `.git` (a file in a linked worktree)."""
    for folder in [path, *path.parents]:
        if (folder / ".git").exists():
            return os.path.realpath(folder)
    return None


def stem(record: dict) -> str:
    key = _run_key(record)
    return f"{key[0]}T{key[1]}Z-{key[2]}" if key and key[0] else (f"{key[1]}Z-{key[2]}" if key else "-")
