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


def test_opens_read_only_and_immutable(tmp_path, project, monkeypatch):
    db = _store(tmp_path, [{"goal": "g", "project_id": "proj"}])
    uris = _record_opens(monkeypatch)
    text = sr.build_session_briefing(str(project), "", db_path=db)
    assert "- Memory (proj): 1 records" in text
    assert uris == [f"file:{db}?mode=ro&immutable=1"]


def test_stays_within_the_ceiling(tmp_path, project):
    rows = [{"goal": "x" * 400, "project_id": "proj"} for _ in range(10)]
    db = _store(tmp_path, rows)
    text = sr.build_session_briefing(str(project), "t1", db_path=db, limit=10,
                                     teams_dir=_team(tmp_path, ["team-lead"]))
    assert len(text.encode("utf-8")) <= sr.MEMORY_BRIEFING_MAX_BYTES
    assert "Secretary: not spawned" in text


def test_total_on_a_missing_store_and_an_empty_project(tmp_path):
    assert "no store yet" in sr.build_session_briefing(
        str(tmp_path), "", db_path=tmp_path / "absent.db")
    assert "project unresolved" in sr.build_session_briefing("", "", db_path=tmp_path / "x")


def test_store_path_parity_with_the_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(tmp_path / "m"))
    assert sr.memory_db_path() == memory_config.resolve_db_path()
    monkeypatch.delenv("PACT_TEST_MEMORY_DIR")
    assert sr.memory_db_path() == memory_config.resolve_db_path()


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


def test_project_key_parity_with_the_cli(tmp_path):
    repo = tmp_path / "main-repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "f").write_text("x")
    _git("add", "f", cwd=repo)
    _git("commit", "-q", "-m", "x", cwd=repo)
    _git("worktree", "add", "-q", str(tmp_path / "wt-branch"), "-b", "b", cwd=repo)
    sub = repo / "sub"
    sub.mkdir()
    plain = tmp_path / "plain dir"
    plain.mkdir()
    for declared in (repo, tmp_path / "wt-branch", sub, plain):
        expected = PACTMemory._project_name_for_declared_dir(str(declared), "test")
        assert sr.memory_project_key(str(declared)) == expected, declared
    assert sr.memory_project_key(str(tmp_path / "wt-branch")) == "main-repo"


def test_the_learning_ii_threshold_mirrors_the_variety_scorer():
    from shared.variety_scorer import LEARNING_II_MIN_MATCHES
    assert sr._LEARNING_II_MIN_MATCHES == LEARNING_II_MIN_MATCHES
