"""
Location: pact-plugin/tests/test_variety_rationale_contamination.py

Variety rationales are read by a teammate BEFORE it starts work, so a rationale
carrying the dispatch's hypothesis hands a seat the thing it may exist not to
know. These arms pin the constraint at both places it has to hold: the
placeholder a lead fills in, and the schema block that defines it.

POPULATION, not a fixed list: the placeholder arm walks every `commands/*.md`
and both protocol files, so a template added later is covered on the day it
arrives rather than when someone remembers this file.
"""

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
# the protocol consolidation: the variety protocol lives once, in workflows.md §Variety (the SSOT copy is deleted).
VARIETY = PLUGIN / "protocols" / "workflows.md"

# A placeholder a lead fills in. The suffix is the constraint; a bare one is the defect.
_PLACEHOLDER = re.compile(r"why this score for THIS dispatch's (?:novelty|scope|uncertainty|risk)")

_RULE = "Do not write the dispatch's hypothesis, its expected answer, or the reasoning that produced it."


def _surfaces():
    # CLEANUP-B: the dispatch templates moved into reference/dispatch/; the writers read them there.
    return sorted(PLUGIN.glob("commands/*.md")) + sorted(PLUGIN.glob("reference/dispatch/*.md")) + [VARIETY]


def test_no_bare_rationale_placeholder_survives():
    """Every writer-side placeholder carries the constraint inline.

    "why this score" solicits causal reasoning, and the honest causal answer is
    often the belief itself — so the prompt must not ask it at all.
    """
    bare = {
        p.relative_to(PLUGIN).as_posix(): len(_PLACEHOLDER.findall(p.read_text(encoding="utf-8")))
        for p in _surfaces()
    }
    offenders = {k: v for k, v in bare.items() if v}
    assert not offenders, f"causal-prompt rationale placeholders: {offenders}"


def test_constraint_reaches_the_writer():
    """Non-vacuity: the population is non-empty and does carry placeholders.

    Without this, the arm above passes on a glob that matches nothing.

    THE FLOOR IS ARBITRARY HEADROOM, NOT A DERIVED COUNT. Its only job is to
    prove the glob is live. It is deliberately far below the real population
    so that legitimately removing a template or consolidating a dispatch block
    does not redden it — this file pins that no placeholder solicits causal
    reasoning, not how many placeholders exist. Do not tighten it to track the
    census: a count that carries no meaning teaches the next reader that it
    does.
    """
    suffixed = sum(
        t.count("never what you expect to find")
        for t in (p.read_text(encoding="utf-8") for p in _surfaces())
    )
    # COMMANDS (m8b) lowered 20 -> 4: consolidating dispatch blocks, as the
    # docstring anticipates, took the population to 16 and orchestrate's slim
    # takes it lower. 4 = one complete stamp template, which still proves the
    # glob reaches a real one.
    assert suffixed >= 4, f"expected the placeholder population, found {suffixed}"



TEACHBACK_SKILL = PLUGIN / "skills" / "pact-teachback" / "SKILL.md"

# The three surfaces that state when a teammate may read the rationales.
_ORDERING_SITES = (VARIETY, TEACHBACK_SKILL)

# The lead-side re-stamp decision point. THREE surfaces, TWO edit-units: the
# persona carries its own wording, VARIETY and PROTOCOLS are the byte-mirrored
# pair. A clause landing on the persona alone leaves the protocol pair still
# instructing the re-stamp, which is how the seat-side fix was reached with the
# lead side left open.
ORCHESTRATOR = PLUGIN / "agents" / "pact-orchestrator.md"
WRAP_UP = PLUGIN / "commands" / "wrap-up.md"
_RESTAMP_SITES = (ORCHESTRATOR, VARIETY)









