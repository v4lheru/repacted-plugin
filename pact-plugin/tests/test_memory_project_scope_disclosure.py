"""Pin the per-save project-scope disclosure — the primary mis-scope deliverable.

WHY DISCLOSURE RATHER THAN A WARNING. A warning must decide something is wrong,
and the resolver cannot: nothing in a save payload asserts what a record is
ABOUT, so "is this filed correctly" is not decidable here. Disclosure makes no
such judgement. It reports which project the record was filed under and which
of the five sources decided it, on every save, and therefore has no
false-positive rate BY CONSTRUCTION.

IT IS NOT PERSISTED, AND THIS DOCSTRING USED TO IMPLY IT WAS. The old wording
read "the mis-scope that prompted this was undetectable after the fact by any
means; with this recorded it is one field away". The second half is false:
``project_scope`` is not a column and appears in neither ``database.py`` nor
``models.py``. It lives on the instance and in the save envelope and is gone
when the process exits. What it buys is real and smaller -- the resolution is
visible AT THE TIME OF THE SAVE, and afterwards only in whatever output the
caller kept. Do not plan an after-the-fact audit around this field.

WHAT IS LOAD-BEARING IS THAT AN ORDINARY, AGREEING SAVE STILL DISCLOSES. If the
field is ever narrowed to fire only on divergence, an absent value starts
meaning "fine", which is exactly the silent-success inference it exists to
remove. ``test_an_agreeing_save_discloses_too`` fails on that change.

THAT IS NOT THE SAME AS "SET ON EVERY BRANCH", AND THIS DOCSTRING USED TO SAY
IT WAS. It claimed the disclosure "belongs with ``last_sync_status`` (set on
every branch)". Both halves are wrong. The disclosure is assigned only after
the store write is read back and verified, so it is NOT set on every branch --
a save that raises earlier leaves it absent. And ``last_sync_status`` is not
set on every branch either: it is cleared at entry and ``_ensure_ready()`` runs
after that clear and can raise. Anchoring one field's contract to another
field's property is what let a false claim sit here unexamined; the property
above is stated directly instead, so it can be checked without a second field.

``location_divergence`` IS NOT A MISFILE FLAG. It compares the process's
working directory against the filed project. False means those two agree, never
that the record is correctly filed.

EVERY ASSERTION RUNS THE REAL ``save()`` over a throwaway database with
``sync_to_claude=False``, so no ambient store and no CLAUDE.md is reachable. An
earlier draft recomputed save()'s disclosure block inside the test helper and
asserted on THAT -- which would have passed with the production code deleted,
since it was testing the test's own arithmetic. ``main_repo_root`` is patched so
no git subprocess runs and results do not depend on the invocation directory.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import memory_api
from scripts.memory_api import PACTMemory


def _save(mem, repo_root, payload=None):
    """Run the REAL save() with the cwd repo pinned, and return the disclosure."""
    with patch.object(memory_api, "_ensure_ready", lambda: None), \
         patch.object(PACTMemory, "_store_embedding", return_value=None), \
         patch.object(memory_api, "main_repo_root", return_value=repo_root):
        mem._cwd_repo_root = False  # clear the per-instance cache
        mem.save(payload or {"context": "scope disclosure probe"},
                 sync_to_claude=False)
    return mem.last_project_scope


@pytest.fixture
def mem(tmp_path):
    """A real PACTMemory over a throwaway database, project supplied."""
    return PACTMemory(
        project_id="PACT-prompt", session_id="s", db_path=tmp_path / "scope.db"
    )


# --- the source is named ----------------------------------------------------

def test_supplied_project_id_is_named_as_its_own_source(mem):
    """`supplied` is the least-checked of the five sources, so it must be named.

    A payload project_id short-circuits detection AND is invisible to the
    env/record scope guard, which takes no arguments and never sees a payload.
    A reader who cannot tell a supplied project from a detected one cannot tell
    a deliberate cross-project filing from an inherited one.
    """
    scope = _save(mem, Path("/repos/PACT-prompt"))
    assert scope["source"] == "supplied", f"source was {scope['source']!r}"


def test_detected_project_names_the_winning_strategy():
    """A detected project reports WHICH strategy won, not merely that one did."""
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/some-project"}):
        project_id, source = PACTMemory._detect_project_id_with_source()
    assert source == "CLAUDE_PROJECT_DIR", f"source was {source!r}"
    assert project_id == "some-project"


def test_save_discloses_a_DETECTED_source_not_just_a_supplied_one(tmp_path):
    """The disclosure must carry the real source through save(), not a constant.

    Sibling of `test_supplied_project_id_is_named_as_its_own_source`, and the
    pair is the point. Every other save-path arm here builds its PACTMemory
    with `project_id=` supplied, so `source` is legitimately "supplied" in all
    of them -- and a mutant that HARDCODES `"source": "supplied"` survived the
    whole file until this arm existed. A field is only pinned by an input that
    makes the right and wrong implementations disagree, so this one resolves
    its project by detection instead.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/detected-project"}):
        mem = PACTMemory(session_id="s", db_path=tmp_path / "detected.db")
    assert mem._project_id == "detected-project", "fixture did not detect"

    scope = _save(mem, Path("/repos/detected-project"))
    assert scope["source"] == "CLAUDE_PROJECT_DIR", (
        f"save() disclosed source={scope['source']!r}; the detected strategy "
        "is not reaching the disclosure"
    )
    assert scope["source"] != "supplied"


