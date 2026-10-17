#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/merge_question_gate.py
Summary: PreToolUse hook (matcher: AskUserQuestion). Refuses a question that names a
    merge-class command (merge, close, force-push, branch delete) when no option of it
    could approve that command, and says which labels approve and where the command
    goes. Without it the user clicks, nothing is approved, and the lead learns it only
    after the click.
Used by: hooks/dispatch_registry.py PreToolUse "AskUserQuestion" entry, beside
    unattended_gate.

It asks merge_guard_post's own mint function, once per option, "would a click on this
option approve anything?", so it cannot disagree with the hook that really mints. It
writes no approval and calls no write_token: a question it lets through still approves
only through merge_guard_post, on the user's real answer, and merge_guard_pre still
reads the approval before the command runs.

Fail direction: OPEN. Stdin that is not a JSON object, a payload it cannot read, more
options than the tool can show, and any internal error (merge_guard_post failing to
load included) allow the question. This check can only withhold a question, so letting
one through approves nothing; failing closed would block every question on a bug.

Input: JSON on stdin, {"tool_name": "AskUserQuestion", "tool_input": {...}, "cwd": ...}
Output: deny = the JSON envelope on stdout, the reason on stderr, exit 2;
        allow = {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

_hooks_dir = Path(__file__).resolve().parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
_TOOL_NAME = "AskUserQuestion"
# AskUserQuestion shows at most 4 questions of 4 options. A payload far beyond that is not
# one the tool would show, and trying every option of it is unbounded work.
MAX_OPTIONS_TRIED = 64
_NO_CODE_REASON = (
    "no option of a single-select question names the command (a multiSelect question and "
    "a free-text answer never approve)")


def _load_mint():
    """merge_guard_post, the module that mints. On a load failure it prints a PostToolUse
    envelope and exits; neither belongs to this hook, so its output is swallowed here and
    the exit reaches main as an error (allow)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import merge_guard_post
    return merge_guard_post


def _option_answers(questions: list):
    """One (question text, label) per option, keyed as merge_guard_post keys an answer."""
    for question in questions:
        if not isinstance(question, dict):
            continue
        text = question.get("question", "")
        text = text if isinstance(text, str) else ""
        options = question.get("options", [])
        for option in options if isinstance(options, list) else []:
            label = option.get("label") if isinstance(option, dict) else None
            if isinstance(label, str):
                yield text, label


def unapprovable_reason(questions: list, mint) -> str | None:
    """Why no option of a command-naming bundle could approve anything, or None when the
    bundle names no merge-class command or at least one option would approve."""
    if not mint._bundle_has_command(questions):
        return None
    codes: list[tuple[bool, str]] = []
    for tried, (text, label) in enumerate(_option_answers(questions), start=1):
        if tried > MAX_OPTIONS_TRIED:
            return None
        contexts, refusals = mint._mint_contexts_from_bundle(questions, {text: label})
        if contexts:
            return None
        codes.extend((not mint.is_approval_label(label), code) for code in refusals)
    # The refusal of an option that already carries an approving label says most.
    code = min(codes)[1] if codes else None
    return mint._REFUSAL_DIAGNOSTICS.get(code, _NO_CODE_REASON)


def decide(payload: dict) -> str | None:
    """The refusal text for one payload, or None to allow."""
    if payload.get("tool_name") != _TOOL_NAME:
        return None
    tool_input = payload.get("tool_input")
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions:
        return None
    mint = _load_mint()
    # The repository a command acts on is resolved from the payload cwd, as the mint does.
    if isinstance(payload.get("cwd"), str) and payload["cwd"]:
        mint.set_invocation_cwd(payload["cwd"])
    why = unapprovable_reason(questions, mint)
    if why is None:
        return None
    return (
        "Blocked before the question is shown: it names a merge-class command, and " + why
        + ". Nothing would be approved by any option. Give the approving option "
        + ("one of those labels" if mint.APPROVAL_LABELS in why else mint.APPROVAL_LABELS)
        + " with the literal command in its description, or drop the "
        "command text if you are not asking for approval. Form: one single-select question, "
        'an option labelled "Yes, merge" whose description is "Run `<the command>`", and '
        'a second option such as "Not yet".'
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decide(payload) if isinstance(payload, dict) else None
    except (Exception, SystemExit) as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print("merge_question_gate: internal error, allowing (the question is asked, and it "
              f"approves nothing by itself): {type(error).__name__}", file=sys.stderr)
        print(_ALLOW_OUTPUT)
        return 0
    if reason is None:
        print(_ALLOW_OUTPUT)
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
