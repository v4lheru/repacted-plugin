"""The `agent_handoff` ordering rule rests on a CALLER property. Enforce it.

WHAT RESTS ON THIS. The harvest resolves a multi-event `agent_handoff` group
by taking the latest `ts`, and `session_resume` does the same when it renders
its Completed Work summary. Both compare `ts` AS A STRING. A string compare
orders timestamps correctly only while every event of the family carries ONE
format.

WHY THAT IS A CALLER PROPERTY AND NOT A SCHEMA PROPERTY, WHICH IS THE WHOLE
CAUSE FOR THIS FILE. `session_journal.make_event` stamps its own timestamp
through `setdefault("ts", ...)`, so A CALLER-SUPPLIED `ts` IS PRESERVED. The
schema does not reject one and nothing warns. The one-format property
thus belongs to the CALLERS, and it holds today only because the one
`agent_handoff` emit path (`repact accept`) passes no `ts` of its own.

THE FAILURE IS SILENT AND IT RUNS IN EITHER DIRECTION. `make_event` stamps a
Z-suffix form and `canonical_since()` emits an offset form. MEASURED:
`ord('+')` is 43 and `ord('Z')` is 90, so for two spellings of ONE instant
the offset form sorts as OLDER. AND THE COMMON SHORT FORM OF THIS HAZARD,
that an offset form always sorts as older, IS TOO STRONG: a string compare
walks left to right, so the DATETIME PREFIX decides first and the suffix
decides nothing until every byte before it is equal. A non-UTC offset naming
the same instant carries a different hour and sorts as NEWER. The accurate
statement is that a mixed-format set is ordered BY BYTES AND NOT BY TIME.
Either way the latest-wins rule can keep the incorrect event, and no test in
the family reddens.

WHAT THIS FILE DOES AND DOES NOT DO. It converts correct-by-a-caller-property
into the caller property ENFORCED: a future edit that passes a `ts` to either
emit path turns this RED at the moment it is written. It does NOT make the
comparison safe against a mixed set.

🔴 THE LOAD-BEARING ASSERTION IN THIS FILE IS THE CALLER-PROPERTY ARM, AND
THE BYTE-RULE ARM IS AN EXPLANATION OF WHAT IS LOST IF THAT PROPERTY GOES.
Read them in that order. If a caller ever does pass a `ts`, the answer is to
PARSE the two values, not to reach for a rule about which byte decides. That
instruction is the one part of this warning that has needed no correction
across three rounds of review: a claim, then a rule replacing the claim, then
a precondition replacing the rule, each complete across its own list of
spellings and each broken by the next probe.
"""

import ast
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"

# The agent_handoff emit path: `repact accept` journals the accepted HANDOFF through
# sides.journal, which hands its fields to make_event. Named rather than derived: this is a
# claim ABOUT this file, so discovering it from the property this file tests would make the
# assertion agree with whatever it found.
EMIT_PATHS = (
    _HOOKS_DIR / "shared" / "ops" / "accept.py",
)
_EMIT_CALLS = ("make_event", "journal")

EVENT_TYPE = "agent_handoff"


def _agent_handoff_make_event_calls(source_path: Path) -> list:
    """Every `make_event("agent_handoff", ...)` or `sides.journal(ctx, "agent_handoff", ...)` call
    in one module."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in _EMIT_CALLS:
            continue
        if any(isinstance(arg, ast.Constant) and arg.value == EVENT_TYPE for arg in node.args[:2]):
            calls.append(node)
    return calls


class TestNeitherEmitPathSuppliesATimestamp:
    """The caller property the string compare rests on."""

    @pytest.mark.parametrize("source_path", EMIT_PATHS, ids=lambda p: p.name)
    def test_no_agent_handoff_emit_passes_ts(self, source_path):
        calls = _agent_handoff_make_event_calls(source_path)
        for call in calls:
            keywords = sorted(k.arg for k in call.keywords if k.arg)
            assert "ts" not in keywords, (
                f"{source_path.name} passes `ts` to make_event for an "
                f"{EVENT_TYPE} event. make_event uses setdefault, so that "
                f"value is PRESERVED, and a second timestamp format breaks "
                f"the string compare the latest-wins rule uses. A mixed "
                f"set then orders BY BYTES and not by time, in either "
                f"direction, and the newest write can lose in silence. "
                f"Keywords found: {keywords}"
            )


class TestTheInstrumentAndThePremise:
    """The two facts the arms above rest on, each shown able to fail."""

    def test_make_event_preserves_a_caller_supplied_ts(self):
        """The premise. If make_event ever OVERWROTE `ts`, the property
        above would stop mattering and this file could retire."""
        from shared.session_journal import make_event

        supplied = "2020-01-01T00:00:00+00:00"
        event = make_event(EVENT_TYPE, ts=supplied)
        assert event["ts"] == supplied, (
            "make_event does not preserve a caller ts at this time. The caller "
            "property this file enforces is not load-bearing at this time, and "
            "this file and the prose adjacent to the ordering helper want a "
            "re-read together."
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
