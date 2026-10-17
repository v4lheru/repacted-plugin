"""
Structural pins for the dual-channel acceptance rules that still hold.

The teachback and HANDOFF notify templates each use only their own payload
delimiters (P15), and no instruction surface scopes the `TaskGet` metadata
blindness to one key (P32). The other presence pins of this module were
retired into tests/contract/rule_homes (W0) and, in 1.5.0, dropped as
verbatim wording pins; git has them and their flip-set record.
"""

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

AGENT_TEAMS_SKILL = PLUGIN_ROOT / "skills" / "teammate" / "references" / "teammate-handoff.md"

TEACHBACK_SKILL = PLUGIN_ROOT / "skills" / "teammate" / "references" / "teammate-teachback.md"



def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")



def _phrase(text: str) -> str:
    """Phrase-matching normalization: strip backticks, then collapse any
    whitespace run to a single space. Tool language is inline-code
    formatted in the shipped markdown, so a pinned phrase must match a
    subject rendered with backticks inside the phrase span. Applied to
    BOTH sides (file text and pinned phrase) per the wake-ordering
    module's convention."""
    return " ".join(text.replace("`", "").split())



def _normalized(path: Path) -> str:
    return _phrase(_raw(path))



# Each notify template must use ONLY its own payload type's delimiters.
# A copy-paste of the wrong template (or a stray foreign BEGIN pasted
# into prose) leaves both delimiter families on one surface; the
# presence pins (P1/P2/P14) stay green because the right tokens are
# still there — measured before this case: a stray
# HANDOFF-PAYLOAD-BEGIN added to the teachback skill flipped nothing
# suite-wide. Like P6/P7, these absence pins flip under the
# re-introduction mutation, not under file revert (the foreign token
# never existed pre-change).
WRONG_DELIMITER_PINS = [
    (TEACHBACK_SKILL, "HANDOFF-PAYLOAD-BEGIN"),
    (AGENT_TEAMS_SKILL, "TEACHBACK-PAYLOAD-BEGIN"),
]



@pytest.mark.parametrize(
    "doc_path, token",
    WRONG_DELIMITER_PINS,
    ids=[f"{p.name}::not-{t}" for p, t in WRONG_DELIMITER_PINS],
)
def test_wrong_payload_delimiter_absent(doc_path: Path, token: str):
    """The surface must not carry the OTHER payload type's delimiter. A
    stray foreign delimiter on a teammate surface teaches an ambiguous
    frame the lead could mis-key on; if a future edit genuinely needs to
    reference the other channel's delimiter in prose, update this guard
    deliberately (same convention as the P6/P7 absence pins)."""
    assert _phrase(token) not in _normalized(doc_path), (
        f"{doc_path.name}: foreign payload delimiter {token!r} present. "
        f"Each notify template uses only its own payload type's "
        f"BEGIN/END delimiters; a stray foreign delimiter makes the "
        f"payload frame ambiguous."
    )



INSTRUCTION_SURFACE_FLOOR = 60



def _instruction_surfaces() -> list:
    patterns = ("skills/*/SKILL.md", "agents/*.md", "commands/*.md", "protocols/*.md")
    surfaces = sorted(
        {p for pattern in patterns for p in PLUGIN_ROOT.glob(pattern)}
    )
    assert len(surfaces) >= INSTRUCTION_SURFACE_FLOOR, (
        f"instruction-surface sweep found only {len(surfaces)} surfaces "
        f"(floor {INSTRUCTION_SURFACE_FLOOR}) — a glob regression has "
        f"narrowed the tree-wide absence scan; fix the glob before "
        f"trusting this pin."
    )
    return surfaces


# P32: tree-wide, no blindness claim scoped to one key.
#
# TREE-WIDE, NOT PER-SITE, and that is the whole design. A per-site arm goes
# green the moment a sixth narrow claim appears in a NEW file, which is
# exactly how this class spread: five surfaces stated the blindness correctly
# but scoped it to one key, and a reader of any of them can conclude the
# other keys ARE surfaced. Blindness is total across keys.
#
# The sweep inherits INSTRUCTION_SURFACE_FLOOR through _instruction_surfaces()
# so a glob regression cannot narrow it to nothing, and it carries its own
# positive control below: an absence result from a search with no known-present
# term in the same run is worth nothing.
# KEYED ON THE CONSTRUCTION, NOT ON A RENDERING, because two literals saw
# only two of the six renderings this claim has in the tree. `does NOT surface
# TASK metadata` is the MAJORITY form (eight of the governed lines) and is not
# a superstring of the canonical claim, so the positive control below cannot
# even count it, let alone police it; the lowercase form is live today in the
# agent-teams skill. Both evaded the literals. These two patterns are a strict
# superset of them, fire on every key and either casing, and were measured to
# produce zero hits on the correct tree.
NARROW_BLINDNESS_CLAIMS = [
    re.compile(r"TaskGet does not surface (?:task )?metadata\.\w+", re.I),
    re.compile(r"TaskGet is metadata-blind for \w+", re.I),
]

CANONICAL_BLINDNESS_CLAIM = "TaskGet does NOT surface metadata"

# Measured 12 surfaces carrying the canonical form. The floor sits below the
# measurement so ordinary editing does not redden it, and above zero so a
# normalization or glob failure that silently matches nothing does.
# COMMANDS (m8b) re-measured 8 of 97 after all 13 commands were slimmed (on TEAMMATE):
# phase B moves each rule to one home, so the number of surfaces REPEATING the
# claim falls by design, and TEAMMATE's slimming of the agent bodies and the
# agent-teams skill can lower it again. The floor is a scan-reached-something
# control, not a count of mirrors to preserve, so it sits well below the
# measurement.
BLINDNESS_CONTROL_FLOOR = 4



@pytest.mark.parametrize(
    "narrow", NARROW_BLINDNESS_CLAIMS, ids=lambda n: n.pattern[:44]
)
def test_blindness_claim_not_scoped_to_handoff_tree_wide(narrow: "re.Pattern"):
    """No instruction surface may state the metadata blindness as applying
    to `handoff` alone. Swept tree-wide with a known-present control in the
    SAME run, so a scan that has stopped matching anything reports as broken
    rather than as clean.

    WHAT THIS CANNOT CATCH: it keys on two narrowing CONSTRUCTIONS, so it
    reaches any key and either casing, but a site that narrows the claim
    without either construction — naming the key in a following sentence,
    say — still passes. It is not a general claim-scope detector."""
    surfaces = _instruction_surfaces()

    control_hits = sum(
        1 for s in surfaces
        if _phrase(CANONICAL_BLINDNESS_CLAIM) in _normalized(s)
    )
    assert control_hits >= BLINDNESS_CONTROL_FLOOR, (
        f"positive control failed: the canonical blindness claim "
        f"{CANONICAL_BLINDNESS_CLAIM!r} was found on only {control_hits} of "
        f"{len(surfaces)} surfaces (floor {BLINDNESS_CONTROL_FLOOR}). The "
        f"absence result below is not trustworthy until this passes — a "
        f"scan matching nothing reports every claim as absent."
    )

    for surface in surfaces:
        hit = narrow.search(_normalized(surface))
        assert hit is None, (
            f"{surface.name}: blindness claim {hit.group(0)!r} is scoped to "
            f"one key (matched {narrow.pattern!r}). `TaskGet` surfaces NO "
            f"metadata, so a reader of this sentence can wrongly conclude "
            f"other keys are surfaced — state the blindness generally."
        )

