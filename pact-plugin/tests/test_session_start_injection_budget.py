"""The SessionStart frame fits the `injection` budgets, measured offline on a live-shaped payload.

refactor surface design §3.3 and §5.1 (the design notes).
tests/contract/budgets.json `injection` holds the ceilings a session start may inject:
`session_init_startup` and `session_init_resume` bytes of `additionalContext`. This test drives the
real hooks/session_init.py main() with the stdin shape the host sends (session_id, source,
agent_type, cwd, hook_event_name, transcript_path) for a lead with no tasks, no snapshot and no
memory, in a temporary config root and project dir, and asserts the bytes are under the ceiling.

What it does not measure: the dynamic parts of a real session (the in-progress task list, the
restored snapshot, the memory briefing). Those are read from the parts ledger in
`context-cost.json` by `repact-report`; the lead's live check after a release records them.
A ceiling here is a floor on what every session pays before any state exists.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BUDGETS = json.loads((PLUGIN_ROOT / "tests" / "contract" / "budgets.json").read_text(encoding="utf-8"))
INJECTION = BUDGETS["injection"]
SESSION_ID = "aabb1122-0000-0000-0000-00000000b0d9"


def _live_shaped_payload(source: str, project_dir: Path) -> str:
    return json.dumps({
        "session_id": SESSION_ID,
        "source": source,
        "agent_type": "pact-orchestrator",
        "cwd": str(project_dir),
        "hook_event_name": "SessionStart",
        "transcript_path": str(project_dir / "transcript.jsonl"),
    })


def _run_session_init(monkeypatch, tmp_path: Path, source: str) -> str:
    """additionalContext of one real main() run for a lead with no state."""
    from session_init import main

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)

    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_resume._check_pr_state", return_value=""), \
         patch("sys.stdin", io.StringIO(_live_shaped_payload(source, project_dir))), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0
    return json.loads(mock_stdout.getvalue())["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source, budget_key", [
    ("startup", "session_init_startup"),
    ("resume", "session_init_resume"),
])
def test_session_start_frame_fits_the_injection_budget(monkeypatch, tmp_path, source, budget_key):
    additional = _run_session_init(monkeypatch, tmp_path, source)
    size = len(additional.encode("utf-8"))
    ceiling = INJECTION[budget_key]
    assert size <= ceiling, (
        f"{source} frame injects {size:,} B > budgets.json injection.{budget_key} {ceiling:,} B; "
        "cut static text in the session_init builders or lower what the frame restores, never raise the row")
