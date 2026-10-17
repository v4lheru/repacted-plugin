#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/amendment_relay.py
Summary: PostToolUse relay. A lead message reaches a busy in-process teammate only at
         its turn end (measured: 0 of 277 mid-turn), so a teammate usually writes its
         HANDOFF before it can read one. This hook reads the amend_<n> keys on the tasks
         the calling teammate owns and has in_progress, and hands each new one to the
         teammate's model as additionalContext within one tool call:
             LEAD AMENDMENT amend_3 on #34 (ruling): <text>
         A "<resource>_grant" key (the lead's answer to a request for a shared resource)
         is handed over the same way, as a GRANT line, and so is an open
         handoff_rejection or teachback_rejection, as a REJECTION line that names the
         reader (repact-payload <id> --key rejection), once per revision_number and rev.
Used by: hooks.json PostToolUse entries for the tools a teammate uses mid-task
         (Bash|Edit|Write|Read|TaskUpdate|MultiEdit|NotebookEdit; 1.4.1, so Grep, Glob,
         MCP and task-list calls start no process for it), registered by EVENTS
         (the design notes section D2).
         Rules and the line format live in hooks/shared/amendments.py, imported only
         when a task carries an amendment not yet relayed.

Cost: it runs after most tool calls, so the fast path imports json, os and sys only and
exits when
  - the payload names no teammate (the lead and a tmux teammate carry no agent_id; an
    Agent-tool subagent's is not built from a member name), or
  - the team's task dir has not been written since this teammate's stamp.
The task dir mtime moves on every task write because the platform writes task files
by rename (measured: dir mtime 93 microseconds after the file's). The stamp, one small
JSON file per teammate in the PACT session dir, also records which amendments were
already relayed, so each is relayed once.

Addressing (measured 2026-10-01 on 314 of 314 run-ledger rows that carry an agent_id): an
in-process teammate's payload has agent_id = "a" + agent_type + "-" + 16 hex, agent_type =
its member name, and the LEAD's session_id. It names no team, so the team comes from the
lead session's pact-session-context.json. Before this was read, the relay accepted only
the older "name@team" agent_id and so never relayed. It opts out of the shared frame
resolver (hooks/shared/frame.py) on purpose: it finds the session dir under any project
slug, and it stays free of the `shared` package on every Bash, Edit and Read.
Delivery (same date): a teammate transcript records another plugin PostToolUse hook's
additionalContext on the teammate's own Bash calls. To confirm this hook live, look for
<PACT session dir>/amendment-relay/<team>--<name>.json and for a 'LEAD AMENDMENT amend_N'
hook_additional_context entry in the teammate's transcript.
Fail-open: any error relays nothing and exits 0.
"""

from __future__ import annotations

import json
import os
import sys

STAMP_DIRNAME = "amendment-relay"
CONTEXT_FILENAME = "pact-session-context.json"
AMEND_PREFIX = "amend_"
GRANT_SUFFIX = "_grant"     # shared/amendments.py GRANT_SUFFIX: a lead's resource grant
REJECTION_KEYS = ("teachback_rejection", "handoff_rejection")   # shared/amendments.py REJECTION_KEYS
_HEX = frozenset("0123456789abcdef")
# A task file renamed into place just before the previous check can carry an mtime older
# than the stamp; files this much older than the stamp are re-read, and the relayed set
# keeps that idempotent.
RESCAN_SLACK_NS = 5_000_000_000
# One call relays at most this many amendments; the rest are counted in one closing line.
MAX_RELAYED = 20
_SILENT = json.dumps({"suppressOutput": True})


def _actor_agent_id(payload):
    """The payload's raw agent_id, or None. The ONLY place this file reads that field.

    Addressing only: the value says whose owned tasks to read and whose stamp to use. It
    never grants or denies anything; a payload without one relays nothing. The fork's
    named-site exemption in test_lead_discriminator_invariant covers exactly this helper.
    """
    return payload.get("agent_id")


def _actor_agent_type(payload):
    """The payload's raw agent_type, or None: an in-process teammate's member name.
    Addressing only, like _actor_agent_id; never compared with a lead spelling."""
    return payload.get("agent_type")


def _path_part(value) -> bool:
    """True for a non-empty string that is safe as one path component."""
    return isinstance(value, str) and bool(value) and "/" not in value and not value.startswith(".")


def addressee(payload: dict) -> tuple[str, str | None] | None:
    """(agent name, team) of the calling teammate, else None. Two agent_id shapes name one:
      - "a" + agent_type + "-" + 16 hex: an in-process teammate. The payload carries no
        team, so team is None here and session_team() supplies it;
      - "name@team": older Claude Code.
    The id is compared whole against one built from agent_type, never parsed, so an
    Agent-tool subagent ("a" + 16 hex) names nobody."""
    raw = _actor_agent_id(payload)
    if not isinstance(raw, str):
        return None
    if "@" in raw:
        name, _, team = raw.partition("@")
        return (name, team) if _path_part(name) and _path_part(team) and "@" not in team else None
    name = _actor_agent_type(payload)
    if not _path_part(name) or not raw.startswith(f"a{name}-"):
        return None
    tail = raw[len(name) + 2:]
    return (name, None) if len(tail) == 16 and _HEX.issuperset(tail) else None


def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def session_dir(session_id) -> str | None:
    """<config>/pact-sessions/<project>/<session_id>, the PACT session dir, if it exists."""
    if not isinstance(session_id, str) or not session_id or "/" in session_id or session_id.startswith("."):
        return None
    root = os.path.join(config_dir(), "pact-sessions")
    try:
        projects = os.listdir(root)
    except OSError:
        return None
    for project in projects:
        candidate = os.path.join(root, project, session_id)
        if os.path.isdir(candidate):
            return candidate
    return None


def session_team(base: str) -> str | None:
    """team_name from the PACT session dir's context file, written at session start. An
    in-process teammate shares the lead's session_id, so `base` is the lead's dir."""
    try:
        with open(os.path.join(base, CONTEXT_FILENAME), encoding="utf-8") as handle:
            context = json.load(handle)
    except (OSError, ValueError):
        return None
    team = context.get("team_name") if isinstance(context, dict) else None
    return team if _path_part(team) else None


def read_stamp(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            stamp = json.load(handle)
    except (OSError, ValueError):
        return {}
    return stamp if isinstance(stamp, dict) else {}


def write_stamp(path: str, stamp: dict) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(stamp, handle)
    os.replace(temporary, path)


def load_rules():
    """hooks/shared/amendments.py, imported only for an amendment not yet relayed: the
    `shared` package costs about 30 ms to load, which no other call pays."""
    from shared import amendments
    return amendments


def owned_in_progress(task_dir: str, name: str, since_ns: int):
    """(task id, metadata) for each task file written since since_ns that `name` owns and
    has in_progress. A cheap byte test skips files that cannot match before parsing."""
    needle = name.encode("utf-8")
    with os.scandir(task_dir) as entries:
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            try:
                if entry.stat().st_mtime_ns < since_ns:
                    continue
                with open(entry.path, "rb") as handle:
                    raw = handle.read()
                if needle not in raw or b"in_progress" not in raw:
                    continue
                if b"amend_" not in raw and b'_grant"' not in raw and b'_rejection"' not in raw:
                    continue
                task = json.loads(raw)
            except (OSError, ValueError):
                continue
            if isinstance(task, dict) and task.get("owner") == name \
                    and task.get("status") == "in_progress" and isinstance(task.get("metadata"), dict):
                yield str(task.get("id") or entry.name[:-5]), task["metadata"]


def unrelayed(metadata: dict, already: set) -> bool:
    """True when the task carries an amendment, a grant or a rejection whose token is not in
    `already`. A rejection's token is "<key>@<revision_number>r<rev>", built as
    shared/amendments.py rejection_token builds it (a test holds the two together)."""
    for key, value in metadata.items():
        if not isinstance(key, str):
            continue
        if key.startswith(AMEND_PREFIX) or key.endswith(GRANT_SUFFIX):
            if key not in already:
                return True
        elif key in REJECTION_KEYS and value:
            number = value.get("revision_number") if isinstance(value, dict) else None
            rev = value.get("rev") if isinstance(value, dict) else None
            token = f"{key}@{number if type(number) is int else 0}r{rev if type(rev) is int else 0}"
            if token not in already:
                return True
    return False


def relay(payload: dict) -> str | None:
    """The additionalContext to hand the teammate, or None. Updates the stamp."""
    who = addressee(payload)
    if who is None:
        return None
    name, team = who
    base = session_dir(payload.get("session_id"))
    if base is None:
        return None
    team = team or session_team(base)
    if team is None:
        return None
    task_dir = os.path.join(config_dir(), "tasks", team)
    try:
        dir_mtime_ns = os.stat(task_dir).st_mtime_ns
    except OSError:
        return None
    stamp_path = os.path.join(base, STAMP_DIRNAME, f"{team}--{name}.json")
    stamp = read_stamp(stamp_path)
    last = stamp.get("dir_mtime_ns")
    if last == dir_mtime_ns:
        return None
    since_ns = last - RESCAN_SLACK_NS if isinstance(last, int) else 0
    relayed = stamp.get("relayed") if isinstance(stamp.get("relayed"), dict) else {}
    rules = None
    lines = []
    amended = False
    for task_id, metadata in owned_in_progress(task_dir, name, since_ns):
        already = set(relayed.get(task_id) or [])
        if not unrelayed(metadata, already):
            continue
        rules = rules or load_rules()
        new_lines, already = rules.unread_lines(metadata, task_id, already)
        amended = amended or any(line.startswith(("LEAD AMENDMENT", "AMENDMENT")) for line in new_lines)
        lines.extend(new_lines)
        relayed[task_id] = sorted(already, key=lambda key: (rules.amendment_number(key) or 0, key))
    write_stamp(stamp_path, {"dir_mtime_ns": dir_mtime_ns, "relayed": relayed})
    if not lines:
        return None
    if len(lines) > MAX_RELAYED:
        lines = lines[:MAX_RELAYED] + [
            f"{len(lines) - MAX_RELAYED} more amendments are not shown: read your task file."]
    return "\n".join(lines + ([rules.DISPOSE_HINT] if amended else []))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        context = relay(payload) if isinstance(payload, dict) else None
    except Exception:   # noqa: BLE001 - fail-open: a relay that cannot run relays nothing
        context = None
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": context}}))
    else:
        print(_SILENT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
