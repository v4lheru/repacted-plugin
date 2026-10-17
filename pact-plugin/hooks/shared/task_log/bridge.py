"""The acceptance bridge: platform task acceptance -> the project task log (D10 P2; design §5).

Location: pact-plugin/hooks/shared/task_log/bridge.py. Called by hooks/evidence_gate.py on every
PostToolUse(TaskUpdate) (`handle`), by `repact-task sync` and every repact-task read command
(`sync`), and by hooks/session_ledger.py's detached SessionEnd child (`sync_quietly`).

What it does, only in the LEAD's frame (the caller passes `lead`, from pact_context.is_lead on the
payload's agent_type; measured on Claude Code 2.1.283, #180 a1_capture_result):
  - project_task or refs in the lead's own TaskUpdate/TaskCreate metadata -> bind: link
    ptask:<team>/<id> (project_task is also marked active). Only these links, and `start --ptask`,
    bind a ref; a ref a teammate writes into metadata is reported at acceptance, never closed;
  - the task completed (statusChange to completed) -> for EVERY project task bound to the
    platform task: journal a lead-acceptance record, THEN `Store.accept` with the evidence: the
    HANDOFF's `evidence` object, else the run ledger's newest parsed record for (team, task id),
    else none (recorded unverified: "accepted without evidence"). Which refs close is the
    HANDOFF's `refs` dispositions (hooks/shared/ref_dispositions.py): `done` closes; partial,
    deferred and dropped stay open and are reported to the lead (`report`). A task with ONE ref
    and no dispositions closes it, as before; several refs and no dispositions close nothing.
  - handoff_rejection written                -> journal the record, then `Store.reject` at its revision.

`completion_view` is the read side the views use: for the repo's sessions, the open project
tasks whose linked platform task is completed (`next` never recommends one; `fsck` lists them)
and the ones a completed platform task merely names in its text.

Attribution fails CLOSED, hook errors fail OPEN (amend_1): a frame that is not the lead's, an owner
whose name equals the frame's agent_type (amend_4), a teachback-exempt owner or a signal task writes
nothing. `sync` writes an accept only for a ptask with a lead-acceptance journal record and no store
event, and lists every other linked completion as not attributable. Only structured fields (ids,
shas, file names) are copied, never HANDOFF prose (§9).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parents[2]
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from shared import evidence as ev  # noqa: E402
from shared.task_log import write as W  # noqa: E402
from shared import ref_dispositions as RD  # noqa: E402

PT = W.PT
JOURNAL_TYPE = "project_task_acceptance"
REJECTION_KEY = "handoff_rejection"
LINK_KEY = "project_task"


# ---------------------------------------------------------------------------------------------
# what the update did
# ---------------------------------------------------------------------------------------------

def triggers(tool_input: dict, tool_response) -> List[str]:
    """Which bridge actions this TaskUpdate asks for, in the order they run: link, reject, accept.

    A completion counts only when the platform reports the transition itself
    (tool_response.statusChange.to == "completed"), so a repeated completion is not an acceptance.
    `link` is a project_task or refs in this write's OWN metadata (TaskUpdate or TaskCreate)."""
    found = []
    metadata = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
    if RD.metadata_refs(metadata):
        found.append("link")
    if metadata.get(REJECTION_KEY):
        found.append("reject")
    change = tool_response.get("statusChange") if isinstance(tool_response, dict) else None
    if tool_input.get("status") == "completed" and isinstance(change, dict) \
            and change.get("to") == "completed":
        found.append("accept")
    return found


