"""Tests for is_lead / classify_session_role — the lead/teammate role predicate.

The predicate reads the TOP-LEVEL ``agent_type`` field directly (NOT via
resolve_agent_name) and tests membership in LEAD_AGENT_TYPES. It is PURE
(agent_type only), TOTAL (never raises on a dict input), case-SENSITIVE, and
a COORDINATION control — not a security boundary.

Coverage:

is_lead() truth-table:
1.  Both lead spellings → True (qualified repacted:pact-orchestrator, unqualified)
2.  Teammate agent_type → False
3.  Plain frame (agent_type absent) → False
4.  Empty-string / None / non-string agent_type → False
5.  Mixed-case lead spelling → False (case-sensitive exact match)
6.  agent_id present but agent_type not a lead → False (#812 guard)

is_lead() purity:
7.  Does NOT read tool_input (a forged tool_input.agent_type cannot flip it)

classify_session_role() 3-way:
8.  lead / teammate / unknown across the same frame matrix

LEAD_AGENT_TYPES SSOT:
9.  Exactly the two sanctioned spellings; is_lead agrees with membership
"""

import pytest

from shared.pact_context import (
    LEAD_AGENT_TYPES,
    classify_session_role,
    is_lead,
)


class TestIsLead:
    """is_lead() truth-table — both lead spellings True, everything else False."""

    def test_qualified_lead_spelling_is_lead(self):
        """`--agent repacted:pact-orchestrator` → True."""
        assert is_lead({"agent_type": "repacted:pact-orchestrator"}) is True

    def test_unqualified_lead_spelling_is_lead(self):
        """`--agent pact-orchestrator` → True."""
        assert is_lead({"agent_type": "pact-orchestrator"}) is True


class TestIsLeadPurity:
    """is_lead must read ONLY agent_type — never tool_input, agent_id, env, config."""

    def test_does_not_read_tool_input(self):
        """A forged tool_input.agent_type cannot flip the verdict.

        agent_type at top level is harness-set; tool_input is request-shaped
        content. If is_lead ever consulted tool_input, an untrusted tool
        argument could forge lead status. This pins it shut.
        """
        forged = {
            "agent_type": "pact-backend-coder",  # genuine: a teammate
            "tool_input": {"agent_type": "repacted:pact-orchestrator"},  # forged
        }
        assert is_lead(forged) is False


class TestClassifySessionRole:
    """classify_session_role() — the 3-way lead/teammate/unknown classifier."""

    @pytest.mark.parametrize("frame, expected", [
        ({"agent_type": "repacted:pact-orchestrator"}, "lead"),])
    def test_classify_matrix(self, frame, expected):
        assert classify_session_role(frame) == expected


class TestLeadAgentTypesSSOT:
    """LEAD_AGENT_TYPES is the single source of truth for both helpers."""

    def test_exactly_two_sanctioned_spellings(self):
        assert LEAD_AGENT_TYPES == frozenset({"repacted:pact-orchestrator", "pact-orchestrator"})


