#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/evidence_gate.py
Summary: PostToolUse(TaskUpdate, TaskCreate) hook. It hands every update to the D10 acceptance bridge and,
         on a write of metadata.handoff or metadata.teachback_submit, tells the writer what the
         task file holds after the write (the stored line). The write-time checks themselves are
         hooks/write_checks.py's and hooks/amendment_gate.py's (PreToolUse).
Used by: hooks.json PostToolUse entry (matcher "TaskCreate|TaskUpdate"; a TaskCreate only binds
         the lead's dispatch refs and prints nothing), added by the fork's
         UL-integrate milestone. Shares hooks/shared/evidence/ with the bin/handoff-evidence CLI.

D10 acceptance bridge (hooks/shared/task_log/bridge.py, design section 5.2): on every update, BEFORE the
stored line, the bridge records the LEAD's acceptance or rejection of a platform task linked to a project
task (metadata.project_task) in the repo's task log. The role decision is hooks/shared/actor.py's
`bridge_writes` over the shared lead predicate (a lead agent_type, or no identity field on the
team's lead session; test_lead_discriminator_invariant). The team comes from the same frame
(task_team). The bridge writes nothing to stdout, and its errors are swallowed.

Stored line: on every write of metadata.handoff or metadata.teachback_submit the writer gets
one line saying what the task file holds after the write: "HANDOFF stored on #<id> (owner
<o>): rev N" or "NOT STORED: ..., Do not notify" (hooks/shared/stored_line.py). A write a
PreToolUse check refused never reaches this hook: the refusing check carries the NOT STORED
line in its own reason. With no team resolved there is no task file to read back: the writer
gets "team unknown: no stored line for task #<id>. Read the task file back before you
notify." in its place (1.4.1 U12).

Directive line (task-system slice 1, CH-3): on a write of a *_rejection or an amend_<n> the
writer (the lead) gets "Rejection stored on #<id> (owner <o>): revision N, digest <d>" or
"Amendment amend_<n> stored on #<id> (owner <o>): digest <d>" from the task file read back
(stored_line.directive_line). The digest is what the lead's one-line wake carries.

It NEVER blocks: no permission decision is ever emitted, exit status is always 0, and any
exception is swallowed (fail-open).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT / "hooks") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

GATED_KEYS = {"handoff": "handoff", "teachback_submit": "teachback"}
BRIDGE_KEYS = ("project_task", "refs", "handoff_rejection")    # metadata keys the bridge acts on
DIRECTIVE_LINES_MAX = 5


def gate_kind(tool_input: dict) -> str | None:
    metadata = tool_input.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key, kind in GATED_KEYS.items():
        if metadata.get(key):
            return kind
    return None


def _load_bridge():
    from shared.task_log import bridge
    return bridge


def bridge_step(payload: dict) -> str | None:
    """Hand the update to the D10 acceptance bridge. Cheap exit first: the bridge only acts on a
    completion, a HANDOFF rejection or a project_task link. Never raises. Returns the lead's
    acceptance line (what the acceptance closed in the task log and what it left open), or
    None."""
    try:
        tool_input = payload.get("tool_input") or {}
        metadata = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
        if tool_input.get("status") != "completed" and not any(metadata.get(key) for key in BRIDGE_KEYS):
            return None
        bridge = _load_bridge()
        from shared.actor import resolve_actor
        bridge.handle(payload, resolve_actor(payload).bridge_writes())
        return bridge.acceptance_line(getattr(bridge, "last_report", {}))
    except Exception:   # noqa: BLE001 - fail open: `repact-task sync` repairs a missed write
        return None


def stored_line(ev, team: str | None, task_id: str, tool_input: dict) -> str | None:
    """What the task file holds after this write, for its writer: stored (a HANDOFF with its
    rev) or NOT STORED with the reason (hooks/shared/stored_line.py). None on any error: no
    line is not a stored line."""
    try:
        from shared import stored_line as rules
        key = rules.written_key(tool_input)
        if not key:
            return None
        task = ev.load_task(ev.task_path(team, task_id)) if team else None
        return rules.after_write(key, task_id, tool_input["metadata"][key], task)
    except Exception:   # noqa: BLE001 - fail open: the writer reads the task file back instead
        return None


def directive_keys(tool_input: dict) -> list[str]:
    """The lead-directive keys this update wrote with a value: a rejection or an amend_<n>
    (stored_line.directive_kind). A cheap test on the key text comes first, so an update
    without one never loads the rules."""
    metadata = tool_input.get("metadata")
    if not isinstance(metadata, dict):
        return []
    named = [key for key, value in metadata.items() if value is not None and isinstance(key, str)
             and (key.endswith("_rejection") or key.startswith("amend_"))]
    if not named:
        return []
    try:
        from shared import stored_line as rules
        return [key for key in named if rules.directive_kind(key)][:DIRECTIVE_LINES_MAX]
    except Exception:   # noqa: BLE001 - fail open: no line is not a stored line
        return []


def directive_lines(ev, team: str | None, task_id: str, keys: list[str]) -> str | None:
    """One line per directive key, from the task file read back: stored with its digest (what
    the wake carries) or NOT STORED. None on any error."""
    try:
        from shared import stored_line as rules
        task = ev.load_task(ev.task_path(team, task_id)) if team else None
        return "\n".join(rules.directive_line(task_id, task, key) for key in keys) or None
    except Exception:   # noqa: BLE001 - fail open: the lead reads the task file back instead
        return None


def context(*lines: str | None) -> dict | None:
    """The PostToolUse output carrying the given lines, or None when there is none."""
    text = "\n".join(line for line in lines if line)
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


def run(payload: dict) -> dict | None:
    """The hook's decision for one payload: None (say nothing) or the output object."""
    if payload.get("tool_name") == "TaskCreate":    # the lead's dispatch metadata binds its refs
        bridge_step(payload)
        return None
    if payload.get("tool_name") != "TaskUpdate":
        return None
    accepted = bridge_step(payload)
    tool_input = payload.get("tool_input") or {}
    kind = gate_kind(tool_input)
    directives = directive_keys(tool_input)
    task_id = str(tool_input.get("taskId") or "")
    if not (kind or directives) or not task_id:
        return context(accepted)
    from shared import evidence as ev
    from shared.frame import resolve as resolve_frame
    team = resolve_frame(payload).task_team(task_id)
    own = None if team else ev.team_from_session(payload.get("session_id"))
    if isinstance(own, str) and ev.PATH_SEGMENT.fullmatch(own):
        # This session's team is known and holds no file of that id: the write is not stored.
        return context(accepted, stored_line(ev, own, task_id, tool_input),
                       directive_lines(ev, own, task_id, directives))
    if not team:            # no task file to read back, so this line stands in for the stored line
        return context(accepted, f"team unknown: no stored line for task #{ev._printable(task_id, 40)}. Read the task "
                       "file back before you notify.")
    return context(accepted, stored_line(ev, team, task_id, tool_input),
                   directive_lines(ev, team, task_id, directives))


def main() -> int:
    try:
        output = run(json.loads(sys.stdin.read() or "{}"))
        if output:
            print(json.dumps(output))
    except Exception:   # noqa: BLE001 - a hook must never fail the tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
