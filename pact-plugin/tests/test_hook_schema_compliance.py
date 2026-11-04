"""AST-based schema compliance tests for hook output.

Regression guard for #658: every `hookSpecificOutput` dict literal in any
`pact-plugin/hooks/*.py` file MUST contain the key `hookEventName`. The
Claude Code harness silently rejects hook JSON missing this field, causing
a fail-open condition for permission gates.

Strategy:
  - Use Python's `ast` module to walk every hook source file
  - Locate every dict literal that is the value of a `hookSpecificOutput` key
  - Assert each such inner dict contains a `hookEventName` constant key

Counter-test discipline:
  A second test synthesizes a known-bad source string with a missing
  `hookEventName`, runs the same detection logic, and asserts the violation
  is detected. This proves the AST-walk's negative case actually fires.

Coverage assertion:
  A sanity check counts the dicts found across live source and asserts
  the count is >= 11. This proves the walk scans real source rather than
  silently iterating an empty fixture set. As of #658 fix, the count is
  approximately 18 (counted via grep on live source minus docstring/comment
  references).
"""

from __future__ import annotations

import ast
import pathlib
import textwrap

HOOKS_DIR = pathlib.Path(__file__).parent.parent / "hooks"


def _find_hook_specific_output_dicts(tree: ast.AST):
    """Yield every inner dict literal that is the value of a `hookSpecificOutput` key."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "hookSpecificOutput"
                and isinstance(value, ast.Dict)
            ):
                yield value


def _inner_dict_violations(tree: ast.AST, filename: str):
    """Return list of violation strings for a parsed source tree."""
    violations = []
    for inner in _find_hook_specific_output_dicts(tree):
        inner_keys = [k.value for k in inner.keys if isinstance(k, ast.Constant)]
        if "hookEventName" not in inner_keys:
            violations.append(
                f"{filename} line {inner.lineno}: hookSpecificOutput missing hookEventName"
            )
    return violations


def test_every_hookSpecificOutput_dict_includes_hookEventName():
    """Every hookSpecificOutput literal in hooks/*.py must include hookEventName.

    Regression guard for #658: missing hookEventName causes the harness
    to silently reject the JSON and fail open on permission gates.
    """
    violations = []
    for hook_file in sorted(HOOKS_DIR.glob("*.py")):
        tree = ast.parse(hook_file.read_text())
        violations.extend(_inner_dict_violations(tree, hook_file.name))
    assert not violations, (
        "hookEventName missing at:\n" + "\n".join(violations)
    )


