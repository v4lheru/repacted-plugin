"""Evidence verification for the project task log (D10 P1; design section 5.3).

Location: pact-plugin/hooks/shared/task_log/evidence.py. Used by task_log/write.py for `done`
evidence and for commit/merge/tag links. Verification is git plumbing only, run in the main
checkout:
  commit:<sha> / merge:<sha>   the object exists and is a commit   (git cat-file -e <sha>^{commit})
  tag:<name>                   the tag exists                      (git rev-parse --verify refs/tags/<name>)
  doc:<path>@<sha>             the path exists at that commit      (git cat-file -e <sha>:<path>)
  run:<log file name>          a bare name (run_name_ok). Verified when the log exists in a runs
                               dir (${CLAUDE_PLUGIN_DATA}/runs, else run-tests' fallback
                               $TMPDIR/repacted-test-runs/runs), that dir's ledger records it by
                               basename with a parsed count, and the log's own counts, re-parsed,
                               agree with that record (D10 P2; on 137 real runs, 137 agree).

A run verified at close is recorded WITH the log's sha256 and its counts (`details`), so a later
`fsck` compares the log's digest and never re-parses it: the parser moves on (1.4.1 U3 taught the
pytest parser to refuse a wrapper log it once read, and one task's evidence "rotted" with the log
intact), and the runs dir is rotated. A recorded run whose log is gone still holds (verify_item).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Optional, Tuple

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
EVIDENCE_KINDS = ("commit", "merge", "tag", "doc", "run")
COUNT_FIELDS = ("collected", "passed", "failed", "errors", "skipped")
ROTATED = "log rotated; the digest and counts recorded at close stand"
UNVERIFIABLE = "unverifiable here (no digest, log not on this machine)"
RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def run_name_ok(name: str) -> bool:
    """A run: target is a bare log file name (ruling on P1 rev 1): no `/`, no `..`, no `~`.
    The acceptance bridge resolves it against the runs dir and matches the ledger's log_path
    basename. Lives here, not in write, so evidence never imports write (write imports this)."""
    return bool(RUN_NAME_RE.match(name)) and ".." not in name and name not in (".",)


def safe_relpath(path: str) -> bool:
    """A repo-relative path: not absolute, not home-relative, and never climbing with `..` (D26)."""
    if not path or os.path.isabs(path) or path.startswith("~"):
        return False
    return ".." not in Path(path).parts


def _git_ok(main: Path, *args: str) -> bool:
    try:
        result = subprocess.run(["git", "-C", str(main), *args], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def verify(main: Path, target: str) -> Tuple[bool, str]:
    """(verified, reason) for one evidence target. The reason is empty when verified."""
    kind, _, rest = target.partition(":")
    if kind in ("commit", "merge"):
        if not SHA_RE.match(rest):
            return False, "not a commit id"
        return (True, "") if _git_ok(main, "cat-file", "-e", f"{rest}^{{commit}}") \
            else (False, "no such commit in this repo")
    if kind == "tag":
        if not TAG_RE.match(rest):
            return False, "not a tag name"
        return (True, "") if _git_ok(main, "rev-parse", "-q", "--verify", f"refs/tags/{rest}") \
            else (False, "no such tag in this repo")
    if kind == "doc":
        path, sep, sha = rest.rpartition("@")
        if not sep or not SHA_RE.match(sha) or not safe_relpath(path.split("#", 1)[0]):
            return False, "a doc is evidence only as <repo-relative path>@<sha>"
        return (True, "") if _git_ok(main, "cat-file", "-e", f"{sha}:{path.split('#', 1)[0]}") \
            else (False, "that path does not exist at that commit")
    if kind == "run":
        return verify_run(rest)
    return False, f"not an evidence kind (use {', '.join(EVIDENCE_KINDS)})"


def item(main: Path, target: str) -> dict:
    """The evidence item the log records for `target`: {target, verified} plus, for a verified
    run, the log's sha256 and counts, so the claim outlives the log and the parser."""
    kind, _, rest = target.partition(":")
    if kind != "run":
        return {"target": target, "verified": verify(main, target)[0]}
    verified, _, details = run_details(rest)
    recorded = {"target": target, "verified": verified}
    if verified and details:
        recorded.update(details)
    return recorded


