"""Contract, TEAMMATE PARITY: the brief and the teammate pages name every field and key the checks read.

Since 2.1 the happy path is the brief plus `repact submit`: the brief carries the teachback and
HANDOFF skeletons, SKILL.md the steps, and the field rules live in teammate-teachback.md,
teammate-handoff.md and (Teams mode) teammate-waiting.md. A name a check reads and none of them
states is a rejection in the field. The expected names are read from the schema constants; the few
a hook reads as a bare literal are listed in LITERALS with the function that reads them.
"""
from __future__ import annotations

from shared import amendments
from shared.background_work import WAIT_ANCHOR_KEY
from shared.handoff_schema import DISPOSITION, HANDOFF_CANONICAL_FIELDS
from shared.intentional_wait import KNOWN_REASONS, KNOWN_RESOLVERS
from shared.teachback_schema import (
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
)

from shared.ops.brief import handoff_skeleton, teachback_skeleton

from tests.contract._surfaces import PLUGIN_ROOT, read

SKILL = PLUGIN_ROOT / "skills" / "teammate" / "SKILL.md"
REFS = PLUGIN_ROOT / "skills" / "teammate" / "references"
PAGES = (SKILL, REFS / "teammate-teachback.md", REFS / "teammate-handoff.md",
         REFS / "teammate-waiting.md")

# Names a hook reads as a literal, with no constant to import.
LITERALS = {
    "teachback_schema (variety_acknowledgment)": ("rationale_articulates_this_dispatch", "concern"),
    "teachback_schema / intentional_wait (sibling keys)": ("reasoning_reconstruction", "intentional_wait"),
    "amendments.handoff_rev": ("rev",),
    "amendments.missing_dispositions": ("amendments",),
    "intentional_wait.validate_wait": ("reason", "expected_resolver", "since"),
}


def named(text: str, name: str) -> bool:
    """The name as a token of its own: in backticks or in quotes, never as part of a longer word."""
    return f"`{name}`" in text or f'"{name}"' in text


# The uncertainty dispositions handoff_schema.DISPOSITION reads (submit's validation).
UNCERTAINTY_LABELS = ("fixed", "known-limit", "not-applicable")
assert all(DISPOSITION.match(f"{label}: x") for label in UNCERTAINTY_LABELS)


def expected() -> list[tuple[str, str, bool]]:
    """(source, what the skill must show, whether it is a token or a plain substring)."""
    rows = []
    for source, names in (
        ("teachback_schema.TEACHBACK_REQUIRED_FIELDS", TEACHBACK_REQUIRED_FIELDS),
        ("teachback_schema.TEACHBACK_REQUIRED_SUBKEYS", TEACHBACK_REQUIRED_SUBKEYS),
        ("teachback_schema.TEACHBACK_VARIETY_ACK_VALID_VALUES", TEACHBACK_VARIETY_ACK_VALID_VALUES),
        ("handoff_schema.HANDOFF_CANONICAL_FIELDS", HANDOFF_CANONICAL_FIELDS),
        ("amendments.ACK", (amendments.ACK,)),
        ("intentional_wait.KNOWN_REASONS", sorted(KNOWN_REASONS)),
        ("intentional_wait.KNOWN_RESOLVERS", sorted(KNOWN_RESOLVERS)),
        ("background_work.WAIT_ANCHOR_KEY", (WAIT_ANCHOR_KEY,)),
        *LITERALS.items(),
    ):
        rows += [(source, name, True) for name in names]
    rows += [("amendments.DISPOSITION_VERBS", f"`{verb}: ", False) for verb in amendments.DISPOSITION_VERBS]
    rows.append(("amendments.AMEND_PREFIX", f"`{amendments.AMEND_PREFIX}<n>`", False))
    rows += [("handoff_schema.DISPOSITION", f"`{label}:`", False) for label in UNCERTAINTY_LABELS]
    rows.append(("teachback_schema.TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN",
                 f"{TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+", False))
    return rows


def missing(text: str) -> list[str]:
    return [f"{shown!r} ({source})" for source, shown, token in expected()
            if not (named(text, shown) if token else shown in text)]


def test_the_teammate_pages_name_every_field_and_key_the_checks_read():
    gone = missing("\n".join(read(page) for page in PAGES))
    assert not gone, "the teammate pages no longer state:\n  " + "\n  ".join(gone)


def test_the_brief_skeletons_carry_every_field_the_checks_read():
    teachback = teachback_skeleton(TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN)
    assert set(TEACHBACK_REQUIRED_FIELDS) | {"reasoning_reconstruction"} <= set(teachback)
    assert set(teachback["reasoning_reconstruction"]) == set(TEACHBACK_REQUIRED_SUBKEYS)
    assert "reasoning_reconstruction" not in teachback_skeleton(
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN - 1)
    handoff = handoff_skeleton(["T-1"])
    assert set(HANDOFF_CANONICAL_FIELDS) | {"amendments", "refs"} <= set(handoff)
    assert "rev" not in handoff  # the CLI assigns it


def test_the_skill_hands_in_through_repact_submit():
    assert "repact submit" in read(SKILL)
