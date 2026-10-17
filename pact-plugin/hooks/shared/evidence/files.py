"""The files an item points at: cited result files and runner logs in the session
scratchpads (ResultFiles), and the files edited after a run (git status).

Location: pact-plugin/hooks/shared/evidence/files.py, split out of the package's
__init__, which re-exports every name here; callers import shared.evidence.
"""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path
from typing import Optional

from shared.plugin_paths import claude_config_dir
from shared.scrub import scrub_text
from shell.words import words as shell_words  # the reader every guard shares
from shared.evidence.ledger import (RUN_STEM, _floor, _inside, _run_end, linked_worktree, own_worktrees,
                        project_root)


# ---------------------------------------------------------------------------
# Cited result files
# ---------------------------------------------------------------------------
# A count may be backed by a result file instead of a tcount run: a VSTest .trx, whose one
# <Counters total=".." passed=".." failed=".." /> element holds the run's counts (1.3.4 field
# report, B: Docker runs outside tcount). The path arrives in a HANDOFF, so the file is read under
# these rules and no others: its real path is inside a root
# (the task's project and its linked worktrees, the ledger's runs directory, the claimant
# session's scratchpad and its session directory); it is a regular file and not a symlink; it is at most RESULT_FILE_MAX
# bytes and was last written at most RESULT_FILE_MAX_AGE before the HANDOFF; no XML parser touches
# it (nothing is expanded or fetched): the Counters tag is found by a regex, exactly once, and
# total, passed and failed are plain ASCII digits. Anything else backs nothing. What the check cannot decide (over the cap, two Counters elements, a relative path with
# no scratchpad known, a directory inside the scratchpad) is a note to the lead, never a return.
# Any other file the item cites inside the scratchpad is a runner's log: read by the
# same rules with the scratchpad as its only root and at most LOG_FILE_MAX bytes, its text goes to
# tcount's parser (read_log_counts); a log with no count is a note. A symlink named in the
# scratchpad is refused whether it points inside it or out. Known limits, left as they are (review
# of 96be605d, finding 11): a hard link inside a root to a file outside it is read (its counters
# only), and a Counters element inside an XML comment or CDATA counts. A file name with a space in
# it is not found (a cited path is cut at the space; a directory name with one is found).
RESULT_FILE_MAX = 32 * 1024 * 1024      # the largest real .trx seen in the field: 11.5 MB
RESULT_FILES_MAX = 16                   # result files opened for one HANDOFF write
PATH_TOKENS_MAX = 24                    # path-like words looked at in one item
NEAR_DIRS_MAX = 16                      # directories one HANDOFF names where a bare file name is looked for
RESULT_FILE_MAX_AGE = 24 * 3600         # seconds a result file may predate the HANDOFF that cites it
NO_AGE_LIMIT = 0.0                      # a HANDOFF time no file predates: acceptance with no recorded write
COUNTERS_TAG_MAX = 4096                 # bytes of one Counters tag (a real one: about 300)
PATH_TOKEN = re.compile(r"(?<![\w./~@+=-])(~?/?(?:[\w.@+=-]+/)*[\w.@+=-]+/?)")
COUNTERS = re.compile(rb"<Counters\b([^<>]{0,%d})>" % COUNTERS_TAG_MAX)
COUNTERS_BODY = re.compile(rb"""(?:\s+[A-Za-z]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?""")
COUNTER_ATTR = re.compile(rb"""\s([A-Za-z]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
PLAIN_COUNT = re.compile(rb"[0-9]{1,9}")
COUNTER_KEYS = (("total", "collected"), ("passed", "passed"), ("failed", "failed"), ("error", "errors"),
                ("notExecuted", "skipped"))
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LOG_FILE_MAX = 8 * 1024 * 1024         # a runner log read for its counts (parse_counts reads all of it)
OVER_CAP = f"is over {RESULT_FILE_MAX // (1024 * 1024)} MiB"
NO_LOG_COUNT = "holds no test count the check can read"
AMBIGUOUS = "has more than one Counters element"
RED_FILE = "the cited result file holds failures or errors"
TOO_OLD = f"was last written more than {RESULT_FILE_MAX_AGE // 3600} hours before this HANDOFF"
NO_SCRATCHPAD = "the session scratchpad is not known to the check"


def over_cap(cap: int) -> str:
    return f"is over {cap // (1024 * 1024)} MiB"


def session_scratchpads(session: Optional[str]) -> list[str]:
    """The scratchpad directories of a Claude Code session on this host, by the path shape seen
    here: <tmp>/claude-<uid>/<project slug>/<session id>/scratchpad (in-process teammates share
    the lead's). [] when the id is not a session id or no such directory exists: the id is only
    ever a path segment made of hex digits and dashes."""
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session) or not hasattr(os, "getuid"):
        return []
    found = []
    for base in dict.fromkeys(os.path.realpath(tmp) for tmp in ("/tmp", "/private/tmp")):
        try:
            for path in Path(base, f"claude-{os.getuid()}").glob(f"*/{session}/scratchpad"):
                if path.is_dir() and not path.is_symlink():
                    found.append(str(path))
        except OSError:
            continue
    return list(dict.fromkeys(found))


def session_dirs(session: Optional[str]) -> list[str]:
    """The session directories of a session: <config dir>/pact-sessions/<project>/<session id>/,
    where a builder is told to keep the run artifacts it cites (the OS purges the scratchpad).
    [] when the id is not a session id or no such directory exists."""
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return []
    try:
        return [str(path) for path in (claude_config_dir() / "pact-sessions").glob(f"*/{session}")
                if path.is_dir() and not path.is_symlink()]
    except OSError:
        return []


def parse_counters(data: bytes) -> tuple[Optional[dict], str]:
    """(counts, "") from a result file's bytes, else (None, why). Exactly one Counters element,
    no entity declarations, each attribute once; total, passed and failed (and error and
    notExecuted when present) are 1 to 9 ASCII digits, and passed plus failed is at most total.
    The tag is at most COUNTERS_TAG_MAX bytes: a longer one is no Counters element (a 32 MiB tag
    of attributes took 2 s and 3 GiB to take apart)."""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return None, "declares entities, which no test runner writes"
    if data.count(b"<Counters") > 1:
        return None, AMBIGUOUS
    tag = COUNTERS.search(data)
    if tag is None:
        return None, "has no Counters element"
    body = tag.group(1)
    if not COUNTERS_BODY.fullmatch(body):
        return None, "has a malformed Counters element"
    pairs = [(name.decode("ascii"), double or single) for name, double, single in COUNTER_ATTR.findall(body)]
    if len({name for name, _ in pairs}) != len(pairs):
        return None, "repeats a Counters attribute"
    values, counts = dict(pairs), {}
    for attribute, key in COUNTER_KEYS:
        if attribute not in values:
            if key in ("collected", "passed", "failed"):
                return None, f"has no Counters {attribute}"
            continue
        if not PLAIN_COUNT.fullmatch(values[attribute]):
            return None, f"has a Counters {attribute} that is not a plain count"
        counts[key] = int(values[attribute])
    if counts["passed"] + counts["failed"] > counts["collected"]:
        return None, "has Counters that do not add up"
    return counts, ""


def read_counters(path: str, roots: list[str], now: Optional[float] = None) -> tuple[Optional[dict], str]:
    """(counts, "") for the result file at an absolute path, else (None, why); the rules are above.
    The open follows no symlink and does not block (a FIFO is not a regular file), and the type,
    size and age are those of the open file, not of the name. now: when the HANDOFF that cites
    it was written (the clock when not given); a file last written more than RESULT_FILE_MAX_AGE
    before that is another day's run and backs nothing (review of 96be605d, finding 7)."""
    data, why = read_result_bytes(path, roots, now)
    return (None, why) if data is None else parse_counters(data)


def read_log_counts(path: str, scratch: list[str], now: Optional[float] = None) -> tuple[Optional[dict], str]:
    """(counts, "") for a runner's output saved as a log in the session scratchpad, else (None,
    why) (a Playwright log the builder kept there was 'not read', and the lead checked
    its 30 passes by hand). Read by the result-file rules with the scratchpad as the only root, so
    a symlink, a file whose real path leaves the scratchpad, an old or an oversized one backs
    nothing; its text goes to the parser tcount uses (parse_counts), never to a shell."""
    from shared.runcounts import parse_counts
    data, why = read_result_bytes(path, scratch, now, LOG_FILE_MAX)
    if data is None:
        return None, why
    counts = parse_counts(data.decode("utf-8", "replace"))
    if counts is None:
        return None, NO_LOG_COUNT
    return {key: getattr(counts, key) or 0 for key in ("collected", "passed", "failed", "errors", "skipped")}, ""


def read_result_bytes(path: str, roots: list[str], now: Optional[float] = None,
                      cap: int = RESULT_FILE_MAX) -> tuple[Optional[bytes], str]:
    """(the bytes, "") of the file at an absolute path under the result-file rules, else (None, why)."""
    now = time.time() if now is None else now
    if os.path.islink(path):
        return None, "is a symlink"
    real = os.path.realpath(path)
    if not any(_inside(real, root) for root in roots):
        return None, "is outside the task's project, worktrees, runs directory and scratchpad"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(real, flags)
    except OSError:
        return None, "cannot be opened"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, "is not a regular file"
        if info.st_size > cap:
            return None, over_cap(cap)
        if info.st_mtime < now - RESULT_FILE_MAX_AGE:
            return None, TOO_OLD
        data = bytearray()
        while len(data) <= cap:
            chunk = os.read(fd, min(1 << 20, cap + 1 - len(data)))
            if not chunk:
                break
            data += chunk
    except OSError:
        return None, "cannot be read"
    finally:
        os.close(fd)
    if len(data) > cap:
        return None, over_cap(cap)
    return bytes(data), ""


class ResultFiles:
    """The result files one HANDOFF write cites: each read once (read_counters), at most
    RESULT_FILES_MAX of them. roots: the task's project, its linked worktrees, the runs directory
    and the session directory; scratch: the claimant session's scratchpad directories (also roots);
    now: when the HANDOFF was written (read_counters), the clock when not given."""

    def __init__(self, roots: list[Optional[str]], scratch: list[str], now: Optional[float] = None):
        self.now = time.time() if now is None else now
        self.scratch = [os.path.realpath(path) for path in scratch]
        self.roots = list(dict.fromkeys([os.path.realpath(root) for root in roots if root] + self.scratch))
        self.near: list[str] = []
        self._read: dict[str, tuple[Optional[dict], str]] = {}

    def prime(self, texts: list[str]) -> None:
        """Note the directories the HANDOFF names by an absolute path that exists inside a root.
        An item that names a result file bare ('probe1.trx: ...') is then looked for beside the
        paths the other items cite (1.3.4 field report: the path stood in `integration`, the
        counts in `uncertainty`). Nothing is opened, and what is found there is read by the same
        rules as any other result file."""
        for text in texts:
            seen = 0
            for match in PATH_TOKEN.finditer(text):
                token = match.group(1).rstrip(".")
                if "/" not in token or re.fullmatch(r"[\d/.,]+", token):
                    continue
                seen += 1
                path = _rooted(text, match.start(1), token)
                if seen > PATH_TOKENS_MAX or len(self.near) >= NEAR_DIRS_MAX:
                    break
                if path is None or not os.path.exists(path):
                    continue
                real = os.path.realpath(path)
                if any(_inside(real, root) for root in self.roots):
                    folder = real if os.path.isdir(real) else os.path.dirname(real)
                    if folder not in self.near:
                        self.near.append(folder)

    def _counters(self, path: str) -> tuple[Optional[dict], str]:
        real = os.path.realpath(path)
        key = path if os.path.islink(path) else real
        if key not in self._read:
            if len(self._read) >= RESULT_FILES_MAX:
                return None, f"is past the {RESULT_FILES_MAX} result files read for one HANDOFF"
            self._read[key] = read_counters(path, self.roots, self.now)
        return self._read[key]

    def _scratch_link(self, path: str) -> bool:
        """A symlink that stands in a scratchpad by its name, wherever it points: it is refused
        with a reason, not passed over as a path outside the scratchpad."""
        parent = os.path.realpath(os.path.dirname(path))
        return os.path.islink(path) and any(_inside(parent, root) for root in self.scratch)

    def _take_log(self, token: str, path: str, found: dict, near: list[str]) -> None:
        """A cited scratchpad path that is no result file: a runner's log is read for its counts
        (read_log_counts); a directory, or a log over the cap or with no count, stays undecided."""
        near.append(path if os.path.isdir(path) else os.path.dirname(path))
        if os.path.isdir(path) and not os.path.islink(path):
            found["undecided"].append(f"cites {token}, which the check does not read")
            return
        key = "log:" + path
        if key not in self._read:
            if len(self._read) >= RESULT_FILES_MAX:
                found["undecided"].append(f"{token} is past the {RESULT_FILES_MAX} result files read for one HANDOFF")
                return
            self._read[key] = read_log_counts(path, self.scratch, self.now)
        counts, why = self._read[key]
        if counts is not None:
            found["counters"].append(counts)
        elif why == NO_LOG_COUNT or why.startswith("is over"):
            found["undecided"].append(f"{token} {why}")
        else:
            found["unread"].append(f"{token} {why}")

    def _in_scratch(self, path: str) -> bool:
        """An existing path strictly inside a scratchpad. The scratchpad itself always exists, so
        the bare word for it cites nothing (review of 96be605d, finding 3)."""
        real = os.path.realpath(path)
        return os.path.exists(path) and any(real != root and _inside(real, root) for root in self.scratch)

    def _resolve(self, token: str, near: list[str], trx: bool) -> Optional[str]:
        """Where a relative citation is: beside another path the item or the HANDOFF cites, in
        the scratchpad (`scratchpad/x` is the scratchpad's x), and for a result file under a root.
        None: nowhere."""
        bases = [*near, *self.near]
        if token.startswith("scratchpad/"):
            bases += [os.path.dirname(path) for path in self.scratch]
        bases += self.scratch
        if trx:
            bases += self.roots
        for base in dict.fromkeys(bases):
            candidate = os.path.normpath(os.path.join(base, token))
            if os.path.lexists(candidate) and (trx or self._in_scratch(candidate) or self._scratch_link(candidate)):
                return candidate
        return None

    def cited(self, text: str) -> dict:
        """What the item's cited files say: {"counters": the counts of each readable result file,
        "absent": absolute result-file paths that do not exist, "undecided": why the check cannot
        decide (said to the lead), "unread": why a cited result file backs nothing}."""
        found = {"counters": [], "absent": [], "undecided": [], "unread": []}
        tokens = []
        for match in PATH_TOKEN.finditer(text):
            token = match.group(1).rstrip(".")
            if ("/" not in token and not token.lower().endswith(".trx")) or re.fullmatch(r"[\d/.,]+", token) \
                    or RUN_STEM.search(token):
                continue                # a word, a ratio, or a tcount log (cited_runs reads those)
            tokens.append((token, _rooted(text, match.start(1), token)))
            if len(tokens) >= PATH_TOKENS_MAX:
                break
        near: list[str] = []
        pending = []
        for token, path in tokens:
            trx = token.lower().endswith(".trx")
            if path is None:
                pending.append((token, trx))
                continue
            if trx:
                self._take(token, path, found, near)
            elif self._in_scratch(path) or self._scratch_link(path):
                self._take_log(token, path, found, near)
        for _ in range(2):              # a path found late can place the ones before it
            later = []
            for token, trx in pending:
                path = self._resolve(token, near, trx)
                if path is None:
                    later.append((token, trx))
                elif trx:
                    self._take(token, path, found, near)
                else:
                    self._take_log(token, path, found, near)
            pending = later
        for token, trx in pending:
            if not self.scratch and (trx or token.startswith("scratchpad/")):
                found["undecided"].append(f"{token} is a relative path and {NO_SCRATCHPAD}")
            elif trx:
                found["unread"].append(f"{token} was not found under the task's roots")
        return {key: list(dict.fromkeys(value)) if key != "counters" else value for key, value in found.items()}

    def _take(self, token: str, path: str, found: dict, near: list[str]) -> None:
        if not os.path.lexists(path):
            found["absent"].append(token)
            return
        counts, why = self._counters(path)
        if counts is not None:
            found["counters"].append(counts)
            near.append(os.path.dirname(path))
        elif why in (OVER_CAP, AMBIGUOUS) or why.startswith("is past"):
            found["undecided"].append(f"{token} {why}")
        else:
            found["unread"].append(f"{token} {why}")


def _rooted(text: str, start: int, token: str) -> Optional[str]:
    """The absolute path a path-like word stands for, or None for a relative one. A path with
    spaces in it reaches here as its last piece: when the text before it, back to an earlier
    '/' that starts a word, makes a path that exists, that is the path."""
    if token.startswith("~/"):
        return os.path.expanduser(token)
    if token.startswith("/"):
        return token
    window = text[max(0, start - 400):start]
    for begin in [m.start(1) for m in re.finditer(r"(?:^|[\s\"'(`=:])(/)", window)][::-1][:8]:
        candidate = window[begin:] + token
        if "\n" not in candidate and os.path.exists(candidate):
            return candidate
    return None


def result_files(records: list[dict], team: Optional[str], task_id: str, owner: Optional[str],
                 since: Optional[float], cwd: Optional[str], session: Optional[str],
                 runs_dir: Optional[Path], now: Optional[float] = None) -> ResultFiles:
    """ResultFiles for one task: its project root, the linked worktree of the writer's cwd and
    every linked worktree the task is anchored in (own_worktrees), the runs directory, the
    claimant session's directory (session_dirs) and its scratchpad. now: when the HANDOFF was
    written."""
    trees = [root for root, _branch in own_worktrees(records, team, task_id, owner, _floor(since))]
    return ResultFiles([project_root(cwd), linked_worktree(cwd), *trees, str(runs_dir) if runs_dir else None,
                        *session_dirs(session)], session_scratchpads(session), now)


# A file changed after the run that backs a count (1.3.5 field report: a full run at 21:36, a
# product file edited at 21:39, only a subset run after; the gate passed it and the lead caught it
# by the file's time). A NOTE for the lead, once per HANDOFF, never a return: the check cannot
# know whether the later edit bears on the run. Bounds: one `git status` (GIT_STATUS_SECONDS, at
# most CHANGED_BYTES_MAX of its output, no index refresh, no fsmonitor program), the first
# CHANGED_FILES_MAX changed files, and only in ONE tree: the one that holds the oldest backing
# run. Any error is silence. Left out, because they cannot change a test result: Markdown files,
# the project task log (its generated views are Markdown) and what a run writes, its .trx result
# file and its .log (two of the three notes a replay of the field session drew named a .trx that
# the run itself or a later one had written).
GIT_STATUS_SECONDS = 2.0
CHANGED_BYTES_MAX = 512 * 1024
CHANGED_FILES_MAX = 500
EDIT_SLACK_SECONDS = 2.0        # ledger stamps are whole seconds
TASK_LOG = ".repacted/tasks.jsonl"
NOT_A_SOURCE = (".md", ".trx", ".log")


def git_root(cwd) -> Optional[str]:
    """The working tree a directory is in: the nearest directory holding .git (a linked worktree's
    file or a checkout's directory), else None. None too when the directory is gone (a removed
    worktree: its nearest ancestor checkout is another tree, and the note named that tree's file)
    or cannot be looked at (no search permission above it, a name too long)."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    try:
        if not os.path.isdir(cwd):
            return None
        start = Path(os.path.realpath(cwd))
        for directory in (start, *start.parents):
            if (directory / ".git").exists():
                return str(directory)
    except (OSError, ValueError):
        pass
    return None


def _cannot_change_a_test(path: str) -> bool:
    return path.lower().endswith(NOT_A_SOURCE) or path == TASK_LOG


def changed_files(tree: str) -> list[tuple[str, float]]:
    """(path, mtime) of the regular files git reports as changed against HEAD in a working tree:
    staged, modified or untracked (not ignored), by ONE `git status` call. [] on any error: git
    missing, not answering within GIT_STATUS_SECONDS, or exiting non-zero. Output past
    CHANGED_BYTES_MAX is not read and at most CHANGED_FILES_MAX files are looked at, so the list
    can be short of files, never wrong about one. A deleted file has no time and is left out.
    git runs in its own process group and the group is killed at the deadline: a helper it
    started (a submodule's status) would otherwise hold the pipe open past it."""
    import select
    import signal
    import subprocess
    command = ["git", "-c", "core.fsmonitor=false", "-C", tree, "status", "--porcelain=v1", "-z",
               "--untracked-files=all", "--no-renames"]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, start_new_session=True,
                                   env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, ValueError):
        return []

    def stop() -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            process.kill()
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass

    deadline, data, code = time.monotonic() + GIT_STATUS_SECONDS, b"", None
    try:
        descriptor = process.stdout.fileno()
        while len(data) <= CHANGED_BYTES_MAX:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([descriptor], [], [], left)[0]:
                return []                                   # git did not answer in time
            chunk = os.read(descriptor, 65536)
            if not chunk:
                code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
                break
            data += chunk
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    finally:
        if process.poll() is None:
            stop()
        process.stdout.close()
    if len(data) > CHANGED_BYTES_MAX:
        data = data[:CHANGED_BYTES_MAX].rpartition(b"\0")[0]       # whole entries only
    elif code != 0:
        return []
    found: list[tuple[str, float]] = []
    for entry in data.split(b"\0")[:CHANGED_FILES_MAX]:
        path = os.fsdecode(entry[3:])
        if len(entry) < 4 or path.endswith("/") or _cannot_change_a_test(path):
            continue
        try:
            info = os.lstat(os.path.join(tree, path))
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode):
            found.append((path, info.st_mtime))
    return found


def _clock(epoch: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _printable(text: str, limit: int = 160) -> str:
    """A file or log name for the lead's line: each control, format or unassigned character (a
    newline, an escape, a bidi override) becomes '?', and a long name is cut. A file name is
    whatever the worktree holds, and a newline in one started a line of its own in the note."""
    import unicodedata
    shown = "".join("?" if unicodedata.category(char).startswith("C") else char for char in text[:limit])
    return shown + ("..." if len(text) > limit else "")


# A changed test file is covered only by a later run that ran it (1.5.0 field report: a baseline
# run, then tests/user_layer/test_scrub.py edited, then a later run of three other test files: the
# note was true). A run counts as running a file when its command names the file or a directory
# above it; a command that names no test path (a whole suite, a script) may have run anything.
TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs"})
_TEST_NAME = re.compile(r"(test_.+|.+_test|conftest|.+\.(?:test|spec))\.[A-Za-z0-9]+")


def is_test_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    return bool(parts) and (bool(_TEST_NAME.fullmatch(parts[-1])) or any(part in TEST_DIRS for part in parts))


def named_test_paths(record: dict) -> list[str]:
    """The real paths of the test files and directories a run's command names, read against the
    directory it ran in. Option words, words with a shell expansion and pytest node suffixes
    (`::test_x`) are left out or cut."""
    command, cwd = record.get("run_cmd") or record.get("cmd"), record.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return []
    words = shell_words(command)
    if words is None:                   # unreadable quoting: the plain split, as before
        words = command.split()
    named = []
    for word in words:
        word = word.split("::", 1)[0]
        if word.startswith("-") or "$" in word or not is_test_path(word):
            continue
        named.append(os.path.realpath(os.path.join(cwd, word)))
    return named


def _covers(named: list[str], tree: str, path: str) -> bool:
    if not is_test_path(path) or not named:
        return True
    target = os.path.realpath(os.path.join(tree, path))
    return any(target == each or target.startswith(each.rstrip(os.sep) + os.sep) for each in named)


def edited_after_run(items: list[dict], now: float, also: tuple = ()) -> Optional[str]:
    """The lead's one line when a file the worktree has changed against HEAD was written after
    the run that backs a count of this HANDOFF, else None. Of the backing runs (item_evidence:
    per count, the newest run that has it) in a git working tree, the oldest is taken, and its
    tree's changed files (changed_files) are compared with the time that run ended. A file
    written after `now` (the HANDOFF's own time: a clock that is wrong, or a time set by hand)
    proves nothing and is left out, and so is one written within EDIT_SLACK_SECONDS of the run.
    A later run of this task in that tree covers a file written no later than EDIT_SLACK_SECONDS
    after it ended (a count matched to an early red-proof run drew the note though the
    same HANDOFF cited a later full run): a run any item cites or whose counts it claims, in any
    list entry, or one of `also` (the runs `uncertainty` cites). A changed test file is covered
    only by a run that ran it (_covers). Another task's run covers nothing (item_evidence keeps it
    out of "cited"). A run whose directory is gone or cannot be looked at is passed over
    (git_root). Nothing here may raise: an error out of handoff_gate lets the write through with
    every return lost."""
    try:
        oldest: Optional[tuple[float, str, dict, dict]] = None
        for item in items:
            for run in item.get("backing") or []:
                ended, root = _run_end(run), git_root(run.get("cwd"))
                if ended is not None and root and (oldest is None or ended < oldest[0]):
                    oldest = (ended, root, run, item)
        if oldest is None:
            return None
        ended, root, run, item = oldest
        runs: dict = {}                 # one entry per run: its records share the log path
        for named in [*[each for other in items for key in ("backing", "cited") for each in other.get(key) or []],
                      *also]:
            end = _run_end(named)
            if end is None or end <= ended or git_root(named.get("cwd")) != root:
                continue
            entry = runs.setdefault(named.get("log_path") or id(named), [end, []])
            entry[0] = max(entry[0], end)
            entry[1] += named_test_paths(named)

        def covered(path: str, mtime: float) -> bool:
            return any(mtime <= end + EDIT_SLACK_SECONDS and _covers(named, root, path)
                       for end, named in runs.values())

        later = [(mtime, path) for path, mtime in changed_files(root)
                 if ended + EDIT_SLACK_SECONDS < mtime <= now and not covered(path, mtime)]
        if not later:
            return None
        mtime, path = max(later)
        name = _printable(Path(str(run.get("log_path") or "")).name) or "no log kept"
        more = f" and {len(later) - 1} more" if len(later) > 1 else ""
        return (f"{item['field']}[{item['idx']}]: a count is backed by a run that ended {_clock(ended)} ({name}), "
                f"and {_printable(scrub_text(path))}{more} changed after it ({_clock(mtime)}), not returned: check "
                "that a later run covers the change")
    except Exception:   # noqa: BLE001 - a note that cannot be made is no note
        return None
