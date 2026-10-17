"""
Location: pact-plugin/hooks/_fail_closed.py
Summary: The one fail-closed deny emitter for a PreToolUse check whose imports or
         gate logic fail. Stdlib only (json, sys): it must work when every other
         import in the calling hook has failed, which is why it is a top-level
         sibling of the hooks and not under shared/ (importing shared.anything runs
         shared/__init__.py, the very import graph this helper outlives). Prints the
         deny envelope with hookEventName (the #658 invariant: an empty stdout or a
         non-2 exit is an allow), one stderr line, then exits 2. Every rendering step
         is guarded: a hostile __str__ or a broken stderr must not turn the deliberate
         exit 2 into a traceback exit 1, which PreToolUse treats as non-blocking.
Used by: bootstrap_gate, dispatch_gate, worktree_guard, git_commit_check,
         merge_guard_pre, shared/dispatch_helpers (each imports it before its own
         import gauntlet); tests/contract/test_shell_package.py pins the single
         definition.
Map: bounded_error_text, emit_load_failure_deny.
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn

# Reason text is context-bound (it can carry attacker-influenced content): cap the
# rendered exception before sanitising it; stderr keeps the full text.
_ERROR_TEXT_MAX = 200


def bounded_error_text(error: BaseException) -> str:
    """Sanitised, length-bounded `Type: text` rendering of an exception.

    Total over hostile exceptions: a metaclass `__name__` that raises or returns a
    str subclass, and a `__str__` that raises, each fall back to a placeholder so
    no branch can raise. Bounded BEFORE the O(n) sanitise join, so a multi-GB
    message never materialises a sanitised copy.
    """
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile metaclass __name__ must not escape
        type_name = "exception"
    if type(type_name) is not str:
        type_name = "exception"
    try:
        text = f"{type_name}: {error}"
    except BaseException:  # noqa: BLE001 — hostile __str__ must not escape the renderer
        text = f"{type_name}: <exception str() raised>"
    truncated = len(text) > _ERROR_TEXT_MAX
    if truncated:
        text = text[:_ERROR_TEXT_MAX]        # bound BEFORE the O(n) sanitize join
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    if truncated:
        text = text + "...[truncated]"
    return text


def emit_load_failure_deny(check: str, stage: str, error: BaseException) -> NoReturn:
    """Print the PreToolUse deny envelope for `check` failing at `stage`, then exit 2.

    `check` is the hook's module name (`bootstrap_gate`), `stage` the phase that
    failed (`module imports`, `runtime`). The reason carries the bounded error text;
    the stderr line carries the full text, guarded, for the operator.
    """
    try:
        error_render = bounded_error_text(error)
    except BaseException:  # noqa: BLE001 — the deny print must happen whatever the error does
        error_render = "<error text unavailable>"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT {check} {stage} failure — blocking for safety. "
                f"{error_render}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-2 path
        error_full = "<exception str() raised>"
    try:
        print(f"Hook load error ({check} / {stage}): {error_full}", file=sys.stderr)
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    sys.exit(2)
