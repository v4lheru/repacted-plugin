#!/usr/bin/env python3
"""Script-first HANDOFF harvest for the PACT secretary.

Location: pact-plugin/scripts/harvest.py. The `pact-handoff-harvest` skill runs these verbs and
keeps only the judgement (extract, dedupe, save) for the secretary:

  candidates      the population (journal agent_handoff events + the task-file metadata census,
                  minus the processed-task ledger), one merged HANDOFF per work unit, the
                  coverage report
  record          append this pass's task ids to the ledger
  archive         D6: copy harvested + completed task JSON to <session>/tasks-archive/ and print
                  the ids the secretary may delete; never an open, blocking or ALERT/BLOCKER task,
                  nor one an open task cites (by `#id`, `task id`, its file path, or a blocks edge)
  compact-summary locate (and with --archive, move) the session's compact-summary.txt
  orphans         sessions (the ACTIVE one included) whose agent_handoff tasks the ledger lacks

THE LEDGER moved from agent memory (`session_processed_tasks.md`, one `## team=` section per team)
to `<config>/pact-sessions/<project>/processed-tasks/<team>.jsonl`: append-only JSON lines, each
naming the session directory its ids belong to, so a task id reused by another session or team
can never mark this session's task as processed. The old file is still READ, with its
count-decides-existence rule (0 = none, 1 = the section, 2+ = refuse), and never written.

Every read here is a READ: no verb deletes a journal, a task file or a ledger line.

Usage: python3 "<plugin>/scripts/harvest.py" <verb> --help
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

from shared.agent_handoff_marker import occupant_hash  # noqa: E402
from shared.session_journal import read_events_from  # noqa: E402

LEDGER_DIRNAME = "processed-tasks"
OLD_LEDGER_NAME = "session_processed_tasks.md"
ARCHIVE_DIRNAME = "tasks-archive"
COMPACT_SUMMARY_NAME = "compact-summary.txt"
OPEN_STATUSES = frozenset({"pending", "in_progress"})
# A Task A (teachback) carries only these keys: it never holds a HANDOFF.
TEACHBACK_KEYS = frozenset({"teachback_submit", "teachback_rejection", "intentional_wait"})
# Signal tasks (algedonic, blockers) are listed on their own, never flagged as missing a HANDOFF.
SIGNAL_PREFIXES = ("ALERT", "BLOCKER", "HALT", "STOP")

# The values that count as EMPTY for the present-and-empty exception (membership, not truthiness:
# a task-file field holding 0 or False is content). Keep None in the set.
_EMPTY_VALUES = (None, "", [], {})


class HarvestError(Exception):
    """A state the harvest must refuse rather than guess past."""


# --- small helpers -----------------------------------------------------------------------------

def config_dir() -> Path:
    value = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".claude"


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_side_subject(subject: Any) -> str:
    """Mirror of the sentinel both emit paths apply before hashing (change them together)."""
    return subject if (subject and str(subject).strip()) else "(no subject)"


def handoff_key(metadata: dict) -> str | None:
    """The metadata key holding the HANDOFF, matched case-insensitively."""
    if "handoff" in metadata:
        return "handoff"
    for key in metadata:
        if isinstance(key, str) and key.lower() == "handoff":
            return key
    return None


def union_preferring_task_file(journal: dict, disk: dict) -> dict:
    """Task file wins each conflict, except a PRESENT-AND-EMPTY task-file field over journal content."""
    merged = dict(journal)
    for field, value in disk.items():
        if value in _EMPTY_VALUES and journal.get(field):
            continue
        merged[field] = value
    return merged


def latest_by_ts_then_journal_order(events: list[dict]) -> dict:
    """Last write wins; an equal ts keeps the later journal line (events in journal order)."""
    winner = events[0]
    for event in events[1:]:
        if str(event.get("ts", "")) >= str(winner.get("ts", "")):
            winner = event
    return winner


def load_tasks(tasks_dir: Path, unreadable: list[str] | None = None,
               mismatched: list[dict] | None = None) -> dict[str, dict]:
    """Every readable task JSON, keyed by its FILE NAME (the platform names a task file by its
    id). A file that does not parse is skipped here and its name appended to `unreadable`, so a
    caller that must not guess (the archive) can refuse. A file whose inner id differs from its
    name is still loaded under its name and appended to `mismatched` as {"file", "id"}: keyed by
    the inner id, a planted zz.json claiming id 20 replaced the real task 20, and an open task
    read as completed (second review, finding 10)."""
    tasks: dict[str, dict] = {}
    if not tasks_dir.is_dir():
        return tasks
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            data = None
        if isinstance(data, dict):
            tasks[path.stem] = data
            inner = data.get("id")
            if mismatched is not None and inner is not None and str(inner) != path.stem:
                mismatched.append({"file": path.name, "id": str(inner)})
        elif unreadable is not None:
            unreadable.append(path.name)
    return tasks


def is_signal(task: dict) -> bool:
    return str((task or {}).get("subject", "")).upper().startswith(SIGNAL_PREFIXES)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


# --- the ledger ----------------------------------------------------------------------------------

def ledger_path(session_dir: Path, team: str) -> Path:
    """<config>/pact-sessions/<project>/processed-tasks/<team>.jsonl (the session dir's parent)."""
    return Path(session_dir).parent / LEDGER_DIRNAME / f"{team}.jsonl"


def read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn line never hides the lines around it
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def read_old_ledger(path: Path, team: str) -> set[str]:
    """Union of every `Processed task IDs` line in the team's `## team=` section of the old file.

    Existence is decided by a COUNT of section headers, never by searching a truncated read:
    0 = no section, 1 = read it, 2 or more = refuse (a duplicate means an earlier pass wrote a
    second section from a truncated read).
    """
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    header = re.compile(rf"^## team={re.escape(team)}(?=[\s(]|$)")
    starts = [i for i, line in enumerate(lines) if header.match(line)]
    if not starts:
        return set()
    if len(starts) > 1:
        raise HarvestError(f"{path}: {len(starts)} '## team={team}' sections; refusing to guess")
    ids: set[str] = set()
    for line in lines[starts[0] + 1:]:
        if line.startswith("## team="):
            break
        if line.startswith("Processed task IDs"):
            ids.update(part.strip().lstrip("#") for part in line.split(":", 1)[1].split(",")
                       if part.strip())
    return ids


def processed_ids(session_dir: Path, team: str, old_ledger: Path | None) -> set[str]:
    """This session's processed set: the new ledger's entries for this session dir, united with
    the old ledger's section (whose ids carry no session and are read as this team's)."""
    here = _norm(session_dir)
    ids = {str(i) for entry in read_ledger(ledger_path(session_dir, team))
           if _norm(entry.get("session_dir", "")) == here for i in entry.get("task_ids", [])}
    if old_ledger is not None:
        ids |= read_old_ledger(old_ledger, team)
    return ids


def record(session_dir: Path, team: str, task_ids: Iterable[str], kind: str,
           origin_session_dir: Path | None = None) -> Path:
    """Append one ledger line. Append-only: nothing is rewritten, nothing is pruned."""
    path = ledger_path(session_dir, team)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": utc_now(), "team": team, "kind": kind,
            "session_dir": str(origin_session_dir or session_dir),
            "task_ids": sorted({str(i) for i in task_ids}, key=lambda s: (len(s), s))}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return path


