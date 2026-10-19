#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/write_checks.py (facts decide)
Summary: PreToolUse(Bash) check that asks no model: a `git commit` whose message, in mode
           commit_rule "gate", carries AI attribution, a body, more than one sentence, or a
           denylisted word (hooks/shared/commit_rules.py; words in bin/checks.json
           commit_rule.deny_words). The commit is RETURNED (a deny carrying the reason) EVERY
           time it is issued (1.4.1 U12: the return-once rail let the same commit through on its
           second try). The TaskUpdate checks this file held until 2.1.0 are gone: the workflow
           writes go through the `repact` commands, and
           task_write_guard keeps them there.
Used by: hooks/dispatch_registry.py (script check, matcher "Bash"). Logic lives in
         hooks/shared/commit_rules.py; modes and words come from hooks/shared/check_modes.py.

Rails: every return is logged (checks/shadow.jsonl, kind write_gate) and repact-report counts
the returns. An internal error allows the call (fail-open: a commit-style rule is no security
check); the registry still denies when this hook's alarm fires.
Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2;
        otherwise {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import hashlib
import json
import sys

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
COMMITS_TEAM, COMMITS_TASK = "_commits", "commits"


def load_evidence():
    from shared import check_modes
    return check_modes


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
    if payload.get("tool_name") != "Bash":
        return None, None
    return commit_gate(payload, load_evidence())


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason, _note = decide(payload) if isinstance(payload, dict) else (None, None)
    except Exception as error:   # noqa: BLE001 - fail-open: a gate that cannot run decided nothing
        print(f"write_checks: internal error, allowing (fail-open): {type(error).__name__}", file=sys.stderr)
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))
        print(reason, file=sys.stderr)
        return 2
    print(_ALLOW_OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
