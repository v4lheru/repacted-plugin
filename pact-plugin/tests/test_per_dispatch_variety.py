"""
Per-dispatch variety stamping — traversal-helper coverage.

Covers _resolve_required_band_via_blocks in task_lifecycle_gate.py: the
disk-based traversal from Task A (teachback subject) through blocks[0] to
Task B's metadata.variety.total. This is the helper that R3 consumes to
decide whether reasoning_reconstruction is REQUIRED.

Test surface architecture:
  - All 4 return values exercised: "required", "recommended", "skipped",
    "unresolvable".
  - All fail-open paths: blocks missing, blocks empty, Task B id
    non-string, Task B file missing, Task B missing metadata, Task B
    missing variety, variety.total non-int.
  - Defensive: Task A is {} (read_task_json returned empty) → unresolvable.
  - Defensive: team_name is "" → unresolvable.

Schema-validator pure-function tests (the D10 + D11 validators) live in
test_task_lifecycle_gate.py alongside the integration tests, because those
validators are gate-local; a validator that lives in a shared module is
tested beside it instead (handoff schema → tests/test_handoff_schema.py).
This file is the traversal-helper surface only.
"""

import json
from pathlib import Path



import task_lifecycle_gate as tlg  # noqa: E402


# =============================================================================
# Helpers
# =============================================================================


def _well_formed_variety(**overrides):
    """Return a well-formed metadata.variety dict per D11."""
    payload = {
        "novelty": 2,
        "novelty_rationale": "x",
        "scope": 2,
        "scope_rationale": "x",
        "uncertainty": 2,
        "uncertainty_rationale": "x",
        "risk": 2,
        "risk_rationale": "x",
        "total": 8,
    }
    payload.update(overrides)
    return payload


def _no_fallback_variety(**overrides):
    """Return a variety dict whose `total` is the ONLY total candidate —
    the four dimension scores are omitted so the dimension-sum fallback
    cannot resolve, and there is no `score` key. Use this to exercise the
    "every resolver candidate invalid → unresolvable" path: override `total`
    with a bad value (or pop it) and no other candidate can recover it. The
    rationales (not resolution candidates) are kept so the write-time schema
    check still treats the shape as rationale-complete."""
    payload = {
        "novelty_rationale": "x",
        "scope_rationale": "x",
        "uncertainty_rationale": "x",
        "risk_rationale": "x",
        "total": 8,
    }
    payload.update(overrides)
    return payload


