#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/amendment_gate.py
Summary: PreToolUse(TaskUpdate) check on a HANDOFF write and on a lead's rejection:
         - a write carrying metadata.handoff must dispose of every amend_<n> key on the
           task in handoff.amendments. This is the only deny condition.
         - handoff.rev (previous rev + 1, 1 first) and the rev a handoff_rejection names
           are AUDIT fields: checked and logged (rev_ok, rejection_rev_ok), never denied
           here (a rejection of a rev BELOW the stored one is returned as closed on arrival).
           Lead ruling on task 103: the rev pin caught 0 of the measured crossings, and
           the disposition check covers the measured cases.
         It keys on structure (the tool_input shape plus a task read), never on the actor.
Used by: hooks.json PreToolUse entry (matcher "TaskUpdate"), registered by EVENTS
         (the design notes section D2). Rules live in
         hooks/shared/amendments.py; the task read, team resolution, constants and shadow
         log come from hooks/shared/evidence/, shared with the evidence gate.

Mode: bin/checks.json "modes"."amendment_gate" ("note" shipped since 1.4.1 U11); when that key
is absent or unknown, "shadow"; "gate", its old word, reads as "enforce".
  off      nothing runs.
  shadow   (default) never denies: one line per checked write goes to checks/shadow.jsonl,
           kind "amendment_gate", event "would_deny" or "pass", with counts and key names
           only (never amendment text).
  note     never denies (1.4.1 U11: of 7 measured returns, 4 were re-sent unchanged within
           20 s and passed as repeats, and at most 3 led to a change; checks.json _notes):
           the writer gets one line naming the undisposed keys, and the lead gets one line per
           key at acceptance (ev.set_gate_notes, use amendment_gate; a later write
           replaces them, so a write that disposes of every key clears them).
  enforce  denies with the missing keys (first 100 chars each), each key ONCE: a key that
           was already returned passes with a note to the lead (ev.return_once, the D30
           rail). A refused write is re-issuable in the same turn; its reason ends with the
           NOT STORED line (hooks/shared/stored_line.py), so the writer does not notify.

Project-task refs (1.5.0, the task-system review; lead ruling amend_1 on #135): a HANDOFF write
on a task that CARRIES project tasks (ptask: links in the repo's task log, written by
`repact-task start --ptask`, or metadata.project_task / metadata.refs) must dispose of each in
handoff.refs (hooks/shared/ref_dispositions.py). This is a HARD return on every such write,
whatever the amendment mode: it keys on structure, not prose, and a missed ref is a task-log
item that stays open after its work merged. A ref the task
only MENTIONS in its text gets a note. Mode: checks.json "modes"."ref_dispositions": "enforce"
(shipped), "note" (never returns) or "off"; a missing key reads as enforce.

Rejection shape (task-system slice 1, CH-2): a write of any metadata key that ends with
"_rejection" must carry the object its readers consume, {reason, corrections, since,
revision_number, rev}, under handoff_rejection or teachback_rejection (rules:
amendments.rejection_shape_problem; null clears one). A malformed object, or a third key with
that ending, is a HARD return whatever the amendment mode: bin/repact-wait wakes on any
*_rejection, so an unchecked key is the route around. It runs on the tool input alone, before
the team and the task are resolved; a task that cannot be read only skips the rules that need
the stored task (rev required, closed on arrival). Closed on arrival (review F6): a well-formed
rejection the owner has already answered (revision_number below the task's, or a
handoff_rejection rev below the stored HANDOFF's; amendments.closed_on_arrival, judged against
the task as the write would leave it) is returned too, with the number to use: the waiter,
the reader and the relay would show nothing for it. Mode: checks.json "modes"."rejection_shape": "enforce"
(shipped), "note" (never returns) or "off"; a missing or unknown word reads as enforce. It keys
on structure, so a teammate writing a rejection on its own task is shape-checked, not refused.

After acceptance: a teammate's metadata.handoff write on a task that is already
`completed` is returned in every mode but off, with "accepted at rev N: message the lead
instead". The lead gets no hook-written line for it (the lead-notes queue is retired): the
return text tells the teammate to message the lead, and the dispatcher journals the refusal.
The accepted copy is what the lead acted on; a later rev would replace it unread. This one check reads the actor: only
a frame that carries an agent_type and is not the lead is returned, so the lead (and a frame
whose role cannot be told) can still write a completed task.

Fail-open: any error allows the write (exit 0). A file that cannot load exits 1, which
the platform treats as a non-blocking hook error.
Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2; a note =
        additionalContext, exit 0; otherwise {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

MODES = ("off", "shadow", "note", "enforce")
DEFAULT_MODE = "shadow"
MODE_KEY = "amendment_gate"
REF_MODE_KEY = "ref_dispositions"
REF_MODES = ("off", "note", "enforce")
REF_DEFAULT_MODE = "enforce"
REF_USE = "ref_dispositions"
SHAPE_MODE_KEY = "rejection_shape"
SHAPE_MODES = ("off", "note", "enforce")
SHAPE_DEFAULT_MODE = "enforce"
SHAPE_USE = "rejection_shape"
REJECTION_SUFFIX = "_rejection"     # shared/amendments.py REJECTION_SUFFIX, tested here without the import
SHAPE_PROBLEMS_SHOWN = 5
SHAPE_HINT = ('A rejection is metadata.handoff_rejection or metadata.teachback_rejection = {"reason": "<text>", '
              '"corrections": ["<one per fix>", ...], "since": "<utc with zone>", "revision_number": <that of the '
              'task metadata, 1 when it has none>, "rev": <the HANDOFF rev reviewed>}; null clears one.')
TITLE_CHARS = 60
_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})


