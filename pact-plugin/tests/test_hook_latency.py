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