def _seed_task_b(
    tmp_path, monkeypatch, team_name, task_b_id,
    metadata=None,
):
    """Seed Task B at ~/.claude/tasks/{team_name}/{task_b_id}.json with
    the given metadata dict (or empty if None)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_b = {
        "id": task_b_id,
        "subject": "implement foo",
        "owner": "pact-backend-coder",
        "metadata": metadata if metadata is not None else {},
    }
    (tasks_dir / f"{task_b_id}.json").write_text(
        json.dumps(task_b), encoding="utf-8"
    )


# =============================================================================
# Return-value coverage: required / recommended / skipped
# =============================================================================


class TestRequiredBandResolution:
    """The 3 successful-resolution return values: required, recommended,
    skipped. Each exercises the variety.total threshold logic."""

    def test_required_at_min_threshold(self, tmp_path, monkeypatch):
        """total = 11 → required (inclusive lower bound for REQUIRED band).
        Pinned via TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=11)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "required"

    def test_required_at_high_score(self, tmp_path, monkeypatch):
        """total = 16 (max possible) → required."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=16)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "required"

    def test_recommended_at_lower_threshold(self, tmp_path, monkeypatch):
        """total = 7 → recommended (inclusive lower bound for the
        recommended band per variety_scorer.COMPACT_MAX = 6)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=7)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "recommended"

    def test_recommended_at_upper_threshold(self, tmp_path, monkeypatch):
        """total = 10 → recommended (inclusive upper bound per
        variety_scorer.ORCHESTRATE_MAX = 10)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=10)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "recommended"

    def test_skipped_at_upper_threshold(self, tmp_path, monkeypatch):
        """total = 6 → skipped (inclusive upper bound per
        variety_scorer.COMPACT_MAX = 6)."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=6)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "skipped"

    def test_skipped_at_min_score(self, tmp_path, monkeypatch):
        """total = 4 (min possible per variety_scorer.MIN_SCORE) → skipped."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=4)},
        )
        task_a = {"blocks": ["2"]}
        assert tlg._resolve_required_band_via_blocks(
            task_a, "test-team"
        ) == "skipped"

    def test_band_threshold_constants_aligned_with_variety_scorer(self):
        """Pin the alignment between
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN and
        variety_scorer. The threshold binds to PLAN_MODE_MIN, which is itself
        ORCHESTRATE_MAX + 1 = 11. If variety_scorer's thresholds shift and
        this module's constant doesn't, this test fails and the drift is
        surfaced."""
        from shared import variety_scorer
        from shared.teachback_schema import (
            TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
        )

        assert (
            TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN
            == variety_scorer.ORCHESTRATE_MAX + 1
        ), (
            "shared.teachback_schema constant drifted from variety_scorer "
            "SSOT — see teachback_schema.py inline comment"
        )


# =============================================================================
# Resolvable-via-fallback: non-canonical stamps that now resolve a band
#
# Before the shared-resolver fix, only a strict int `total` resolved; a stamp
# carrying a non-canonical score / top-level variety_score / valid dimension
# scores read as "unresolvable". These cases pin that such stamps now resolve
# to the correct band — the false-positive the fix removes. They exercise the
# caller's int→band mapping through each fallback candidate, so a regression
# in either the resolver chain or the band cuts surfaces here.
# =============================================================================


class TestBandResolvableViaFallback:
    """Non-canonical variety stamps resolve to a band via the shared
    resolver's fallback chain, instead of reading as unresolvable."""

    def test_score_fallback_resolves_band_for_field_report_shape(
        self, tmp_path, monkeypatch,
    ):
        """The exact reported false-positive shape: rationales + a
        non-canonical `score` int (no `total`), with a sibling top-level
        `variety_score`. Must resolve to a band — NOT unresolvable."""
        variety = _no_fallback_variety(score=12)
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety, "variety_score": 12},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_score_fallback_maps_to_recommended_band(
        self, tmp_path, monkeypatch,
    ):
        """score=8 (no total) → recommended band (7..10)."""
        variety = _no_fallback_variety(score=8)
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_top_level_variety_score_fallback_resolves_band(
        self, tmp_path, monkeypatch,
    ):
        """No `total`, no `score`, but a top-level metadata.variety_score
        int → resolves via candidate 3 (reachable because the caller passes
        Task B's full metadata to the resolver)."""
        variety = _no_fallback_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety, "variety_score": 11},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_dimension_sum_fallback_resolves_band(
        self, tmp_path, monkeypatch,
    ):
        """No total/score/variety_score, but all four dimension scores
        valid → resolves to the sum's band. _well_formed_variety carries
        2/2/2/2 = 8, so popping `total` lands on recommended via the sum."""
        variety = _well_formed_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_junk_total_falls_through_to_dimension_sum_band(
        self, tmp_path, monkeypatch,
    ):
        """An out-of-range `total` does not shadow a recoverable dimension
        sum: total=99 with dims 3/3/3/3=12 → required (the sum's band)."""
        variety = _well_formed_variety(
            total=99, novelty=3, scope=3, uncertainty=3, risk=3,
        )
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"


# =============================================================================
# Fail-open paths: every "unresolvable" return path
# =============================================================================