def load_rules():
    """hooks/shared/amendments.py. Imported here, not at module level, so a TaskUpdate
    without a HANDOFF or a rejection never pays for the `shared` package."""
    from shared import amendments
    return amendments


def load_evidence():
    from shared import evidence
    return evidence


def gate_mode(ev) -> str:
    modes = ev.load_constants().get("modes", {})
    mode = str(modes.get(MODE_KEY, DEFAULT_MODE) if isinstance(modes, dict) else DEFAULT_MODE)
    mode = mode.strip().lower()
    mode = load_rules().ALIASES.get(mode, mode)
    return mode if mode in MODES else DEFAULT_MODE


def ref_mode(ev) -> str:
    modes = ev.load_constants().get("modes", {})
    mode = str(modes.get(REF_MODE_KEY, REF_DEFAULT_MODE) if isinstance(modes, dict) else REF_DEFAULT_MODE)
    mode = load_rules().ALIASES.get(mode.strip().lower(), mode.strip().lower())
    return mode if mode in REF_MODES else REF_DEFAULT_MODE


def shape_mode(ev) -> str:
    """modes.rejection_shape; enforce when the key is missing, unknown or cannot be read."""
    try:
        modes = ev.load_constants().get("modes", {})
        mode = str(modes.get(SHAPE_MODE_KEY, SHAPE_DEFAULT_MODE) if isinstance(modes, dict) else SHAPE_DEFAULT_MODE)
    except Exception:   # noqa: BLE001 - a mode that cannot be read is the shipped one
        return SHAPE_DEFAULT_MODE
    mode = load_rules().ALIASES.get(mode.strip().lower(), mode.strip().lower())
    return mode if mode in SHAPE_MODES else SHAPE_DEFAULT_MODE


def writes_rejection(update_metadata: dict) -> bool:
    """True when the update names a key the shape gate checks (amendments.is_rejection_key,
    repeated here so a TaskUpdate without one never loads the `shared` package)."""
    return any(isinstance(key, str) and key.strip().casefold().endswith(REJECTION_SUFFIX)
               for key in update_metadata)


def _stored_metadata(payload: dict, ev, task_id: str):
    """(team, the metadata the task file holds); (team or None, None) when the team or the task
    cannot be resolved or read. Never raises: the shape rules that need no task still run."""
    try:
        from shared.frame import resolve as resolve_frame
        team = resolve_frame(payload).task_team(task_id) if task_id else None
        task = ev.load_task(ev.task_path(team, task_id)) if team else None
    except Exception:   # noqa: BLE001 - an unreadable task skips the rules that need the stored task only
        return None, None
    if not isinstance(task, dict):
        return team, None
    return team, task["metadata"] if isinstance(task.get("metadata"), dict) else {}


