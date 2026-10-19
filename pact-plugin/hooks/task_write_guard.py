#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_write_guard.py
Summary: PreToolUse(TaskUpdate|TaskCreate) security check, the one guard that keeps the
    workflow writes on the `repact` commands. A reserved key is
    refused on ANY task (a plain task given dispatch, acceptance or a handoff
    could pass for an accepted one), and so is a TaskCreate carrying one. Status completed is
    refused on a task that carries metadata.dispatch; the lead's own todo, alert and plan tasks
    may still be completed by TaskUpdate: the task-log close gate passes only a
    task with an op-owned acceptance, so such a completion closes no row. The refusal names
    the `repact` command that makes the write.
Used by: hooks/dispatch_registry.py (native check `task_write_guard:dispatch_check`, deny on
    timeout); `main` runs it alone when the dispatcher runs a deeply nested input apart.

Keys are compared after NFKC, casefold and the removal of format characters, at every depth
of the tool input (a reserved key nested under another key counts), and every spelling the
raw stdin holds counts, so a duplicated key cannot hide behind the one json.loads keeps. A
tool input this check cannot read is refused. A task file that exists but cannot be read is
refused only for an update that would write a reserved key or complete it.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from typing import Iterator, Optional

GUARDED_TOOLS = ("TaskUpdate", "TaskCreate")
RESERVED = ("dispatch", "teachback", "handoff", "handoff_rejection", "acceptance",
            "evidence_waiver", "parked", "last_op", "revision_number")
AMEND_PREFIX = "amend_"
# The command that makes each reserved write (the way through of a refusal).
WAY_THROUGH = {
    "completed": "repact accept {id}",
    "dispatch": "repact dispatch (it writes metadata.dispatch when it makes the task)",
    "teachback": "repact submit {id} --teachback <file> (the coder), or repact accept {id} (the lead)",
    "handoff": "repact submit {id} (the coder)",
    "handoff_rejection": "repact reject {id} --reason \"<why>\"",
    "acceptance": "repact accept {id}",
    "evidence_waiver": "repact accept {id} --waive \"<why>\" (attended; unattended it parks the task for a human)",
    "parked": "repact accept {id} --waive \"<why>\" (unattended), or the third failed repact accept",
    "last_op": "the repact command whose step it records (rerun it)",
    "revision_number": "repact reject {id} --reason \"<why>\" (each rejection counts once)",
    "amend_": "repact amend {id} --text \"<text>\"",
}
RULE_COMPLETED = "task_write.completed"
RULE_RESERVED = "task_write.reserved_key"
RULE_UNREADABLE = "task_write.unreadable"
_MAX_NODES = 100_000


class _Keys(dict):
    """A JSON object that remembers every key it was given, duplicates included."""

    all_keys: list


def _pairs(pairs: list) -> _Keys:
    obj = _Keys(pairs)
    obj.all_keys = [key for key, _value in pairs]
    return obj


def fold(key: str) -> str:
    """`key` as the guard compares it: NFKC, casefold, format characters and edge spaces gone."""
    text = unicodedata.normalize("NFKC", key).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").strip()


def reserved_of(key: str) -> Optional[str]:
    """The reserved name a key folds to ("amend_" for any amend_<n>), or None."""
    folded = fold(key)
    if folded in RESERVED:
        return folded
    return AMEND_PREFIX if folded.startswith(AMEND_PREFIX) else None


