"""
Tests for orchestration retrospective in wrap-up.md.

Tests cover:
1. Orchestration Retrospective section exists
2. Four assessment questions defined
3. pact-memory save convention documented
4. Estimation pattern question documented
5. Q6 journal-read hardening
6. Q6 extraction is total (no direct-indexing comprehension)
7. Q7 handoff_refusal_degraded question presence pin
"""
import re
from pathlib import Path

import pytest

from shared.teachback_schema import resolve_variety_total  # noqa: E402

WRAPUP_PATH = Path(__file__).parent.parent / "commands" / "wrap-up.md"
# COMMANDS (m8b): the step-4 procedure moved to reference/wrap-up/retrospective.md; the
# retrospective contract is the command plus that reference, so both are read.
RETRO_PATH = Path(__file__).parent.parent / "reference" / "wrap-up" / "retrospective.md"


def _wrapup_text():
    return WRAPUP_PATH.read_text(encoding="utf-8") + "\n" + RETRO_PATH.read_text(encoding="utf-8")


@pytest.fixture
def q6():
    return _question_line(
        _wrapup_text(),
        "6. **Variety acknowledgment signals**",
    )


class TestOrchestrationRetrospective:
    """Tests for orchestration retrospective in wrap-up command."""

    @pytest.fixture
    def wrapup_content(self):
        return _wrapup_text()

    def test_retrospective_section_exists(self, wrapup_content):
        assert "Orchestration Retrospective" in wrapup_content

    def test_variety_accuracy_question(self, wrapup_content):
        assert "Variety accuracy" in wrapup_content

    def test_phase_efficiency_question(self, wrapup_content):
        assert "Phase efficiency" in wrapup_content

    def test_specialist_fit_question(self, wrapup_content):
        assert "Specialist fit" in wrapup_content

    def test_estimation_pattern_question(self, wrapup_content):
        assert "Estimation pattern" in wrapup_content

    def test_memory_save_convention(self, wrapup_content):
        assert "orchestration_calibration" in wrapup_content

    def test_handoff_refusal_degradation_question(self, wrapup_content):
        # Presence pin for the Q7 degrade-telemetry consumer: the heading and
        # the event-type literal it reads are the load-bearing tokens.
        assert "HANDOFF refusal degradations" in wrapup_content
        assert "handoff_refusal_degraded" in wrapup_content


def _backticked_expression(line, prefix):
    """Return the single inline-backticked code span in `line` that starts with
    `prefix`. The retrospective questions carry their extraction code inline, so
    this lifts the ACTUAL documented expression out of the instruction rather
    than restating it in the test — a restatement would drift from the markdown
    silently and pin nothing."""
    for span in re.findall(r"`([^`]+)`", line):
        if span.startswith(prefix):
            return span
    raise AssertionError(f"no backticked expression starting with {prefix!r}")


def _exec_documented_chain(expressions, namespace):
    """Execute the documented extraction chain and return the namespace.

    `exec` is the point, not a shortcut: wrap-up.md is executed by an LLM at
    runtime, so the only way to pin the instruction's BEHAVIOUR (rather than
    grep its wording) is to run the expression the file actually carries. The
    input is repo-controlled markdown.

    THE NAMESPACE IS THE CALLER'S: what a runner puts in the namespace decides
    what the documented text can reach, so a name that is absent is
    unreachable rather than quietly empty.
    """
    if isinstance(expressions, str):
        expressions = [expressions]
    for expression in expressions:
        # noqa: S102, repo-controlled instruction text
        exec(expression, namespace)  # noqa: S102
    return namespace


def _run_q6_extraction(expression, events):
    """Run the Q6 chain. THERE IS NO SNAPSHOT PARAMETER, BY CONSTRUCTION.

    Q6 reads the acknowledgment flags and it does not read the snapshot
    stream. The namespace carries no `snapshot_events` name, so a Q6
    expression that ever reaches for one raises NameError. That failure
    direction is correct: a loud stop beats a silent read of an empty list.
    """
    return _exec_documented_chain(
        expression,
        {
            "events": events,
            "resolve_variety_total": resolve_variety_total,
        },
    )


def _question_line(content, prefix):
    """Return the single wrap-up.md line beginning with `prefix` (each
    retrospective question is one long markdown line). Asserting WITHIN the
    specific question line is stronger than a whole-file substring grep."""
    for line in content.splitlines():
        if line.lstrip().startswith(prefix):
            return line
    raise AssertionError(f"question line not found for prefix {prefix!r}")