def rejection_shape(payload: dict, task_id: str, update_metadata: dict) -> tuple[str | None, str | None]:
    """(deny reason, note) for the *_rejection keys of one update; (None, None) when it
    writes none, every one is well formed, or the mode is off. The reason names keys and
    fields only, never a value, and ends with the NOT STORED line."""
    if not writes_rejection(update_metadata):
        return None, None
    ev = load_evidence()
    mode = shape_mode(ev)
    if mode == "off":
        return None, None
    rules = load_rules()
    team, stored = _stored_metadata(payload, ev, task_id)
    current_handoff = stored.get("handoff") if stored is not None else None
    after = rules.after_write(stored, update_metadata) if stored is not None else None
    found = [(key, rules.rejection_shape_problem(key, update_metadata[key], current_handoff)
              or (rules.closed_on_arrival(key, update_metadata[key], after) if after is not None else None))
             for key in update_metadata if rules.is_rejection_key(key)]
    problems = [problem for _, problem in found if problem]
    if not problems:
        return None, None
    try:
        ev.shadow_log({"kind": "write_gate", "use": SHAPE_USE, "event": "deny" if mode == "enforce" else "note",
                       "team": team, "task_id": task_id[:TITLE_CHARS], "problems": len(problems),
                       "keys": [key for key in rules.REJECTION_KEYS if key in update_metadata]})
    except Exception:   # noqa: BLE001 - the log line is an addition; the return does not depend on it
        pass
    shown = problems[:SHAPE_PROBLEMS_SHOWN]
    if len(problems) > SHAPE_PROBLEMS_SHOWN:
        shown.append(f"... and {len(problems) - SHAPE_PROBLEMS_SHOWN} more")
    listed = "\n".join(f"  {problem}" for problem in shown)
    if mode == "note":
        return None, (f"Note on #{_shown(task_id)}: this rejection does not have the shape its readers "
                      f"consume, and it is stored as written:\n{listed}\n{SHAPE_HINT}")
    return "\n".join(filter(None, [
        f"The rejection written on #{_shown(task_id)} does not have the shape its readers consume:",
        listed, SHAPE_HINT, rejection_not_stored(task_id)])), None


def _shown(task_id: str) -> str:
    return "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")[:32] or "?"


def rejection_not_stored(task_id: str) -> str:
    """The lead's NOT STORED line for a returned rejection; a plain one when the shared line
    cannot be built (the write is refused all the same)."""
    try:
        from shared import stored_line
        return stored_line.directive_refused("rejection", task_id)
    except Exception:   # noqa: BLE001 - the line is an addition; the deny does not depend on it
        return f"NOT STORED: the rejection write on #{_shown(task_id)} was refused. Do not send the wake."


def _log_state(cwd):
    """The replayed task log of the repo `cwd` is in, or None (no checkout, no log, unreadable).
    Read only: the gate never writes the log."""
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        from shared.task_log import write as W
        return W.Store.locate(cwd, by="gate").state()
    except Exception:   # noqa: BLE001 - no log is no claim; the metadata refs still count
        return None


