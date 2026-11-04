"""
Integration tests for Task system functions.

Tests the Task-first code path that reads workflow state from the Claude Task system
for post-compaction recovery. These functions are the primary state source, with
checkpoint files serving as fallback.

Location: pact-plugin/tests/test_task_integration.py

Core Task utilities are in hooks/shared/task_utils.py.
build_post_compaction_checkpoint also lives there (renamed from
build_refresh_from_tasks in #444).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# =============================================================================
# Fixtures for Task Testing
# =============================================================================

@pytest.fixture
def mock_tasks_dir(tmp_path: Path, monkeypatch, pact_context):
    """
    Create mock ~/.claude/tasks/{team_name}/ structure.

    Post team-dir-resolution fix: a team session (team_name truthy) resolves
    tasks under {team_name}, so the fixture builds the team-named dir and sets
    a matching team_name on the context. Tests writing JSON into the returned
    dir exercise the real team branch of get_task_list.

    Returns:
        Path to the tasks directory for the test team
    """
    session_id = "test-session-123"
    team_name = "test-team"
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True)
    pact_context(session_id=session_id, team_name=team_name)
    monkeypatch.setenv("HOME", str(tmp_path))

    # Patch Path.home() to return our temp directory
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    return tasks_dir


@pytest.fixture
def make_task():
    """Factory fixture to create task dictionaries."""
    def _make(
        task_id: str,
        subject: str,
        status: str = "in_progress",
        blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = {
            "id": task_id,
            "subject": subject,
            "status": status,
        }
        if blocked_by:
            task["blockedBy"] = blocked_by
        if metadata:
            task["metadata"] = metadata
        return task
    return _make


@pytest.fixture
def sample_task_list(make_task) -> list[dict[str, Any]]:
    """
    Create a realistic task list representing an in-progress orchestration.

    Structure:
    - Feature task: "Implement user authentication"
    - Phase tasks: PREPARE (completed), ARCHITECT (completed), CODE (in_progress)
    - Agent tasks: backend-coder (in_progress)
    """
    return [
        # Feature task (top-level, not blocked by anything)
        make_task(
            task_id="task-001",
            subject="Implement user authentication",
            status="in_progress",
        ),
        # Completed phase tasks
        make_task(
            task_id="task-002",
            subject="PREPARE: user-authentication",
            status="completed",
            blocked_by=["task-001"],
        ),
        make_task(
            task_id="task-003",
            subject="ARCHITECT: user-authentication",
            status="completed",
            blocked_by=["task-002"],
        ),
        # Current phase task (in_progress)
        make_task(
            task_id="task-004",
            subject="CODE: user-authentication",
            status="in_progress",
            blocked_by=["task-003"],
        ),
        # Active agent task
        make_task(
            task_id="task-005",
            subject="backend-coder: Implement auth endpoint",
            status="in_progress",
            blocked_by=["task-004"],
        ),
    ]


# =============================================================================
# Tests for get_task_list()
# =============================================================================

class TestGetTaskList:
    """Tests for get_task_list() function."""

    def test_reads_valid_json_files(self, mock_tasks_dir, make_task):
        """Test reading task list from valid JSON files."""
        from shared.task_utils import get_task_list

        # Create task files
        task1 = make_task("task-1", "Feature task", "in_progress")
        task2 = make_task("task-2", "Agent task", "pending")

        (mock_tasks_dir / "task-1.json").write_text(json.dumps(task1))
        (mock_tasks_dir / "task-2.json").write_text(json.dumps(task2))

        result = get_task_list()

        assert result is not None
        assert len(result) == 2
        subjects = {t["subject"] for t in result}
        assert "Feature task" in subjects
        assert "Agent task" in subjects

    def test_returns_none_for_empty_directory(self, mock_tasks_dir):
        """Test returns None when tasks directory is empty."""
        from shared.task_utils import get_task_list

        # Directory exists but is empty
        result = get_task_list()

        assert result is None

    def test_returns_none_for_missing_directory(self, tmp_path, monkeypatch, pact_context):
        """Test returns None when tasks directory doesn't exist."""
        from shared.task_utils import get_task_list

        session_id = "nonexistent-session"
        pact_context(session_id=session_id)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_task_list()

        assert result is None


# =============================================================================
# Tests for find_feature_task()
# =============================================================================

class TestFindFeatureTask:
    """Tests for find_feature_task() function."""

    def test_finds_top_level_feature_task(self, sample_task_list):
        """Test finds feature task that has no blockedBy."""
        from shared.task_utils import find_feature_task

        result = find_feature_task(sample_task_list)

        assert result is not None
        assert result["id"] == "task-001"
        assert result["subject"] == "Implement user authentication"

    def test_filters_out_phase_tasks(self, make_task):
        """Test excludes tasks with phase prefixes from feature detection."""
        from shared.task_utils import find_feature_task

        tasks = [
            make_task("task-1", "PREPARE: some-feature", "in_progress"),
            make_task("task-2", "ARCHITECT: some-feature", "in_progress"),
            make_task("task-3", "CODE: some-feature", "in_progress"),
            make_task("task-4", "TEST: some-feature", "in_progress"),
            make_task("task-5", "Review: some-feature", "in_progress"),
        ]

        result = find_feature_task(tasks)

        assert result is None

    def test_returns_none_when_no_feature_task(self, make_task):
        """Test returns None when no suitable feature task exists."""
        from shared.task_utils import find_feature_task

        # Only agent work and a task not started
        tasks = [
            make_task("task-1", "backend-coder: agent work", "in_progress", blocked_by=["task-0"]),
            make_task("task-2", "More work", "pending"),
        ]

        result = find_feature_task(tasks)

        assert result is None


