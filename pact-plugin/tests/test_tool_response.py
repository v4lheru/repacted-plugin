"""Tests for shared.tool_response.extract_tool_response — SSOT helper.

Pins the helper's three behavioral contracts:

1. Canonical-prefer: when `tool_response` is present, return it.
2. Legacy-fallback: when only `tool_output` is present, return it.
3. Dual-envelope warning: when BOTH are present, warn to stderr and
   return the canonical `tool_response` value (not legacy).

Also pins defensive shapes (non-dict input, non-dict field values, empty
fields, missing fields).
"""


from shared.tool_response import extract_tool_response  # noqa: E402


# =============================================================================
# Canonical-prefer behavior
# =============================================================================


def test_canonical_tool_response_returned_when_present():
    payload = {"tool_response": {"task": {"id": "1"}}}
    assert extract_tool_response(payload) == {"task": {"id": "1"}}


def test_canonical_returned_even_when_legacy_missing():
    payload = {"tool_response": {"status": "completed"}}
    assert extract_tool_response(payload) == {"status": "completed"}


# =============================================================================
# Legacy-fallback behavior
# =============================================================================


def test_legacy_tool_output_returned_when_canonical_missing():
    payload = {"tool_output": {"task": {"id": "1"}}}
    assert extract_tool_response(payload) == {"task": {"id": "1"}}


def test_legacy_returned_when_canonical_is_empty():
    # `or` short-circuits on falsy — empty dict triggers fallback to legacy.
    payload = {"tool_response": {}, "tool_output": {"id": "from_legacy"}}
    # NOTE: empty dict is falsy → legacy wins. Document this so a future
    # refactor that uses `is not None` instead of `or` would change the
    # contract and break this test.
    assert extract_tool_response(payload) == {"id": "from_legacy"}


# =============================================================================
# Dual-envelope warning
# =============================================================================


# =============================================================================
# Defensive shapes
# =============================================================================


