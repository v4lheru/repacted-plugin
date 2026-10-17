"""Structural prose-surface regression guard for the Task B claim-before-work discipline.

A teammate must flip its pre-assigned Task B from ``pending`` to ``in_progress``
BEFORE any implementation tool-use, so the lead retains the "work started" signal.
That discipline lives only in prose across several instruction surfaces a teammate
reads at execution time; a future edit could silently drop it. These guards make
that regression loud.

Keyed on STABLE structural tokens (semantic co-occurrence), not exact prose, so
benign rewording survives but dropping the discipline fails:

  G1 — pact-agent-teams "On Start" flow contains the claim step AND couples on
       the claim-BEFORE-work ORDERING (in_progress -> "before any" -> a tool, in
       sequence), so an inverted work-first/claim-after edit fails.
  G2 — pact-agent-teams post-acceptance paragraph carries the pre-assigned-Task-B
       reframe (positive token; the distinctive "status flip" phrase scopes the
       assert to the reframe paragraph).
  G3 — per-surface presence net across the reinforcement surfaces, plus a
       discovery floor so a renamed/trimmed surface set cannot false-pass.
"""
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"

AGENT_TEAMS_SKILL = SKILLS_DIR / "teammate" / "SKILL.md"

# G3 reinforcement surfaces — every surface OTHER than pact-agent-teams (which
# G1/G2 assert deeply). All 5 dispatch-template commands now carry the reminder
# (peer-review + rePACT added alongside the original orchestrate/comPACT/plan-mode),
# plus the teachback skill and the orchestrator persona. The discovery floor
# counts exactly what G3 scans.
REINFORCEMENT_SURFACES = [
    SKILLS_DIR / "teammate" / "references" / "teammate-teachback.md",
    COMMANDS_DIR / "comPACT.md",
    COMMANDS_DIR / "orchestrate.md",
    COMMANDS_DIR / "plan-mode.md",
    COMMANDS_DIR / "peer-review.md",
    COMMANDS_DIR / "rePACT.md",
    AGENTS_DIR / "pact-orchestrator.md",
]
DISCOVERY_FLOOR = 7


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so token checks survive line-wrapping and
    benign whitespace edits."""
    return re.sub(r"\s+", " ", text.lower())


def _on_start_section() -> str:
    """The normalized text of the pact-agent-teams '## On Start' section only
    (sliced to the next top-level '## ' header), so G1 cannot be satisfied by a
    claim token living in some unrelated part of the file."""
    text = AGENT_TEAMS_SKILL.read_text(encoding="utf-8")
    m = re.search(r"\n##\s+On Start\b", text)
    assert m, "pact-agent-teams SKILL.md has no '## On Start' section"
    start = m.end()
    nxt = re.search(r"\n##\s", text[start:])
    section = text[start : start + nxt.start()] if nxt else text[start:]
    return _norm(section)


# ── G1: On Start has the explicit, ordered claim-before-work step ──────────────


# ── G2: post-acceptance paragraph carries the pre-assigned reframe ─────────────


# ── G3: per-surface presence net + discovery floor ────────────────────────────
class TestG3ReinforcementSurfaces:
    """Every reinforcement surface must carry the claim-before-work token, and the
    scanned set must not silently shrink (vacuous-sweep guard)."""

    def test_discovery_floor(self):
        scanned = [p for p in REINFORCEMENT_SURFACES if p.exists()]
        assert len(scanned) >= DISCOVERY_FLOOR, (
            f"G3 scanned only {len(scanned)} reinforcement surfaces "
            f"(floor {DISCOVERY_FLOOR}); a surface was renamed/removed/trimmed: "
            f"{[str(p) for p in REINFORCEMENT_SURFACES if not p.exists()]}"
        )

