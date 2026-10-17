"""dispatch_gate refuses to spawn a teammate named like the lead (CLEANUP-B item B, #180 review S1).

An in-process teammate's agent_type is its member NAME, so a teammate named `pact-orchestrator`
would pass is_lead and the D10 owner guard. The spawn is the one place the name enters, so the
gate makes that name unrepresentable there. Lead ruling (amend_1 on #192), compared casefolded
after NFKC:
  (1) the name equals a member of pact_context.LEAD_AGENT_TYPES (read, never copied);
  (2) its last ':' segment equals the last segment of a member;
  (3) it is 'team-lead', the lead's member name.
The check runs before the name regex, so it holds even if the regex is ever relaxed.
"""
from __future__ import annotations

import pytest

import shared.pact_context as pact_context
from dispatch_gate import evaluate_dispatch

RULE = "name_lead_identity"


def spawn(name: str) -> dict:
    return {"subagent_type": "pact-architect", "name": name,
            "prompt": "Check TaskList for tasks assigned to you."}


@pytest.mark.parametrize("name", [
    "pact-orchestrator",                 # (1) the bare member
    "repacted:pact-orchestrator",        # (1) the namespaced member
    "REPACTED:PACT-ORCHESTRATOR",        # (1) casefolded
    "Pact-Orchestrator",                 # (2) case variant of the last segment
    "other:pact-orchestrator",           # (2) any namespace, same last segment
    "ｐａｃｔ-orchestrator",  # (2) fullwidth, NFKC-normalised first
    "team-lead",                         # (3) the lead's member name
    "Team-Lead",                         # (3) casefolded
], ids=["bare", "namespaced", "upper", "mixed-case", "other-ns", "fullwidth", "team-lead", "Team-Lead"])
def test_a_lead_identity_name_is_refused_by_its_own_rule(name):
    decision, reason, rule = evaluate_dispatch(spawn(name))
    assert (decision, rule) == ("DENY", RULE), (decision, rule, reason)
    assert "reserved-token" in reason


@pytest.mark.parametrize("name", ["architect-core", "orchestrator-notes", "pact-orchestrator-2", "lead-reviewer"])
def test_an_ordinary_name_is_not_refused_as_the_lead(name):
    _decision, _reason, rule = evaluate_dispatch(spawn(name))
    assert rule != RULE


def test_the_rule_reads_the_lead_agent_types_constant(monkeypatch):
    """A new lead spelling added to LEAD_AGENT_TYPES is refused with no edit to the gate."""
    monkeypatch.setattr(pact_context, "LEAD_AGENT_TYPES",
                        frozenset(pact_context.LEAD_AGENT_TYPES | {"repacted:" "pact-conductor"}))
    assert evaluate_dispatch(spawn("pact-conductor"))[2] == RULE


# --- rev 2 (architect-pact R1): the refusal holds for EVERY spawn that carries a name ---------
# The carve-outs (SOLO_EXEMPT research agents, and non-pact-* types) used to ALLOW before the
# name was looked at, so a general-purpose or custom-typed spawn could take a lead name.

@pytest.mark.parametrize("subagent_type, name", [
    ("general-purpose", "pact-orchestrator"),
    ("general-purpose", "team-lead"),
    ("Explore", "team-lead"),
    ("Plan", "Pact-Orchestrator"),
    ("claude", "team-lead"),
    ("some-custom-agent", "pact-orchestrator"),
    ("general-purpose", "other:pact-orchestrator"),
], ids=["gp-orchestrator", "gp-team-lead", "explore-team-lead", "plan-mixed-case", "claude-team-lead",
        "custom-orchestrator", "gp-namespaced"])
def test_a_lead_name_is_refused_on_a_non_pact_spawn_too(subagent_type, name):
    decision, _reason, rule = evaluate_dispatch({"subagent_type": subagent_type, "name": name,
                                                 "prompt": "look something up"})
    assert (decision, rule) == ("DENY", RULE)


@pytest.mark.parametrize("tool_input", [
    {"subagent_type": "general-purpose", "prompt": "look something up"},
    {"subagent_type": "general-purpose", "name": "researcher", "prompt": "look something up"},
    {"subagent_type": "Explore", "name": "", "prompt": "find it"},
    {"subagent_type": "some-custom-agent", "name": "helper", "prompt": "do it"},
], ids=["gp-nameless", "gp-ordinary", "explore-empty-name", "custom-ordinary"])
def test_a_research_or_custom_spawn_without_a_lead_name_still_passes(tool_input):
    assert evaluate_dispatch(tool_input) == ("ALLOW", None, None)
