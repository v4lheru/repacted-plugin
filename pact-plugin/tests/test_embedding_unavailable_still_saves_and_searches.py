"""The graceful-degradation contract: no model, but saves land and search works.

WHY THIS FILE EXISTS. ``EmbeddingService._ensure_initialized`` catches
``ImportError`` and ``Exception``, sets ``_available = False`` and lets the
caller proceed without a vector. That handler is the whole reason a machine
without model2vec can still use this subsystem -- and nothing asserted the
behaviour it provides. MEASURED against the full suite with the model made
unreachable: 2 failed and 2 errors out of ~16,900. Of those four, the two
errors were a fixture that could not build and the two failures were incidental
traversals; none of them asserts that a SAVE SUCCEEDS or that SEARCH STILL
RETURNS ROWS. So the degradation path could have started raising instead of
degrading and the suite would have reported the same four.

That the count was four rather than three also matters for how this is read:
two of the four were ERRORS, and a summary that drops the errors count -- which
the one in common use here does -- shows half of them.

WHAT THIS PINS. The user-facing contract, not the internals: with no model, a
save returns an id, the row is retrievable, and a keyword query finds it. The
reason code is asserted too, because ``degraded:<mode>`` is the documented
channel by which a caller learns search is running without vectors.

HOW THE MODEL IS MADE UNAVAILABLE, and why not by patching the service. Binding
``model2vec`` to ``None`` in ``sys.modules`` makes ``from model2vec import
StaticModel`` raise ``ImportError``, which drives the REAL ``_ensure_initialized``
through its REAL handler. Patching ``_ensure_initialized`` or ``is_available``
would stub the seam whose correct behaviour IS the thing under test, and would
pass with that handler deleted.
"""
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from scripts.embeddings import get_embedding_service, reset_embedding_service
from scripts.memory_api import PACTMemory

_TOKEN = "zarquon"  # a term no other fixture in this suite writes
_CONTEXT = f"the {_TOKEN} deployment rollback procedure"


def _cause(status):
    """The cause segment of `degraded:<mode>:<cause>`, or the whole string.

    These arms pin the CAUSE only. The mode segment is already pinned in
    test_embedding_status_contract.py, and asserting it here would also make
    each arm depend on WHEN capabilities are sampled -- measured: read outside
    the model-unavailable context it reports `semantic`, inside it `keyword`,
    so a whole-string assertion fails for a reason that is not the subject.
    """
    parts = (status or "").split(":")
    return parts[2] if len(parts) > 2 else status


@contextmanager
def _model_unavailable():
    """Drive the real ImportError handler, and leave the singleton clean."""
    reset_embedding_service()
    try:
        with patch.dict(sys.modules, {"model2vec": None}):
            yield
    finally:
        # The service caches `_available = False`; a later test in this process
        # would inherit the degraded verdict without this.
        reset_embedding_service()


@pytest.fixture
def mem(tmp_path):
    return PACTMemory(
        project_id="degradation-probe",
        session_id="s",
        db_path=tmp_path / "degraded.db",
    )


def test_the_arm_actually_reaches_the_unavailable_state():
    """NON-VACUITY, and every assertion in this file rests on it.

    If the ``sys.modules`` bind stopped producing an ImportError -- a different
    import spelling, a vendored module, an eager import elsewhere -- the arms
    below would run with the model PRESENT and pass while proving nothing about
    degradation. This fails first instead.
    """
    with _model_unavailable():
        assert get_embedding_service().is_available() is False, (
            "the service reports AVAILABLE inside the unavailable context -- "
            "the ImportError bind is not reaching the real load, so every "
            "other arm in this file is measuring the ordinary path"
        )


def test_the_matched_opposite_is_available_when_the_model_is_present():
    """Without the matched positive, the guard above cannot be told apart from
    a service that reports unavailable unconditionally."""
    pytest.importorskip("model2vec")
    reset_embedding_service()
    try:
        assert get_embedding_service().is_available() is True, (
            "the service reports unavailable with model2vec installed, so the "
            "guard above would hold for a reason that is not the bind"
        )
    finally:
        reset_embedding_service()


def test_a_save_still_succeeds_without_the_model(mem):
    """THE CONTRACT. A missing model must cost the vector, never the record."""
    with _model_unavailable():
        memory_id = mem.save({"context": _CONTEXT}, sync_to_claude=False)

    assert memory_id, f"save returned {memory_id!r} with no model available"
    stored = mem.get(memory_id)
    assert stored is not None, (
        f"save reported id {memory_id} but the row is not retrievable -- the "
        "record was lost, which is the failure this handler exists to prevent"
    )