def ref_findings(payload: dict, team: str, task_id: str, task: dict, update_metadata: dict) -> dict:
    """{carried: [ref], missing: [ref], mentioned: [ref], malformed: [entry], titles: {ref: title}}
    for one HANDOFF write. `carried` is machine-readable: the log's ptask: links to this task
    and the task's own metadata refs, before this write and in it (a write that clears `refs`
    cannot shed them), less the refs the log already settled. `mentioned` is text only, less
    the refs this HANDOFF disposes of in handoff.refs: the note is not repeated on them."""
    from shared import ref_dispositions as RD
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    state = _log_state(payload.get("cwd"))
    carried = [row["ref"] for row in state.tasks.values()
               if {"kind": "reference", "target": f"ptask:{team}/{task_id}"} in row["links"]] if state else []
    carried += RD.metadata_refs(metadata) + RD.metadata_refs(update_metadata)
    from shared.project_tasks import SETTLED
    settled = {ref for ref, row in state.tasks.items() if row["status"] in SETTLED} if state else set()
    carried = [ref for ref in dict.fromkeys(carried) if ref not in settled]
    handoff = update_metadata.get("handoff")
    titles = {ref: state.tasks[ref]["title"][:TITLE_CHARS] for ref in carried
              if state and ref in state.tasks}
    return {"carried": carried, "missing": RD.missing(carried, handoff),
            "mentioned": [ref for ref in RD.mentioned_refs(task)
                          if ref not in carried and ref not in RD.dispositions(handoff)],
            "malformed": RD.malformed(handoff), "titles": titles}


def ref_deny_reason(task_id: str, findings: dict) -> str:
    from shared import ref_dispositions as RD
    lines = [f"Task #{task_id} carries project tasks your HANDOFF does not dispose of:"]
    lines += [f"  {ref}" + (f": {findings['titles'][ref]}" if findings["titles"].get(ref) else "")
              for ref in findings["missing"][:20]]
    if len(findings["missing"]) > 20:
        lines.append(f"  ... and {len(findings['missing']) - 20} more")
    if findings["malformed"]:
        lines.append("  (not a disposition: " + ", ".join(findings["malformed"][:5]) + ")")
    lines.append(RD.DISPOSE_HINT)
    return "\n".join(lines)


def ref_note(ev, team: str, task_id: str, findings: dict) -> str | None:
    """The writer's line for refs the task only mentions (and, in note mode, the carried refs
    it leaves undisposed); the lead gets the same at acceptance."""
    lead_lines = [f"{ref}: carried by this task, no disposition in handoff.refs" for ref in findings["missing"]]
    lead_lines += [f"{ref}: named in the task text, not carried; dispose of it or ask the lead to "
                   f"`repact-task start {ref} --ptask`" for ref in findings["mentioned"]]
    ev.set_gate_notes(team, task_id, REF_USE, lead_lines)
    named = findings["missing"] + findings["mentioned"]
    if not named:
        return None
    ev.shadow_log({"kind": "write_gate", "use": REF_USE, "event": "note", "team": team,
                   "task_id": task_id, "refs": named})
    return (f"Note on #{task_id}: project task(s) {', '.join(named[:5])}{', ...' if len(named) > 5 else ''} "
            "are named by this task and not disposed of in handoff.refs. The HANDOFF is stored; the lead "
            "reads this at acceptance. If one is yours, dispose of it in your next HANDOFF write: "
            '"done: <evidence>", "partial: <what is left>", "deferred: <why>" or "dropped: <why>".')


def written_time(task_file: Path) -> str:
    """When the task file was last written, as UTC ISO: the current HANDOFF's time."""
    try:
        stamp = os.stat(task_file).st_mtime
    except OSError:
        return "unknown time"
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat(timespec="seconds")