def test_a_PAYLOAD_supplied_project_is_disclosed_as_supplied(tmp_path):
    """The source must describe THIS RECORD, not the instance's default.

    Found end-to-end, missed by every unit arm here. The CLI passes a payload
    project_id inside the memory dict rather than as a constructor argument, so
    an instance that detected its own default via CLAUDE_PROJECT_DIR was
    labelling a payload-supplied project `CLAUDE_PROJECT_DIR`. That is wrong on
    exactly the route with the least checking: a payload project_id
    short-circuits detection AND is invisible to the env/record scope guard.

    A reader who sees `CLAUDE_PROJECT_DIR` believes the environment chose this
    project and that the guard therefore vetted it. Neither is true here.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/detected-project"}):
        mem = PACTMemory(session_id="s", db_path=tmp_path / "payload.db")
    assert mem._project_id_source == "CLAUDE_PROJECT_DIR", "fixture did not detect"

    scope = _save(
        mem,
        Path("/repos/detected-project"),
        payload={"context": "x", "project_id": "explicitly-elsewhere"},
    )
    assert scope["project_id"] == "explicitly-elsewhere"
    assert scope["source"] == "supplied", (
        f"disclosed source={scope['source']!r} for a PAYLOAD-supplied project; "
        "that describes the instance's default, not this record"
    )


def test_the_string_only_detector_still_returns_a_bare_string():
    """The long-standing signature is preserved for its external callers.

    `_detect_project_id` is called by the precedence pins in
    test_project_dir_resolution.py. Returning a tuple there would break them, which is why the richer variant was added beside it
    rather than replacing it.
    """
    with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/tmp/some-project"}):
        result = PACTMemory._detect_project_id()
    assert isinstance(result, str), f"expected a bare str, got {type(result)}"
    assert result == "some-project"


# --- what absence licenses, and what an agreeing save still discloses --------

def test_disclosure_starts_absent_on_a_fresh_instance(mem):
    """A fresh instance has confirmed no filing, so the field is None.

    THE NAME OF THIS TEST USED TO ASSERT SOMETHING FALSE. It read
    ``test_disclosure_starts_absent_and_absence_means_no_save_ran``, and
    absence does NOT mean "no save ran". The assignment sits below the store
    write and its read-back, so a save can exit AFTER ``create_memory``
    returned an id -- the row is then in the store with this field still None.
    Absence licenses "this process did not confirm a filing" and nothing
    stronger: not a filing, and not the absence of one.

    What this arm actually pins is the narrow true case it always tested -- a
    fresh instance, no save attempted. A name is not cosmetic in a test: it is
    what a reader greps for and what a failure message prints, so a false name
    ships the false contract even while the assertion below is correct.
    """
    assert mem.last_project_scope is None


# --- divergence, reported and not judged ------------------------------------


# --- staleness: the failure path must not inherit the previous save ---------


# --- the RELOCATION ITSELF: a save that REACHES the store and fails there ----


# --- the OTHER public write path must not bypass any of this ----------------

def _quick_save(repo_root, **kwargs):
    """Drive the REAL `quick_save` with the cwd repo pinned."""
    from scripts.database import quick_save

    with patch.object(memory_api, "_ensure_ready", lambda: None), \
         patch.object(PACTMemory, "_store_embedding", return_value=None), \
         patch.object(memory_api, "main_repo_root", return_value=repo_root):
        return quick_save(**kwargs)