class TestRetroReadHardening:
    """#966 (docs-only): wrap-up Q6 journal-read guidance hardening — the
    explicit single-JSON-array parse, the output-masking bans, the
    masked-empty re-read guard, and the Q6 fallback-on-zero that prevents a
    fabricated 0% signal rate from a masked/crashed read.

    These are STRUCTURAL (prose) assertions: they detect REMOVAL or
    rewording-away of the hardening instruction, but cannot prove an LLM
    obeys it at runtime — a known vacuity limit of prose tests, documented
    as a residual risk in the TEST HANDOFF. They are the appropriate
    coverage for a docs-only change whose logic is not extracted to code.
    """

    @pytest.fixture
    def wrapup_content(self):
        return _wrapup_text()


    @pytest.fixture
    def q6(self, wrapup_content):
        return _question_line(wrapup_content, "6. **Variety acknowledgment signals**")



    # --- Q6 structural safety (no explicit re-read guard; rests on the
    #     parse bans + fallback-on-zero) ---

    def test_q6_states_single_json_array_parse(self, q6):
        assert "SINGLE JSON array" in q6
        assert "json.loads(output)" in q6

    def test_q6_bans_output_masking_pipes(self, q6):
        assert "2>/dev/null" in q6
        assert "|| echo" in q6
        assert "`head`" in q6

    def test_q6_fallback_on_zero_prevents_fabricated_signal_rate(self, q6):
        """The structural guarantee that a masked/crashed read (which yields
        zero teachback_ack events) cannot be reported as a real 0% signal
        rate: Q6 falls back to the task store ONLY when the journal yields
        zero teachback_ack events (#966 noted a masked read corrupting Q6 to
        0% vs a true 44%)."""
        assert "zero `teachback_ack` events" in q6
        assert "fall back to the legacy" in q6

    def test_q6_arc_scoped_with_since(self, q6):
        assert "--since" in q6

    def test_q6_carries_masked_empty_reread_guard(self, q6):
        """Q6 now MIRRORS Q5's explicit masked-empty re-read guard (the
        follow-up that closes the Q5/Q6 asymmetry): a masked/crashed
        teachback_ack read must be re-confirmed against the raw journal before
        any conclusion, so it cannot be reported as a real 0% cargo-cult
        signal rate (#966 noted a masked read corrupting Q6 to 0% vs a true
        44%). The guard must precede the fallback-on-zero branch."""
        assert "Masked-empty guard" in q6
        assert "re-read the raw" in q6
        assert "session-journal.jsonl" in q6
        assert "genuinely absent" in q6
        assert "0% signal rate" in q6
        # ordering: the re-read guard precedes the fallback-on-zero clause
        assert q6.index("Masked-empty guard") < q6.index("fall back to the legacy")


class TestTotalExtraction:
    """Both retrospective extractions must be TOTAL — defined for every event
    the journal can hand them — rather than aborting on the first bad one.

    Both questions used a direct-indexing comprehension, so a single event
    missing its key raised `KeyError` and destroyed the WHOLE list, not just
    that event. The blast radius is the session, not the row: measured
    2026-07-28 over 2254 journals, 20 `dispatch_variety` events lacked a usable
    `variety['total']`, and they sat in 8 sessions holding 289 events between
    them.

    These are STRUCTURAL (prose) assertions with the vacuity limit the sibling
    class documents: they detect the guard being removed or reworded away, but
    cannot prove an LLM obeys it at runtime. The negative halves are the
    load-bearing ones — they fail if a direct-indexing form is reinstated.
    """

    @pytest.fixture
    def wrapup_content(self):
        return _wrapup_text()


    @pytest.fixture
    def q6(self, wrapup_content):
        return _question_line(wrapup_content, "6. **Variety acknowledgment signals**")


    def test_q6_guards_the_flag_extraction(self, q6):
        assert "isinstance(e.get(\"rationale_articulates_this_dispatch\"), str)" in q6

    def test_q6_does_not_direct_index_the_flag(self, q6):
        assert "for e in events]" not in q6

    def test_q6_excludes_unreadable_acks_from_both_terms(self, q6):
        """Counting an unreadable ack in the denominator but not the numerator
        dilutes the rate toward "no concern" — the direction that hides the
        signal Q6 exists to surface."""
        assert "total_teachbacks = len(flags)" in q6
        assert "len(events) - len(flags)" in q6

    def test_q6_does_not_divide_when_no_flag_is_readable(self, q6):
        """`len(flags)` can be 0 while `events` is non-empty — a divisor the
        previous `len(events)` form could never produce."""
        assert "total_teachbacks > 0" in q6


class TestQ6ExtractionSurvivesAnUnreadableAck:
    """The Q6 flag extraction, run the same way and for the same reason.

    Measured 2026-07-28: 616 `teachback_ack` events, 0 missing the flag — the
    journal schema types that key `str`, which is why. So this guard is
    defense-in-depth against a shape the corpus has not yet produced, which is
    exactly when it is cheap to add.
    """

    @pytest.fixture
    def q6_expression(self, q6):
        return _backticked_expression(q6, "flags = ")

    def test_extraction_drops_only_the_unreadable_acks(self, q6_expression):
        events = [
            {"task_id": "1", "rationale_articulates_this_dispatch": "yes"},
            {"task_id": "2"},                                        # key absent
            {"task_id": "3", "rationale_articulates_this_dispatch": None},
            {"task_id": "4", "rationale_articulates_this_dispatch": "no"},
            "not-a-dict",
        ]
        assert _run_q6_extraction(q6_expression, events)["flags"] == ["yes", "no"]

    def test_extraction_is_empty_not_raising_when_no_flag_is_readable(
        self, q6_expression
    ):
        """`len(flags) == 0` with `events` non-empty is the divisor the previous
        `len(events)` form could never produce — the case Q6's prose must (and
        does) tell the reader not to divide by."""
        assert _run_q6_extraction(q6_expression, [{"task_id": "1"}])["flags"] == []