def verify_item(main: Path, recorded: dict) -> Tuple[bool, str]:
    """Re-verify a recorded item (fsck). A run with a recorded digest holds when the log still
    hashes to it, or when the log is gone (rotated); it fails only when the log changed. A run
    recorded before the digest whose log is not on this machine (another machine, a rotated runs
    dir) holds as UNVERIFIABLE: nothing here can prove it either way. An item without details is
    verified as at close."""
    target = recorded.get("target") or ""
    digest = recorded.get("sha256")
    if not (target.startswith("run:") and isinstance(digest, str) and digest):
        if target.startswith("run:") and find_run(target[len("run:"):]) is None:
            return True, UNVERIFIABLE
        return verify(main, target)
    log = find_run(target[len("run:"):])
    if log is None:
        return True, ROTATED
    try:
        current = hashlib.sha256(log.read_bytes()).hexdigest()
    except OSError:
        return False, "the log could not be read"
    return (True, "") if current == digest else (False, "the log changed since it was recorded")


# ---------------------------------------------------------------------------------------------
# run: a recorded test run (D10 P2)
# ---------------------------------------------------------------------------------------------

def runs_dirs() -> Iterator[Path]:
    """Where logs and their ledger live: the plugin data dir, then run-tests' fallback."""
    from shared.plugin_paths import plugin_data_dir
    yield plugin_data_dir(create=False) / "runs"
    yield Path(tempfile.gettempdir()) / "repacted-test-runs" / "runs"


def find_run(name: str) -> Optional[Path]:
    """The log file `name` in the first runs dir that holds it, or None."""
    if not run_name_ok(name):
        return None
    for runs in runs_dirs():
        log = runs / name
        if log.is_file():
            return log
    return None


def _ledger_record(ledger: Path, name: str) -> Optional[dict]:
    """The newest parsed-count record whose log_path basename is `name`."""
    from shared.ledger import parse_ledger_lines
    try:
        rows, _ = parse_ledger_lines(ledger.read_bytes())
    except OSError:
        return None
    for row in reversed(rows):
        if row.get("count_parsed") is True \
                and isinstance(row.get("log_path"), str) and Path(row["log_path"]).name == name:
            return row
    return None


def _counts_agree(text: str, record: dict) -> Optional[dict]:
    """The log's counts when some parse of it agrees with the ledger record, else None. The
    record's runner is tried first, then the generic parsers: a wrapper log (tcount around
    run-tests) parses under the generic set and not under the runner it names."""
    from shared.runcounts import parse_counts
    hints = list(dict.fromkeys([record.get("runner"), None]))
    for hint in hints:
        try:
            counts = parse_counts(text, runner_hint=hint)
        except Exception:   # noqa: BLE001 - a parser that fails on one hint is tried on the next
            continue
        if counts is None:
            continue
        fields = counts.as_ledger_fields()
        if all(fields.get(key) == record.get(key) for key in COUNT_FIELDS):
            return {key: record.get(key) for key in COUNT_FIELDS}
    return None


def run_details(name: str) -> Tuple[bool, str, Optional[dict]]:
    """(verified, reason, details) for `run:<name>`: the log exists, the ledger beside it records
    it, and the counts parsed again from the log agree with the recorded ones. details carries
    the log's sha256 and the agreed counts when verified."""
    if not run_name_ok(name):
        return False, "a run is evidence only as a bare log file name", None
    for runs in runs_dirs():
        log = runs / name
        if not log.is_file():
            continue
        record = _ledger_record(runs / "ledger.jsonl", name)
        if record is None:
            return False, "the run ledger has no parsed record of that log", None
        try:
            raw = log.read_bytes()
        except OSError:
            return False, "the log could not be read", None
        counts = _counts_agree(raw.decode("utf-8", errors="replace"), record)
        if counts is None:
            return False, "the log's counts disagree with the ledger record (or none parse)", None
        return True, "", {"sha256": hashlib.sha256(raw).hexdigest(), "counts": counts}
    return False, "no such log in the runs dir", None


def verify_run(name: str) -> Tuple[bool, str]:
    """(verified, reason) for `run:<name>`; run_details with the details dropped."""
    verified, reason, _ = run_details(name)
    return verified, reason