def check(rules, task: dict, written: str, update_metadata: dict) -> dict:
    """The findings for one TaskUpdate: {missing, rev, rejection, amend_total}."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    current_handoff = metadata.get("handoff")
    task_amendments = rules.amendments(dict(metadata, **update_metadata))
    findings = {"missing": [], "rev": None, "rejection": None, "amend_total": len(task_amendments)}
    new_handoff = update_metadata.get("handoff")
    if isinstance(new_handoff, dict):
        findings["missing"] = rules.missing_dispositions(task_amendments, new_handoff)
        findings["rev"] = rules.rev_problem(current_handoff, new_handoff)
    if "handoff_rejection" in update_metadata:
        findings["rejection"] = rules.rejection_problem(
            current_handoff, update_metadata.get("handoff_rejection"), written)
    return findings


def would_deny(findings: dict) -> bool:
    """Only an undisposed amendment denies; the rev findings are audit-only."""
    return bool(findings["missing"])


def deny_reason(rules, task_id: str, findings: dict) -> str:
    lines = []
    if findings["missing"]:
        lines.append(f"Task #{task_id} has amendments your HANDOFF does not dispose of "
                     "(read the amend_* keys in your task file):")
        lines += [f"  {key}: {preview}" for key, preview in findings["missing"]]
    lines.append(rules.DISPOSE_HINT)
    return "\n".join(lines)


def shadow_event(team: str, task_id: str, mode: str, findings: dict) -> dict:
    """Counts and key names only: the log never carries amendment or HANDOFF text."""
    return {
        "event": "would_deny" if would_deny(findings) else "pass",
        "kind": "amendment_gate", "mode": mode, "team": team, "task_id": task_id,
        "amend_total": findings["amend_total"], "missing": len(findings["missing"]),
        "missing_keys": [key for key, _ in findings["missing"]],
        "rev_ok": findings["rev"] is None, "rejection_rev_ok": findings["rejection"] is None,
    }


def not_stored(task_id: str) -> str:
    """The writer's NOT STORED line for a refused HANDOFF write; "" when it cannot be built
    (the write is refused all the same)."""
    try:
        from shared import stored_line
        return stored_line.refused("handoff", task_id)
    except Exception:   # noqa: BLE001 - the line is an addition; the deny does not depend on it
        return ""


def after_acceptance(payload: dict, rules, team: str, task_id: str, task: dict,
                     update_metadata: dict) -> str | None:
    """The reason that returns a teammate's HANDOFF write on a completed task, else None."""
    if "handoff" not in update_metadata or task.get("status") != "completed":
        return None
    from shared.frame import resolve as resolve_frame
    frame = resolve_frame(payload)
    if frame.role != "teammate":
        return None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    rev = rules.handoff_rev(metadata.get("handoff"))
    rejection = metadata.get("handoff_rejection")
    if rev is not None and isinstance(rejection, dict) and rules.handoff_rev(rejection) == rev:
        return None     # the lead rejected this very rev after completing: the rewrite answers it
    accepted = f"accepted at rev {rev}" if rev is not None else "accepted with no HANDOFF rev stored"
    return (f"Task #{task_id} is completed: the lead accepted this HANDOFF and acted on it, so a later "
            f"write would replace what was accepted, unread. {accepted}: message the lead instead "
            "(SendMessage: what changed, why, which files), and leave the deliverable as accepted "
            "until the lead answers.\n"
            f"NOT STORED: the HANDOFF write on #{task_id} was returned, so the task file still holds "
            "the accepted copy. Do not notify and do not write it again.")


def note(ev, team: str, task_id: str, findings: dict, update_metadata: dict) -> str | None:
    """Note mode: record the undisposed keys for the lead's acceptance lines (a HANDOFF write
    replaces the previous write's; a rejection leaves them), log one shadow line, and return the
    writer's one line (key names only), or None when every key is disposed."""
    if not isinstance(update_metadata.get("handoff"), dict):
        return None
    missing = findings["missing"]
    ev.set_gate_notes(team, task_id, "amendment_gate",
                      [f"{key}: no disposition in handoff.amendments: {preview}" for key, preview in missing])
    if not missing:
        return None
    keys = [key for key, _ in missing]
    ev.shadow_log({"kind": "write_gate", "use": "amendment_gate", "event": "note", "team": team,
                   "task_id": task_id, "keys": keys})
    return (f"Note on #{task_id}: {len(keys)} amendment(s) with no disposition in handoff.amendments "
            f"({', '.join(keys[:5])}{', ...' if len(keys) > 5 else ''}). The HANDOFF is not returned for "
            "this; the lead reads these lines at acceptance. Dispose of each in your next HANDOFF write: "
            "\"applied: <where>\", \"declined: <why>\", \"disputed: <why>\", \"partial: <what is left>\" "
            "or \"ack\".")


