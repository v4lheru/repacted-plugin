"""
Tests for hooks/shared/session_state.py — single-session state summarizer
that replaces task_scanner.py (#411 root cause).

Covers the 18-test matrix from the design notes
§3.5 + 3 explicit regression guards for #411 and #412 Fix A:
- iteration-order independence (filesystem order must not influence output)
- no-foreign-session-read (a second session's journal in the same parent
  must not leak into the summary)
- no-cross-team-scan (a sibling team's config.json must not surface members)

Every test drives the public API via explicit args (session_dir, team_name,
tasks_base_dir, teams_base_dir) — no reliance on pact_context. The
underlying read path is real file I/O (synthetic journal.jsonl + tmp
~/.claude/{teams,tasks}/{team_name}/ fixtures), per the HIGH-risk-tier
constraint of using real fixtures rather than mocks.

Risk tier: HIGH (regression guards directly encode the #411 / #412 bug
invariants; they are load-bearing).
"""

from __future__ import annotations
import json
from pathlib import Path

import pytest

from shared.session_journal import make_event
from shared.session_state import (
    _derive_feature_from_journal,
    _derive_phase_from_journal,
    _derive_variety_from_journal,
    _is_safe_path_component,
    _read_task_counts,
    _read_team_members,
    _sanitize_member_name,
    _default_state,
    summarize_session_state,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_journal(session_dir: Path, events: list[dict]) -> Path:
    """
    Write a list of journal events as JSONL to {session_dir}/session-journal.jsonl.

    Creates the session_dir if it does not exist. Returns the journal path.
    Events are written in the order given (supports iteration-order testing
    by letting callers shuffle timestamps vs line order).
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    journal = session_dir / "session-journal.jsonl"
    with journal.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return journal


def _write_team_config(
    teams_base: Path,
    team_name: str,
    members: list[str],
) -> Path:
    """
    Write ~/.claude/teams/{team_name}/config.json shape: members is a list
    of dicts each with {"name": <str>}. Returns the config path.
    """
    team_dir = teams_base / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config_path = team_dir / "config.json"
    config_path.write_text(
        json.dumps({
            "name": team_name,
            "members": [{"name": n} for n in members],
        }),
        encoding="utf-8",
    )
    return config_path


def _partial_event(event_type: str, **fields: object) -> dict:
    """
    Construct a deliberately-partial journal event for malformed-input tests.

    Mirrors session_journal.make_event's shape (sets `v` and `type`) but
    intentionally accepts callers that omit required fields like `phase`,
    `task_id`, or `ts`. Use this for tests that exercise per-event
    isinstance / truthiness guards in the deriver helpers.

    DO NOT use this for happy-path tests — use make_event there. The
    distinction matters: if session_journal ever gains read-path schema
    validation (currently only append-side), happy-path tests should still
    pass; malformed-input tests will keep working because they bypass the
    write path entirely (callers feed events directly to the derivers).
    """
    event: dict = {"v": 1, "type": event_type}
    event.update(fields)
    return event


def _write_task(
    tasks_base: Path,
    team_name: str,
    task_id: str,
    status: str,
    subject: str = "",
    metadata: dict | None = None,
) -> Path:
    """
    Write ~/.claude/tasks/{team_name}/{task_id}.json with the canonical
    task shape. Returns the path.
    """
    team_dir = tasks_base / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    task_path = team_dir / f"{task_id}.json"
    task_path.write_text(
        json.dumps({
            "id": task_id,
            "subject": subject,
            "status": status,
            "metadata": metadata or {},
        }),
        encoding="utf-8",
    )
    return task_path


# ---------------------------------------------------------------------------
# Matrix row 1-6: TestJournalFields — journal-sourced fields only
# ---------------------------------------------------------------------------


class TestJournalFields:
    """Tests for the four journal-sourced fields:
    feature_subject, feature_id, current_phase, variety_score."""

    def test_happy_path_all_journal_fields(self, tmp_path):
        """Row 1: variety + dispatch + phase events populate all journal-sourced fields."""
        session_dir = tmp_path / "session-abc"
        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="5",
                       variety={"score": 7, "level": "MEDIUM"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("phase_transition", phase="PREPARE", status="completed",
                       ts="2026-04-14T00:00:03Z"),
            make_event("phase_transition", phase="ARCHITECT", status="started",
                       ts="2026-04-14T00:00:04Z"),
            make_event("agent_dispatch", agent="architect", task_id="9",
                       phase="ARCHITECT", ts="2026-04-14T00:00:05Z"),
            make_event("agent_handoff", agent="architect", task_id="5",
                       task_subject="Build auth flow",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:06Z"),
        ])

        # Call with explicit empty team_name to isolate journal-only fields
        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "no-tasks"),
            teams_base_dir=str(tmp_path / "no-teams"),
        )

        assert result["current_phase"] == "ARCHITECT"
        assert result["feature_id"] == "5"
        assert result["feature_subject"] == "Build auth flow"
        assert result["variety_score"] == {"score": 7, "level": "MEDIUM"}

    def test_empty_journal_defaults_fail_open(self, tmp_path):
        """Row 2: empty events list → all journal fields fall back to None."""
        session_dir = tmp_path / "session-empty"
        _write_journal(session_dir, [])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "nx"),
            teams_base_dir=str(tmp_path / "nx"),
        )

        assert result["feature_id"] is None
        assert result["feature_subject"] is None
        assert result["current_phase"] is None
        assert result["variety_score"] is None


        # Bad line was silently skipped — no raise, all good events present


# ---------------------------------------------------------------------------
# Matrix row 7-9: TestTeamMembers — _read_team_members exercises
# ---------------------------------------------------------------------------


class TestTeamMembers:
    """Tests for _read_team_members — reads
    ~/.claude/teams/{team_name}/config.json."""

    def test_members_list(self, tmp_path):
        """Row 7: config.json members → teammates list in order."""
        teams = tmp_path / "teams"
        _write_team_config(teams, "pact-test", ["coder", "tester", "architect"])

        names = _read_team_members("pact-test", teams_base_dir=str(teams))

        assert names == ["coder", "tester", "architect"]

    def test_config_missing_empty_list(self, tmp_path):
        """Row 8: config.json missing → []. Fail-open."""
        teams = tmp_path / "teams"
        # Do not create any config
        assert _read_team_members("pact-ghost", teams_base_dir=str(teams)) == []


# ---------------------------------------------------------------------------
# Matrix row 10-12: TestTaskCounts — _read_task_counts exercises
# ---------------------------------------------------------------------------


class TestTaskCounts:
    """Tests for _read_task_counts — reads
    ~/.claude/tasks/{team_name}/*.json and counts by status."""

    def test_mixed_status_counts(self, tmp_path):
        """Row 10: N tasks with mixed statuses → counts sum to total."""
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "completed", "Task 1")
        _write_task(tasks, "pact-test", "2", "completed", "Task 2")
        _write_task(tasks, "pact-test", "3", "in_progress", "Task 3")
        _write_task(tasks, "pact-test", "4", "pending", "Task 4")
        _write_task(tasks, "pact-test", "5", "pending", "Task 5")
        _write_task(tasks, "pact-test", "6", "pending", "Task 6")

        counts = _read_task_counts("pact-test", tasks_base_dir=str(tasks))

        assert counts["completed"] == 2
        assert counts["in_progress"] == 1
        assert counts["pending"] == 3
        assert counts["total"] == 6

    def test_empty_tasks_dir_all_zero(self, tmp_path):
        """Row 11: empty tasks dir → all zero."""
        tasks = tmp_path / "tasks"
        # Create the team dir but no task files
        (tasks / "pact-empty").mkdir(parents=True)

        counts = _read_task_counts("pact-empty", tasks_base_dir=str(tasks))

        assert counts == {"completed": 0, "in_progress": 0, "pending": 0,
                          "total": 0}


# ---------------------------------------------------------------------------
# Matrix row 13-15: TestSummarize — full integration contract
# ---------------------------------------------------------------------------


class TestSummarize:
    """Tests for summarize_session_state — the full 10-key contract."""

    def test_full_integration_dict_shape(self, tmp_path):
        """Row 13: all inputs populated → dict has all 10 keys with correct types."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="1",
                       variety={"score": 5, "level": "MEDIUM"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="CODE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("agent_handoff", agent="coder", task_id="1",
                       task_subject="Build dashboard",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:03Z"),
        ])
        _write_team_config(teams, "pact-test", ["coder", "tester"])
        _write_task(tasks, "pact-test", "1", "in_progress", "Build dashboard")
        _write_task(tasks, "pact-test", "2", "pending", "Something else")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # The 10 required keys must be present; additive future fields are
        # permitted (subset-not-equality keeps this test stable across
        # forward-compatible schema growth, while still failing if any
        # required key is removed or renamed).
        required_keys = {
            "completed", "in_progress", "pending", "total",
            "feature_subject", "feature_id", "current_phase",
            "variety_score", "teammates", "team_names",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing required keys: {required_keys - set(result.keys())}"
        )
        # Types
        assert isinstance(result["completed"], int)
        assert isinstance(result["in_progress"], int)
        assert isinstance(result["pending"], int)
        assert isinstance(result["total"], int)
        assert isinstance(result["feature_subject"], str)
        assert isinstance(result["feature_id"], str)
        assert isinstance(result["current_phase"], str)
        assert isinstance(result["variety_score"], dict)
        assert isinstance(result["teammates"], list)
        assert isinstance(result["team_names"], list)
        # Values
        assert result["completed"] == 0
        assert result["in_progress"] == 1
        assert result["pending"] == 1
        assert result["total"] == 2
        assert result["feature_subject"] == "Build dashboard"
        assert result["feature_id"] == "1"
        assert result["current_phase"] == "CODE"
        assert result["variety_score"] == {"score": 5, "level": "MEDIUM"}
        assert result["teammates"] == ["coder", "tester"]
        assert result["team_names"] == ["pact-test"]

    def test_team_name_empty_fails_open_disk_fields(self, tmp_path):
        """Row 14: team_name empty → disk fields default; journal fields still populated."""
        session_dir = tmp_path / "session"
        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="99",
                       variety={"score": 2},
                       ts="2026-04-14T00:00:01Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
            tasks_base_dir=str(tmp_path / "nx"),
            teams_base_dir=str(tmp_path / "nx"),
        )

        assert result["feature_id"] == "99"
        assert result["variety_score"] == {"score": 2}
        assert result["completed"] == 0
        assert result["in_progress"] == 0
        assert result["pending"] == 0
        assert result["total"] == 0
        assert result["teammates"] == []
        assert result["team_names"] == []


# ---------------------------------------------------------------------------
# Matrix row 16: TestIterationOrder — REGRESSION GUARD #1
# ---------------------------------------------------------------------------


class TestIterationOrderIndependence:
    """REGRESSION GUARD #1: feature_id derivation must depend on event
    timestamps, NOT on line order or filesystem iteration order.

    Old task_scanner.analyze_task_state used filesystem iterdir() which
    is nondeterministic across platforms. The journal-based derivation
    must be ts-ordered and platform-independent."""

    def test_dispatch_events_line_order_does_not_shadow_ts_order(self, tmp_path):
        """Line order 1→2→3 but ts order 2→1→3.

        Expected: feature_id corresponds to the chronologically-earliest
        timestamp (ts="2026-04-14T00:00:50Z"), NOT the line-first event."""
        session_dir = tmp_path / "session-order"
        _write_journal(session_dir, [
            # Line 1, ts=100 — written first but NOT earliest
            make_event("agent_dispatch", agent="a1", task_id="LINE1",
                       phase="CODE",
                       ts="2026-04-14T00:01:40Z"),
            # Line 2, ts=50 — chronologically earliest; should win
            make_event("agent_dispatch", agent="a2", task_id="LINE2",
                       phase="CODE",
                       ts="2026-04-14T00:00:50Z"),
            # Line 3, ts=200
            make_event("agent_dispatch", agent="a3", task_id="LINE3",
                       phase="CODE",
                       ts="2026-04-14T00:03:20Z"),
        ])

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="",
        )

        # The fallback uses chronologically-first agent_dispatch.task_id
        # (when no variety_assessed is present). LINE2 has the earliest ts.
        assert result["feature_id"] == "LINE2", (
            f"Expected feature_id='LINE2' (earliest ts), got "
            f"{result['feature_id']!r}. File-order-based derivation would "
            f"return 'LINE1' — this test guards against that regression."
        )

    # NOTE — `test_phase_transition_order_invariance` was REMOVED in cycle 3
    # (PR #426 round-2 finding F2). It claimed to counter-test the outer
    # `sorted()` call in `_derive_phase_from_journal`, but the deriver
    # buckets events by phase NAME first into `latest_per_phase` and then
    # applies `ts >= prev[0]` per bucket. Different-phase events cannot
    # collide, so the outer sort is only meaningful for SAME-phase tied-ts
    # events — which `TestDerivePhaseFromJournal::test_derive_phase_tied_ts_completed_wins`
    # already covers. The removed test passed regardless of whether the
    # outer `sorted()` was present, so it provided a false sense of
    # coverage. Do not re-add a "shuffled phase line order" test unless
    # it constructs a fixture where the outer sort genuinely changes the
    # outcome (none currently identified).


# ---------------------------------------------------------------------------
# Matrix row 17: TestNoJournalCrossSession — REGRESSION GUARD #2
# ---------------------------------------------------------------------------


class TestNoForeignSessionRead:
    """REGRESSION GUARD #2: the summarizer must only read THIS session's
    journal. A neighbor session's journal (same parent dir) must NOT leak.

    This guards against a regression of #411's root cause: the old
    task_scanner iterated across the parent and surfaced phantom state
    from unrelated sessions."""

    def test_foreign_session_journal_not_read(self, tmp_path):
        """Create two sessions under a common parent — pass session_dir=B;
        assert none of A's events surface."""
        common = tmp_path / "pact-sessions"
        session_a = common / "session-A"
        session_b = common / "session-B"

        # Session A — feature_id "AAA", phase "PREPARE" (distinct values)
        _write_journal(session_a, [
            make_event("variety_assessed", task_id="AAA",
                       variety={"score": 9, "level": "FOREIGN"},
                       ts="2026-04-14T00:00:01Z"),
            make_event("phase_transition", phase="PREPARE", status="started",
                       ts="2026-04-14T00:00:02Z"),
            make_event("agent_handoff", agent="foreign-agent", task_id="AAA",
                       task_subject="FOREIGN SUBJECT that must not leak",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:03Z"),
        ])

        # Session B — feature_id "BBB", phase "TEST"
        _write_journal(session_b, [
            make_event("variety_assessed", task_id="BBB",
                       variety={"score": 2, "level": "LOCAL"},
                       ts="2026-04-14T00:00:04Z"),
            make_event("phase_transition", phase="TEST", status="started",
                       ts="2026-04-14T00:00:05Z"),
            make_event("agent_handoff", agent="local-agent", task_id="BBB",
                       task_subject="LOCAL SUBJECT",
                       handoff={"produced": [], "decisions": []},
                       ts="2026-04-14T00:00:06Z"),
        ])

        # Query session B
        result = summarize_session_state(
            session_dir=str(session_b),
            team_name="",
        )

        # None of session A's data leaks
        assert result["feature_id"] == "BBB"
        assert result["feature_subject"] == "LOCAL SUBJECT"
        assert result["current_phase"] == "TEST"
        assert result["variety_score"] == {"score": 2, "level": "LOCAL"}
        # The FOREIGN values must never appear
        assert "AAA" not in (result.get("feature_id") or "")
        assert "FOREIGN" not in json.dumps(result)
        assert "foreign" not in json.dumps(result).lower()


# ---------------------------------------------------------------------------
# Matrix row 18: TestNoCrossTeamScan — REGRESSION GUARD #3
# ---------------------------------------------------------------------------


class TestNoCrossTeamScan:
    """REGRESSION GUARD #3: #411 root cause. task_scanner.scan_team_members
    did ~/.claude/teams/*.iterdir(), surfacing phantom members from
    OTHER teams into the current session's state.

    The new _read_team_members must ONLY read
    ~/.claude/teams/{team_name}/config.json — never the parent
    directory — so a sibling team's members stay invisible."""

    def test_sibling_team_members_do_not_leak(self, tmp_path):
        """Two teams under tmp_teams/: pact-test (empty members) and
        pact-ghost (phantom members). Query pact-test → teammates=[]."""
        teams = tmp_path / "teams"

        # Our team — empty members
        _write_team_config(teams, "pact-test", [])

        # Ghost team — phantom members that must never surface
        _write_team_config(teams, "pact-ghost", [
            "ghost-phantom-1", "ghost-phantom-2", "ghost-phantom-3"
        ])

        # Tasks similarly segregated
        tasks = tmp_path / "tasks"
        _write_task(tasks, "pact-test", "1", "in_progress", "Real task")
        _write_task(tasks, "pact-ghost", "99", "completed", "Ghost task")
        _write_task(tasks, "pact-ghost", "100", "in_progress", "Another ghost")

        result = summarize_session_state(
            session_dir="",
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # Our team's reality — empty members + single task
        assert result["teammates"] == []
        assert result["team_names"] == ["pact-test"]
        assert result["total"] == 1
        assert result["in_progress"] == 1
        assert result["completed"] == 0

        # Structural assertions — primary guard. Each ghost member must
        # be absent from the typed teammates list (membership-not-substring),
        # and the ghost team must not surface in team_names. These are
        # robust against future numeric-string fields being added to the
        # output dict (whereas raw substring checks on json.dumps could
        # false-positive on e.g. a future variety_score == 99).
        for ghost in ("ghost-phantom-1", "ghost-phantom-2", "ghost-phantom-3"):
            assert ghost not in result["teammates"]
        assert "pact-ghost" not in result["team_names"]
        # Ghost task counts must not have inflated the totals: pact-ghost
        # has 2 tasks; if cross-team scan had occurred, total would be 3.
        assert result["total"] != 3

        # Defense-in-depth — broad raw-string sweep. Kept as a backstop
        # in case a future field surfaces ghost data via an unexpected
        # path (e.g., a debug field). The structural asserts above are
        # the load-bearing checks; these are belt-and-suspenders.
        raw = json.dumps(result)
        assert "ghost-phantom" not in raw
        assert "pact-ghost" not in raw

    def test_sibling_team_subject_does_not_leak_into_feature_subject(
        self, tmp_path,
    ):
        """The disk fallback for feature_subject must only read
        tasks/{team_name}/{feature_id}.json — NOT scan a sibling team.

        Write a journal pointing at feature_id=42 without a handoff
        (forcing disk fallback). Put task 42 ONLY in the ghost team.
        Assert feature_subject stays None."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            # Dispatch with no handoff → no journal subject → disk fallback
            make_event("agent_dispatch", agent="coder", task_id="42",
                       phase="CODE",
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", [])
        # Task 42 exists ONLY in the ghost team
        _write_task(tasks, "pact-ghost", "42", "in_progress",
                    "PHANTOM FEATURE SUBJECT")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        # The journal identified feature_id=42, but the disk fallback is
        # scoped to pact-test/42.json (which does not exist) — so the
        # phantom subject from pact-ghost/42.json must NOT leak.
        # Structural primary check: feature_subject is the field that
        # would carry the leaked subject — assert it directly. The raw
        # substring sweep is defense-in-depth.
        assert result["feature_id"] == "42"
        assert result["feature_subject"] is None
        assert result["feature_subject"] != "PHANTOM FEATURE SUBJECT"
        # Defense-in-depth raw sweep
        assert "PHANTOM" not in json.dumps(result)


# ---------------------------------------------------------------------------
# Private helpers — direct unit coverage
# ---------------------------------------------------------------------------


class TestDefaultState:
    """Tests for _default_state — used both as accumulator and fail-open return."""

    def test_default_state_shape(self):
        result = _default_state("pact-x")
        assert set(result.keys()) == {
            "completed", "in_progress", "pending", "total",
            "feature_subject", "feature_id", "current_phase",
            "variety_score", "teammates", "team_names",
        }
        assert result["team_names"] == ["pact-x"]
        assert result["teammates"] == []
        assert result["completed"] == 0
        assert result["feature_id"] is None

    def test_default_state_empty_team_name(self):
        result = _default_state("")
        assert result["team_names"] == []


class TestDerivePhaseFromJournal:
    """Direct tests for _derive_phase_from_journal beyond integration."""

    def test_no_events(self):
        assert _derive_phase_from_journal([]) is None

    def test_non_phase_events_ignored(self):
        events = [
            make_event("variety_assessed", task_id="1",
                       variety={"score": 1}, ts="2026-04-14T00:00:01Z"),
            make_event("agent_dispatch", agent="c", task_id="1", phase="CODE",
                       ts="2026-04-14T00:00:02Z"),
        ]
        assert _derive_phase_from_journal(events) is None


class TestDeriveFeatureFromJournal:
    """Direct tests for _derive_feature_from_journal edge cases."""

    def test_variety_preferred_over_dispatch(self):
        """variety_assessed wins over agent_dispatch for feature_id."""
        events = [
            make_event("agent_dispatch", agent="c", task_id="DISPATCH-ID",
                       phase="CODE", ts="2026-04-14T00:00:01Z"),
            make_event("variety_assessed", task_id="VARIETY-ID",
                       variety={"score": 1},
                       ts="2026-04-14T00:00:02Z"),
        ]
        feature_id, _ = _derive_feature_from_journal(events)
        assert feature_id == "VARIETY-ID"

    def test_handoff_subject_matched_by_task_id(self):
        """Handoff subject only applies when task_id matches feature_id."""
        events = [
            make_event("variety_assessed", task_id="F-1",
                       variety={"score": 1}, ts="2026-04-14T00:00:01Z"),
            # Unrelated handoff for a different task
            make_event("agent_handoff", agent="a", task_id="OTHER-99",
                       task_subject="UNRELATED",
                       handoff={}, ts="2026-04-14T00:00:02Z"),
            # Matching handoff
            make_event("agent_handoff", agent="b", task_id="F-1",
                       task_subject="CORRECT",
                       handoff={}, ts="2026-04-14T00:00:03Z"),
        ]
        feature_id, subject = _derive_feature_from_journal(events)
        assert feature_id == "F-1"
        assert subject == "CORRECT"


class TestDeriveVarietyFromJournal:
    """Direct tests for _derive_variety_from_journal."""

    def test_no_variety_events_returns_none(self):
        assert _derive_variety_from_journal([]) is None

    def test_latest_variety_event_wins(self):
        """The LATEST (current-arc) feature-level event's variety wins —
        the same selection _derive_feature_from_journal applies, so a
        resumed session cannot render one arc's feature id under another
        arc's variety."""
        events = [
            make_event("variety_assessed", task_id="1",
                       variety={"first": True},
                       ts="2026-04-14T00:00:01Z"),
            make_event("variety_assessed", task_id="2",
                       variety={"second": True},
                       ts="2026-04-14T00:00:02Z"),
        ]
        assert _derive_variety_from_journal(events) == {"second": True}


# ---------------------------------------------------------------------------
# Full behavior: feature_subject disk fallback
# ---------------------------------------------------------------------------


class TestTaskBStampedBeforeItsGate:
    """FIX-STATE (f5). journal_emitter writes a FEATURE-level variety row for a Task B
    stamped before its Task A exists (its early guess), then a DISPATCH row for the same
    task at the wiring. The feature pick must never adopt that Task B: a task whose
    LATEST row (session_journal.latest_variety_by_task, journal order) is dispatch-marked
    is skipped. A task whose latest row is a feature re-stamp stays eligible (lead
    ruling, amend_1 on #132)."""

    FEATURE = make_event("variety_assessed", task_id="1", variety={"total": 10},
                         ts="2026-09-27T01:00:00Z")
    B_EARLY_GUESS = make_event("variety_assessed", task_id="7", variety={"total": 6},
                               ts="2026-09-27T01:05:00Z")
    B_WIRED = make_event("variety_assessed", task_id="7", scope="dispatch",
                         variety={"total": 6}, ts="2026-09-27T01:06:00Z")

    def test_the_rewired_task_b_is_never_the_feature(self):
        events = [self.FEATURE, self.B_EARLY_GUESS, self.B_WIRED]
        assert _derive_feature_from_journal(events)[0] == "1"
        assert _derive_variety_from_journal(events) == {"total": 10}

    def test_before_the_wiring_lands_the_early_guess_still_reads_as_a_feature(self):
        """Control: the fix keys on the dispatch row; without one there is nothing to skip."""
        events = [self.FEATURE, self.B_EARLY_GUESS]
        assert _derive_feature_from_journal(events)[0] == "7"


class TestFeatureSubjectDiskFallback:
    """Tests for the disk-read fallback for feature_subject when the
    journal identifies feature_id but no matching agent_handoff exists."""

    def test_disk_fallback_reads_task_subject(self, tmp_path):
        """variety_assessed without handoff → disk fallback supplies subject."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="17",
                       variety={"score": 4},
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", ["coder"])
        _write_task(tasks, "pact-test", "17", "in_progress",
                    "Fallback subject from disk")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["feature_id"] == "17"
        assert result["feature_subject"] == "Fallback subject from disk"

    def test_disk_fallback_filters_system_prefixes(self, tmp_path):
        """Disk fallback must skip task subjects starting with system
        prefixes (Phase:, BLOCKER:, ALERT:, HALT:). Regression guard."""
        session_dir = tmp_path / "session"
        teams = tmp_path / "teams"
        tasks = tmp_path / "tasks"

        _write_journal(session_dir, [
            make_event("variety_assessed", task_id="42",
                       variety={"score": 4},
                       ts="2026-04-14T00:00:01Z"),
        ])
        _write_team_config(teams, "pact-test", [])
        # Task 42 has a Phase: prefix subject — must NOT be used as feature
        _write_task(tasks, "pact-test", "42", "in_progress",
                    "Phase: CODE")

        result = summarize_session_state(
            session_dir=str(session_dir),
            team_name="pact-test",
            tasks_base_dir=str(tasks),
            teams_base_dir=str(teams),
        )

        assert result["feature_id"] == "42"
        # Phase:-prefixed subject is NOT accepted as feature_subject
        assert result["feature_subject"] is None


# ---------------------------------------------------------------------------
# TestIsSafePathComponent — cycle-2 regression: _is_safe_path_component
# must reject "..", ".", and other path-traversal fragments. Prior
# implementation used `Path(value).name == value` which returned True
# for ".." (since `Path("..").name == ".."`), admitting a one-directory
# escape at the disk-helper boundary. See security-engineer memory
# the Path.name escape rule.
# ---------------------------------------------------------------------------


class TestIsSafePathComponent:
    """Regression guards for the path-component allowlist primitive."""

    def test_rejects_parent_directory_reference(self):
        # The cycle-1 bug: `Path("..").name` returns ".." verbatim,
        # not empty string — the old identity-check admitted this.
        assert _is_safe_path_component("..") is False

    def test_rejects_current_directory_reference(self):
        assert _is_safe_path_component(".") is False


class TestReadHelpersRejectTraversal:
    """
    End-to-end regression guards that the three disk-reading helpers
    refuse to compose path-traversal components. These exercise the
    integration of `_is_safe_path_component` with the helpers.
    """

    def test_read_team_members_rejects_parent_reference(self, tmp_path):
        # If team_name=".." were accepted, we'd read
        # <tmp_path/teams/../config.json> == <tmp_path/config.json>.
        # Place a file there to prove the helper does NOT read it.
        (tmp_path / "config.json").write_text(
            '{"members": [{"name": "LEAKED"}]}', encoding="utf-8"
        )
        teams = tmp_path / "teams"
        teams.mkdir()
        result = _read_team_members("..", teams_base_dir=str(teams))
        assert result == []


# ---------------------------------------------------------------------------
# TestSanitizeMemberName — sanitizer must strip every C0 control char
# (0x00-0x1F), DEL (0x7F), and the Unicode line terminators NEL / U+2028 /
# U+2029, matching `_RENDER_STRIP_RE`.
# ---------------------------------------------------------------------------


class TestSanitizeMemberName:
    """Character-class coverage for the member-name sanitizer."""

    def test_strips_newline(self):
        # Primary prompt-injection vector — a name containing "\n"
        # would emit a second line into the model-visible rendering.
        assert _sanitize_member_name("bob\nEVIL") == "bobEVIL"


# =============================================================================
# is_safe_path_component — cycle-8 promoted public helper (#412 Fix B)
# =============================================================================
# NOTE: the cycle-2 regression class is also named `TestIsSafePathComponent`
# (line 1242). Python class redefinition in a single module silently
# clobbers the earlier class — this class's name is disambiguated with a
# `_Public` suffix so both classes run. Caught during cycle-8 verify-only
# re-review; without the rename, the cycle-2 path-traversal regression
# guards (`..`, `.`, `/tmp`, `a/b`, null-byte) were being silently skipped.


class TestIsSafePathComponent_Public:
    """Cycle-8 Test 3 — promoted shared helper, public-API surface.

    Previously `_is_safe_path_component` (private). Cycle-8 promoted to
    public `is_safe_path_component` (imported from `shared.session_state`)
    so session_end.py's three allowlist callsites can DRY them out.

    COUNTER-TEST BY REVERT target: removing the `fullmatch` regex
    predicate in `is_safe_path_component` (replacing with `return True`)
    flips the hostile-input tests — all the malicious values would
    pass through. The regex IS the defense; this test class pins it.
    """

    @pytest.mark.parametrize("value", [
        "pact-0001639f",])
    def test_valid_inputs_pass(self, value):
        """Allowlist-shaped inputs (alphanumeric, `_`, `-`) pass."""
        from shared.session_state import is_safe_path_component

        assert is_safe_path_component(value) is True


class TestSessionStateTaskReaderContainmentParity:
    """#926 remediation: session_state's 3 task/team readers gained bounded
    containment parity (mirror iter_team_task_jsons / read_task_json). The
    CONTAINMENT tests drive the DI seam (teams_base_dir / tasks_base_dir) -- the
    correct use: they exercise the resolve()/relative_to anchor under an injected
    base, NOT the resolver (resolver-derivation is covered by the env-SET sibling
    tests + the central resolver L1). Per-reader guard asymmetry:
      - GLOB reader _read_task_counts: full hygiene set -- containment +
        per-file is_symlink skip + dotfile skip (it COUNTS, so plants inflate).
      - SINGLE-FILE readers _read_team_members / _read_feature_subject_from_disk:
        only is_safe_path_component + containment; dotfile/symlink-file guards
        are CORRECTLY ABSENT (they read ONE named file, never glob) -- so this
        suite does NOT assert those guards on them (an absent-guard test would be
        a wrong-expectation test).

    NON-VACUITY (per guard, surgical-neuter against 5080ae2b, verified):
      - neuter the relative_to anchor in a reader -> that reader's escape-reject FAILS.
      - neuter _read_task_counts dotfile-skip -> dotfile-not-inflated FAILS.
      - neuter _read_task_counts per-file is_symlink skip -> symlink-skip FAILS.
    """

    # --- _read_team_members (single-file: config.json) ---
    def test_team_members_legit_accepted(self, tmp_path):
        from shared.session_state import _read_team_members
        base = tmp_path / "teams"
        td = base / "pact-legit"
        td.mkdir(parents=True)
        (td / "config.json").write_text(
            json.dumps({"members": [{"name": "alice"}]}), encoding="utf-8")
        assert _read_team_members("pact-legit", teams_base_dir=str(base)) == ["alice"]


    # --- _read_feature_subject_from_disk (single-file: {feature_id}.json) ---


    # --- _read_task_counts (GLOB reader: full hygiene set) ---


