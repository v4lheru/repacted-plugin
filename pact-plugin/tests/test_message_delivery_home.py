"""The Communication Charter's Part I (message delivery mechanics) has one home: lead-reference.md
§Message Delivery (CLEANUP-A). Every rule it held moved verbatim; the charter keeps only forwarding
stubs under the old headings, so a link to a charter anchor still lands on a pointer.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROTOCOLS = Path(__file__).resolve().parent.parent / "protocols"
LEAD_REFERENCE = PROTOCOLS / "lead-reference.md"
CHARTER = PROTOCOLS / "pact-communication-charter.md"

# The charter's Part I, rule by rule, as it stood before the move (the before-inventory).
RULES = [
    'SendMessage(to="teammate-name", message="[sender→recipient] ...", summary="5-10 word preview")',
    "Messages are queued-async, delivered at the recipient's next idle boundary.",
    "Agents read queued messages in FIFO order on reaching idle.",
    "No cancellation primitive exists — a follow-up message cannot supersede a queued earlier one.",
    "The only mid-turn interrupt mechanism is user-side (Escape). Agent-to-agent `SendMessage` has no equivalent.",
    "`SendMessage` requires a specific `to=` recipient name. There is no broadcast addressing mode; reaching "
    "multiple teammates means iterating and sending one message per recipient (see [Lead-Side HALT Fan-Out]"
    "(algedonic.md#lead-side-halt-fan-out) for the canonical pattern).",
    "> Moved: see agents/pact-orchestrator.md §1.",
    "> Moved: see agents/pact-orchestrator.md §1 (lead) and skills/teammate/references/teammate-messaging.md "
    "§Pre-Send Self-Check (teammate).",
    "> Moved: see skills/teammate/references/teammate-messaging.md.",
    "A HALT is an `amend_<n>` (kind `halt`): a busy teammate gets it after its next tool call.",
    "For immediate halt of in-flight teammate work, user-side manual interrupt is required.",
    "The team-lead's responsibility to \"surface immediately\" means at the team-lead's next idle, not at "
    "arbitrary real-time.",
]
SUBSTANTIVE = [r for r in RULES if not r.startswith("> Moved")]
ANCHORS = ["delivery-model", "algedonic-signal-latency-caveat", "pre-send-self-check",
           "lead-side-discipline--verify-before-dispatching"]


def slugs(text: str) -> set[str]:
    out = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        out.add(slug)
    return out


@pytest.mark.parametrize("rule", RULES, ids=[r[:40] for r in RULES])
def test_each_part_i_rule_lives_once_in_lead_reference(rule):
    assert LEAD_REFERENCE.read_text(encoding="utf-8").count(rule) == 1


@pytest.mark.parametrize("rule", SUBSTANTIVE, ids=[r[:40] for r in SUBSTANTIVE])
def test_the_charter_no_longer_holds_the_rule(rule):
    assert rule not in CHARTER.read_text(encoding="utf-8")


def test_the_rules_sit_under_message_delivery():
    text = LEAD_REFERENCE.read_text(encoding="utf-8")
    section = text.split("\n## Message Delivery\n", 1)[1].split("\n## ", 1)[0]
    assert all(rule in section for rule in RULES)


def test_every_old_charter_anchor_still_resolves_and_the_new_ones_exist():
    assert set(ANCHORS) <= slugs(CHARTER.read_text(encoding="utf-8"))
    assert {"message-delivery", "delivery-model", "algedonic-signal-latency-caveat"} <= slugs(
        LEAD_REFERENCE.read_text(encoding="utf-8"))


def test_the_charter_part_i_forwards_to_the_new_home():
    text = CHARTER.read_text(encoding="utf-8")
    part_i = text.split("## Part I — Message Delivery Mechanics", 1)[1].split("## Part II", 1)[0]
    assert "(lead-reference.md#message-delivery)" in part_i
    assert "(lead-reference.md#algedonic-signal-latency-caveat)" in part_i
