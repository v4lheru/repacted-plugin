# pact-plugin/tests/test_session_dir_ages_by_newest_file.py
"""
Regression for upstream PACT #1650 (e1d6f5f8): cleanup_old_sessions ages a
session dir by the newer of its own mtime and its newest child's.

Appending to the journal, or rewriting a file in place, does not move the
dir's own mtime, so a live session whose dir had no entry created or removed
for a whole TTL used to be reaped. A dir whose children cannot be observed is
kept.
"""
import json
import os
import time

CURRENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER = "11111111-2222-3333-4444-555555555555"


def _set_age(path, days):
    stamp = time.time() - days * 86400
    os.utime(str(path), (stamp, stamp))


def _session(tmp_path, dir_days, child_days, checkpointed=False):
    session_dir = tmp_path / "proj" / OTHER
    session_dir.mkdir(parents=True)
    events = [{"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"}]
    if checkpointed:
        events.append({"v": 1, "type": "session_paused", "ts": "2026-01-01T00:00:00Z",
                       "pr_number": 1, "pr_url": "https://example.invalid/pull/1",
                       "branch": "b", "worktree_path": "/tmp/x",
                       "consolidation_completed": True})
    (session_dir / "session-journal.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    (session_dir / "pact-session-context.json").write_text("{}")
    for child in session_dir.iterdir():
        _set_age(child, child_days)
    _set_age(session_dir, dir_days)  # last: child writes bump it
    return session_dir


def _reap(tmp_path):
    from session_end import cleanup_old_sessions
    cleanup_old_sessions(project_slug="proj", current_session_id=CURRENT,
                         sessions_dir=str(tmp_path), max_age_days=30,
                         paused_max_age_days=180)


def test_an_old_dir_with_a_fresh_journal_survives(tmp_path):
    session_dir = _session(tmp_path, dir_days=40, child_days=0)
    _reap(tmp_path)
    assert session_dir.exists()


def test_an_old_dir_with_old_children_is_reaped(tmp_path):
    session_dir = _session(tmp_path, dir_days=40, child_days=40)
    _reap(tmp_path)
    assert not session_dir.exists()


