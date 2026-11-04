"""Tests for shared/teachback_schema.py — canonical teachback schema constants
and the reasoning_reconstruction sub-key validator.

Pins:
  - TEACHBACK_REQUIRED_FIELDS: canonical 5-tuple (4 string fields + variety_acknowledgment)
  - TEACHBACK_REQUIRED_SUBKEYS: canonical 3-tuple (decision_attribution / assumption_trace /
    contingency_clause)
  - TEACHBACK_VARIETY_ACK_VALID_VALUES: 3-tuple enum (yes / no / concern)
  - TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN: 11 (variety-band threshold mirror)
  - validate_reasoning_reconstruction:
      - None on well-formed 3-sub-key triangle of non-empty strings
      - "malformed_reasoning_reconstruction" on not-dict / wrong-keys
      - "empty_reasoning_reconstruction_field" on empty/non-string sub-key

These constants are consumed by hooks/task_lifecycle_gate.py at runtime and by
tests/test_teachback_reasoning_reconstruction.py at the lead-side band-routing
layer. The validator is the SSOT for the schema gate; band routing is
layered on top by callers.
"""

from __future__ import annotations

import pytest

from shared.teachback_schema import (
    TEACHBACK_OBJECT_FIELDS,
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_SCHEMA_ECHO,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
    resolve_variety_total,
    validate_reasoning_reconstruction,
)
from shared.variety_scorer import (
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
)


# ============================================================================
# Constant shape pins
# ============================================================================


class TestConstants:
    """Pin the exact canonical shapes — drift will require paired SKILL.md
    and pact-orchestrator §12 prose edits."""

    def test_required_fields_is_5_tuple(self):
        assert isinstance(TEACHBACK_REQUIRED_FIELDS, tuple)
        assert len(TEACHBACK_REQUIRED_FIELDS) == 5

    def test_required_fields_exact_names(self):
        assert TEACHBACK_REQUIRED_FIELDS == (
            "understanding",
            "most_likely_wrong",
            "least_confident_item",
            "first_action",
            "variety_acknowledgment",
        )


# ============================================================================
# TEACHBACK_SCHEMA_ECHO — the remediation string appended to the schema-invalid
# deny message (#958). Pins the no-hardcoded-copy DERIVATION property and the
# variety_acknowledgment-is-an-OBJECT note. The gate-side integration (echo is
# present in the advisory message) is covered in test_task_lifecycle_gate.py;
# this class pins what the echo SAYS.
# ============================================================================


class TestSchemaEcho:
    """Pin TEACHBACK_SCHEMA_ECHO's content and its derivation from
    TEACHBACK_REQUIRED_FIELDS."""

    def test_echo_is_nonempty_string(self):
        assert isinstance(TEACHBACK_SCHEMA_ECHO, str)
        assert TEACHBACK_SCHEMA_ECHO.strip()

    def test_echo_names_every_required_field(self):
        # Derivation pin (no-hardcoded-copy property): EVERY canonical required
        # field name must appear in the echo. The 4 string fields appear in the
        # joined list; variety_acknowledgment appears in the is-OBJECT note. A
        # field added to TEACHBACK_REQUIRED_FIELDS that the echo's derivation
        # failed to surface would fail here — the echo cannot silently drift
        # from the enforced tuple.
        for field in TEACHBACK_REQUIRED_FIELDS:
            assert field in TEACHBACK_SCHEMA_ECHO, (
                f"echo must name required field {field!r}; "
                f"got: {TEACHBACK_SCHEMA_ECHO!r}"
            )


# ============================================================================
# TEACHBACK_OBJECT_FIELDS — the string/object partition SSOT (#958 Stage 1).
# Both TEACHBACK_SCHEMA_ECHO's comma-list carve-out and the lifecycle gate's
# string-field validator derive their "skip the object fields" set from this
# one tuple. These tests pin the partition and couple the echo's carve-out
# back to it so the two cannot silently desync.
# ============================================================================


class TestObjectFieldsPartition:
    """#958 Future-2 (Stage-1 follow-through): pin TEACHBACK_OBJECT_FIELDS as
    the string/object partition SSOT."""

    def test_object_fields_is_nonempty_tuple(self):
        assert isinstance(TEACHBACK_OBJECT_FIELDS, tuple)
        assert TEACHBACK_OBJECT_FIELDS

    def test_object_fields_subset_of_required(self):
        # Partition validity: every object field must be a canonical required
        # field — an object field outside the required tuple would mean the
        # carve-out skips a name the schema never enforced.
        assert set(TEACHBACK_OBJECT_FIELDS) <= set(TEACHBACK_REQUIRED_FIELDS)


