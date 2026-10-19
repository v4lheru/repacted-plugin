"""The close gate: in an autopilot or unattended session a task-log row closes only on an accepted HANDOFF.

Location: pact-plugin/hooks/shared/task_log/close_gate.py. Used by bin/repact-task (`done`)
and bin/repact-autopilot (`record <ref> done`), the two commands the lead closes a row with. In an early
autopilot run the lead closed both rows with `repact-task done --evidence commit:<sha>` while the
linked work tasks were still pending, one with no HANDOFF, so the no-HANDOFF refusal never came
into play.

The rule is an allowlist, judged from the row's own ptask: links, never from the
current autopilot run, so a second `repact-autopilot start` changes nothing:
  - it applies when unattended mode is on as the evidence controls read it (unattended.controls_on:
    REPACT_UNATTENDED or the flag, a bad flag reads ON), when the session has an autopilot run, or
    when the row was begun by one (the `autopilot` tag `repact-autopilot begin` writes, read from the
    log's ops so a later tag change does not clear it). With no
    session directory known only the environment and the tag can say so, and then nothing passes
    without a waiver (fail closed);
  - the row needs at least one linked platform task of THIS session's team (the context's team name
    or the session id the platform keys it by), and every such task must be completed, be made by
    `repact dispatch` (metadata.dispatch), carry a metadata.acceptance whose op_id has an accept
    row and a done row in this session's ops.jsonl (a task file or a TaskUpdate
    cannot stand for an acceptance; no session dir known: refused), carry a
    stored metadata.handoff and have evidence the bridge accepts (bridge.collect_evidence: the
    HANDOFF's evidence object, the run ledger's row of the task, or a run its acceptance names);
  - a task of a feature with more than one task (its acceptance says the integration is
    "waiting") passes only once `repact accept --feature` has recorded the feature in
    .repacted/features.jsonl (rows close only on integrated);
  - a link to another team's task is refused; a link that points to no task file is ignored (the
    bridge drops such links when it binds);
  - a lead waiver, with a reason of at least one word, passes only when attended: the row closes
    with a `waiver` field and the autopilot log keeps it. Unattended (controls_on) a waiver never
    closes: the caller parks the row for a human (W.Store.park, repact decide lists it) and the log
    keeps a `park` row. The refusal text offers the waiver
    only as that park when unattended.
  - with no session directory known, a waiver never closes either (the session's
    sticky unattended state cannot be read, and the environment alone can be changed by one
    prefix): it parks, unless the caller proved the owner at their own terminal (`owner`,
    shared/owner_terminal.owner_confirms; bin/repact-task asks).
Outside those modes nothing changes.

Example:
    decision = check(store.main, task, session_dir_from_env(), waiver=None)
    if decision.refusal: raise W.Refused(decision.refusal)
    if decision.park: store.park(ref, decision.park)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from shared import task_files as ev
from shared.task_log import bridge as B

AUTOPILOT_TAG = "autopilot"
WAIVER_MAX = 300
_WORD = re.compile(r"[^\W_]{2,}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
WAY_THROUGH = ("complete each linked platform task with the teammate's HANDOFF stored (a relayed HANDOFF: "
               "store it verbatim as metadata.handoff, then complete the task), or pass --waive \"<reason>\"")
WAY_THROUGH_UNATTENDED = ("accept each linked platform task with `repact accept` (a failed check goes back to the "
                          "coder as a rejection), or park the row for a human with --waive \"<reason>\": "
                          "unattended, a waiver never closes it")


class Decision(NamedTuple):
    """What the gate says about one close: a refusal, or a pass with an optional note and the
    attended waiver to keep on the done row, or the reason to park the row (unattended waiver)."""
    refusal: Optional[str] = None
    note: Optional[str] = None
    waiver: Optional[dict] = None
    park: Optional[str] = None


def session_dir_from_env() -> str:
    """The live session's directory (CLAUDE_CODE_SESSION_ID and CLAUDE_PROJECT_DIR, else the
    session id found under <config>/pact-sessions/ as the ops find it), or ""."""
    from shared.ops import records
    from shared.pact_context import reconstruct_session_dir
    session = next((os.environ[name] for name in SESSION_ENV if os.environ.get(name)), "")
    folder = reconstruct_session_dir(os.environ.get("CLAUDE_PROJECT_DIR", ""), session)
    if folder and os.path.isdir(folder):
        return folder
    found = records.host_session_dir()
    return str(found) if found is not None else ""


def _context(session_dir: str) -> dict:
    try:
        data = json.loads((Path(session_dir) / "pact-session-context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def session_teams(session_dir: str) -> List[str]:
    """The names this session's team can have on disk: the context's team name and the session id."""
    context = _context(session_dir) if session_dir else {}
    names = [context.get("team_name"), context.get("session_id")]
    return [name for name in dict.fromkeys(names)
            if isinstance(name, str) and ev.PATH_SEGMENT.fullmatch(name)]


def applies(session_dir: str, task: dict, environ=None, main: Optional[Path] = None) -> Optional[str]:
    """Why the gate applies to closing `task`, or None when it does not. With `main`, a row the log
    shows was ever tagged autopilot counts as begun by autopilot."""
    from shared import autopilot_run as R
    from shared.unattended import controls_on
    if controls_on(session_dir, environ).on:
        return "unattended mode is on"
    if session_dir and any(row.get("kind") == "start" for row in R.read_rows(session_dir)):
        return "this session runs /repacted:autopilot"
    if AUTOPILOT_TAG in (task.get("tags") or []) \
            or (main is not None and began_by_autopilot(main, task.get("ref"))):
        return "an autopilot run began this task"
    return None


def began_by_autopilot(main: Path, ref) -> bool:
    """Whether the task log ever gave `ref` the autopilot tag (its create op, or a tags set such as
    `repact-autopilot begin` writes). Read from the log, not the row's current tags: `repact-task set
    <ref> --tag chore` replaces the tags and would take the row out of the gate."""
    from shared.task_log import write as W
    try:
        events = W.PT.read_log(W.PT.log_path(Path(main))).events
    except (W.PT.LogMissing, W.PT.LogUnreadable, OSError):
        return False                                    # no log to read: the close cannot write either
    for event in events:
        if not isinstance(event, dict) or event.get("t") != ref:
            continue
        tags = event.get("tags") if event.get("op") == "create" else (
            event.get("value") if event.get("op") == "set" and event.get("field") == "tags" else None)
        if isinstance(tags, list) and AUTOPILOT_TAG in tags:
            return True
    return False


def valid_waiver(waiver) -> Optional[str]:
    """The waiver reason when it names one (a word of two letters or digits), else None."""
    if not isinstance(waiver, str):
        return None
    text = " ".join(_CONTROL.sub(" ", waiver).split())[:WAIVER_MAX]
    return text if _WORD.search(text) else None


def linked_ptasks(task: dict) -> List[Tuple[str, str]]:
    found = []
    for link in task.get("links") or []:
        target = link.get("target") if isinstance(link, dict) else None
        if link.get("kind") == "reference" and isinstance(target, str) and target.startswith("ptask:"):
            team, _, task_id = target[len("ptask:"):].partition("/")
            if ev.PATH_SEGMENT.fullmatch(team) and ev.PATH_SEGMENT.fullmatch(task_id):
                found.append((team, task_id))
    return found


def _task_file(team: str, task_id: str) -> Optional[Path]:
    path = ev.task_path(team, task_id)
    return path if path is not None and path.is_file() else None


def problems(main: Path, task: dict, session_dir: str) -> List[str]:
    """What keeps the row from closing; [] when every linked task of this session passes."""
    teams = session_teams(session_dir)
    found, live = [], 0
    for team, task_id in linked_ptasks(task):
        path, ptask = _task_file(team, task_id), f"ptask:{team}/{task_id}"
        if path is None:
            continue                                    # points to no task: the bridge drops it
        if team not in teams:
            found.append(f"{ptask} is not a task of this session's team"
                         + ("" if teams else " (no session directory is known)"))
            continue
        live += 1
        platform = ev.load_task(path)
        if platform is None:
            found.append(f"{ptask} cannot be read")
            continue
        metadata = platform.get("metadata") if isinstance(platform.get("metadata"), dict) else {}
        if platform.get("status") != "completed":
            found.append(f"{ptask} is {platform.get('status')!s:.20}, not completed")
        elif not isinstance(metadata.get("dispatch"), dict):
            found.append(f"{ptask} was not made by `repact dispatch`, so no acceptance can close a row for it")
        elif (unbound := _unbound_acceptance(session_dir, task_id, metadata)):
            found.append(unbound)
        elif not isinstance(metadata.get("handoff"), dict) or not metadata["handoff"]:
            found.append(f"{ptask} was completed with no stored HANDOFF")
        elif not B.collect_evidence(main, team, task_id, metadata, task=platform):
            found.append(f"{ptask} has a HANDOFF but no evidence (no evidence object, no recorded run)")
        elif B.waits_for_feature(metadata) and not _integrated(main, metadata):
            feature = (metadata.get("dispatch") or {}).get("feature")
            found.append(f"{ptask} is one task of the feature {feature}, which is not integrated yet: "
                         f"run `repact accept --feature {feature}` on the merged tree")
    if not live and not found:
        found.append("no platform task of this session is linked (repact-task start <ref> --ptask <team>/<id>)")
    return found


ACCEPT_OPS = ("accept", "accept_waive")


def _unbound_acceptance(session_dir: str, task_id: str, metadata: dict) -> Optional[str]:
    """Why the task's acceptance is not the ops' own, or None when its op_id has an accept intent
    row for this task and a done row in the session's ops.jsonl."""
    from shared.ops import records
    acceptance = metadata.get("acceptance")
    op_id = acceptance.get("op_id") if isinstance(acceptance, dict) else None
    if not isinstance(op_id, str) or not op_id:
        return f"ptask {task_id} was completed without `repact accept` (no acceptance record)"
    if not session_dir:
        return (f"ptask {task_id}: no session directory is known, so its acceptance cannot be read "
                "from the ops log; run the close inside the session that ran `repact accept`")
    log = records.read_log(session_dir)
    began = any(row.get("op_id") == op_id and row.get("op") in ACCEPT_OPS and row.get("task") == task_id
                for row in log)
    if not (began and any(row.get("op_id") == op_id and row.get("done") is True for row in log)):
        return (f"ptask {task_id}: its acceptance names {op_id[:24]}, which the ops log of this session "
                "does not show finished; run `repact accept` again")
    return None


def _integrated(main: Path, metadata: dict) -> bool:
    """Whether the feature of the task (dispatch.feature) has a record in features.jsonl."""
    from shared.task_log import features
    feature = (metadata.get("dispatch") or {}).get("feature") if isinstance(metadata.get("dispatch"), dict) else None
    return isinstance(feature, str) and features.integrated(main, feature) is not None


def check(main: Path, task: dict, session_dir: str, waiver=None, environ=None,
          owner: bool = False) -> Decision:
    """The gate's Decision for closing `task`: a refusal names the rule and the way through; an
    attended waiver passes with a note and the waiver record; an unattended one parks, and so
    does one with no session dir known unless `owner` (the owner proved at their terminal). An
    empty Decision when the gate does not apply or the row passes."""
    from shared.unattended import controls_on
    why = applies(session_dir, task, environ, main)
    if why is None:
        return Decision()
    found = problems(main, task, session_dir)
    if not found:
        return Decision()
    unattended = controls_on(session_dir, environ).on or (not session_dir and not owner)
    reason = valid_waiver(waiver)
    if reason and unattended:
        park = f"close of {task.get('ref')} without an accepted HANDOFF asked: {reason}. Problems: " \
               + "; ".join(p[:200] for p in found[:3])
        log_waiver(session_dir, task.get("ref"), reason, found, "park")
        return Decision(park=park)
    if reason:
        log_waiver(session_dir, task.get("ref"), reason, found)
        return Decision(note=f"close of {task.get('ref')} waived by the lead: {reason} (logged)",
                        waiver={"why": reason, "by": "lead", "at": _now()})
    bad = " (a waiver needs a reason of at least one word)" if waiver is not None else ""
    way = WAY_THROUGH_UNATTENDED if unattended else WAY_THROUGH
    return Decision(refusal=f"{task.get('ref')} stays open because {why}: {'; '.join(found[:3])}. "
                            f"To close it, {way}{bad}. [rule: task_log.close_unaccepted]")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_waiver(session_dir: str, ref, reason: str, found: List[str], kind: str = "close_waiver") -> None:
    """Keep the waiver (or the park it became) in the session's autopilot log and the gate's
    shadow log (best effort)."""
    from shared import autopilot_run as R
    from shared.check_modes import shadow_log
    row = {"kind": kind, "ref": ref, "why": reason, "problems": [p[:200] for p in found[:3]]}
    if session_dir:
        R.append(session_dir, row)
    try:
        shadow_log({"kind": "write_gate", "use": "task_log_close", "event": "parked" if kind == "park" else "waived",
                    "ref": ref, "keys": ["task_log.close_unaccepted"]})
    except Exception:  # noqa: BLE001 - the autopilot log row is the record; the shadow line is extra
        pass
