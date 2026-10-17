"""
pact-plugin/skills/pact-handoff-harvest/test_skill_loading_harvest.py

Tests for verifying the pact-handoff-harvest skill file structure and content.
Ensures SKILL.md exists, has valid YAML frontmatter, includes all three workflow
variants, supporting sections, keyword routing, and critical protocol references.
"""

import pytest
from pathlib import Path


SKILL_DIR = Path(__file__).parent
SKILL_FILE = SKILL_DIR / "SKILL.md"
AGENTS_DIR = SKILL_DIR.parent.parent / "agents"
SECRETARY_FILE = AGENTS_DIR / "pact-secretary.md"

ORPHAN_HEADING = "## Orphaned Handoff Recovery"

# A known sentence of the Orphaned Handoff Recovery section, used as the
# anchor of the non-vacuity guard. It must sit in that section and in no
# other.
ORPHAN_ANCHOR = "Layer 4 fallback"

# The population rule's opening phrase. It must appear ONCE: the ordering arm
# locates the rule with `find`, which returns the FIRST occurrence, so a
# second mention placed earlier would let the block itself sit anywhere.
RULE_PHRASE = "DISCARD ANY SCOPE YOU ARRIVED WITH"

# The two sweeping entry points. Each must POINT AT the population rule.
# NOT `## Standard Harvest Workflow`: see `section` on why it cannot be sliced.
SWEEPING_HEADINGS = (
    "## Incremental Harvest Workflow",
    "## Consolidation Harvest Workflow",
)

# The reference each sweeping section must carry. Deliberately NOT the pointer
# sentence: the property under test is that the section REFERS to the rule, so
# a reworded pointer must stay green and an INLINED COPY of the rule must fail.
# Deliberately NOT "Standard Harvest rule" either — "Standard Harvest Step"
# already appears in both sections, so that shorter phrase is one reword of
# existing text away from being satisfied with no pointer present.
POINTER_ANCHOR = "Standard Harvest population rule"

# Call spellings that remove a file from disk. THIS LIST IS A FLOOR AND NOT
# A PROOF OF ABSENCE: prose can authorise a removal in words no list
# anticipates. The POSITIVE assertion carries the contract. This list catches
# the machine-shaped forms an editor pastes back in.
REMOVAL_CALL_TOKENS = ("unlink", "shutil.rmtree", "os.remove", "os.rmdir", "rm -")


def section(content, heading):
    """Return the slice of `content` running from `heading` to the next `## `.

    Runs to the end of the file when that section is the last one. THE SLICE
    RULE IS THE PARAMETER OF EVERY ARM THAT USES IT, so each arm asserts the
    slice terminated correctly before it reads anything out of it.

    DO NOT SLICE `## Standard Harvest Workflow` WITH THIS. That slice
    terminates on the `## team={team_id}` file-format example inside Step 8
    and never reaches Steps 9 or 10, so an absence read out of it would be an
    absence from two thirds of a section. The Incremental and Consolidation
    sections carry no interior `## ` and slice cleanly, which is why only
    those two are sliced here.
    """
    start = content.find(heading)
    if start == -1:
        return ""
    rest = content[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


@pytest.fixture
def skill_content():
    """Load the skill file content."""
    return SKILL_FILE.read_text()


@pytest.fixture
def secretary_content():
    """Load the secretary agent definition."""
    return SECRETARY_FILE.read_text()






class TestSkillFileExists:
    """Test that the skill file exists."""

    def test_skill_md_exists(self):
        """SKILL.md file must exist in the skill directory."""
        assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"

    def test_skill_md_is_file(self):
        """SKILL.md must be a regular file, not a directory."""
        assert SKILL_FILE.is_file(), f"{SKILL_FILE} exists but is not a file"














def _prune_bullet(content: str) -> str:
    """The Step 3 prune block: the one head bullet that authorises removing
    another team's section, plus its indented sub-items, up to the next
    top-level bullet or heading."""
    step = section(content, "### Step 3: Consolidate and Prune")
    assert step, "Step 3 heading not found"
    lines = step.splitlines()
    heads = [i for i, ln in enumerate(lines) if "`## team=` sections" in ln]
    assert len(heads) == 1, "expected exactly one prune bullet in Step 3"
    block = [lines[heads[0]]]
    for ln in lines[heads[0] + 1:]:
        if ln.strip() and not ln[0].isspace():
            break
        block.append(ln)
    return "\n".join(block)




