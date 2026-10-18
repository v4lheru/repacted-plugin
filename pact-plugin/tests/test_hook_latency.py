"""
Tests for the hook latency record (hooks/shared/hook_latency.py, written by hooks/dispatch.py),
its report lines, and the budget arithmetic of tests/perf/hook_latency.py.

- a dispatch appends one line with total_ms and each check's handler ms;
- a record that cannot be written changes neither the exit code nor the printed result;
- summarise reads both files, skips malformed lines, and gives nearest-rank p50/p95;
- the budget normalises by the runner's calibration and fails a real regression only.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

import dispatch  # noqa: E402  (hooks/ is on the path: tests/conftest.py)
import dispatch_registry as registry  # noqa: E402
from shared import hook_latency  # noqa: E402
from shared.pact_context import reconstruct_session_dir  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("perf_hook_latency", PLUGIN_ROOT / "tests" / "perf" / "hook_latency.py")
budget_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(budget_check)

RESULT = b'{"suppressOutput": true}'


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A throwaway config dir and project with session `s1`'s dir created."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    folder = Path(reconstruct_session_dir(str(project), "s1"))
    folder.mkdir(parents=True)
    return folder


def _run(monkeypatch, capfd) -> tuple:
    check = registry.Check(name="fake_check", script="hooks/fake_check.py")
    monkeypatch.setattr(dispatch, "run_check", lambda c, raw, event, tool_name="": dispatch.Outcome(
        check=c, stdout=RESULT))
    code = dispatch.dispatch("Stop", json.dumps({"session_id": "s1"}).encode(), checks=[check])
    return code, capfd.readouterr().out


def test_a_dispatch_appends_its_total_and_each_checks_time(session, monkeypatch, capfd):
    code, out = _run(monkeypatch, capfd)
    rows = hook_latency.read_rows(session)
    assert (code, out.encode()) == (0, RESULT)
    assert len(rows) == 1
    assert rows[0]["event"] == "Stop" and list(rows[0]["checks"]) == ["fake_check"]
    assert 0 <= rows[0]["checks"]["fake_check"] <= rows[0]["total_ms"] and set(rows[0]) == {"v", "ts", "event", "group", "total_ms", "checks"}


def test_a_record_that_cannot_be_written_changes_nothing(session, monkeypatch, capfd):
    expected = _run(monkeypatch, capfd)

    def broken(folder, data):
        raise OSError("read-only file system")

    monkeypatch.setattr(hook_latency, "append", broken)
    assert _run(monkeypatch, capfd) == expected
    assert len(hook_latency.read_rows(session)) == 1   # only the first run's line


def test_summary_reads_both_files_and_skips_bad_lines(tmp_path):
    lines = [json.dumps({"event": "PreToolUse", "total_ms": ms, "checks": {"git_commit_check": ms / 2}})
             for ms in range(1, 101)]
    (tmp_path / (hook_latency.LATENCY_FILE + ".1")).write_text("\n".join(lines[:50]) + "\n", encoding="utf-8")
    (tmp_path / hook_latency.LATENCY_FILE).write_text(
        "\n".join(lines[50:] + ["not json", '{"total_ms": -1}', '{"total_ms": true}', "[]"]) + "\n",
        encoding="utf-8")
    summary = hook_latency.summarise([tmp_path])
    assert summary["overall"] == {"runs": 100, "p50_ms": 50, "p95_ms": 95, "max_ms": 100}
    assert summary["checks"]["git_commit_check"]["p95_ms"] == 47.5
    assert hook_latency.render(summary)[0].startswith("hook latency         100 runs: p50 50.0 ms, p95 95.0 ms")


def test_the_budget_scales_with_the_runner_and_fails_a_real_regression():
    baseline = {"calibration_ms": 10.0, "checks": {"PreToolUse:git_commit_check": {"p95_ms": 30.0}},
                "walls": {"pre-bash": {"p95_ms": 150.0}}}

    def result(calibration, check_ms, wall_ms):
        return {"calibration_ms": calibration, "checks": {"PreToolUse:git_commit_check": {"p95_ms": check_ms}},
                "walls": {"pre-bash": {"p95_ms": wall_ms}}}

    # A runner four times slower, with every time four times longer: within budget.
    assert budget_check.over_budget(result(40.0, 120.0, 600.0), baseline) == []
    # Same runner speed, the check four times slower than its baseline: over (budget 90 ms).
    assert budget_check.over_budget(result(10.0, 120.0, 150.0), baseline) == [
        ("check", "PreToolUse:git_commit_check", 120.0, 90.0)]