# Doc-surface field enumeration: the surfaces that teach the teachback fields are
# rendered regions pinned by tests/contract/test_protocol_rendered.py.


# ============================================================================
# Validator behavior — well-formed input
# ============================================================================


def _well_formed_triangle() -> dict:
    return {
        "decision_attribution": "I understand X chose Y because Z",
        "assumption_trace": "This depends on A and B",
        "contingency_clause": "If A changes, the decision should change to C",
    }


class TestValidatorAccept:
    """Well-formed inputs return None."""

    def test_canonical_triangle_accepted(self):
        assert validate_reasoning_reconstruction(_well_formed_triangle()) is None

    def test_subkey_with_internal_whitespace_accepted(self):
        rr = _well_formed_triangle()
        rr["assumption_trace"] = "A long\nmulti-line\nrationale is fine"
        assert validate_reasoning_reconstruction(rr) is None


# ============================================================================
# Validator behavior — malformed inputs
# ============================================================================


class TestValidatorMalformed:
    """Wrong type / wrong keys → malformed_reasoning_reconstruction."""

    def test_not_dict_returns_malformed(self):
        assert (
            validate_reasoning_reconstruction("a string")
            == "malformed_reasoning_reconstruction"
        )

    def test_list_returns_malformed(self):
        assert (
            validate_reasoning_reconstruction(["d", "a", "c"])
            == "malformed_reasoning_reconstruction"
        )


# ============================================================================
# Validator behavior — empty/non-string sub-key values
# ============================================================================


class TestValidatorEmptyField:
    """Right keys, wrong values → empty_reasoning_reconstruction_field."""

    def test_empty_string_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["contingency_clause"] = ""
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )

    def test_whitespace_only_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["assumption_trace"] = "   \n   "
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )


# ============================================================================
# Purity / never-raises contract
# ============================================================================


class TestValidatorPurity:
    """Validator never raises — pure function contract."""

    @pytest.mark.parametrize(
        "weird_input",
        [
            42,],
    )
    def test_does_not_raise_on_weird_inputs(self, weird_input):
        # Should return malformed reason, not raise
        result = validate_reasoning_reconstruction(weird_input)
        assert result == "malformed_reasoning_reconstruction"


# ============================================================================
# resolve_variety_total — per-candidate type matrix
#
# The resolver walks an ordered candidate chain (total → score →
# metadata.variety_score → dimension-sum) and returns the first valid
# in-range int, or None when nothing resolves. These tests isolate each
# candidate so the value-type acceptance/rejection contract is pinned for
# every candidate independently; precedence interactions are covered in the
# precedence section below.
#
# In-range means MIN_SCORE..MAX_SCORE (4..16) for the total/score/
# variety_score candidates, and MIN_DIMENSION..MAX_DIMENSION (1..4) per
# dimension for the dimension-sum candidate — referenced via the
# variety_scorer constants, never hard-coded.
# ============================================================================


# A set of values that are NOT a valid in-range int under any candidate.
# `id`s double as readable parametrize labels.
_REJECTED_VALUES = [
    pytest.param(True, id="bool_true"),
    pytest.param(False, id="bool_false"),
    pytest.param(8.0, id="float_whole"),
    pytest.param(8.5, id="float_fractional"),
    pytest.param("8", id="numeric_string"),
    pytest.param("high", id="non_numeric_string"),
    pytest.param(None, id="none"),
    pytest.param([8], id="list"),
    pytest.param({"x": 8}, id="dict"),
]

# Out-of-range ints for the [4, 16] total/score candidates: below MIN_SCORE
# and above MAX_SCORE. These are well-typed ints that still must NOT resolve.
_OUT_OF_RANGE_SCORE_INTS = [
    pytest.param(0, id="zero"),
    pytest.param(MIN_SCORE - 1, id="below_min"),  # 3
    pytest.param(MAX_SCORE + 1, id="above_max"),  # 17
    pytest.param(99, id="far_above_max"),
]

# Boundary + interior in-range ints. The 6/7 and 10/11 pairs straddle the
# band cuts the CALLER applies; the resolver itself returns the int unchanged
# for any of them (band mapping is the caller's job, asserted separately).
_IN_RANGE_SCORE_INTS = [4,]