# =============================================================================
# Tests for find_current_phase()
# =============================================================================

class TestFindCurrentPhase:
    """Tests for find_current_phase() function."""

    def test_finds_in_progress_phase(self, sample_task_list):
        """Test finds phase task with status in_progress."""
        from shared.task_utils import find_current_phase

        result = find_current_phase(sample_task_list)

        assert result is not None
        assert result["id"] == "task-004"
        assert result["subject"] == "CODE: user-authentication"
        assert result["status"] == "in_progress"

    def test_returns_none_with_no_in_progress_phases(self, make_task):
        """Test returns None when no phase is in_progress."""
        from shared.task_utils import find_current_phase

        tasks = [
            make_task("task-1", "PREPARE: feature", "completed"),
            make_task("task-2", "ARCHITECT: feature", "completed"),
            make_task("task-3", "Feature task", "in_progress"),  # Not a phase
        ]

        result = find_current_phase(tasks)

        assert result is None


# =============================================================================
# Tests for find_active_agents()
# =============================================================================

class TestFindActiveAgents:
    """Tests for find_active_agents() function."""

    def test_finds_in_progress_agent_tasks(self, sample_task_list):
        """Test finds agent tasks with status in_progress."""
        from shared.task_utils import find_active_agents

        result = find_active_agents(sample_task_list)

        assert len(result) == 1
        assert result[0]["id"] == "task-005"
        assert "backend-coder" in result[0]["subject"].lower()

    def test_finds_multiple_active_agents(self, make_task):
        """Test finds all active agent tasks when multiple exist."""
        from shared.task_utils import find_active_agents

        tasks = [
            make_task("task-1", "backend-coder: API endpoint", "in_progress"),
            make_task("task-2", "frontend-coder: UI component", "in_progress"),
            make_task("task-3", "test-engineer: Integration tests", "in_progress"),
            make_task("task-4", "architect: Design review", "completed"),  # Not active
        ]

        result = find_active_agents(tasks)

        assert len(result) == 3
        agent_ids = {t["id"] for t in result}
        assert agent_ids == {"task-1", "task-2", "task-3"}


# =============================================================================
# Tests for find_blockers()
# =============================================================================

class TestFindBlockers:
    """Tests for find_blockers() function."""

    def test_finds_blocker_tasks(self, make_task):
        """Test finds tasks with metadata.type == 'blocker'."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "Blocked: Missing API credentials",
                "pending",
                metadata={"type": "blocker"},
            ),
            make_task("task-2", "Normal task", "in_progress"),
        ]

        result = find_blockers(tasks)

        assert len(result) == 1
        assert result[0]["id"] == "task-1"

    def test_finds_algedonic_tasks(self, make_task):
        """Test finds tasks with metadata.type == 'algedonic'."""
        from shared.task_utils import find_blockers

        tasks = [
            make_task(
                "task-1",
                "HALT: Security vulnerability detected",
                "pending",
                metadata={"type": "algedonic", "level": "HALT"},
            ),
        ]

        result = find_blockers(tasks)

        assert len(result) == 1
        assert result[0]["metadata"]["level"] == "HALT"


# =============================================================================
# Tests for build_post_compaction_checkpoint()
# (Pre-#444 name: build_refresh_from_tasks, lived in compaction_refresh.py.
# Function moved to shared/task_utils.py and renamed in #444.)
# =============================================================================

class TestBuildPostCompactionCheckpoint:
    """Tests for build_post_compaction_checkpoint() function."""

    def test_builds_complete_message(self, make_task):
        """Test builds complete refresh message with all components."""
        from shared.task_utils import build_post_compaction_checkpoint

        feature = make_task("feat-1", "Implement auth", "in_progress")
        phase = make_task("phase-1", "CODE: auth", "in_progress")
        agents = [make_task("agent-1", "backend-coder: work", "in_progress")]
        blockers = []

        result = build_post_compaction_checkpoint(feature, phase, agents, blockers)

        assert "[POST-COMPACTION CHECKPOINT]" in result
        assert "Implement auth" in result
        assert "CODE: auth" in result
        assert "backend-coder" in result
        assert "Monitor active agents" in result

    def test_handles_missing_feature(self, make_task):
        """Test handles None feature task gracefully."""
        from shared.task_utils import build_post_compaction_checkpoint

        phase = make_task("phase-1", "CODE: work", "in_progress")
        agents = []

        result = build_post_compaction_checkpoint(None, phase, agents, [])

        assert "cannot tell which task is the feature" in result


# =============================================================================
# Integration Tests for Task-First Code Path
# =============================================================================

class TestTaskFirstIntegration:
    """Integration tests for the Task-first code path in main()."""

    # Removed in #444: the compaction_refresh.main() entry point was deleted.
    # Equivalent integration coverage for the session_init.main() source=compact
    # branch lives in test_session_init.py:
    #   - TestSessionInitCompactE2E.test_feature_plus_phase_plus_blockers_render_into_additional_context
    #     (covers the "tasks available → checkpoint block appended" path)
    #   - TestSessionInitCompactPhantomWorkflow.test_compact_with_only_completed_tasks_emits_no_checkpoint
    #     (covers the "no in_progress → no checkpoint block" path;
    #     session_init does NOT emit suppressOutput — it always emits
    #     additionalContext with the bootstrap directive, just without a
    #     checkpoint block when no in_progress tasks exist).
