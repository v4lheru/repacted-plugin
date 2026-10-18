#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/write_checks.py (D30: facts decide)
Summary: PreToolUse(TaskUpdate|Bash) checks that ask no model.
           - Bash `git commit`, mode commit_rule "gate": the message carries AI attribution, a
             body, more than one sentence, or a denylisted word (hooks/shared/commit_rules.py; words in
             bin/checks.json commit_rule.deny_words). The commit is RETURNED (a deny carrying
             the reason) EVERY time it is issued (1.4.1 U12: the return-once rail let the same
             commit through on its second try).
           - TaskUpdate writing metadata.handoff: two NOTES, never a return.
             The uncertainty check (mode uncertainty_disposition "advisory" or "gate", a note
             since 1.4.1 U11: 0 of 8 measured returns led to a real change): a
             handoff.uncertainty line that does not open with fixed:, known-limit: or
             not-applicable: (ev.uncertainty_gate) lets the HANDOFF through; its writer gets one
             line (ev.uncertainty_note) and the lead the item lines at acceptance. A labelled
             item is disposed.
             The run-evidence check (mode handoff_evidence "advisory" or "gate", a note by
             design, 1.4.1): an item with a parsed count and no recorded run
             in scope (NO-RUN), with counts no recorded run has (MISMATCH), or with a clean count
             only runs that exited non-zero have (EXIT) (ev.handoff_gate) lets the HANDOFF
             through; its writer gets one line (ev.evidence_note) and the lead gets the item
             lines at acceptance (ev.set_gate_notes), beside the advisories (an unparsed count,
             an out-of-scope citation, a file edited after the run). It keeps no return-once
             state. Prose with no parsed count is never flagged.
           An undisposed amend_<n> is hooks/amendment_gate.py's (a note too since 1.4.1 U11).
Used by: hooks.json PreToolUse entries (matchers "TaskUpdate" and "Bash"). Logic lives in
         hooks/shared/evidence/ and hooks/shared/commit_rules.py.

Rails: the lead can always accept over a check (completion is never checked here); every
return and note is logged (checks/shadow.jsonl, kind write_gate) and repact-report counts the
returns. Any internal error allows the call.
Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2; a HANDOFF
        note = additionalContext; otherwise
        {"suppressOutput": true}, exit 0.
"""
from __future__ import annotations

import hashlib
import json
import sys

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
COMMITS_TEAM, COMMITS_TASK = "_commits", "commits"


def load_evidence():
    from shared import evidence
    return evidence


def task_gate(payload: dict, ev) -> tuple[str | None, str | None]:
    """(deny reason, note) for a TaskUpdate writing a HANDOFF: a note at most, never a deny."""
    tool_input = payload.get("tool_input") or {}
    metadata = tool_input.get("metadata")
    task_id = str(tool_input.get("taskId") or "")
    if not isinstance(metadata, dict) or not task_id:
        return None, None
    handoff = metadata.get("handoff")
    if not isinstance(handoff, dict):
        return None, None
    uses = [use for use in ("handoff_evidence", "uncertainty_disposition")
            if ev.use_mode(use) in ev.SPEAKING_MODES]
    team = ev.resolve_team(payload.get("session_id"), task_id) if uses else None
    task = ev.load_task(ev.task_path(team, task_id)) if team else None
    if task is None:
        return None, None
    return None, handoff_notes(payload, ev, uses, team, task_id, task, handoff)


def handoff_notes(payload: dict, ev, uses: list[str], team: str, task_id: str, task: dict,
                  handoff: dict) -> str | None:
    """The HANDOFF checks, which never return (1.4.1: run evidence by design, the
    uncertainty disposition by U11's measurement). Each flagged item is a note for the lead at
    acceptance, replacing the use's notes of the previous write (an empty list clears them);
    the writer gets one sentence per use, on one line. Returns it, or None."""
    writer_lines = []
    for use in uses:
        if use == "handoff_evidence":
            result = ev.handoff_gate(task, team, handoff, cwd=payload.get("cwd"),
                                     session_id=payload.get("session_id"))
            ev.set_gate_notes(team, task_id, ev.HANDOFF_ADVISORY, result.get("advisories", []))
            writer_lines.append(ev.evidence_note(task_id, result["flagged"]))
        else:
            result = ev.uncertainty_gate(handoff)
            writer_lines.append(ev.uncertainty_note(task_id, result["flagged"]))
        flagged = result["flagged"]
        ev.set_gate_notes(team, task_id, use, [f["line"] for f in flagged])
        if flagged:
            ev.shadow_log({"kind": "write_gate", "use": use, "event": "note", "team": team,
                           "task_id": task_id, "keys": [f["key"] for f in flagged]})
    return " ".join(filter(None, writer_lines)) or None


def commit_gate(payload: dict, ev) -> tuple[str | None, str | None]:
    """(deny reason, note) for a Bash `git commit` whose message breaks the commit rules."""
    if ev.use_mode("commit_rule") != "gate":
        return None, None
    from shared import commit_rules as rules
    command = str((payload.get("tool_input") or {}).get("command") or "")
    message = rules.commit_message(command, payload.get("cwd"))
    # A list, even an empty one, is the configured words; only a missing or malformed key
    # falls back to the shipped words.
    words = (ev.load_constants().get("commit_rule") or {}).get("deny_words")
    if not isinstance(words, list) or not all(isinstance(word, str) for word in words):
        words = rules.DEFAULT_DENY_WORDS
    found = rules.problems(message, words)
    if rules.built_at_run_time(command, payload.get("cwd")):
        found.append("part of it is built by the shell at run time, so it cannot be read first "
                     "(write it literally, in single quotes)")
    if not found:
        return None, None
    # Every time, with no return-once rail (1.4.1): a commit is permanent and
    # the fix is one line, so the same message sent again is returned again.
    line = "; ".join(found)
    ev.shadow_log({"kind": "write_gate", "use": "commit_rule", "event": "return",
                   "team": COMMITS_TEAM, "task_id": COMMITS_TASK,
                   "keys": [hashlib.sha256((message or command).encode("utf-8")).hexdigest()[:12]]})
    from shared.team_policy import refusal_note
    return ("Commit returned: the message breaks the commit rule (one sentence, no "
            f"body, no AI attribution, no security-tooling words): {line}. Reword it and commit "
            "again; the same message is returned every time."
            + refusal_note(("modes.commit_rule", "commit_rule."))), None


def decide(payload: dict) -> tuple[str | None, str | None]:
    tool = payload.get("tool_name")
    if tool not in ("TaskUpdate", "Bash"):
        return None, None
    ev = load_evidence()
    return task_gate(payload, ev) if tool == "TaskUpdate" else commit_gate(payload, ev)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason, note = decide(payload) if isinstance(payload, dict) else (None, None)
    except Exception as error:   # noqa: BLE001 - fail-open: a gate that cannot run decided nothing
        print(f"write_checks: internal error, allowing (fail-open): {type(error).__name__}", file=sys.stderr)
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))
        print(reason, file=sys.stderr)
        return 2
    if note:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}))
        return 0
    print(_ALLOW_OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