def _revision(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def rejection_rev(metadata: dict) -> int:
    rejection = metadata.get(REJECTION_KEY)
    return (_revision(rejection.get("revision_number")) if isinstance(rejection, dict) else None) or 1


def acceptance_rev(metadata: dict) -> int:
    """The revision being accepted: the task's revision_number, and never one already rejected."""
    rejection = metadata.get(REJECTION_KEY)
    rejected = _revision(rejection.get("revision_number")) if isinstance(rejection, dict) else None
    return max(_revision(metadata.get("revision_number")) or 1, (rejected or 0) + 1)


# ---------------------------------------------------------------------------------------------
# who may close
# ---------------------------------------------------------------------------------------------

def excluded(task: dict, team: str) -> bool:
    """A teachback-exempt owner (the secretary) or a signal task never closes a project task."""
    from shared.intentional_wait import is_self_complete_exempt
    return bool(is_self_complete_exempt(task, team))


def owner_guard_holds(payload: dict, task: dict) -> bool:
    """False when the frame's agent_type IS the task owner's name: a lead never owns the Task B it
    accepts, and an in-process teammate's agent_type is its own name (amend_4)."""
    agent_type, owner = payload.get("agent_type"), task.get("owner")
    return not (isinstance(agent_type, str) and isinstance(owner, str) and agent_type == owner)


# ---------------------------------------------------------------------------------------------
# evidence (design §5.3)
# ---------------------------------------------------------------------------------------------

def _object_targets(evidence: dict) -> List[str]:
    targets = []
    for key, kind in (("head", "commit"), ("merge", "merge"), ("tag", "tag")):
        if isinstance(evidence.get(key), str) and evidence[key]:
            targets.append(f"{kind}:{evidence[key]}")
    for test in evidence.get("tests") or []:
        log = test.get("log") if isinstance(test, dict) else None
        if isinstance(log, str) and log and W.run_name_ok(log):
            targets.append(f"run:{log}")
    for doc in evidence.get("docs") or []:
        if isinstance(doc, str) and doc:
            targets.append(f"doc:{doc}")
    return targets


def _ledger_targets(team: str, task_id: str) -> List[str]:
    """The newest parsed record of this team's task: its log (by name) and its head."""
    from shared.ledger import parse_ledger_lines
    from shared.plugin_paths import ledger_path
    try:
        rows, _ = parse_ledger_lines(ledger_path(create_dir=False).read_bytes())
    except OSError:
        return []
    for row in reversed(rows):
        if not (row.get("count_parsed") is True
                and row.get("team") == team and str(row.get("task_id")) == str(task_id)):
            continue
        targets = []
        if isinstance(row.get("log_path"), str) and W.run_name_ok(Path(row["log_path"]).name):
            targets.append(f"run:{Path(row['log_path']).name}")
        if isinstance(row.get("git_head"), str) and row["git_head"]:
            targets.append(f"commit:{row['git_head']}")
        return targets
    return []


def _valid(main: Path, targets: Iterable[str]) -> List[str]:
    """Drop any target the store would refuse, so one bad item never costs the acceptance."""
    kept = []
    for target in targets:
        try:
            W.check_text(target)
            W.check_target(main, target, evidence=True)
        except W.TaskError:
            continue
        if target not in kept:
            kept.append(target)
    return kept


def collect_evidence(main: Path, team: str, task_id: str, metadata: dict) -> List[str]:
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    evidence = handoff.get("evidence")
    if isinstance(evidence, dict):
        targets = _valid(main, _object_targets(evidence))
        if targets:
            return targets
    return _valid(main, _ledger_targets(team, task_id))


# ---------------------------------------------------------------------------------------------
# the lead-acceptance journal record (amend_2): written BEFORE the store
# ---------------------------------------------------------------------------------------------

def _journal():
    from shared import session_journal
    return session_journal


def session_dir_of(session_id) -> Optional[Path]:
    if not isinstance(session_id, str) or not session_id:
        return None
    for context in (ev.claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/pact-session-context.json"):
        return context.parent
    return None


def journal(session_id, ptask: str, op: str, rev: int, ref: str, main: Path) -> bool:
    session_dir = session_dir_of(session_id)
    if session_dir is None:
        return False
    sj = _journal()
    event = sj.make_event(JOURNAL_TYPE, ptask=ptask, op=op, rev=rev, t=ref, main=str(main))
    return sj.append_event(event, session_dir=str(session_dir))


# ---------------------------------------------------------------------------------------------
# the hook path
# ---------------------------------------------------------------------------------------------

def linked_refs(state, team: str, task_id: str) -> List[str]:
    """The project tasks BOUND to the platform task: the log's ptask: links to it. Only the lead's
    frame writes one (`start --ptask`, or bind() on the lead's own TaskCreate/TaskUpdate), so a ref
    a teammate puts in metadata is never closed or rejected through this task (F2). The task file
    records no author; the frame of each write does, and the link keeps it."""
    return [task["ref"] for task in PT.tasks_for_ptask(state, team, task_id)]


def unbound_refs(state, refs: List[str], metadata: dict) -> List[str]:
    """Refs the task's metadata names that the log has and the lead never bound: reported, never closed."""
    return [ref for ref in RD.metadata_refs(metadata) if ref in state.tasks and ref not in refs]


def bind(store, ptask: str, written: dict) -> None:
    """Bind the refs the LEAD's own write names (`written` is the tool_input metadata, never the
    merged file): project_task is started (marked active and linked), as before; each refs entry
    is linked only. A ref the log does not hold, or a settled project_task, is skipped."""
    state = store.state()
    linked = written.get(LINK_KEY)
    for ref in RD.metadata_refs(written):
        if ref not in state.tasks:
            continue
        try:
            if ref == linked:
                store.start(ref, ptask=ptask)
            else:
                store.link(ref, "reference", f"ptask:{ptask}")
        except W.Refused:
            pass


def to_close(refs: List[str], metadata: dict) -> Tuple[List[str], List[Tuple[str, Optional[str]]]]:
    """(the refs the acceptance closes, [(ref, its disposition or None)] for the ones it keeps
    open). A lone ref with no dispositions closes (the pre-1.5.0 contract); several refs with
    none close nothing: the HANDOFF did not say which were done."""
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    given = RD.dispositions(handoff)
    if not given and len(refs) == 1:
        return list(refs), []
    close = [ref for ref in refs if RD.verb(given.get(ref)) == "done"]
    return close, [(ref, given.get(ref)) for ref in refs if ref not in close]


last_report: dict = {}       # what the last handle() accepted: {task_id, closed, open, refused, unbound}


def _created_id(tool_response) -> str:
    """The id a TaskCreate just made (tool_response.task.id), as journal_emitter reads it."""
    task = tool_response.get("task") if isinstance(tool_response, dict) else None
    return str(task.get("id") or "") if isinstance(task, dict) else ""


def handle(payload: dict, lead: bool) -> Optional[str]:
    """Apply one PostToolUse(TaskUpdate or TaskCreate). Returns what it did (for tests and logs),
    or None. On an acceptance `last_report` holds what closed, what stayed open, what the store
    refused and what was named but not bound, for the lead's acceptance line (acceptance_line).
    A refusal on one ref is recorded and the next ref still closes; any other store error raises,
    the hook swallows it (fail open), and sync repairs the miss."""
    last_report.clear()
    tool_name = payload.get("tool_name")
    if tool_name not in ("TaskUpdate", "TaskCreate"):
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool_response = payload.get("tool_response")
    if tool_name == "TaskCreate":
        actions = ["link"] if "link" in triggers(tool_input, None) else []
        task_id = _created_id(tool_response)
    else:
        actions = triggers(tool_input, tool_response)
        task_id = str(tool_input.get("taskId") or "")
    if not actions or not task_id:
        return None
    if not lead:
        return "not-the-lead"
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return "no-cwd"
    try:
        # a repo without a task log stops here
        store = W.Store.locate(cwd, by=W.with_team("bridge", payload.get("session_id") or ""))
    except W.NoLog:
        return "no-log"
    team = ev.resolve_team(payload.get("session_id"), task_id)
    task = ev.load_task(ev.task_path(team, task_id)) if team else None
    if task is None:
        return "no-task"
    if not owner_guard_holds(payload, task):
        return "owner-guard"
    if excluded(task, team):
        return "excluded"
    # The task file can persist after this hook runs, so the update's own metadata wins (amend_5).
    written = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
    metadata = dict(task.get("metadata") if isinstance(task.get("metadata"), dict) else {})
    metadata.update(written)
    ptask = f"{team}/{task_id}"
    done = []
    if "link" in actions:
        bind(store, ptask, written)         # the lead's own write only, never the merged file
        done.append("linked")
    refs = linked_refs(store.state(), team, task_id)
    unbound = unbound_refs(store.state(), refs, metadata)
    if not refs and not ("accept" in actions and unbound):
        return "+".join(done) or "unlinked"
    if "reject" in actions:
        rev = rejection_rev(metadata)
        for ref in refs:
            journal(payload.get("session_id"), ptask, "reject", rev, ref, store.main)
            store.reject(ref, ptask, rev)
        done.append("rejected")
    if "accept" in actions:
        rev = acceptance_rev(metadata)
        close, kept = to_close(refs, metadata)
        evidence = collect_evidence(store.main, team, task_id, metadata) if close else []
        closed, refused = [], []
        for ref in close:
            journal(payload.get("session_id"), ptask, "accept", rev, ref, store.main)
            try:
                store.accept(ref, ptask, rev, evidence)
            except W.Refused as err:        # one ref's refusal never costs the others (F15)
                refused.append((ref, str(err)))
                continue
            closed.append(ref)
        last_report.update({"task_id": task_id, "closed": closed, "open": kept,
                            "refused": refused, "unbound": unbound})
        done.append("accepted" if closed else "kept-open")
    return "+".join(done) or None


def acceptance_line(report: dict) -> Optional[str]:
    """The lead's one line after an acceptance: what closed, what stays open and why, what the
    store refused, what the task named without the lead binding it, and the verbs that settle
    the open ones. None when the acceptance carried no project task."""
    closed, kept = report.get("closed") or [], report.get("open") or []
    refused, unbound = report.get("refused") or [], report.get("unbound") or []
    if not (closed or kept or refused or unbound):
        return None
    parts = []
    if closed:
        parts.append("closed " + ", ".join(closed))
    if kept:
        parts.append("still open: " + ", ".join(
            f"{ref} ({disposition or 'no disposition in handoff.refs'})" for ref, disposition in kept))
    if refused:
        parts.append("refused: " + ", ".join(f"{ref} ({reason})" for ref, reason in refused))
    if unbound:
        parts.append("named in metadata but not bound by the lead, left as is: " + ", ".join(unbound))
    line = f"Project tasks on #{report.get('task_id')}: " + "; ".join(parts) + "."
    if kept or refused or unbound:
        line += (" Settle each: `repact-task done <ref> --evidence commit:<sha> [--remaining \"<title>\"]`, "
                 "`reopen`, or `drop`; `repact-task next` lists them until then.")
    return line


# ---------------------------------------------------------------------------------------------
# sync (design §5.4)
# ---------------------------------------------------------------------------------------------

def _slugs(main: Path) -> List[str]:
    """The session-dir slugs of this repo: its main checkout and every linked worktree (a session
    started in a worktree gets that worktree's folder name)."""
    names = [main.name]
    try:
        out = subprocess.run(["git", "-C", str(main), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            name = Path(line[len("worktree "):]).name
            if name not in names:
                names.append(name)
    return names


def _session_dirs(main: Path) -> List[Path]:
    root = ev.claude_config_dir() / "pact-sessions"
    dirs = []
    for slug in _slugs(main):
        base = root / slug
        if base.is_dir():
            dirs.extend(sorted(path for path in base.iterdir() if path.is_dir()))
    return dirs


def _team_of(session_dir: Path) -> Optional[str]:
    try:
        team = json.loads((session_dir / "pact-session-context.json").read_text(encoding="utf-8")).get("team_name")
    except (OSError, ValueError, AttributeError):
        return None
    return team if isinstance(team, str) and team else None


def _lead_records(session_dirs: List[Path], main: Path) -> List[dict]:
    sj = _journal()
    records, seen = [], set()
    for session_dir in session_dirs:
        for event in sj.read_events_from(str(session_dir), JOURNAL_TYPE):
            if event.get("main") and Path(event["main"]).resolve() != main.resolve():
                continue
            key = (event.get("op"), event.get("ptask"), event.get("rev"))
            if key not in seen:
                seen.add(key)
                records.append(event)
    return records


def _platform_tasks(session_dirs: List[Path]) -> Dict[str, dict]:
    """ptask -> task, from each team's task dir, then the D6 archive for files already reaped."""
    tasks: Dict[str, dict] = {}
    for session_dir in session_dirs:
        team = _team_of(session_dir)
        if not team:
            continue
        sources = [ev.task_store().team_dir(team), session_dir / "tasks-archive"]
        for source in (s for s in sources if s is not None):
            for path in sorted(source.glob("*.json")) if source.is_dir() else []:
                task = ev.load_task(path)
                if task is not None and str(task.get("id") or path.stem):
                    tasks.setdefault(f"{team}/{task.get('id') or path.stem}", dict(task, _team=team))
    return tasks


def _recorded(state, op: str, ptask: str, rev: int) -> bool:
    rows = "accepts" if op == "accept" else "rejects"
    return any(row["ptask"] == ptask and row["rev"] == rev
               for task in state.tasks.values() for row in task[rows])


def sync(start, check: bool = False) -> dict:
    """Reconcile the log with lead-acceptance records and rejections. `check` writes nothing.

    Returns {written|missed, not_attributable, excluded, refused}: lists of short labels."""
    store = W.Store.locate(start, by=W.with_team("bridge"))
    main = store.main
    dirs = _session_dirs(main)
    platform = _platform_tasks(dirs)
    state = store.state()
    report = {"written": [], "missed": [], "not_attributable": [], "excluded": [], "refused": []}
    attributed = set()

    def apply(op: str, ptask: str, rev: int, ref: str, task: Optional[dict]) -> None:
        label = f"{op} {ptask} rev {rev}"
        if _recorded(store.state(), op, ptask, rev):
            return
        if check:
            report["missed"].append(label)
            return
        try:
            if op == "reject":
                wrote = store.reject(ref, ptask, rev)
            else:
                team, task_id = ptask.split("/", 1)
                metadata = (task or {}).get("metadata") or {}
                wrote = store.accept(ref, ptask, rev, collect_evidence(main, team, task_id, metadata))
        except W.TaskError as err:
            report["refused"].append(f"{label}: {err}")
            return
        if wrote:
            report["written"].append(label)

    for record in _lead_records(dirs, main):
        ptask, op, rev, ref = record.get("ptask"), record.get("op"), record.get("rev"), record.get("t")
        if op not in ("accept", "reject") or not isinstance(ref, str) or ref not in state.tasks:
            continue
        task = platform.get(ptask)
        if op == "accept":
            attributed.add(ptask)
        if task is not None and excluded(task, task["_team"]):
            report["excluded"].append(ptask)
            continue
        apply(op, ptask, rev, ref, task)

    for ptask, task in sorted(platform.items()):
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        team, task_id = ptask.split("/", 1)
        refs = linked_refs(state, team, task_id)
        if not refs:
            continue
        if metadata.get(REJECTION_KEY):                     # lead-written by protocol (amend_1)
            for ref in refs:
                apply("reject", ptask, rejection_rev(metadata), ref, task)
        if task.get("status") != "completed":
            continue
        close, _kept = to_close(refs, metadata)
        unrecorded = [ref for ref in close
                      if not any(row["ptask"] == ptask for row in state.tasks[ref]["accepts"])]
        if unrecorded and ptask not in attributed:
            if excluded(task, task["_team"]):
                if ptask not in report["excluded"]:
                    report["excluded"].append(ptask)
            elif ptask not in report["not_attributable"]:
                report["not_attributable"].append(ptask)
    report["open_after_completion"] = [f"{ref} <- {ptask}" + (f" ({disposition})" if disposition else "")
                                       for ref, rows in sorted(completion_view(start, platform=platform,
                                                                               state=store.state())["linked"].items())
                                       for ptask, disposition in rows]
    return report


# ---------------------------------------------------------------------------------------------
# the read side of completion: what the views say about completed platform tasks
# ---------------------------------------------------------------------------------------------

def completion_view(start, platform: Optional[Dict[str, dict]] = None, state=None) -> dict:
    """{"linked": {ref: [(ptask, disposition)]}, "mentioned": {ref: [ptask]}} for the open
    project tasks of this repo. `linked`: the platform task that carries the ref (a ptask: link,
    project_task or refs) is completed and the ref is still open, so the work is done or was
    dropped without the log hearing it. `mentioned`: a completed platform task names the ref
    in its subject or description without carrying it (the plan's refs, pasted): a hint, never a
    status. Platform task files that cannot be read are skipped (a missing file is no claim)."""
    store = W.Store.locate(start, by=W.with_team("bridge"))
    if platform is None:
        platform = _platform_tasks(_session_dirs(store.main))
    if state is None:
        state = store.state()
    linked: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    mentioned: Dict[str, List[str]] = {}
    for ptask, task in sorted(platform.items()):
        if task.get("status") != "completed":
            continue
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        team, task_id = ptask.split("/", 1)
        refs = linked_refs(state, team, task_id)
        given = RD.dispositions(metadata.get("handoff"))
        for ref in refs:
            if state.tasks[ref]["status"] not in PT.SETTLED:
                linked.setdefault(ref, []).append((ptask, given.get(ref)))
        for ref in dict.fromkeys(RD.mentioned_refs(task) + unbound_refs(state, refs, metadata)):
            if ref in state.tasks and ref not in refs and state.tasks[ref]["status"] not in PT.SETTLED:
                mentioned.setdefault(ref, []).append(ptask)
    return {"linked": linked, "mentioned": mentioned}


def sync_quietly(start) -> None:
    """sync for a caller that must never fail (the SessionEnd child, the read commands)."""
    try:
        sync(start)
    except Exception:  # noqa: BLE001 - reconciliation is best-effort; `sync --check` shows misses
        pass