# --- candidates: the population, the merged HANDOFFs and the coverage report ---------------------

def _journal_by_task(events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(str(event.get("task_id", "")), []).append(event)
    return grouped


def build_units(task_id: str, events: list[dict], task: dict, snapshots: list[dict],
                reports: list[str]) -> list[dict]:
    """One work unit per (agent, subject) identity: the Step 3 selection and union."""
    metadata = (task.get("metadata") or {}) if task else {}
    key = handoff_key(metadata) if isinstance(metadata, dict) else None
    disk = metadata.get(key) if key else None
    disk = disk if isinstance(disk, dict) else {}
    if key and key != "handoff":
        reports.append(f"task {task_id}: HANDOFF stored under key {key!r} (key-case mismatch); "
                       "read as the handoff")
    disk_identity = (occupant_hash(task.get("owner", "") or "", emit_side_subject(task.get("subject")))
                     if task else None)
    groups: dict[str, list[dict]] = {}
    for event in events:
        ident = occupant_hash(event.get("agent", "") or "", emit_side_subject(event.get("task_subject")))
        groups.setdefault(ident, []).append(event)
    snapshot_keys = sorted({k for s in snapshots for k in (s.get("metadata") or {})})
    if not groups:
        if not task:
            reports.append(f"task {task_id}: no source resolves (journal and task file both empty)")
            return []
        return [{"task_id": task_id, "agent": task.get("owner", ""), "subject": task.get("subject", ""),
                 "status": task.get("status", ""), "handoff": disk,
                 "carriers": ["task_file"] if disk else [], "snapshot_keys": snapshot_keys}]
    units = []
    for ident, group in groups.items():
        winner = latest_by_ts_then_journal_order(group)
        handoff = winner.get("handoff") if isinstance(winner.get("handoff"), dict) else {}
        carriers = ["journal"]
        if ident == disk_identity:
            if disk:
                handoff = union_preferring_task_file(handoff, disk)
                carriers.append("task_file")
        elif not task:
            reports.append(f"handoff divergence check unavailable for task {task_id}: task file absent")
        else:
            reports.append(f"handoff identity mismatch for task {task_id}: task file present, "
                           "no event group carries its identity")
        units.append({"task_id": task_id, "agent": winner.get("agent", ""),
                      "subject": winner.get("task_subject", ""), "ts": winner.get("ts", ""),
                      "status": (task or {}).get("status", "") if ident == disk_identity else "",
                      "handoff": handoff, "carriers": carriers, "snapshot_keys": snapshot_keys})
    return units


def coverage_row(task_id: str, task: dict, units: list[dict], snapshots: list[dict]) -> dict:
    """Which carrier captured the task's HANDOFF; flag a task with none (amend_2, minimal M12)."""
    carriers = sorted({c for unit in units for c in unit["carriers"] if unit["handoff"]})
    metadata = (task.get("metadata") or {}) if task else {}
    other = sorted(k for k in metadata if handoff_key({k: 1}) is None)
    if snapshots:
        carriers.append("snapshot")
    row = {"task_id": task_id, "subject": (task or {}).get("subject") or
           (units[0]["subject"] if units else ""), "handoff_carriers": carriers,
           "other_keys": other}
    if not any(c in ("journal", "task_file") for c in carriers):
        if metadata and set(metadata) <= TEACHBACK_KEYS:
            row["kind"] = "teachback"  # a Task A carries a teachback, never a HANDOFF
        elif is_signal(task):
            row["kind"] = "signal"     # listed under `signals`, not flagged
        else:
            row["flag"] = "no HANDOFF carrier"
    return row


def candidates(session_dir: Path, team: str, tasks_dir: Path, old_ledger: Path | None,
               include: Iterable[str] = ()) -> dict:
    """The harvest population and everything the secretary judges from."""
    handoff_events = read_events_from(str(session_dir), "agent_handoff")
    snapshot_events = read_events_from(str(session_dir), "task_metadata_snapshot")
    unreadable: list[str] = []
    tasks = load_tasks(tasks_dir, unreadable)
    by_task = _journal_by_task(handoff_events)
    snaps = _journal_by_task(snapshot_events)
    census = {tid for tid, t in tasks.items() if isinstance(t.get("metadata"), dict) and t["metadata"]
              and (t.get("status") == "completed" or handoff_key(t["metadata"]))}
    done = processed_ids(session_dir, team, old_ledger)
    forced = {str(i) for i in include}  # the quiescent-task re-sweep: widening is allowed
    population = sorted((set(by_task) | census) - done | forced, key=lambda s: (len(s), s))
    reports: list[str] = []
    units, coverage = [], []
    for tid in population:
        task_units = build_units(tid, by_task.get(tid, []), tasks.get(tid, {}), snaps.get(tid, []),
                                 reports)
        units.extend(task_units)
        coverage.append(coverage_row(tid, tasks.get(tid, {}), task_units, snaps.get(tid, [])))
    for unit in units:
        if unit["status"] and unit["status"] != "completed" and unit["handoff"]:
            reports.append(f"task {unit['task_id']}: HANDOFF not yet accepted (status "
                           f"{unit['status']}); hold it until the team-lead completes the task")
    for name in unreadable:
        reports.append(f"task file {name} does not parse; its task is not in this population")
    for row in coverage:
        if "flag" in row:
            reports.append(f"task {row['task_id']}: no HANDOFF found (no key matches 'handoff' in "
                           "any case, and no agent_handoff event)")
    return {"session_dir": str(session_dir), "team": team,
            "ledger": str(ledger_path(session_dir, team)), "processed": sorted(done),
            "population": population, "units": units, "coverage": coverage, "reports": reports,
            "signals": [{"task_id": tid, "subject": t.get("subject", ""), "status": t.get("status", "")}
                        for tid, t in sorted(tasks.items(), key=lambda kv: (len(kv[0]), kv[0]))
                        if is_signal(t)]}


# --- D6: archive, then the secretary deletes -------------------------------------------------------

def _strings(value: Any) -> Iterable[str]:
    """Every string in a JSON value, dict keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _citation_re(task_id: str) -> re.Pattern:
    """How text names ONE task directly: its file `<id>.json` (so any path ending in it), the
    same path without the suffix (`tasks/<team>/<id>`), or `#<id>`. A bare number is not a
    citation."""
    tid = re.escape(task_id)
    return re.compile(rf"(?<![\w.-]){tid}\.(?i:json)(?!\w)|(?<!\w)#{tid}(?!\w)"
                      rf"|(?<![\w.-])tasks/[^/\s\"']+/{tid}(?![\w/]|\.\w)")


# `task`, `tasks`, `task id`, `taskId`, `task_id` or a Task tool name, then the number or a list
# of them: "task 12", "tasks 11 and 12", "tasks 11, 12", "tasks 11/12", "tasks 10-12",
# "task ID: 12", "TaskGet(taskId=12)", "task-12".
_TASK_NUMBERS = re.compile(
    r"(?i)(?<![a-z])task(?:s|get|update|stop|[\s_-]*ids?)?[\s:=#(\"'_-]*"
    r"(\d+(?:\s*(?:,|/|&|-|\u2013|\band\b|\bor\b|\bto\b|\bthrough\b)\s*#?\d+)*)")
_LIST_PART = re.compile(r"\d+|-|\u2013|\bto\b|\bthrough\b", re.I)
RANGE_MAX = 1000                # a longer "range" is two numbers, not a list of tasks


def _listed_numbers(listing: str) -> set[int]:
    """Every task number a list after `task(s)` names; `10-12` and `10 to 12` name 10, 11, 12."""
    parts = _LIST_PART.findall(listing)
    numbers = {int(part) for part in parts if part.isdigit()}
    for left, between, right in zip(parts, parts[1:], parts[2:]):
        if left.isdigit() and right.isdigit() and not between.isdigit() \
                and 0 < int(right) - int(left) <= RANGE_MAX:
            numbers.update(range(int(left), int(right) + 1))
    return numbers


def _cites(text: str, task_id: str) -> bool:
    """True when the text names the task: directly (_citation_re) or in a list or range after
    `task(s)`. Only the first id of a list used to count, and `task id 12`, `taskId=12` and the
    path without `.json` did not, so a file a live brief names was listed deletable (second
    review, finding 7). A bare number is still not a citation."""
    if _citation_re(task_id).search(text):
        return True
    if not (task_id.isascii() and task_id.isdigit()):
        return False
    number = int(task_id)
    return any(number in _listed_numbers(found.group(1)) for found in _TASK_NUMBERS.finditer(text))


def cited_by_open(tasks: dict[str, dict], wanted: Iterable[str]) -> dict[str, list[str]]:
    """For each wanted id that an OPEN task cites, the citing ids: through a blocks/blockedBy edge,
    or anywhere in the open task's text (every string in the task file). Deleting a cited file
    breaks the path a live dispatch points at, so the match errs toward keeping."""
    open_tasks = {oid: t for oid, t in tasks.items() if t.get("status") in OPEN_STATUSES}
    texts = {oid: "\n".join(_strings(t)) for oid, t in open_tasks.items()}
    edges = {oid: {str(ref) for key in ("blocks", "blockedBy") for ref in (t.get(key) or [])}
             for oid, t in open_tasks.items()}
    cited: dict[str, list[str]] = {}
    for tid in wanted:
        citing = [oid for oid in open_tasks
                  if oid != tid and (tid in edges[oid] or _cites(texts[oid], tid))]
        if citing:
            cited[tid] = sorted(citing, key=lambda s: (len(s), s))
    return cited


def archive(session_dir: Path, team: str, tasks_dir: Path, old_ledger: Path | None,
            ids: Iterable[str] | None = None, dry_run: bool = False) -> dict:
    """Copy each harvested + completed task JSON to <session>/tasks-archive/<id>.json, byte-identical.

    Never eligible: a pending/in_progress task (a pending ALERT/BLOCKER/HALT included), a task any
    open task names in blockedBy or cites (see cited_by_open: a live brief may point at the file),
    or a task this session's ledger does not list as harvested.
    Refuses outright when any task file fails to parse: an unreadable open task could be the one
    whose blockedBy or text protects an id, so no eligibility answer is safe (fails closed).
    A file whose inner id differs from its name is reported in `id_mismatch`, is never archived
    itself, and stands for no other task: tasks are keyed by file name (load_tasks).
    Returns `deletable`: the ONLY ids the secretary may delete, all archived byte-identically in
    this run. A dry run returns them as `would_archive` and leaves `archived`/`deletable` empty.
    """
    unreadable: list[str] = []
    mismatched: list[dict] = []
    tasks = load_tasks(tasks_dir, unreadable, mismatched)
    odd_files = {Path(entry["file"]).stem for entry in mismatched}
    if unreadable:
        raise HarvestError(f"task files that do not parse: {', '.join(unreadable)}; archiving "
                           "refused (a blockedBy they hold could protect a task)")
    harvested = processed_ids(session_dir, team, old_ledger)
    wanted = sorted({str(i) for i in ids} if ids is not None else harvested, key=lambda s: (len(s), s))
    blocking = {str(ref) for t in tasks.values() if t.get("status") in OPEN_STATUSES
                for ref in (t.get("blockedBy") or [])}
    cited = cited_by_open(tasks, wanted)
    dest_dir = Path(session_dir) / ARCHIVE_DIRNAME
    archived, skipped = [], []
    for tid in wanted:
        task = tasks.get(tid)
        status = (task or {}).get("status")
        reason = None
        if task is None:
            reason = "no task file"
        elif tid in odd_files:
            reason = "file name and inner id differ"
        elif status != "completed":
            reason = f"status {status}"
        elif tid in blocking:
            reason = "named in an open task's blockedBy"
        elif tid in cited:
            reason = f"kept: cited by open task {', '.join(cited[tid])}"
        elif tid not in harvested:
            reason = "not harvested in this session"
        if reason:
            skipped.append({"id": tid, "reason": reason, **({"cited_by": cited[tid]}
                                                             if reason.startswith("kept:") else {})})
            continue
        source = tasks_dir / f"{tid}.json"
        data = source.read_bytes()
        dest = dest_dir / f"{tid}.json"
        if dest.exists() and dest.read_bytes() != data:
            skipped.append({"id": tid, "reason": "archive exists with different bytes"})
            continue
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".json.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            if dest.read_bytes() != data:
                raise HarvestError(f"archive of task {tid} did not round-trip")
        archived.append(tid)
    sha = {tid: hashlib.sha256((tasks_dir / f"{tid}.json").read_bytes()).hexdigest()
           for tid in archived}
    if dry_run:
        return {"archive_dir": str(dest_dir), "would_archive": archived, "archived": [],
                "deletable": [], "skipped": skipped, "dry_run": True, "sha256": sha,
                "id_mismatch": mismatched}
    return {"archive_dir": str(dest_dir), "archived": archived, "deletable": list(archived),
            "skipped": skipped, "dry_run": False, "sha256": sha, "id_mismatch": mismatched}


# --- compact summary, per session ---------------------------------------------------------------

def compact_summary(session_dir: Path, root: Path, do_archive: bool) -> dict:
    """The session-scoped compact-summary.txt, else the root singleton; with do_archive, MOVE it
    to compact-summary-<UTC timestamp>.txt in the session directory. Never a delete."""
    candidates_ = [(Path(session_dir) / COMPACT_SUMMARY_NAME, "session"),
                   (root / "pact-sessions" / COMPACT_SUMMARY_NAME, "root")]
    for path, origin in candidates_:
        if path.is_file():
            if not do_archive:
                return {"path": str(path), "origin": origin, "archived": None}
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            dest = Path(session_dir) / f"compact-summary-{stamp}.txt"
            size = path.stat().st_size
            os.replace(path, dest)
            if not dest.is_file() or dest.stat().st_size != size or size == 0:
                raise HarvestError(f"compact summary move to {dest} did not verify")
            return {"path": str(path), "origin": origin, "archived": str(dest)}
    return {"path": None, "origin": None, "archived": None}


# --- orphans: the active session is included ---------------------------------------

def orphans(root: Path, team: str, current_session_dir: Path, old_ledger: Path | None) -> list[dict]:
    """Every session journal whose agent_handoff task ids no ledger entry for that session covers."""
    found = []
    for journal in sorted((root / "pact-sessions").glob("*/*/session-journal.jsonl")):
        sdir = journal.parent
        ids = {str(e.get("task_id")) for e in read_events_from(str(sdir), "agent_handoff")}
        if not ids:
            continue
        done: set[str] = set()
        for ledger in (sdir.parent / LEDGER_DIRNAME).glob("*.jsonl"):
            done |= {str(i) for e in read_ledger(ledger) if _norm(e.get("session_dir", "")) == _norm(sdir)
                     for i in e.get("task_ids", [])}
        if _norm(sdir) == _norm(current_session_dir) and old_ledger is not None:
            done |= read_old_ledger(old_ledger, team)
        missing = sorted(ids - done, key=lambda s: (len(s), s))
        if missing:
            found.append({"session_dir": str(sdir), "active": _norm(sdir) == _norm(current_session_dir),
                          "unprocessed": missing})
    return found


# --- CLI ------------------------------------------------------------------------------------------

def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--session-dir", required=True, help="absolute session directory")
    p.add_argument("--team", required=True, help="team id, e.g. session-5a318e14")
    p.add_argument("--old-ledger", default=None,
                   help="old ledger file to read as a fallback (default: agent-memory/pact-secretary/"
                        f"{OLD_LEDGER_NAME} under the config dir)")


def _old_ledger(args) -> Path:
    return Path(args.old_ledger) if args.old_ledger else \
        config_dir() / "agent-memory" / "pact-secretary" / OLD_LEDGER_NAME


def _tasks_dir(args) -> Path:
    return Path(args.tasks_dir) if args.tasks_dir else config_dir() / "tasks" / args.team


def main(argv: list[str] | None = None) -> int:
    prog = f'python3 "{Path(__file__).resolve()}"'
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="\n".join([
        f"{prog} candidates --session-dir \"$SESSION_DIR\" --team session-XXXXXXXX",
        f"{prog} record --session-dir \"$SESSION_DIR\" --team session-XXXXXXXX --ids 3,5,7",
        f"{prog} archive --session-dir \"$SESSION_DIR\" --team session-XXXXXXXX",
        f"{prog} compact-summary --session-dir \"$SESSION_DIR\" --archive",
        f"{prog} orphans --session-dir \"$SESSION_DIR\" --team session-XXXXXXXX"]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)
    for name in ("candidates", "record", "archive", "orphans"):
        p = sub.add_parser(name)
        _common(p)
        if name in ("candidates", "archive"):
            p.add_argument("--tasks-dir", default=None, help="default: <config>/tasks/<team>")
        if name == "candidates":
            p.add_argument("--include", default="", help="comma-separated ids to re-sweep")
        if name == "record":
            p.add_argument("--ids", required=True)
            p.add_argument("--kind", default="standard",
                           choices=["standard", "incremental", "consolidation", "orphan"])
            p.add_argument("--origin-session-dir", default=None)
        if name == "archive":
            p.add_argument("--ids", default=None, help="default: every id harvested this session")
            p.add_argument("--dry-run", action="store_true")
    cs = sub.add_parser("compact-summary")
    cs.add_argument("--session-dir", required=True)
    cs.add_argument("--archive", action="store_true")
    args = parser.parse_args(argv)
    ids = lambda raw: [i.strip().lstrip("#") for i in (raw or "").split(",") if i.strip()]  # noqa: E731
    try:
        sdir = Path(args.session_dir)
        if not sdir.is_absolute():
            raise HarvestError("--session-dir must be absolute")
        if args.verb == "candidates":
            out: Any = candidates(sdir, args.team, _tasks_dir(args), _old_ledger(args), ids(args.include))
        elif args.verb == "record":
            origin = Path(args.origin_session_dir) if args.origin_session_dir else None
            out = {"ledger": str(record(sdir, args.team, ids(args.ids), args.kind, origin))}
        elif args.verb == "archive":
            out = archive(sdir, args.team, _tasks_dir(args), _old_ledger(args),
                          ids(args.ids) if args.ids else None, args.dry_run)
        elif args.verb == "compact-summary":
            out = compact_summary(sdir, config_dir(), args.archive)
        else:
            out = orphans(config_dir(), args.team, sdir, _old_ledger(args))
    except HarvestError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
