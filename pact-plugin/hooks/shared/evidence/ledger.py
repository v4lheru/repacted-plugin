"""Check modes (checks.json), task files and team resolution, the run ledger and which
runs are in an item's scope, and the check state: shadow log, gate state, return-once.

Location: pact-plugin/hooks/shared/evidence/ledger.py, split out of the package's
__init__, which re-exports every name here; callers import shared.evidence.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from shared import team_policy
from shared.amendments import ALIASES
from shared.clock import utc_now_iso
from shared.ledger import parse_ledger_lines
from shared.plugin_paths import checks_dir, claude_config_dir, plugin_root, task_store
from shared.team_labels import teammate_label


CHECKS_FILE = plugin_root() / "bin" / "checks.json"
FALLBACK_WINDOW_SECONDS = 24 * 3600
# A run an item cites. EXACT: a tcount log name or path, its stem, or the short 'HHMMSSZ-pid'
# form (runs/2026-09-29T215711Z-26744-node.log, 215711Z-26744): one the ledger does not hold is
# NO-RUN. LOOSE: a bare pid after log/run ("log 13884", "runs 13884/13894"): pids repeat, so a
# pid only ever resolves to a run, it never flags one.
RUN_STEM = re.compile(r"(?<![\w-])(?:(\d{4}-\d{2}-\d{2})T)?(\d{6})Z-(\d{2,7})(?![\w])")
RUN_PIDS = re.compile(r"\b(?:logs?|runs?)\b[\s:#]*(\d{4,7}(?:\s*(?:,|/|&|and)\s*\d{4,7})*)(?![\w/.-])", re.I)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_constants() -> dict:
    """bin/checks.json: the check modes and the commit rule's words, tightened by the work
    tree's team policy (shared.team_policy: a mode only toward blocking, deny words only added).
    {} when unreadable or not a JSON object and no policy applies."""
    try:
        constants = json.loads(CHECKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        constants = {}
    return team_policy.tighten_constants(constants if isinstance(constants, dict) else {})


MODES = ("off", "shadow", "advisory", "gate")
SPEAKING_MODES = ("advisory", "gate")      # the modes whose notes reach the lead
# The shipped mode of each check read through use_mode (checks.json "modes"): used when the file
# cannot be read, is malformed, or names no mode this reader knows for the check.
DEFAULT_MODES = {"handoff_evidence": "advisory", "uncertainty_disposition": "advisory",
                 "commit_rule": "gate"}
_mode_noted: set = set()


def _gate_words() -> dict:
    """amendments.ALIASES read the other way round: the one table of words that mean 'gate'
    ('enforce', amendment_gate's own word, among them)."""
    return {canonical: alias for alias, canonical in ALIASES.items()}


def use_mode(use: str) -> str:
    """'off' | 'shadow' | 'advisory' | 'gate' for a checks.json modes key, read without regard
    to case ('enforce' reads 'gate'). When checks.json cannot be read, is malformed or names no
    known mode, the check's shipped mode (DEFAULT_MODES; 'shadow' for any other key) is used and
    one note line goes to stderr. What each mode does, per check, is in checks.json _notes."""
    shipped = DEFAULT_MODES.get(use, "shadow")
    modes = load_constants().get("modes")
    mode = str(modes.get(use, "")).strip().lower() if isinstance(modes, dict) else ""
    mode = _gate_words().get(mode, mode)
    if mode in MODES:
        return mode
    if use not in _mode_noted:
        _mode_noted.add(use)
        print(f"repacted: bin/checks.json gives no readable mode for {use}; using the shipped "
              f"mode '{shipped}'.", file=sys.stderr)
    return shipped


# ---------------------------------------------------------------------------
# Tasks and teams
# ---------------------------------------------------------------------------

def task_path(team: str, task_id: str) -> Optional[Path]:
    """The task file's path from the store adapter; None for an unsafe segment."""
    from shared.task_store import TaskRef
    store = task_store()
    return store.path(TaskRef(team, task_id))


def load_task(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    try:
        task = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return task if isinstance(task, dict) else None


def lead_session_id(team: str) -> Optional[str]:
    """The team config's leadSessionId, or None."""
    try:
        path = claude_config_dir() / "teams" / team / "config.json"
        return json.loads(path.read_text(encoding="utf-8")).get("leadSessionId")
    except (OSError, ValueError, AttributeError):
        return None


def session_journal(session_id: str) -> Optional[Path]:
    """The newest session-journal.jsonl of that session, or None."""
    paths = list(claude_config_dir().glob(f"pact-sessions/*/{session_id}/session-journal.jsonl"))
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def team_journals(team: str) -> list[Path]:
    """The session journals of that team, oldest first: the team config's lead session, and every
    session whose own pact-session-context.json names the team. After a resume the config still
    names the old session id while the journal sits under the new one (measured 2026-10-08 on
    session-76c7bf88), and a config-less team has no config to ask."""
    sessions = []
    lead = lead_session_id(team)
    if isinstance(lead, str) and PATH_SEGMENT.fullmatch(lead):
        sessions.append(lead)
    for context in (claude_config_dir() / "pact-sessions").glob("*/*/pact-session-context.json"):
        try:
            named = json.loads(context.read_text(encoding="utf-8")).get("team_name")
        except (OSError, ValueError, AttributeError):
            continue
        if named == team and context.parent.name not in sessions:
            sessions.append(context.parent.name)
    journals = {journal for journal in map(session_journal, sessions) if journal is not None}
    return sorted(journals, key=lambda path: (path.stat().st_mtime, str(path)))


def _journal_events(journal: Path, task_id: str, kinds: tuple) -> list[dict]:
    """The journal's rows of those kinds for task_id, in file order; damaged lines are skipped."""
    rows = []
    with open(journal, encoding="utf-8") as handle:
        for raw in handle:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") in kinds \
                    and str(event.get("task_id")) == str(task_id):
                rows.append(event)
    return rows


def task_from_journal(team: Optional[str], task_id: str, with_metadata: bool = False) -> Optional[dict]:
    """A stand-in task built from the session journal's latest `agent_handoff` for task_id,
    for when the task file is gone (a finished team's tasks are cleaned up, and the store has
    drained completed tasks mid-session; the journal stays). None when the team, its journals
    (team_journals) or the event is missing.

    with_metadata also folds in the latest `task_metadata_snapshot` of
    the same task (the teachback, rulings and grants; the HANDOFF is never in a snapshot), and
    then a task with a snapshot and no HANDOFF is returned too. A task id is reused across arcs
    (ids restart after a drain), so a snapshot under another subject than the HANDOFF's is a
    different task and is left out, the LATEST task of that id is the one returned, and `arcs`
    counts the distinct subjects seen under the id.
    """
    if not isinstance(team, str) or not PATH_SEGMENT.fullmatch(team):
        return None
    try:
        kinds = ("agent_handoff", "task_metadata_snapshot") if with_metadata else ("agent_handoff",)
        rows = [row for journal in team_journals(team) for row in _journal_events(journal, task_id, kinds)]
    except (OSError, ImportError):
        return None
    handoffs = [row for row in rows if row["type"] == "agent_handoff" and isinstance(row.get("handoff"), dict)]
    latest = handoffs[-1] if handoffs else None
    subject = latest.get("task_subject") if latest else None
    snapshots = [row for row in rows if row["type"] == "task_metadata_snapshot"
                 and isinstance(row.get("metadata"), dict)
                 and (latest is None or row.get("subject") == subject)]
    snapshot = snapshots[-1] if snapshots else None
    if latest is None and snapshot is None:
        return None
    metadata = {key: value for key, value in (snapshot or {}).get("metadata", {}).items() if key != "handoff"}
    if latest is not None:
        metadata["handoff"] = latest["handoff"]
    return {"id": str(task_id), "subject": (latest or {}).get("task_subject") or (snapshot or {}).get("subject") or "",
            "owner": latest.get("agent") if latest else snapshot.get("owner"),
            "metadata": metadata, "source": "journal",
            "arcs": len({str(row.get("task_subject") or row.get("subject")) for row in rows})}


def team_from_session(session_id: Optional[str]) -> Optional[str]:
    """Team named by this session's pact-session-context.json, if one exists."""
    if not session_id:
        return None
    for context in (claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/pact-session-context.json"):
        try:
            team = json.loads(context.read_text(encoding="utf-8")).get("team_name")
        except (OSError, ValueError):
            continue
        if isinstance(team, str) and team:
            return team
    return None


PATH_SEGMENT = re.compile(r"(?!\.\.?$)[A-Za-z0-9_.-]{1,128}")   # one plain path segment: a team name or task id


def resolve_team(session_id: Optional[str], task_id: str) -> Optional[str]:
    """The team of this session that holds the task: the labelling registry's team, then the
    session context's, the first whose <task_id>.json exists; else None, 'unknown team'.

    Never another session's team: the freshest task file of that id in any team was
    the last guess, and a task id repeats across teams. The registry can hold a phantom team under
    a lead's session (the F14 trap), so the session context is tried after it, not only when it
    names no team. The result only chooses which file to read; it grants nothing (labelling, not
    authority).
    """
    if not isinstance(task_id, str) or not PATH_SEGMENT.fullmatch(task_id):
        return None                     # a forged id (`../other-team/5`) names no file of this session's
    for team in (teammate_label(session_id)[1], team_from_session(session_id)):
        path = task_path(team, task_id) if isinstance(team, str) else None
        if path is not None and PATH_SEGMENT.fullmatch(team) and path.exists():
            return team
    return None


def claim_time(team: str, task: dict) -> Optional[float]:
    """When work on the task could have started: the blocking (teachback) task's last write."""
    times = []
    for blocker in task.get("blockedBy") or []:
        path = task_path(team, str(blocker))
        try:
            times.append(path.stat().st_mtime)
        except (OSError, AttributeError):
            continue
    return min(times) if times else None


# ---------------------------------------------------------------------------
# Run ledger
# ---------------------------------------------------------------------------

def read_runs(path: Path) -> tuple[list[dict], int]:
    """(records, skipped): one record per run, as shared.ledger.read_ledger_counted reads them
    (tcount's own row is dropped when a hook row carries its log path), and that hook row also
    gets "run_cmd", the argv tcount ran, and "run_ended", the stamp of tcount's row (written when
    the run ended; the hook's own stamp is the end of the whole Bash call). The hook's cmd is the
    whole Bash line, which differs
    between two runs of one command (`cd tree && tcount x`, `tcount x | tail -3`), so the latest
    run per command is told by run_cmd (superseded). In memory only: the ledger is not written."""
    try:
        records, skipped = parse_ledger_lines(Path(path).read_bytes())
    except OSError:
        return [], 0
    own = {r["log_path"]: r for r in records
           if r.get("source") == "tcount" and isinstance(r.get("log_path"), str) and r["log_path"]}
    hooked = {r["log_path"] for r in records
              if r.get("source") == "hook" and isinstance(r.get("log_path"), str) and r["log_path"]}
    runs = []
    for record in records:
        log = record.get("log_path") if isinstance(record.get("log_path"), str) else None
        if record.get("source") == "tcount" and log in hooked:
            continue
        if record.get("source") == "hook" and log in own:
            record["run_cmd"], record["run_ended"] = own[log].get("cmd"), own[log].get("ts")
        runs.append(record)
    return runs, skipped


def _command_key(record: dict) -> Optional[tuple[str, str]]:
    """(command, tree) of a run: the argv tcount ran (else the row's cmd) and the real path of
    the directory it ran in. None when either is missing: such a run supersedes nothing."""
    cmd, cwd = record.get("run_cmd") or record.get("cmd"), record.get("cwd")
    if not isinstance(cmd, str) or not cmd.strip() or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    return " ".join(cmd.split()), os.path.realpath(cwd)


def newest_runs(runs: list[dict]) -> tuple[dict[tuple[str, str], dict], set[int]]:
    """(the newest counted run of each command and tree, id() of each counted run it replaced).
    Two runs with one stamp: the later ledger line is the later run."""
    newest: dict[tuple[str, str], tuple[float, dict]] = {}
    old: set[int] = set()
    for run in runs:
        key, stamp = _command_key(run), _record_time(run)
        if key is None or stamp is None or not run.get("count_parsed"):
            continue
        if key in newest and stamp < newest[key][0]:
            old.add(id(run))
            continue
        if key in newest:
            old.add(id(newest[key][1]))
        newest[key] = (stamp, run)
    return {key: run for key, (_stamp, run) in newest.items()}, old


def superseded(runs: list[dict]) -> set[int]:
    """id() of each counted run that a LATER counted run of the same command in the same tree
    replaced: a count claimed without citing its run must
    equal a run that is not superseded, so the green run before the last red one backs nothing."""
    return newest_runs(runs)[1]

def _record_time(record: dict, key: str = "ts") -> Optional[float]:
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(record.get(key))).timestamp()
    except ValueError:
        return None


def _run_end(record: dict) -> Optional[float]:
    """When the run ended: the stamp of tcount's own row (read_runs), else the row's."""
    return _record_time(record, "run_ended") or _record_time(record)


def _floor(since: Optional[float]) -> float:
    return since if since is not None else time.time() - FALLBACK_WINDOW_SECONDS


def project_root(cwd: Optional[str]) -> Optional[str]:
    """The project a working tree belongs to: the nearest directory holding .git, and for a
    linked worktree (.git is a 'gitdir: <main>/.git/worktrees/<name>' file) the main checkout,
    so its worktrees are inside it. The cwd itself when no .git is found, or when a directory on
    the way cannot be looked at (no search permission, a name too long: a ledger row's cwd is
    whatever the row holds, and the lookup raised out of the gate); None with no cwd."""
    if not isinstance(cwd, str) or not cwd:
        return None
    start = Path(os.path.realpath(cwd))
    try:
        for directory in (start, *start.parents):
            marker = directory / ".git"
            if marker.is_dir():
                return str(directory)
            if marker.is_file():
                try:
                    gitdir = marker.read_text(encoding="utf-8").partition("gitdir:")[2].strip()
                except OSError:
                    return str(directory)
                main, sep, _ = gitdir.partition(f"{os.sep}.git{os.sep}worktrees{os.sep}")
                return os.path.realpath(main) if sep and main else str(directory)
    except (OSError, ValueError):
        pass
    return str(start)


def _inside(path, project: Optional[str]) -> bool:
    """A relative path is inside nothing: it would resolve against this process's own cwd."""
    if not isinstance(path, str) or not os.path.isabs(path) or not project:
        return False
    real, root = os.path.realpath(path), os.path.realpath(project)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def _same_dir(left, right) -> bool:
    """Two absolute paths to one directory, whatever symlink either was spelled through."""
    return all(isinstance(path, str) and os.path.isabs(path) for path in (left, right)) \
        and os.path.realpath(left) == os.path.realpath(right)


def linked_worktree(cwd) -> Optional[str]:
    """The root of the LINKED git worktree a directory is in (its .git is a file), else None:
    the main checkout, a directory outside git, a relative path. One agent works in a linked
    worktree; the main checkout is every agent's session cwd, so it names no one."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    start = Path(os.path.realpath(cwd))
    try:
        for directory in (start, *start.parents):
            marker = directory / ".git"
            if marker.is_file():
                return str(directory)
            if marker.is_dir():
                return None
    except (OSError, ValueError):     # a directory that cannot be looked at names no worktree
        pass
    return None


def _tree_key(record: dict) -> Optional[tuple[str, Optional[str]]]:
    """(linked worktree root, branch) of the tree a row's run ran in, or None: not in a linked
    worktree, or a branch that is neither text nor absent (a damaged row names no tree, and a
    list there would not hash)."""
    branch = record.get("git_branch")
    if branch is not None and not isinstance(branch, str):
        return None
    root = linked_worktree(record.get("cwd"))
    return (root, branch) if root else None


def own_worktrees(records: list[dict], team: Optional[str], task_id: str, owner: Optional[str],
                  floor: float) -> dict[tuple[str, Optional[str]], float]:
    """{(worktree root, branch): time of the first anchor} for every linked worktree this task
    is anchored in. An anchor is a run the run-ledger hook attributed to this task (or to its
    owner, with no task), joined to tcount's row by its log (so its cwd is where tcount ran),
    whose OWN Bash call was made in that worktree: one of the row's call_dirs (the payload's cwd,
    a cd or git -C in the command) is inside it. A row that only adopted a run through a shown
    log line names a tree its call never entered, and a row with no call_dirs (an older row)
    proves nothing: neither anchors (review R8). The attribution is the hook payload's, never
    the HANDOFF's."""
    trees: dict[tuple[str, Optional[str]], float] = {}
    for record in records:
        if record.get("source") != "hook" or not record.get("log_path"):
            continue
        key, stamp, dirs = _tree_key(record), _record_time(record), record.get("call_dirs")
        if key is None or stamp is None or not isinstance(dirs, list):
            continue
        if not any(_inside(path, key[0]) for path in dirs):
            continue
        if run_standing(record, team, task_id, owner, floor) in ("task", "agent"):
            trees[key] = min(stamp, trees.get(key, stamp))
    return trees


def _after_anchor(record: dict, own_trees: dict[tuple[str, Optional[str]], float]) -> bool:
    """True when an unattributed run ran in a worktree this task is anchored in, after the
    anchor: what ran there before the task's own call may be anyone's."""
    key, stamp = _tree_key(record), _record_time(record)
    return key in own_trees and stamp is not None and stamp > own_trees[key]


def _session_row(record: dict, floor: float, session: Optional[str]) -> bool:
    """A row with no team, task or agent (tcount's own) that the claimant's session wrote since
    the claim time."""
    stamp = _record_time(record)
    return bool(session) and record.get("session_id") == session and stamp is not None and stamp >= floor \
        and all(record.get(key) is None for key in ("team", "task_id", "agent_name"))


def run_standing(record: dict, team: Optional[str], task_id: str, owner: Optional[str],
                 floor: float, session: Optional[str] = None, project: Optional[str] = None) -> Optional[str]:
    """Whose run a ledger record is, seen from one task:
      'task'          attributed to this team and task (any time);
      'agent'         attributed to the task's owner with no task, since the claim time;
      'unattributed'  no team, task or agent (a tcount row), since the claim time, run in the
                      claimant's session and inside its project (J4: another project's row backed
                      a count); with no session or project known, no row is;
      'team'          another agent's or task's run in this team, since the claim time;
      None            anything else (another team or session, or older than the claim time).
    Only 'task', 'agent' and 'unattributed' runs can back this task's counts (lead ruling,
    counts that match only another agent's or task's run do not pass)."""
    rec_team, rec_task, rec_agent = record.get("team"), record.get("task_id"), record.get("agent_name")
    if team and rec_team == team and rec_task is not None and str(rec_task) == str(task_id):
        return "task"
    stamp = _record_time(record)
    if stamp is None or stamp < floor:
        return None
    if rec_team is None and rec_task is None and rec_agent is None:
        own = _session_row(record, floor, session) and _inside(record.get("cwd"), project)
        return "unattributed" if own else None
    if owner and rec_agent == owner and rec_task is None and rec_team in (None, team):
        return "agent"
    return "team" if team and rec_team == team else None


def runs_in_scope(records: list[dict], team: Optional[str], task_id: str, cwd: Optional[str],
                  branch: Optional[str], since: Optional[float], owner: Optional[str] = None,
                  counted: bool = True, session: Optional[str] = None) -> list[dict]:
    """This task's runs: attributed to its team and task, or to its owner, or unattributed in
    the claimant's session since the claim time and either in the same cwd (and branch, when
    known) or in a linked worktree this task is anchored in, after the anchor (own_worktrees: a
    teammate's session cwd is the main checkout, its runs are in its worktree, and tcount cannot
    name the agent). The anchored worktree need not lie inside the project's own directory: a
    worktree made beside the main checkout is the task's all the same (1.3.4 field report, C: six
    runs whose report a pipe cut stayed out of scope and nine correct counts were returned).
    Never another agent's or task's run. counted=False keeps runs with no parsed count (a scratch
    probe still ran)."""
    floor, project = _floor(since), project_root(cwd)
    own_trees = own_worktrees(records, team, task_id, owner, floor)
    scoped = []
    for record in records:
        if counted and not record.get("count_parsed"):
            continue
        standing = run_standing(record, team, task_id, owner, floor, session, project)
        if standing in ("task", "agent"):
            scoped.append(record)
        elif standing == "unattributed":
            same_tree = _same_dir(record.get("cwd"), cwd) and (not branch or record.get("git_branch") == branch)
            if same_tree or (own_trees and _after_anchor(record, own_trees)):
                scoped.append(record)
        elif own_trees and _session_row(record, floor, session) and _after_anchor(record, own_trees):
            scoped.append(record)
    return scoped


def _run_key(record: dict) -> Optional[tuple]:
    match = RUN_STEM.search(Path(str(record.get("log_path") or "")).name)
    return match.groups() if match else None


def cited_runs(text: str, records: list[dict], team: Optional[str], task_id: str, owner: Optional[str],
               since: Optional[float], session: Optional[str] = None,
               project: Optional[str] = None, scope: Optional[list[dict]] = None,
               ) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """(own, other, missing, ambiguous) for the runs an item cites. own: this task's cited runs
    (their counts back the item), which every run in its scope is; other: cited runs of another
    agent or task in the team (proof that something ran, never of the item's counts); missing:
    EXACT citations with no such run for
    this task in the ledger (a loose pid that resolves to nothing is dropped, never missing);
    ambiguous: citations by the short stamp alone ('203418Z-23083', no date) that two or more
    recorded logs have (a pid and a clock second repeat across days). A short stamp is the run
    when exactly one ledger log has it; shared, it names no run: it backs nothing, it is not
    missing, and the lead gets a note."""
    floor = _floor(since)
    keyed = [(key, record) for record in records if (key := _run_key(record))]
    in_scope = {id(record) for record in scope or []}
    own: list[dict] = []
    other: list[dict] = []
    missing: list[str] = []
    ambiguous: list[str] = []

    def take(matches: list[dict]) -> bool:
        found = False
        for record in matches:
            standing = run_standing(record, team, task_id, owner, floor, session, project)
            if id(record) in in_scope or standing in ("task", "agent", "unattributed"):
                own.append(record)
                found = True
            elif standing == "team":
                other.append(record)
                found = True
        return found

    for match in RUN_STEM.finditer(text):
        date, clock, pid = match.groups()
        hits = [r for k, r in keyed if k[1] == clock and k[2] == pid and (date is None or k[0] == date)]
        if date is None and len({k for k, _ in keyed if k[1] == clock and k[2] == pid}) > 1:
            ambiguous.append(match.group(0))
        elif not take(hits):
            missing.append(match.group(0))
    for match in RUN_PIDS.finditer(RUN_STEM.sub(" ", text)):
        for pid in re.findall(r"\d+", match.group(1)):
            take([r for k, r in keyed if k[2] == pid])
    return list({id(r): r for r in own}.values()), list({id(r): r for r in other}.values()), missing, \
        list(dict.fromkeys(ambiguous))


COUNT_KEYS = ("collected", "passed", "failed", "errors", "skipped")


def cited_total(own: list[dict], other: list[dict], missing: list[str], ambiguous: list[str],
                newest: Optional[dict[tuple[str, str], dict]] = None) -> tuple[Optional[dict], list[dict]]:
    """(total, the runs it adds): the counts of the runs an item cites, summed key by key, when
    it cites two or more and every one is this task's own counted run; else (None, []) (lead
    rule: "Gate: 14 projects, 1815/1815" over fourteen cited logs is in
    no single log). A citation that is another task's, missing, uncounted or a short stamp two
    logs share leaves no sum: the sender named a run the check cannot add.
    newest: the newest known run of each command and tree (newest_runs). A cited run that a
    later run of its command replaced is added as that newest run, and each command adds one
    run: two cited runs of one suite backed a doubled total, and a total stood over a cited
    run that a later red run of its command had replaced (review of 69d9de3a, EV3). The run is
    exchanged, not dropped: the total of a unit log and an e2e log is still their total after
    the unit suite was run again with the same result (review of fb0be49a, finding 1). Fewer
    than two commands left is no sum."""
    if other or missing or ambiguous or not all(run.get("count_parsed") for run in own):
        return None, []
    added: list[dict] = []
    for run in own:
        key = _command_key(run)
        run = (newest or {}).get(key, run) if key is not None else run
        if not any(run is one for one in added):
            added.append(run)
    if len(added) < 2:
        return None, []
    total = {}
    for key in COUNT_KEYS:
        values = [run.get(key) for run in added]
        if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            total[key] = sum(values)
    return total, added

def recorded_run_ids(records: list[dict], runs_dir: Optional[Path]) -> set[tuple]:
    """The id (date, clock, pid) of every run that exists at all: each ledger row's log name,
    whatever its team, task, session or age, and each regular log file in the runs directory.
    The directory is listed by name only: no file is opened, and a symlink is not a log (a
    planted link can carry any name)."""
    found = {key for record in records if (key := _run_key(record))}
    try:
        with os.scandir(runs_dir) as entries:
            for entry in entries:
                match = RUN_STEM.search(entry.name)
                if match and entry.is_file(follow_symlinks=False):
                    found.add(match.groups())
    except (OSError, TypeError, ValueError):
        pass
    return found


def recorded_elsewhere(citations: list[str], run_ids: set[tuple]) -> list[str]:
    """The exact citations cited_runs calls missing whose run exists (recorded_run_ids): cited_runs
    answers for ONE task, so a real run of another team, session or project, or from before the
    claim time, reads there as a run the ledger lacks. These are out of scope, not absent."""
    outside = []
    for citation in citations:
        match = RUN_STEM.search(citation)
        date, clock, pid = match.groups() if match else (None, None, None)
        if any(key[1] == clock and key[2] == pid and (date is None or key[0] == date) for key in run_ids):
            outside.append(citation)
    return outside


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def claimant_session(session_id: Optional[str], team: Optional[str]) -> Optional[str]:
    """The session the HANDOFF's writer runs in: the hook's session_id, else the team lead's
    (in-process teammates share it). A teammate in its own session whose id is not passed gets
    the lead's, so its unattributed rows back nothing (NO-RUN, never a false MATCH)."""
    if session_id or not team:
        return session_id or None
    return lead_session_id(team)


def written_at(team: Optional[str], task_id: str) -> Optional[float]:
    """When the gate last saw a HANDOFF write of this task (handoff_gate records it in the gate
    state), or None. Acceptance can come a day after the write, and a result file is as old as it
    was when the HANDOFF cited it. The task file's own time is no use: the completing write and
    every metadata write after the HANDOFF move it (probe rerun, item 3)."""
    stamp = read_gate_state(team, task_id).get("handoff_written")
    known = isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and 0 < stamp <= time.time()
    return float(stamp) if known else None


# ---------------------------------------------------------------------------
# Persistence and messages
# ---------------------------------------------------------------------------

def shadow_log(event: dict) -> None:
    """One JSON line per check event (a return, a repeat, a note): repact-report counts them."""
    line = (json.dumps(dict({"ts": utc_now_iso()}, **event), ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")
    try:
        fd = os.open(checks_dir() / "shadow.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass


def gate_state_path(team: Optional[str], task_id: str, create: bool = False) -> Path:
    directory = checks_dir(create=create) / "gates" / (team or "_unknown")
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{task_id}.json"


def read_gate_state(team: Optional[str], task_id: str) -> dict:
    state = load_task(gate_state_path(team, task_id)) or {}
    for key in ("returns", "repeats", "notes"):
        state.setdefault(key, [])
    return state


def _write_gate_state(team: Optional[str], task_id: str, state: dict) -> None:
    path = gate_state_path(team, task_id, create=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def return_once(team: Optional[str], task_id: str, use: str, flagged: list[dict]) -> list[dict]:
    """Split flagged items ({key, line} and, for a HANDOFF item, {where, text, sig}) into the ones
    to RETURN now (never returned before) and repeats, which pass. Records both in the gate state
    and the shadow log; returns the items to return (empty: the write passes). A repeat is the
    same claim (key). handoff_evidence, uncertainty_disposition and commit_rule never come
    here (1.4.1): the first two are never returned, and a commit is returned every time."""
    if not flagged:
        return []
    state = read_gate_state(team, task_id)
    seen = {(r.get("use"), r.get("key")) for r in state["returns"]}
    now = utc_now_iso()

    def again(flag: dict) -> bool:
        return (use, flag["key"]) in seen

    fresh = [f for f in flagged if not again(f)]
    repeats = [f for f in flagged if again(f)]

    def entry(flag: dict) -> dict:
        return {"ts": now, "use": use, **{k: flag[k] for k in ("key", "sig", "line", "where", "text") if k in flag}}

    state["returns"] += [entry(f) for f in fresh]
    state["repeats"] += [entry(f) for f in repeats]
    try:
        _write_gate_state(team, task_id, state)
    except OSError:
        pass
    for event, items in (("return", fresh), ("repeat_pass", repeats)):
        if items:
            shadow_log({"kind": "write_gate", "use": use, "event": event, "team": team,
                        "task_id": task_id, "keys": [f["key"] for f in items]})
    return fresh
