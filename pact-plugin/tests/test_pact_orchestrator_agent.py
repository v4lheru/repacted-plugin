"""
Structural tests for agents/pact-orchestrator.md (v4.0.0 orchestrator agent file).

The orchestrator persona is delivered via `claude --agent repacted:pact-orchestrator`
under v4.0.0; the agent body carries orchestration content inlined directly
into the durable persona file. Frontmatter is intentionally
minimal (name, description, memory:user, color) — model/permissionMode/tools
are inherited defaults, and `skills:` is omitted because the preload mechanism
does not apply to --agent main sessions.

Marker discipline: tests whose passing depends on a NOT-YET-LANDED
production change carry `@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")`.
Tests against production already on disk (C1: orchestrator file present)
are plain tests. C10 flips the remaining xfail markers as their dependent
commits land.
"""
from pathlib import Path


from helpers import parse_frontmatter

ORCHESTRATOR_PATH = (
    Path(__file__).parent.parent / "agents" / "pact-orchestrator.md"
)

REQUIRED_FRONTMATTER_KEYS = {"name", "description", "memory", "color"}
EXPECTED_NAME = "pact-orchestrator"
EXPECTED_MEMORY = "user"
EXPECTED_COLOR = "#FFD700"


def test_pact_orchestrator_file_exists():
    assert ORCHESTRATOR_PATH.exists(), (
        f"pact-orchestrator.md missing at {ORCHESTRATOR_PATH}"
    )


def test_pact_orchestrator_frontmatter_has_required_keys():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm is not None, "pact-orchestrator.md missing YAML frontmatter"
    missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
    assert not missing, f"missing required frontmatter keys: {missing}"


def test_pact_orchestrator_frontmatter_name_matches():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm["name"] == EXPECTED_NAME, (
        f"frontmatter.name expected {EXPECTED_NAME!r}, got {fm['name']!r}"
    )


def test_pact_orchestrator_memory_is_user():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm["memory"] == EXPECTED_MEMORY, (
        f"frontmatter.memory expected {EXPECTED_MEMORY!r}, got {fm['memory']!r}"
    )


def test_pact_orchestrator_color_is_gold():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    actual = fm["color"].strip().strip('"').strip("'")
    assert actual == EXPECTED_COLOR, (
        f"frontmatter.color expected {EXPECTED_COLOR!r}, got {fm['color']!r}"
    )


def test_pact_orchestrator_omits_model_permissionmode_tools():
    """Frontmatter asymmetry: orchestrator inherits defaults; teammate files keep them.

    Negative half of the contract; the positive half (every teammate def
    must pin `model: inherit`) is enforced by test_agents_structure.py::
    TestTeammateModelInheritance.
    """
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    forbidden = {"model", "permissionMode", "tools"}
    present = forbidden & set(fm.keys())
    assert not present, (
        f"orchestrator frontmatter should inherit defaults; found explicit keys: {present}"
    )


def test_pact_orchestrator_omits_skills_frontmatter():
    """`skills:` preload does not apply to --agent main sessions; omitting is intentional."""
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert "skills" not in fm, (
        "orchestrator must omit `skills:` frontmatter — preload does not apply "
        "to --agent main sessions; declaring skills creates a misleading contract"
    )




def test_pact_orchestrator_body_references_imperative_protocols():
    """The 4 imperative protocols (PROTO targets) must appear as cross-references in the body."""
    text = ORCHESTRATOR_PATH.read_text()
    expected_imperative = [
        "algedonic.md",
        "workflows.md",
        "recovery.md",
        "completion.md",
    ]
    missing = [p for p in expected_imperative if p not in text]
    assert not missing, (
        f"orchestrator body missing imperative-protocol cross-references: {missing}"
    )


def test_pact_orchestrator_body_references_soft_protocols():
    """The soft (reference-only) protocols must appear as cross-references in the body."""
    text = ORCHESTRATOR_PATH.read_text()
    expected_soft = [
        "lead-reference.md",
        "pact-communication-charter.md",
    ]
    missing = [p for p in expected_soft if p not in text]
    assert not missing, (
        f"orchestrator body missing soft-protocol cross-references: {missing}"
    )




def test_pact_orchestrator_direct_edit_lane_has_three_conditions_and_high_risk_carve_out():
    """§8's direct-edit lane: the lead edits small known changes itself, never high-risk code."""
    text = ORCHESTRATOR_PATH.read_text()
    section = text[text.index("## 8. "):text.index("## 9. ")]
    assert "**Direct-edit lane**" in section and "only when ALL hold" in section
    assert "(1) the change is known before editing (no investigation or design choice)" in section
    assert "(2) at most ~3 files and ~40 changed lines" in section
    assert "(3) NOT high risk (the §11 class)" in section
    assert "High risk never enters the lane, however \"simple\", urgent or known the fix." in section
    assert "security code (auth, filters, guards), validation and parsers, and shared contracts" in text
    assert "NEVER add, change, or remove application code yourself" not in text


def test_lead_caught_outside_the_lane_keeps_the_diff_and_never_restores_the_whole_file():
    """A whole-file `git checkout -- <file>` would discard the user's or a peer's unstaged edits."""
    algedonic = ORCHESTRATOR_PATH.parent.parent / "protocols" / "algedonic.md"
    for path in (ORCHESTRATOR_PATH, algedonic):
        text = path.read_text()
        assert "git checkout -- <file>" not in text, path.name
        assert "undo only your own isolated patch" in text and "never a whole-file restore" in text
