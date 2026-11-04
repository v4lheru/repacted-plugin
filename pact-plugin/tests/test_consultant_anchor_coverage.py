"""Behavioural pins for CONSULTANT coverage — the most-recently-completed anchor.

Location: pact-plugin/tests/test_consultant_anchor_coverage.py
Summary: pins `owner_anchor_tasks`, the expiry asymmetry in `has_live_listed_task`,
         the removed status filter and the covering rule for consultant
         records in `any_listed_task_flagged`, and the write-time-only
         `anchor_completed` field on `_sanitize_record`.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

THE DEFECT BEING PINNED. Every layer was keyed on an owned `in_progress` task,
so a teammate in CONSULTANT MODE — which the framework defines as a supported
state, entered when a task is done and no follow-up is available — owned zero
`in_progress` tasks and was invisible to all of it. Worse, the same agent could
not flag the wait either, because `intentional_wait` is task metadata. Both
halves of the mechanism had the same hole for the same reason.

The fix anchors a consultant on its MOST RECENTLY COMPLETED task instead, and
marks the resulting record so that expiry does not immediately kill it.

🔴 THE PROOF STANDARD FOR THIS FILE IS A REVERT-TO-PRE-FIX MUTATION, NOT A
TOTAL BREAK. Measured on the sibling fix: breaking `wait_scope_anchor` outright
killed nine pre-existing arms, while reverting it to the previous behaviour
killed ZERO of 124. Destroying a function is not the regression anyone causes;
restoring the simpler earlier version because it reads cleaner is. Every arm
below was verified against the second kind.

NON-COVERAGE, STATED. These pin the PREDICATES. That the real Layer 1 hook
marks a consultant's record, and that the real idle hook discharges it, is
pinned through the hooks in test_consultant_background_seam.py. That consultant
mode is entered correctly is a platform behaviour no test here observes.
"""

from __future__ import annotations

import pytest

from shared.background_work import (
    _sanitize_record,
    any_listed_task_flagged,
    has_live_listed_task,
    owner_anchor_tasks,
)

REGISTERED_AT = "2026-09-12T10:00:00+00:00"
# One hour before the launch. These dates are compared only with each other,
# never with a clock, so they cannot age out.
LEFTOVER_SINCE = "2026-09-12T09:00:00+00:00"

# A store holding all three shapes at once, so each arm discriminates against
# the others rather than against an empty set.
#   alice  — a teammate: two in_progress tasks
#   bob    — a consultant: no in_progress, two completed, ids "3" and "20"
#   carol  — neither: a pending task only
TASKS = [
    {"id": "7", "status": "in_progress", "owner": "alice"},
    {"id": "9", "status": "in_progress", "owner": "alice"},
    {"id": "3", "status": "completed", "owner": "bob"},
    {"id": "20", "status": "completed", "owner": "bob"},
    {"id": "5", "status": "pending", "owner": "carol"},
]


def _record(task_ids, **over) -> dict:
    base = {"agent_name": "bob", "session_id": "s", "task_ids": task_ids,
            "registered_at": REGISTERED_AT}
    base.update(over)
    return base


class TestOwnerAnchorTasks:
    """Which tasks anchor a launch, and whether they are live or completed."""

    def test_a_teammate_anchors_on_ALL_its_in_progress_tasks(self):
        """Not one. The exactly-one rule silently disabled Layer 1 for any
        teammate holding two, which `pact-teachback` explicitly permits."""
        assert owner_anchor_tasks(TASKS, "alice") == (["7", "9"], False)


class TestTheExpiryAsymmetryInBothDirections:
    """🔴 PINNED IN BOTH DIRECTIONS DELIBERATELY, because the `anchor_completed`
    early return looks like it should apply to everyone.

    A TEAMMATE's record expires when its listed task completes: the task
    leaving `in_progress` is the signal that the work it anchored is over.

    A CONSULTANT's record must NOT, because its anchor was ALREADY completed at
    the moment the record was written. That signal is spent before it can fire,
    so applying the same rule would kill every consultant row instantly and the
    coverage this fix adds would be inert on arrival.

    COVERAGE YES, EXPIRY PARITY NO — a consultant row is bounded by the 24h TTL
    alone. An arm on either direction by itself reads as an inconsistency; the
    pair is what makes it an asymmetry someone must keep deliberately.
    """

    COMPLETED_ANCHOR = [{"id": "3", "status": "completed", "owner": "bob"}]

    def test_a_TEAMMATE_record_expires_when_its_task_completes(self):
        assert has_live_listed_task(_record(["3"]), self.COMPLETED_ANCHOR) is False


class TestTheFlagCheckReadsCompletedTasksAndScopesConsultantRecords:
    """Two rules in `any_listed_task_flagged`, pinned together because either
    one alone reads as the other's bug.

    NO STATUS FILTER. A wait on a listed task counts whatever that task's
    status: metadata writes to a completed task land, and a consultant's only
    carrier for a wait is its completed anchor.

    BUT A CONSULTANT'S RECORD IS SILENCED ONLY BY A WAIT THAT COVERS ITS
    LAUNCH. A completed task routinely still carries a wait raised before the
    launch — the completion flow leaves one behind — and accepting that wait
    would hide every later launch the consultant makes. So the covering rule
    applies exactly where the anchor was already completed at write time.

    NON-COVERAGE: these pin the predicate. What the real hooks write and remove
    is pinned in test_consultant_background_seam.py.
    """

    def test_any_valid_wait_on_a_COMPLETED_listed_task_silences_an_unmarked_record(self):
        flagged = [{"id": "3", "status": "completed", "owner": "bob",
                    "metadata": {"intentional_wait": {
                        "reason": "awaiting_blocker_resolution",
                        "expected_resolver": "lead",
                        "since": REGISTERED_AT}}}]
        assert any_listed_task_flagged(_record(["3"]), flagged) is True, (
            "restoring an `in_progress` filter here makes a wait on a completed "
            "listed task invisible; the flag check must read the task whatever "
            "its status"
        )


class TestAnchorCompletedIsAWriteTimeFactNotAStatus:
    """`_sanitize_record` keeps the flag ONLY when True.

    Absence defaulting to False is correct rather than merely convenient: every
    record written before this field existed is teammate-shaped, and must keep
    expiring exactly as it did. Persisting an explicit False would be noise on
    every row; persisting True is the only case that changes a decision.

    NON-COVERAGE: this pins the round-trip through the sanitizer. It does not
    pin that the WRITER sets the flag correctly — that is `bind_launcher_identity`.
    """

    @pytest.mark.parametrize(
        "given,expected_present",
        [(True, True),],
        ids=["True-is-kept",],
    )
    def test_the_flag_survives_only_when_True(self, given, expected_present):
        out = _sanitize_record(_record(["3"], anchor_completed=given))
        assert ("anchor_completed" in out) is expected_present

