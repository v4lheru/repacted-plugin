"""The hook-computed session briefing.

Location: pact-plugin/tests/test_session_briefing.py

The secretary spawns on demand, so the orientation it delivered at spawn is
computed by shared.session_resume.build_session_briefing and appended to the
SessionStart context. These arms pin: the <= 2 KB ceiling, what the briefing
reads (the store, read-only, under the store-access bar) and reports, the
on-demand secretary reminder, and parity of the two derivations it mirrors
(the store path and the project id) with the pact-memory CLI's own.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from shared import session_resume as sr
from scripts import config as memory_config  # noqa: E402
from scripts.database import create_memory, ensure_initialized, get_connection  # noqa: E402
from scripts.memory_api import PACTMemory  # noqa: E402


def _store(tmp_path: Path, rows: list[dict]) -> Path:
    db = tmp_path / "memory.db"
    conn = get_connection(db)
    ensure_initialized(conn)
    for row in rows:
        create_memory(conn, row)
    conn.commit()
    conn.close()
    return db


def _team(tmp_path: Path, names: list[str]) -> str:
    teams = tmp_path / "teams"
    (teams / "t1").mkdir(parents=True)
    (teams / "t1" / "config.json").write_text(
        json.dumps({"members": [{"name": n} for n in names]}), encoding="utf-8")
    return str(teams)


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return project


def test_reports_count_newest_and_calibration(tmp_path, project):
    rows = [{"goal": f"goal {i}", "project_id": "proj"} for i in range(5)]
    rows.append({"goal": "calibrated", "project_id": "proj",
                 "entities": [{"name": "orchestration_calibration"}]})
    rows.append({"goal": "other project", "project_id": "elsewhere"})
    db = _store(tmp_path, rows)
    text = sr.build_session_briefing(str(project), "t1", db_path=db,
                                     teams_dir=_team(tmp_path, ["team-lead", "secretary"]))
    assert "- Memory (proj): 6 records; newest: " in text
    assert len(re.findall(r"\[[0-9a-f]{8}\]", text)) == 3, text
    assert "other project" not in text
    assert "1 orchestration_calibration records; gate not met (threshold 5)" in text
    assert "Secretary: not spawned" not in text


def test_secretary_reminder_only_when_no_secretary_member(tmp_path, project):
    db = _store(tmp_path, [])
    teams = _team(tmp_path, ["team-lead"])
    text = sr.build_session_briefing(str(project), "t1", db_path=db, teams_dir=teams)
    assert "Secretary: not spawned. Spawn it before the first memory query or harvest" in text
    assert "- Memory (proj): no records for this project." in text


def _record_opens(monkeypatch):
    uris = []
    real = sr._store_uri
    monkeypatch.setattr(sr, "_store_uri", lambda p: uris.append(real(p)) or real(p))
    return uris


def test_never_opens_the_store_when_a_sidecar_is_present(tmp_path, project, monkeypatch):
    db = _store(tmp_path, [{"goal": "g", "project_id": "proj"}])
    (tmp_path / "memory.db-wal").write_bytes(b"")
    uris = _record_opens(monkeypatch)
    text = sr.build_session_briefing(str(project), "", db_path=db)
    assert "not read (memory.db-wal present)" in text
    assert uris == []


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


