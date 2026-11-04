"""Arms for the DECLINE path of the three-step write-window rule.

THE MECHANISM. `_resolve_write_window` picks the window a section write may
search, in three steps. STEP 1 uses the memory marker pair. STEP 2 derives the
window from the session-block end to the managed end. STEP 3 DECLINES, and the
two sync writers turn that decline into `SyncResult.NO_WINDOW`.

WHY THESE ARMS ARE HERE. The decline shipped with no arm. The new result
reason, the two sync mappings and step 3 itself were all green with none of
them exercised, so nothing reddened if the decline broke.

HOW EACH ARM IS SHOWN TO REACH THE DECLINE, and not a neighbour. A document
that declines is malformed in more than one way at once, so a red alone does
not say which cause fired. The answer is a ONE-AXIS FAMILY: one document that
resolves at step 1, the same document less ONLY the memory marker pair, and
that one less ONLY the session block. The three give three different answers,
so the family reads the STEP SELECTION rather than a malformation.

ASSERTION ORDER IS LOAD-BEARING IN EACH ARM BELOW. More than one leg reddens
when the decline breaks, and the leg asserted first names the defect. The
coarse fact goes first, the finer discrimination second.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# The one-axis document family
# ---------------------------------------------------------------------------

def _family():
    """Build the three documents, each differing from the last by ONE removal.

    Returns (step1_doc, step2_doc, step3_doc, memory_pair_text, session_text).

    THE LITERALS COME FROM THE MODULE UNDER TEST, never spelled again here. A
    twin literal in a test goes stale without a gate, which is the shape this
    branch keeps removing.
    """
    from scripts.working_memory import (
        MEMORY_END_MARKER,
        MEMORY_START_MARKER,
        _MANAGED_END_MARKER,
        _MANAGED_START_MARKER,
        _SESSION_END_MARKER,
    )

    session_text = (
        "<!-- SESSION_START -->\n"
        "- Resume: `claude --resume abc`\n"
        f"{_SESSION_END_MARKER}\n"
    )
    memory_pair_text = (
        f"{MEMORY_START_MARKER}\n"
        "## Working Memory\n"
        f"{MEMORY_END_MARKER}\n"
    )

    step1 = (
        "# Project\n"
        "\n"
        f"{_MANAGED_START_MARKER}\n"
        "# PACT Framework and Managed Project Memory\n"
        "\n"
        f"{session_text}"
        "\n"
        f"{memory_pair_text}"
        "\n"
        f"{_MANAGED_END_MARKER}\n"
    )
    # ONE REMOVAL: the memory marker pair. Nothing else changes.
    step2 = step1.replace(memory_pair_text, "")
    # ONE MORE REMOVAL: the session block. Nothing else changes.
    step3 = step2.replace(session_text, "")
    return step1, step2, step3, memory_pair_text, session_text


class TestTheWindowFamilySelectsThreeDifferentSteps:
    """Step 3 itself, proven to be step 3 and not a malformed document."""

    def test_the_family_gives_step_1_step_2_and_a_decline(self):
        """The three documents select three different windows.

        WHAT THIS CATCHES IF STEP 3 IS DELETED: the third row. With the decline
        removed the resolver falls through to whatever the caller had before,
        so `step3` stops returning `None` and this arm reddens on that row.

        WHAT IT STOPS CATCHING: nothing about steps 1 and 2. Rows one and two
        assert the OTHER branches, and they are here as the control that makes
        row three mean something: a family that answered the same way at each
        row would separate no states at all.
        """
        from scripts.working_memory import (
            MEMORY_START_MARKER,
            _SESSION_END_MARKER,
            _resolve_write_window,
        )

        step1, step2, step3, memory_pair_text, session_text = _family()

        # THE AXIS IS ONE, AND THIS IS MEASURED RATHER THAN ASSERTED. Each
        # neighbour differs from the last by exactly the named removal.
        assert memory_pair_text in step1 and memory_pair_text not in step2
        assert step1.replace(memory_pair_text, "") == step2
        assert session_text in step2 and session_text not in step3
        assert step2.replace(session_text, "") == step3

        w1 = _resolve_write_window(step1)
        w2 = _resolve_write_window(step2)
        w3 = _resolve_write_window(step3)

        # COARSE FIRST: the decline is the property this file exists for.
        assert w3 is None, (
            "a document with neither a memory marker pair nor a session block "
            "resolved a window instead of declining"
        )
        # THEN THE CONTROLS. Without these the arm above passes for a resolver
        # that declines on everything, which is a different defect.
        assert w1 is not None, "the memory marker pair no longer resolves a window"
        assert w2 is not None, "the session-block window no longer resolves"

        # 🔴 EACH CONTROL ROW ASSERTS THE IDENTITY OF ITS WINDOW, NOT MERELY
        # THAT THE TWO DIFFER. A `w1 != w2` check is satisfied when BOTH rows
        # took step 2, because the two documents differ in text, so it does
        # not show that step 1 ran at all. MEASURED: this arm survived a
        # mutant that made step 1 unavailable, until these two lines existed.
        w1_text, _ = w1
        w2_text, _ = w2
        # The step-1 window IS the memory region, so the start marker bounds
        # it from outside. With step 1 unavailable this document falls to
        # step 2, whose window opens above the pair and CONTAINS that marker.
        assert MEMORY_START_MARKER not in w1_text, (
            "the step-1 window contains the memory start marker, so it is the "
            "session-block window and step 1 did not run"
        )
        assert "## Working Memory" in w1_text, (
            "the step-1 window does not hold the memory section it should be"
        )
        # The step-2 window opens BELOW the session block, so the end marker
        # of that block bounds it from outside.
        assert _SESSION_END_MARKER not in w2_text, (
            "the step-2 window contains the session-end marker, so it is the "
            "wide region rather than the window below the session block"
        )


# ---------------------------------------------------------------------------
# The two sync mappings, and the reason each one carries
# ---------------------------------------------------------------------------

def _declining_project(tmp_path, monkeypatch):
    """Write the declining document to a CLAUDE.md under a fresh root.

    THE PROJECT DIR IS DECLARED, AND WITHOUT IT THESE ARMS MEASURE THE WRONG
    THING. The write path refuses a target outside the project containment
    boundary, and that refusal produces `failed` BEFORE the window rule is
    reached. MEASURED: with the variable unset each arm below returned
    `failed`, which is a non-write for a cause that has nothing to do with the
    decline. An arm asserting only `it did not write` passes in that state.
    """
    _, _, step3, _, _ = _family()
    root = tmp_path / "declined-project"
    root.mkdir()
    (root / "CLAUDE.md").write_text(step3, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# The command-line status field, driven by a RUN
# ---------------------------------------------------------------------------