def _keys(value) -> Iterator[str]:
    """Every key of every object under `value`, iteratively (no recursion limit)."""
    stack, seen = [value], 0
    while stack:
        node = stack.pop()
        seen += 1
        if seen > _MAX_NODES:
            raise ValueError("tool input too large to read")
        if isinstance(node, dict):
            yield from getattr(node, "all_keys", None) or list(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def read_input(payload: dict, raw: str) -> Optional[dict]:
    """The tool input with every key spelling kept, or None when it cannot be read: the raw
    stdin is parsed again with a pairs hook, and must agree with the payload's tool name."""
    try:
        parsed = json.loads(raw, object_pairs_hook=_pairs) if raw else payload
    except (ValueError, RecursionError):
        return None
    tool_input = parsed.get("tool_input") if isinstance(parsed, dict) else None
    if not isinstance(tool_input, dict) or parsed.get("tool_name") != payload.get("tool_name"):
        return None
    return tool_input


def wanted_writes(tool_input: dict) -> list:
    """The guarded writes this input asks for: "completed" and each reserved name, in order."""
    writes = []
    status = tool_input.get("status")
    if status is not None and (not isinstance(status, str) or fold(status) == "completed"):
        writes.append("completed")
    for key in _keys(tool_input):
        name = reserved_of(key) if isinstance(key, str) else None
        if name and name not in writes:
            writes.append(name)
    return writes


def _task_of(task_id: str, team: str):
    """(task or None, readable). A team that cannot be resolved reads every team holding the id."""
    from shared.task_store import TaskRef, TaskStore, is_safe_task_id

    if not is_safe_task_id(task_id):
        return None, False
    store = TaskStore()
    teams = [team] if team else store.teams_holding(task_id)
    for name in teams:
        ref = TaskRef(name, task_id)
        if not store.exists(ref):
            continue
        task = store.get(ref)
        if task is None:
            return None, False
        meta = task.get("metadata")
        if isinstance(meta, dict) and meta.get("dispatch") is not None:
            return task, True
    return None, True


def _refusal(task_id: str, writes: list, dispatched: bool = True) -> tuple:
    first = writes[0]
    ways = "; ".join(dict.fromkeys(WAY_THROUGH[name].format(id=task_id) for name in writes))
    named = ", ".join("status completed" if name == "completed" else
                      ("amend_<n>" if name == AMEND_PREFIX else f"metadata.{name}") for name in writes)
    rule = RULE_COMPLETED if first == "completed" else RULE_RESERVED
    whose = (f"task #{task_id} was made by repact dispatch, so {named} is written by its repact "
             "command" if dispatched else f"{named} is written only by the repact commands, on any task")
    return (f"task_write_guard: {whose}, not by a task write. Run instead: {ways}. "
            f"[rule: {rule}]", rule)


def decide(payload: dict, raw: str, team: str = "") -> tuple:
    """(deny reason, rule) or (None, None). The check runs only where the host matched a task
    tool, so a payload that names no tool is stdin that could not be read."""
    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name and tool_name not in GUARDED_TOOLS:
        return None, None
    tool_input = read_input(payload, raw) if tool_name in GUARDED_TOOLS else None
    if tool_input is None:
        return ("task_write_guard: the task tool input could not be read, so the write was "
                f"refused. Run the same call again with plain JSON. [rule: {RULE_UNREADABLE}]",
                RULE_UNREADABLE)
    try:
        writes = wanted_writes(tool_input)
    except ValueError:
        writes = None
    if writes is None:
        return (f"task_write_guard: the task tool input is too large to check, so the write was "
                f"refused. Send a smaller update. [rule: {RULE_UNREADABLE}]", RULE_UNREADABLE)
    if not writes:
        return None, None
    if payload.get("tool_name") == "TaskCreate":
        reserved = [name for name in writes if name != "completed"]
        if not reserved:
            return None, None
        return _refusal("<new>", reserved)
    task_id = tool_input.get("taskId")
    if not isinstance(task_id, str):
        return ("task_write_guard: the update names no task id it can read, so the write was "
                f"refused. Name the task by its id string. [rule: {RULE_UNREADABLE}]", RULE_UNREADABLE)
    reserved = [name for name in writes if name != "completed"]
    if reserved:                                        # on any task, made by repact or not
        return _refusal(task_id, reserved, dispatched=False)
    task, readable = _task_of(task_id, team)
    if not readable:
        return (f"task_write_guard: task #{task_id} could not be read, so whether repact made it "
                "is unknown and the write was refused. Run repact-doctor, then the matching "
                f"repact command. [rule: {RULE_UNREADABLE}]", RULE_UNREADABLE)
    if task is None:
        return None, None
    return _refusal(task_id, writes)


def _team(payload: dict) -> str:
    try:
        import shared.pact_context as pact_context

        pact_context.init(payload)
        return pact_context.get_team_name() or ""
    except Exception:  # noqa: BLE001 - an unknown team reads every team holding the id
        return ""


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry: a block with the rule, or None. An error raises, and the
    registry denies (`on_timeout="deny"`)."""
    import dispatch_registry as registry

    raw = getattr(ctx, "raw", "") if ctx is not None else ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    reason, rule = decide(payload, raw or "", _team(payload))
    if not reason:
        return None
    return registry.Result(decision="block", reason=reason, rule=rule)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, RecursionError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        reason, _rule = decide(payload, raw, _team(payload))
    except Exception as error:  # noqa: BLE001 - a security check that cannot run refuses
        reason = (f"task_write_guard: check failed ({type(error).__name__}); refused (fail closed) "
                  "[rule: task_write_guard.error]")
    if not reason:
        print(json.dumps({"suppressOutput": True}))
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