class TestBandUnresolvableFailOpen:
    """All paths returning "unresolvable". Fail-open by design: each
    failure mode keeps traversal silent so the caller can emit the
    band_unresolvable advisory documenting the gap."""

    def test_task_a_empty_dict(self):
        """read_task_json returns {} → no `blocks` key → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {}, "test-team"
        ) == "unresolvable"

    def test_blocks_key_absent(self):
        """Task A has no blocks key → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"subject": "x"}, "test-team"
        ) == "unresolvable"

    def test_blocks_empty_list(self):
        """Task A.blocks = [] → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": []}, "test-team"
        ) == "unresolvable"

    def test_blocks_is_not_list(self):
        """Task A.blocks = "2" (string, not list) → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": "2"}, "test-team"
        ) == "unresolvable"

    def test_blocks_first_id_non_string(self):
        """Task A.blocks = [123] (int, not string) → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": [123]}, "test-team"
        ) == "unresolvable"

    def test_blocks_first_id_empty_string(self):
        """Task A.blocks = [""] → unresolvable."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": [""]}, "test-team"
        ) == "unresolvable"

    def test_empty_team_name(self):
        """team_name = "" → unresolvable (cannot resolve disk path)."""
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, ""
        ) == "unresolvable"

    def test_task_b_file_missing(self, tmp_path, monkeypatch):
        """Task A.blocks=['999'] but no 999.json on disk → unresolvable."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Create the team directory but no task file
        (tmp_path / ".claude" / "tasks" / "test-team").mkdir(parents=True)
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["999"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_missing_metadata(self, tmp_path, monkeypatch):
        """Task B exists but no metadata key → unresolvable."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
        tasks_dir.mkdir(parents=True)
        task_b = {"id": "2", "subject": "x", "owner": "x"}
        (tasks_dir / "2.json").write_text(json.dumps(task_b), encoding="utf-8")
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_metadata_not_dict(self, tmp_path, monkeypatch):
        """Task B.metadata is a string → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2", metadata="not a dict",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_missing_variety(self, tmp_path, monkeypatch):
        """Task B.metadata is empty dict, no variety key → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_task_b_variety_not_dict(self, tmp_path, monkeypatch):
        """Task B.metadata.variety is a string → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": "not a dict"},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_missing_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety has no `total` key AND no recoverable
        fallback (dimensions stripped) → unresolvable. A missing total alone
        now resolves via the dimension-sum fallback when the four dimensions
        are valid; unresolvable requires every candidate to be invalid."""
        variety = _no_fallback_variety()
        variety.pop("total")
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": variety},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_non_int_string_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = "twelve" with no recoverable
        fallback → unresolvable."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total="twelve")},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_bool_rejected_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = True with no recoverable fallback
        → unresolvable. Defensive: bool is a subclass of int in Python;
        reject explicitly."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total=True)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_variety_total_float_and_no_fallback(self, tmp_path, monkeypatch):
        """Task B.metadata.variety.total = 12.5 with no recoverable fallback
        → unresolvable. The resolver accepts ints only; floats indicate
        malformed data."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _no_fallback_variety(total=12.5)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"


# =============================================================================
# Multi-block defensiveness: first-block convention
# =============================================================================


class TestMultiBlockTraversal:
    """The traversal takes blocks[0] as the canonical Task B pointer.
    Multi-block teachback tasks are not in current convention; this pins
    the behavior so a future schema change explicitly opts in."""

    def test_first_block_is_canonical_work_task(
        self, tmp_path, monkeypatch,
    ):
        """blocks = ['2', '999'] — first id resolves; second is ignored.
        If '2' exists with REQUIRED-band variety, the helper returns
        required regardless of '999' being absent."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2", "999"]}, "test-team"
        ) == "required"

    def test_first_block_dominates_even_if_skipped(
        self, tmp_path, monkeypatch,
    ):
        """blocks = ['low', 'high'] — first resolves to skipped; second
        is ignored even if it would have been required. Pins the
        first-block convention against a future "max-over-blocks" drift."""
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "low",
            metadata={"variety": _well_formed_variety(total=4)},
        )
        _seed_task_b(
            tmp_path, monkeypatch, "test-team", "high",
            metadata={"variety": _well_formed_variety(total=15)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["low", "high"]}, "test-team"
        ) == "skipped"


# =============================================================================
# #891 Opt2: parent-inheritance fallback (_inherit_band_from_parent)
# =============================================================================
#
# When Task B carries no resolvable variety, the band is inherited from the
# PARENT (Plan/feature/umbrella) task that Task B blocks. These tests pin both
# halves of the C2 contract:
#   - RESOLVE: an unstamped Task B whose singleton blocks[0] points at a
#     STAMPED parent inherits the parent's REAL band (the #891 fix — a
#     consultation Task B that would mis-resolve as "skipped" now resolves to
#     the parent's 11-13 reality).
#   - FAIL-OPEN GUARDRAIL: never inherit a WRONG parent's band. Ambiguous
#     blocks (>1 entry, empty, non-list) or a parent that is not itself
#     stamped → "unresolvable" (the preserved floor), NOT a guess.
# =============================================================================


