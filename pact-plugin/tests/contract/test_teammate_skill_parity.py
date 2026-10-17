"""Contract, TEAMMATE PARITY: skills/teammate/SKILL.md names every field and key the gates check.

The skill is the whole happy path of a teachback, a HANDOFF and a wait (1.3.8, unit S2): the
references load only after a rejection or an advisory. A name a gate checks and the skill does not
state is then a rejection in the field. The expected names are read from the hook constants; the
few a hook reads as a bare literal are listed in LITERALS with the function that reads them.
"""
from __future__ import annotations

import evidence_gate
import validate_handoff
import write_checks
from shared import amendments
from shared.background_work import WAIT_ANCHOR_KEY
from shared.handoff_schema import HANDOFF_CANONICAL_FIELDS
from shared.intentional_wait import KNOWN_REASONS, KNOWN_RESOLVERS
from shared.teachback_schema import (
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
)

from tests.contract._surfaces import PLUGIN_ROOT, read

SKILL = PLUGIN_ROOT / "skills" / "teammate" / "SKILL.md"

# Names a hook reads as a literal, with no constant to import.
LITERALS = {
    "task_lifecycle_gate._validate_variety_acknowledgment": ("rationale_articulates_this_dispatch", "concern"),
    "task_lifecycle_gate.evaluate_lifecycle": ("reasoning_reconstruction", "intentional_wait"),
    "handoff_ordering_gate._evaluate_handoff_rev / amendments.handoff_rev": ("rev",),
    "amendments.missing_dispositions": ("amendments",),
    "intentional_wait.validate_wait": ("reason", "expected_resolver", "since"),
}


def named(text: str, name: str) -> bool:
    """The name as a token of its own: in backticks or in quotes, never as part of a longer word."""
    return f"`{name}`" in text or f'"{name}"' in text


def expected() -> list[tuple[str, str, bool]]:
    """(source, what the skill must show, whether it is a token or a plain substring)."""
    ev = write_checks.load_evidence()      # shared/evidence, through the hook's own loader
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
    rows += [("amendments.DISPOSITION_VERBS", f'"{verb}: ', False) for verb in amendments.DISPOSITION_VERBS]
    rows.append(("amendments.AMEND_PREFIX", f"`{amendments.AMEND_PREFIX}<n>`", False))
    rows += [("_evidence.DISPOSITIONS", f"`{label}:`", False) for label in ev.DISPOSITIONS]
    rows += [("evidence_gate.GATED_KEYS", f"`metadata.{key}`", False) for key in evidence_gate.GATED_KEYS]
    rows.append(("teachback_schema.TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN",
                 f"{TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+", False))
    rows += [("validate_handoff.LOSSLESS_FIELDS", f"`{field['description']}:`", False)
             for field in validate_handoff.LOSSLESS_FIELDS.values()]
    return rows


def missing(text: str) -> list[str]:
    return [f"{shown!r} ({source})" for source, shown, token in expected()
            if not (named(text, shown) if token else shown in text)]


def test_the_skill_names_every_field_and_key_the_gates_check():
    gone = missing(read(SKILL))
    assert not gone, "skills/teammate/SKILL.md no longer states:\n  " + "\n  ".join(gone)
