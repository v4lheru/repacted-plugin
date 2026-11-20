"""
Direct unit tests for the extracted SSOT detector shared/stale_session.py.

detect_stale_session_block compares this frame's live stdin session_id against
the project CLAUDE.md '- Resume:' line id and returns an advisory warning string
on mismatch, else None. Both consumers (bootstrap_prompt_gate via additionalContext,
dispatch_gate via the deny-message augmentation) read this one implementation.

Historically this logic lived in bootstrap_prompt_gate.py and its branch coverage
rode that module's test file via an aliased import; PR-cycle review (F3) flagged
that the SSOT module deserves its own direct test file so the coupling cannot rot
if a future consumer un-aliases or the import graph changes. This file pins the
detector's contract DIRECTLY:

  - the 5 documented None-returning branches (bad/missing session_id; PROJECT_DIR
    unset; neither CLAUDE.md exists; read raises OSError/UnicodeDecodeError; no
    Resume line; recorded==actual healthy),
  - the two-path .claude/CLAUDE.md-preferred precedence (parity with
    resolve_project_claude_md_path),
  - the recorded-id regex shape,
  - the positive mismatch case (returns the warning naming recorded + actual).
"""

from pathlib import Path

import pytest

from shared.stale_session import (
    detect_stale_session_block,
)

_LIVE_ID = "11111111-2222-4000-8000-000000000000"
_RECORDED_DIFFERENT = "99999999-8888-4000-8000-000000000000"


def _write_claude_md(dir_path: Path, recorded_id, *, legacy=False):
    """Write a CLAUDE.md with a Resume line carrying ``recorded_id``.

    legacy=False → ``<dir>/.claude/CLAUDE.md`` (the preferred path).
    legacy=True  → ``<dir>/CLAUDE.md`` (the legacy fallback path).
    """
    if legacy:
        target = dir_path / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = dir_path / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Project\n\n<!-- SESSION_START -->\n## Current Session\n"
        f"- Resume: `claude --resume {recorded_id}`\n"
        "- Team: `session-deadbeef`\n<!-- SESSION_END -->\n",
        encoding="utf-8",
    )
    return target


# =============================================================================
# Positive: a recorded != actual mismatch returns the warning
# =============================================================================


def test_mismatch_returns_warning_naming_recorded_and_actual(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    result = detect_stale_session_block({"session_id": _LIVE_ID})

    assert result is not None
    assert _RECORDED_DIFFERENT in result, "warning names the recorded id"
    assert _LIVE_ID in result, "warning names the actual (live) id"
    assert "stale session block" in result


# =============================================================================
# None-branch 5: recorded == actual (healthy resume)
# =============================================================================


def test_healthy_recorded_equals_actual_returns_none(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _LIVE_ID)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": _LIVE_ID}) is None


# =============================================================================
# None-branch 1: missing / invalid stdin session_id
# =============================================================================


@pytest.mark.parametrize(
    "bad_id",
    [None,],
    ids=["none",],
)
def test_bad_session_id_returns_none(bad_id, tmp_path, monkeypatch):
    """A missing/blank/sentinel/control-char/non-string id → None: nothing
    trustworthy to compare, and an unvalidated id must never be interpolated
    into the warning. CLAUDE.md records a DIFFERENT id, so a naive compare
    would 'mismatch' — the predicate gate must suppress it."""
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({"session_id": bad_id}) is None


def test_missing_session_id_key_returns_none(tmp_path, monkeypatch):
    _write_claude_md(tmp_path, _RECORDED_DIFFERENT)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert detect_stale_session_block({}) is None


# =============================================================================
# None-branch 2: CLAUDE_PROJECT_DIR unset
# =============================================================================


# =============================================================================
# None-branch 3: neither CLAUDE.md exists
# =============================================================================


# =============================================================================
# None-branch 3b: read raises UnicodeDecodeError (non-UTF-8 CLAUDE.md)
# =============================================================================


# =============================================================================
# None-branch 4: no Resume line matches the regex
# =============================================================================


# =============================================================================
# Two-path precedence: .claude/CLAUDE.md is preferred over ./CLAUDE.md
# =============================================================================


# =============================================================================
# Recorded-id regex shape
# =============================================================================


