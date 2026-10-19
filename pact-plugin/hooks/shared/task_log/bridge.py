"""The acceptance bridge: platform task acceptance -> the project task log (D10 P2; design §5).

Location: pact-plugin/hooks/shared/task_log/bridge.py. Called by shared/ops/sides.py for
the lead's own `repact dispatch`, `accept` and `reject` (`apply`; since 2.1.0, when the TaskUpdate
hook that called `handle` was deleted), by `repact-task sync` and every repact-task read command
(`sync`), and by hooks/session_ledger.py's detached SessionEnd child (`sync_quietly`).

What it does, only for the LEAD (only the lead can run those commands):
  - project_task or refs in the lead's own dispatch metadata -> bind: link
    ptask:<team>/<id> (project_task is also marked active). Only these links, and `start --ptask`,
    bind a ref; a ref a teammate writes into metadata is reported at acceptance, never closed;
  - the task accepted -> for EVERY project task bound to the
    platform task: journal a lead-acceptance record, THEN `Store.accept` with the evidence: the
    HANDOFF's `evidence` object, else the run ledger's newest parsed record for (team, task id),
    else none (recorded unverified: "accepted without evidence"; with unattended mode on, none
    leaves the row open instead and the autopilot run log keeps why, `hold`, never for a row that
    is already closed; an attended metadata.evidence_waiver closes it with the row's `waiver`
    field). Which refs close is the
    HANDOFF's `refs` dispositions (hooks/shared/ref_dispositions.py): `done` closes; partial,
    deferred and dropped stay open and are reported to the lead (`report`). A task with ONE ref
    and no dispositions closes it, as before; several refs and no dispositions close nothing.
    A task of a feature with more than one task (acceptance.statuses.integrated "waiting") closes
    nothing at its acceptance: `integrate`, called by `repact accept --feature` once the merged
    tree passes, closes its rows with the merged head commit as evidence.
  - handoff_rejection written                -> journal the record, then `Store.reject` at its revision.
  - metadata.parked written (`park`)          -> every bound open row is parked for a human.

`completion_view` is the read side the views use: for the repo's sessions, the open project
tasks whose linked platform task is completed (`next` never recommends one; `fsck` lists them)
and the ones a completed platform task merely names in its text.

Attribution fails CLOSED, errors fail OPEN. `sync` writes an accept only for a ptask with a lead-acceptance journal record and no store
event, and lists every other linked completion as not attributable. Only structured fields (ids,
shas, file names) are copied into the task log, never HANDOFF prose. The one exception is the
ledger: every lead acceptance, bound or not, appends the HANDOFF's known
limits, decisions and remaining items to `.repacted/ledger.jsonl` through ledger.record, and
`backfill` does the same for earlier acceptances (`repact-task ledger --backfill`).
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

from shared import task_files as ev  # noqa: E402
from shared.plugin_paths import claude_config_dir, task_store  # noqa: E402
from shared.task_log import write as W  # noqa: E402
from shared import ref_dispositions as RD  # noqa: E402

PT = W.PT
JOURNAL_TYPE = "project_task_acceptance"
REJECTION_KEY = "handoff_rejection"
LINK_KEY = "project_task"


# ---------------------------------------------------------------------------------------------
# what the update did
# ---------------------------------------------------------------------------------------------

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


def _accepted_targets(metadata: dict) -> List[str]:
    """The log of each run the acceptance record names (metadata.acceptance.runs: the passing runs
    on the accepted tree, written by repact accept from the evidence check): a `claude -p`
    teammate's runs carry no team or task, so an honestly relayed task has no ledger row of its
    own."""
    stems = (metadata.get("acceptance") or {}).get("runs") if isinstance(metadata.get("acceptance"), dict) else None
    if not isinstance(stems, list) or not stems:
        return []
    from shared import acceptance as AC
    runs = AC.ledger_runs() or []
    targets = []
    for stem in reversed(stems):
        for row in AC.runs_for(str(stem), runs):
            name = Path(row["log_path"]).name if isinstance(row.get("log_path"), str) else ""
            if W.run_name_ok(name) and f"run:{name}" not in targets:
                targets.append(f"run:{name}")
    return targets[:3]


def collect_evidence(main: Path, team: str, task_id: str, metadata: dict,
                     task: Optional[dict] = None) -> List[str]:
    """The HANDOFF's evidence object, else the run ledger's row of this task, else (given the task)
    the runs its acceptance record names; [] when none verifies."""
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    evidence = handoff.get("evidence")
    if isinstance(evidence, dict):
        targets = _valid(main, _object_targets(evidence))
        if targets:
            return targets
    targets = _valid(main, _ledger_targets(team, task_id))
    if targets or not isinstance(task, dict):
        return targets
    try:
        return _valid(main, _accepted_targets(metadata))
    except Exception:  # noqa: BLE001 - a ledger read that fails finds no evidence (the row stays open)
        return []


# ---------------------------------------------------------------------------------------------
# the lead-acceptance journal record (amend_2): written BEFORE the store
# ---------------------------------------------------------------------------------------------

def _journal():
    from shared import session_journal
    return session_journal


def session_dir_of(session_id) -> Optional[Path]:
    if not isinstance(session_id, str) or not session_id:
        return None
    for context in (claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/pact-session-context.json"):
        return context.parent
    return None


HOLD_WHY = "no evidence (no HANDOFF evidence object, no recorded run) in an unattended run: left open"


def unattended(session_id) -> bool:
    """Unattended mode for the session as the evidence controls read it (shared/unattended.controls_on:
    REPACT_UNATTENDED or the flag says on, and a bad flag reads ON)."""
    from shared.unattended import controls_on
    session_dir = session_dir_of(session_id)
    return bool(controls_on(str(session_dir) if session_dir else "").on)


def hold(session_id, ref: str, ptask: str) -> bool:
    """Keep why `ref` stayed open in the session's current autopilot run, so `record done`
    leaves it open and the report names the reason (shared/autopilot_run.evidence_holds)."""
    from shared import autopilot_run as R
    session_dir = session_dir_of(session_id)
    run = R.current_run(R.read_rows(str(session_dir))) if session_dir else None
    if run is None:
        return False
    return R.append(str(session_dir), {"kind": "evidence_hold", "run": run["start"].get("id"), "ref": ref,
                                       "ptask": ptask, "why": HOLD_WHY})


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
            drop_dangling(store, state.tasks[ref], ptask)
        except W.Refused:
            pass


def drop_dangling(store, task: dict, ptask: str) -> List[str]:
    """Remove the row's links to the same task id as `ptask` under a team with no task dir (a
    lead under `claude -p` linked session-<8>/<id> while the platform keeps the task under the full session
    id), so one row never carries a live link and a dead twin. Returns the targets removed."""
    bound_id = ptask.rpartition("/")[2]
    removed = []
    for link in list(task.get("links") or []):
        target = link.get("target") if isinstance(link, dict) else None
        if link.get("kind") != "reference" or not isinstance(target, str) or not target.startswith("ptask:"):
            continue
        team, _, task_id = target[len("ptask:"):].partition("/")
        if task_id != bound_id or target == f"ptask:{ptask}":
            continue
        path = ev.task_path(team, task_id) if ev.PATH_SEGMENT.fullmatch(team or "-") and task_id else None
        if path is not None and not path.is_file() and not path.parent.is_dir():
            store.link(task["ref"], "reference", target, remove=True)
            removed.append(target)
    return removed


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


last_report: dict = {}       # what the last apply() accepted: {task_id, closed, open, refused, unbound}


def apply(store, team: str, task_id: str, task: dict, written: dict, actions: List[str],
          session_id) -> Optional[str]:
    """The bridge actions ("link", "reject", "accept", in that order) for one platform task,
    after the caller has settled who may act: shared/ops/accept|reject|dispatch for
    `repact accept|reject|dispatch` (the lead's own command; it writes the task file itself, so
    no TaskUpdate fires). `written` is the metadata the act wrote, which wins over the file's.
    Returns what it did (for tests and logs); on an acceptance `last_report` holds what closed,
    what stayed open, what the store refused and what was named but not bound. A refusal on one
    ref is recorded and the next ref still closes; any other store error raises, the op's side
    runner swallows it (fail open), and sync repairs the miss. A task owned by the secretary or
    a signal task never links or closes a project task ("excluded")."""
    last_report.clear()
    if excluded(task, team):
        return "excluded"
    metadata = dict(task.get("metadata") if isinstance(task.get("metadata"), dict) else {})
    metadata.update(written)
    ptask = f"{team}/{task_id}"
    done = []
    if "link" in actions:
        bind(store, ptask, written)         # the lead's own write only, never the merged file
        done.append("linked")
    if "accept" in actions:                 # bound or not, an accepted HANDOFF feeds the ledger
        last_report.update({"task_id": task_id,
                            "ledger": record_ledger(store, ptask, acceptance_rev(metadata), metadata)})
    refs = linked_refs(store.state(), team, task_id)
    unbound = unbound_refs(store.state(), refs, metadata)
    if not refs and not ("accept" in actions and unbound):
        return "+".join(done) or "unlinked"
    if "reject" in actions:
        rev = rejection_rev(metadata)
        for ref in refs:
            journal(session_id, ptask, "reject", rev, ref, store.main)
            store.reject(ref, ptask, rev)
        done.append("rejected")
    if "accept" in actions:
        close, kept = to_close(refs, metadata)
        waiting = waits_for_feature(metadata)
        if waiting:                         # a feature's rows close only on integrated
            kept = [(ref, waiting) for ref in close] + kept
            close = []
        evidence = collect_evidence(store.main, team, task_id, metadata, task=task) if close else []
        closed, refused = _close(store, ptask, close, kept, acceptance_rev(metadata), evidence,
                                 waiver_of(metadata), session_id)
        last_report.update({"task_id": task_id, "closed": closed, "open": kept,
                            "refused": refused, "unbound": unbound})
        done.append("accepted" if closed else "kept-open")
    return "+".join(done) or None


def _close(store, ptask: str, close: List[str], kept: list, rev: int, evidence: List[str],
           waiver: Optional[dict], session_id) -> Tuple[List[str], list]:
    """Journal and record the lead's acceptance of each ref in `close`: (closed, refused). With no
    evidence and no waiver in an unattended session every ref is held open instead (appended to
    `kept`), with no journal record, so sync never closes it later."""
    held = HOLD_WHY if close and not evidence and not waiver and unattended(session_id) else None
    closed, refused = [], []
    settled = store.state().tasks
    for ref in close:
        if held:
            kept.append((ref, held))
            if settled.get(ref, {}).get("status") not in W.PT.SETTLED:
                hold(session_id, ref, ptask)    # never a hold after the row's close
            continue
        journal(session_id, ptask, "accept", rev, ref, store.main)
        try:
            store.accept(ref, ptask, rev, evidence, waiver=waiver)
        except W.Refused as err:            # one ref's refusal never costs the others
            refused.append((ref, str(err)))
            continue
        closed.append(ref)
    return closed, refused


def waits_for_feature(metadata: dict) -> Optional[str]:
    """Why the task's rows stay open after its acceptance: its feature has more than one task, so
    they close when `repact accept --feature` passes on the merged tree (acceptance.statuses
    .integrated is "waiting"). None otherwise."""
    record = metadata.get("acceptance") if isinstance(metadata.get("acceptance"), dict) else {}
    integrated = (record.get("statuses") or {}).get("integrated") if isinstance(record.get("statuses"), dict) else None
    if isinstance(integrated, dict) and integrated.get("state") == "waiting":
        return str(integrated.get("line") or "waits for the feature's integration")[:300]
    return None


def integrate(store, team: str, task_id: str, task: dict, evidence: List[str], session_id) -> dict:
    """Close the rows bound to one task of a feature that `repact accept --feature` has just
    integrated, with the merged tree's evidence (its head commit and the runs on
    it) ahead of the task's own. Which refs close is the HANDOFF's dispositions, as at accept.
    Returns the report acceptance_line reads."""
    if excluded(task, team):
        return {}
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    ptask = f"{team}/{task_id}"
    close, kept = to_close(linked_refs(store.state(), team, task_id), metadata)
    found = _valid(store.main, evidence)
    found += [item for item in collect_evidence(store.main, team, task_id, metadata, task=task) if item not in found]
    closed, refused = _close(store, ptask, close, kept, acceptance_rev(metadata), found,
                             waiver_of(metadata), session_id)
    return {"task_id": task_id, "closed": closed, "open": kept, "refused": refused, "unbound": []}


def log_report(store, team: str, task_id: str) -> dict:
    """What the task log holds now for the rows bound to the task, in the shape acceptance_line
    reads: the done ones as closed, the rest open with their status (the receipt's line)."""
    state = store.state()
    refs = linked_refs(state, team, task_id)
    closed = [ref for ref in refs if state.tasks.get(ref, {}).get("status") == "done"]
    kept = [(ref, f"status {state.tasks.get(ref, {}).get('status')}") for ref in refs if ref not in closed]
    return {"task_id": task_id, "closed": closed, "open": kept}


def waiver_of(metadata: dict) -> Optional[dict]:
    """The row's `waiver` from an attended metadata.evidence_waiver ({why, by, at}), else None."""
    given = metadata.get("evidence_waiver")
    if not isinstance(given, dict) or given.get("attended") is not True:
        return None
    waiver = {key: given.get(key) for key in ("why", "by", "at")}
    return waiver if all(isinstance(value, str) and value.strip() for value in waiver.values()) else None


def park(store, team: str, task_id: str, why: str) -> List[str]:
    """Park every open task-log row bound to the platform task for a human (an
    unattended `repact accept --waive`, or the rejection limit). Returns the refs parked."""
    parked = []
    for ref in linked_refs(store.state(), team, task_id):
        try:
            if store.park(ref, why):
                parked.append(ref)
        except W.Refused:
            continue
    return parked


def record_ledger(store, ptask: str, rev: int, metadata: dict) -> dict:
    """Append the accepted HANDOFF's known limits, decisions and remaining items to the project
    ledger (hooks/shared/task_log/ledger.py). A ledger failure never costs the acceptance: it is
    swallowed here and `repact-task ledger --backfill` repairs the miss."""
    try:
        from shared.task_log import ledger
        return ledger.record(store, ptask, rev, metadata.get("handoff"))
    except (W.TaskError, OSError, ValueError):
        return {}


def _ledger_part(ledger: dict) -> Optional[str]:
    limits, decisions = ledger.get("limits") or [], ledger.get("decisions") or []
    proposed = ledger.get("proposed") or []
    parts = [f"{len(limits)} known limit(s)"] if limits else []
    parts += ["decisions " + ", ".join(decisions)] if decisions else []
    parts += ["proposed for the user from its remaining items: " + ", ".join(proposed)] if proposed else []
    return "ledger: " + ", ".join(parts) if parts else None


def acceptance_line(report: dict) -> Optional[str]:
    """The lead's one line after an acceptance: what closed, what stays open and why, what the
    store refused, what the task named without the lead binding it, and the verbs that settle
    the open ones. None when the acceptance carried no project task."""
    closed, kept = report.get("closed") or [], report.get("open") or []
    refused, unbound = report.get("refused") or [], report.get("unbound") or []
    ledger = _ledger_part(report.get("ledger") or {})
    if not (closed or kept or refused or unbound or ledger):
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
    if ledger:
        parts.append(ledger)
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
    root = claude_config_dir() / "pact-sessions"
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
        sources = [task_store().team_dir(team), session_dir / "tasks-archive"]
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
                wrote = store.accept(ref, ptask, rev, collect_evidence(main, team, task_id, metadata, task=task))
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


def backfill(start, team: Optional[str] = None) -> dict:
    """`repact-task ledger --backfill`: feed the ledger from every completed platform task of this
    repo's teams (or of `team` alone), as the bridge would have at each acceptance. Idempotent:
    the ledger skips an item it already holds. Teams come from the repo's session dirs, then
    each team's task dir and its archive (_platform_tasks). A completed task is the lead's
    acceptance, since a teammate cannot complete its own task. NoLog when the repo has none."""
    from shared.task_log import ledger
    store = W.Store.locate(start, by=W.with_team("ledger"))
    if team is not None:
        directory = task_store().team_dir(team)
        tasks = {f"{team}/{path.stem}": dict(task, _team=team)
                 for path in (sorted(directory.glob("*.json")) if directory and directory.is_dir() else [])
                 for task in [ev.load_task(path)] if task is not None}
    else:
        tasks = _platform_tasks(_session_dirs(store.main))
    totals = {"tasks": 0, "limits": 0, "decisions": 0, "proposed": 0}

    def order(ptask: str):
        tail = ptask.rsplit("/", 1)[-1]
        return (ptask.rsplit("/", 1)[0], int(tail) if tail.isdigit() else 0, ptask)

    for ptask in sorted(tasks, key=order):
        task = tasks[ptask]
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        if task.get("status") != "completed" or not isinstance(metadata.get("handoff"), dict) \
                or excluded(task, task["_team"]):
            continue
        report = ledger.record(store, ptask, acceptance_rev(metadata), metadata["handoff"])
        totals["tasks"] += 1
        for key in ("limits", "decisions", "proposed"):
            totals[key] += len(report[key])
    return totals


def sync_quietly(start) -> None:
    """sync for a caller that must never fail (the SessionEnd child, the read commands)."""
    try:
        sync(start)
    except Exception:  # noqa: BLE001 - reconciliation is best-effort; `sync --check` shows misses
        pass
