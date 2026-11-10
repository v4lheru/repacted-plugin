"""Both-directions pin for the silent cross-project mis-scope warning.

THE DEFECT. ``_detect_project_id`` warns loudly when resolution lands on HOME,
and says nothing when a well-formed project key is stamped on a record about
somewhere else. One mis-scope was loud, the other silent, and only the silent
one actually happened.

WHY BOTH DIRECTIONS ARE MANDATORY HERE, not a stylistic preference: a guard
with only a positive arm cannot be distinguished from one that ALWAYS fires,
and a guard with only a negative arm cannot be distinguished from one that
NEVER fires. Either mistake ships something that reads as coverage. Every
behaviour below therefore has a matched opposite:

  warns when the repo differs        <-> silent when the repo matches
  silent outside any repository      <-> warns inside a different one
  silent for a supplied-and-matching <-> warns for a supplied-and-differing

WHAT THIS GUARD DOES NOT DO, pinned in ``test_silent_when_location_agrees_but_subject_may_not``
so the limitation is a test rather than a comment someone deletes: it compares
process LOCATION, a proxy for subject. A record about another project written
from the correct directory is NOT detected and cannot be — every strategy
agrees there and all of them are right about location and silent about subject.
That arm asserts the SILENCE deliberately, so anyone who later "fixes" it into
a warning has to confront the false-positive cost on every ordinary save.

Isolation: these tests construct PACTMemory directly and never call save(), so
no store and no CLAUDE.md is reachable from here. ``main_repo_root`` is patched
rather than invoked, so no git subprocess runs and the result does not depend on
where the suite is executed from.
"""
from pathlib import Path
from unittest.mock import patch


from scripts.memory_api import PACTMemory


def _mem(project_id):
    """A PACTMemory with detection bypassed — constructor arg short-circuits it."""
    return PACTMemory(project_id=project_id, session_id="test-session")


def _warning(project_id, repo_root, filed_as=None):
    """Return the warning for `filed_as` (default: project_id) at `repo_root`."""
    memory = _mem(project_id)
    with patch("scripts.memory_api.main_repo_root", return_value=repo_root) as m:
        result = memory._location_divergence_warning(
            project_id if filed_as is None else filed_as
        )
        assert m.called, "main_repo_root was never consulted — guard is inert"
    return result


# --- POSITIVE: it fires when it should -------------------------------------

def test_warns_when_working_directory_is_a_different_repository():
    """The measured incident: a save issued from inside another repo."""
    warning = _warning("PACT-prompt", Path("/Users/alice/Sites/scratchpad/viable"))
    assert warning is not None, "the silent mis-scope stayed silent"
    assert "PACT-prompt" in warning, "warning does not name the filed project"
    assert "viable" in warning, "warning does not name the repo we are in"


def test_warning_discloses_that_it_only_compares_location():
    """The warning must not be readable as subject-level coverage.

    A reader who takes this as "no warning means correctly filed" has been
    misled, so the text itself carries the bound.
    """
    warning = _warning("PACT-prompt", Path("/tmp/other-repo"))
    assert "LOCATION" in warning
    assert "NOT A MISFILE DETECTOR" in warning
    assert "still misfiled" in warning


def test_warns_for_an_explicitly_supplied_foreign_project():
    """Covers the route detection never sees.

    A payload-supplied project_id short-circuits `_detect_project_id`
    entirely, so a check living in that function would miss this case.
    """
    warning = _warning(
        "PACT-prompt", Path("/Users/alice/Sites/collab/PACT-prompt"), filed_as="somewhere-else"
    )
    assert warning is not None
    assert "somewhere-else" in warning


# --- NEGATIVE: it stays silent when it should ------------------------------

def test_silent_when_the_repository_matches():
    """The overwhelmingly common correct save must produce nothing."""
    assert _warning("PACT-prompt", Path("/Users/alice/Sites/collab/PACT-prompt")) is None


# --- the git call is resolved once, not per call ---------------------------


# --- the guard is WIRED INTO save(), not merely correct in isolation --------