class TestResolverCandidateTotal:
    """Candidate 1: variety['total'] in isolation (no other candidate
    present, so a fall-through lands on None)."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_total_resolves_to_itself(self, value):
        assert resolve_variety_total({"total": value}) == value


class TestResolverCandidateScore:
    """Candidate 2: variety['score'] in isolation (no 'total' key, so the
    canonical candidate is absent and the chain reaches 'score')."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_score_resolves_to_itself(self, value):
        assert resolve_variety_total({"score": value}) == value


class TestResolverCandidateVarietyScore:
    """Candidate 3: metadata['variety_score'] in isolation — a top-level
    sibling reached only when the `metadata` argument is supplied. The
    variety dict carries no resolvable candidate of its own."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_variety_score_resolves_to_itself(self, value):
        assert resolve_variety_total({}, {"variety_score": value}) == value


def _dimensions(novelty, scope, uncertainty, risk):
    """A variety dict carrying ONLY the four per-dimension scores (no total,
    no score), so the dimension-sum candidate is the only one that can fire."""
    return {
        "novelty": novelty,
        "scope": scope,
        "uncertainty": uncertainty,
        "risk": risk,
    }


class TestResolverCandidateDimensionSum:
    """Candidate 4: sum of the four dimension scores, valid only when ALL
    four are in-range dimension ints (1..4 each). The sum is in [4, 16] by
    construction."""

    def test_all_min_dimensions_sums_to_min_score(self):
        v = _dimensions(MIN_DIMENSION, MIN_DIMENSION, MIN_DIMENSION, MIN_DIMENSION)
        assert resolve_variety_total(v) == MIN_SCORE  # 4


# ============================================================================
# resolve_variety_total — precedence & conflict combinations
# ============================================================================


class TestResolverPrecedence:
    """Ordered first-valid-match precedence and the fall-through-on-invalid
    robustness property: a junk higher-precedence candidate never shadows a
    recoverable lower-precedence one."""

    def test_canonical_total_wins_over_all_other_candidates(self):
        v = {"total": 12, "score": 8, "novelty": 1, "scope": 1,
             "uncertainty": 1, "risk": 1}
        assert resolve_variety_total(v, {"variety_score": 8}) == 12


# ============================================================================
# resolve_variety_total — exception-safety negative property
#
# The hook fires on every Task-tool use; the resolver must be total (defined
# for every input) and never raise. This is the helper-leg of the negative
# property; the hook-level leg (full evaluate_lifecycle envelopes) lives in
# test_task_lifecycle_gate.py.
# ============================================================================


class TestResolverNeverRaises:
    """No input — well-formed, malformed, or exotic — escapes as an
    exception; the resolver always returns an int or None."""

    @pytest.mark.parametrize(
        "variety",
        [
            None,],
    )
    def test_malformed_variety_returns_int_or_none_never_raises(self, variety):
        result = resolve_variety_total(variety)
        assert result is None or isinstance(result, int)


# =============================================================================
# Candidate 3 REACHABILITY — a non-dict `variety` must not short-circuit the
# chain, because candidate 3 does not read `variety` at all.
# =============================================================================
# Every pre-existing candidate-3 test above passes `{}` as the variety — the
# EMPTY DICT, which always worked. None of them covers a variety that is
# ABSENT or NON-DICT, which is the shape the defect actually occurred on, and
# that gap is why the contract could be false while the suite stayed green.
_NON_DICT_VARIETIES = [
    pytest.param(None, id="absent"),]


class TestResolverNonDictVarietyDoesNotShortCircuitCandidate3:
    """INAPPLICABLE IS NOT THE SAME AS UNREACHABLE, and the distinction is the
    whole defect. Candidates 1, 2 and 4 all READ FROM `variety`, so with no
    dict there is nothing for them to read — skipping them costs nothing and
    they are merely inapplicable. Candidate 3 reads only
    `metadata["variety_score"]` and has no dependency on `variety`, so an
    early `return None` on a non-dict `variety` made a DOCUMENTED candidate
    unreachable in exactly the case it exists to serve.

    MUTATION THAT REDDENS: in `resolve_variety_total`, restore the original
    guard — replace `variety = {}` with `return None`. Every test in this
    class flips, and nothing else in this file does.
    """

    @pytest.mark.parametrize("variety", _NON_DICT_VARIETIES)
    def test_sibling_resolves_when_variety_is_not_a_dict(self, variety):
        assert resolve_variety_total(variety, {"variety_score": 12}) == 12