def run(payload: dict) -> tuple[str | None, str | None]:
    """(deny reason, note) for one payload; (None, None) allows silently. A deny names the team
    policy when the policy raised one of this gate's modes (shared.team_policy.refusal_note)."""
    reason, note = _run(payload)
    if reason:
        from shared.team_policy import refusal_note
        reason += refusal_note((f"modes.{MODE_KEY}", f"modes.{REF_MODE_KEY}",
                                f"modes.{SHAPE_MODE_KEY}"))
    return reason, note


def _run(payload: dict) -> tuple[str | None, str | None]:
    """The rejection shape is checked first, on the tool input alone, so no early return below
    is a way past it."""
    if payload.get("tool_name") != "TaskUpdate":
        return None, None
    tool_input = payload.get("tool_input")
    update_metadata = tool_input.get("metadata") if isinstance(tool_input, dict) else None
    if not isinstance(update_metadata, dict):
        return None, None
    task_id = str(tool_input.get("taskId") or "")
    reason, shape_note = rejection_shape(payload, task_id, update_metadata)
    if reason:
        return reason, None
    reason, writer_note = amendment_checks(payload, tool_input, update_metadata, task_id)
    return reason, None if reason else "\n".join(filter(None, [shape_note, writer_note])) or None


def amendment_checks(payload: dict, tool_input: dict, update_metadata: dict,
                     task_id: str) -> tuple[str | None, str | None]:
    """(deny reason, note) of the disposition, ref and after-acceptance checks. Logs in shadow mode."""
    if not ("handoff" in update_metadata or "handoff_rejection" in update_metadata) or not task_id:
        return None, None
    ev = load_evidence()
    mode = gate_mode(ev)
    from shared.frame import resolve as resolve_frame
    team = resolve_frame(payload).task_team(task_id)
    if mode == "off" or not team:
        return None, None
    task_file = ev.task_path(team, task_id)
    task = ev.load_task(task_file)
    if task is None:
        return None, None
    rules = load_rules()
    closed = after_acceptance(payload, rules, team, task_id, task, update_metadata)
    if closed:
        ev.shadow_log({"kind": "write_gate", "use": "handoff_after_acceptance", "event": "deny",
                       "team": team, "task_id": task_id})
        return closed, None
    findings = check(rules, task, written_time(task_file), update_metadata)
    refs_mode = ref_mode(ev)
    refs = ref_findings(payload, team, task_id, task, update_metadata) \
        if refs_mode != "off" and isinstance(update_metadata.get("handoff"), dict) else None
    if refs and refs["missing"] and refs_mode == "enforce":
        ev.shadow_log({"kind": "write_gate", "use": REF_USE, "event": "deny", "team": team,
                       "task_id": task_id, "refs": refs["missing"]})
        return "\n".join(filter(None, [ref_deny_reason(task_id, refs), not_stored(task_id)])), None
    refs_line = ref_note(ev, team, task_id, refs) if refs else None
    if mode == "shadow":
        ev.shadow_log(shadow_event(team, task_id, mode, findings))
        return None, refs_line
    if mode == "note":
        return None, "\n".join(filter(None, [note(ev, team, task_id, findings, update_metadata), refs_line])) or None
    if not would_deny(findings):
        return None, refs_line
    fresh = ev.return_once(team, task_id, "amendment_gate",
                           [{"key": key, "line": f"{key}: {preview}"} for key, preview in findings["missing"]])
    fresh_keys = {item["key"] for item in fresh}
    findings = dict(findings, missing=[m for m in findings["missing"] if m[0] in fresh_keys])
    if not findings["missing"]:
        return None, refs_line
    return "\n".join(filter(None, [deny_reason(rules, task_id, findings), not_stored(task_id)])), None


def decide(payload: dict) -> str | None:
    """The deny reason for one payload, or None to allow (run's note is dropped here)."""
    return run(payload)[0]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason, writer_note = run(payload) if isinstance(payload, dict) else (None, None)
    except Exception as error:   # noqa: BLE001 - fail-open: a gate that cannot run decided nothing
        print(f"amendment_gate: internal error, allowing (fail-open): {type(error).__name__}",
              file=sys.stderr)
        return 0
    if not reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": writer_note}})
              if writer_note else _ALLOW_OUTPUT)
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