def _seed_task(
    tmp_path, monkeypatch, team_name, task_id, *, metadata=None, blocks=None,
    status=None,
):
    """Seed an arbitrary task on disk with optional `blocks` + `metadata`
    (+ `status`). Used to build the Task B → parent chain the inheritance
    fallback walks."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task = {"id": task_id, "subject": "x", "owner": "pact-backend-coder"}
    if status is not None:
        task["status"] = status
    if blocks is not None:
        task["blocks"] = blocks
    if metadata is not None:
        task["metadata"] = metadata
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(task), encoding="utf-8"
    )


class TestParentInheritanceFallback:
    """#891 Opt2: an unstamped Task B inherits its parent's band."""

    # ----- RESOLVE: parent inheritance maps to the parent's real band ----

    def test_inherits_required_band_from_parent(self, tmp_path, monkeypatch):
        """Task B has no variety but blocks a stamped parent (total=12) →
        the band resolves to the parent's REQUIRED band instead of
        unresolvable. The #891 consultation-Task-B fix."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "required"

    def test_inherits_recommended_band_from_parent(self, tmp_path, monkeypatch):
        """Parent total=9 → inherited band is recommended."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=9)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    def test_inherits_skipped_band_from_parent(self, tmp_path, monkeypatch):
        """Parent total=5 → inherited band is skipped (the parent's REAL
        low band; inheritance is not biased toward required)."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=5)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "skipped"

    def test_stamped_task_b_does_not_inherit(self, tmp_path, monkeypatch):
        """When Task B IS stamped, its OWN band wins — the parent is never
        consulted (inheritance is a fallback, not an override). Task B
        total=7 (recommended); parent total=16 (required) is ignored."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=7)},
            blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=16)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "recommended"

    # ----- FAIL-OPEN GUARDRAILS: never inherit a WRONG parent ------------

    def test_unstamped_b_no_blocks_is_unresolvable(self, tmp_path, monkeypatch):
        """Unstamped Task B with NO blocks pointer → no parent to inherit
        from → unresolvable (floor preserved). This is the legacy shape;
        pins that inheritance does not regress it."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_multi_block_fails_open(self, tmp_path, monkeypatch):
        """Task B.blocks has >1 entry → AMBIGUOUS parent → fail-open to
        unresolvable rather than guess blocks[0]. The lead's
        don't-blindly-trust-blocks[0] guardrail: a wrong-parent inherit
        mis-resolves the band, the exact bug being fixed."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100", "200"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_unstamped_parent_fails_open(self, tmp_path, monkeypatch):
        """Singleton blocks[0] points at a parent that is NOT itself stamped
        → fail-open to unresolvable. The structural 'looks like a
        Plan/feature task' guardrail: only a stamped task is an inheritable
        parent, so a mis-pointed blocks[0] (e.g. at a phase/teachback task)
        cannot inherit a wrong band."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"],
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100", metadata={},
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_unstamped_b_missing_parent_file_fails_open(self, tmp_path, monkeypatch):
        """blocks[0] points at a parent id with no file on disk → fail-open
        to unresolvable (read_task_json returns {})."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["999"],
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"



# =============================================================================
# A closed task's variety is never read (M12, field report row 134)
# =============================================================================
#
# A teachback is gated on the variety of the work it is about to start. A
# Task B, or an inherited parent, that is already completed or deleted is a
# stale pointer: its score belongs to finished work, so gating a new
# teachback on it quotes the wrong dispatch. The band falls to the
# "unresolvable" floor instead, which emits its own advisory.
# =============================================================================


class TestClosedTaskVarietyNeverRead:
    """M12: the band resolver never reads a completed or deleted task."""

    def test_completed_task_b_is_unresolvable(self, tmp_path, monkeypatch):
        """Task A points at a Task B that is already completed and stamped
        REQUIRED (total=12) → unresolvable, not required."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12)},
            status="completed",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_deleted_task_b_is_unresolvable(self, tmp_path, monkeypatch):
        """A deleted Task B is closed too → unresolvable."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12)},
            status="deleted",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_completed_task_b_does_not_fall_through_to_parent(
        self, tmp_path, monkeypatch,
    ):
        """A closed Task B is not an unstamped one: the parent fallback is
        not consulted either, so a stamped open parent (total=12) does not
        rescue a stale pointer."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"], status="completed",
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
            status="in_progress",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_completed_parent_is_not_inherited(self, tmp_path, monkeypatch):
        """An unstamped open Task B whose parent is completed (total=12)
        → unresolvable, not the finished parent's REQUIRED band."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={}, blocks=["100"], status="pending",
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
            status="completed",
        )
        assert tlg._resolve_required_band_via_blocks(
            {"blocks": ["2"]}, "test-team"
        ) == "unresolvable"

    def test_open_statuses_still_resolve(self, tmp_path, monkeypatch):
        """Positive control: pending and in_progress Task B, and an
        in_progress parent, still resolve. Without it the tests above
        would pass on a resolver that returned unresolvable for everything."""
        _seed_task(
            tmp_path, monkeypatch, "test-team", "2",
            metadata={"variety": _well_formed_variety(total=12)},
            status="pending",
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "3",
            metadata={"variety": _well_formed_variety(total=12)},
            status="in_progress",
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "4",
            metadata={}, blocks=["100"], status="pending",
        )
        _seed_task(
            tmp_path, monkeypatch, "test-team", "100",
            metadata={"variety": _well_formed_variety(total=12)},
            status="in_progress",
        )
        for task_b_id in ("2", "3", "4"):
            assert tlg._resolve_required_band_via_blocks(
                {"blocks": [task_b_id]}, "test-team"
            ) == "required", task_b_id
