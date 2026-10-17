"""FIX-MEM (f4): two of the three M12 pact-memory defects, pinned red-first.

(1) An additive `update` appended a corrected keyed item beside the stale one,
    so both were served. Items with an identity key (entities by `name`,
    active_tasks by `task`, decisions by `decision`) now REPLACE their same-key
    predecessor in place; unkeyed lists and `replace=True` are unchanged.
(3) With no declared project, the cwd walk accepted the Claude config dir as a
    project root (its CLAUDE.md is a marker), filing records under `.claude`.
    A cwd inside the config dir now resolves to no project.

(2), Working Memory after an update, is covered in test_memory_cli.py
(TestCliSyncSubprocess): `update` stays quiet by default and `--sync` runs the
`sync` path.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


@pytest.fixture
def db_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "m12.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    with patch("scripts.database.ensure_initialized"):
        yield conn
    conn.close()


def _write(conn, fields, updates, **kwargs):
    from scripts.database import create_memory, get_memory, update_memory
    mem_id = create_memory(conn, fields)
    assert update_memory(conn, mem_id, updates, **kwargs)
    return get_memory(conn, mem_id)


# ---------------------------------------------------------------------------
# (1) keyed items replace their same-key predecessor
# ---------------------------------------------------------------------------

class TestKeyedItemsReplace:

    def test_a_corrected_entity_replaces_the_stale_one_in_place(self, db_conn):
        row = _write(
            db_conn,
            {"entities": [{"name": "Redis", "notes": "guard uncommitted"}, {"name": "Pg"}]},
            {"entities": [{"name": "Redis", "notes": "guard committed"}]},
        )
        assert row["entities"] == [{"name": "Redis", "notes": "guard committed"}, {"name": "Pg"}]

    def test_a_task_status_change_replaces_the_task(self, db_conn):
        row = _write(
            db_conn,
            {"active_tasks": [{"task": "T1"}, {"task": "T2"}]},
            {"active_tasks": [{"task": "T1", "status": "completed"}]},
        )
        assert row["active_tasks"] == [
            {"task": "T1", "status": "completed"},
            {"task": "T2", "status": "pending"},
        ]

    def test_a_revised_rationale_replaces_the_decision(self, db_conn):
        row = _write(
            db_conn,
            {"decisions": [{"decision": "Use Redis", "rationale": "fast"}]},
            {"decisions": [{"decision": "Use Redis", "rationale": "fast and already deployed"}]},
        )
        assert row["decisions"] == [
            {"decision": "Use Redis", "rationale": "fast and already deployed"},
        ]

    def test_existing_same_key_duplicates_collapse_to_the_correction(self, db_conn):
        """A row already carrying the defect's duplicate is healed by the next
        correction: every same-key item goes, the correction takes the first slot."""
        row = _write(
            db_conn,
            {"entities": [{"name": "Redis", "notes": "a"}, {"name": "Pg"},
                          {"name": "Redis", "notes": "b"}]},
            {"entities": [{"name": "Redis", "notes": "c"}]},
        )
        assert row["entities"] == [{"name": "Redis", "notes": "c"}, {"name": "Pg"}]

    def test_the_last_same_key_item_in_one_payload_wins(self, db_conn):
        row = _write(
            db_conn,
            {"entities": [{"name": "Pg"}]},
            {"entities": [{"name": "Redis", "notes": "first"}, {"name": "Redis", "notes": "second"}]},
        )
        assert row["entities"] == [{"name": "Pg"}, {"name": "Redis", "notes": "second"}]

    # -- controls: behaviour that must NOT change --------------------------

    def test_a_new_key_is_still_appended(self, db_conn):
        row = _write(db_conn, {"entities": [{"name": "Pg"}]}, {"entities": [{"name": "Redis"}]})
        assert row["entities"] == [{"name": "Pg"}, {"name": "Redis"}]

    def test_unkeyed_lessons_still_append(self, db_conn):
        row = _write(db_conn, {"lessons_learned": ["a"]}, {"lessons_learned": ["a", "b"]})
        assert row["lessons_learned"] == ["a", "b"]

    def test_replace_still_keeps_same_key_items_of_its_payload(self, db_conn):
        row = _write(
            db_conn,
            {"entities": [{"name": "Pg"}]},
            {"entities": [{"name": "Redis", "notes": "x"}, {"name": "Redis", "notes": "y"}]},
            replace=True,
        )
        assert row["entities"] == [{"name": "Redis", "notes": "x"}, {"name": "Redis", "notes": "y"}]

    def test_an_unknown_sub_key_is_still_refused_and_writes_nothing(self, db_conn):
        from scripts.database import create_memory, get_memory, update_memory
        mem_id = create_memory(db_conn, {"entities": [{"name": "Redis", "notes": "old"}]})
        with pytest.raises(ValueError):
            update_memory(db_conn, mem_id, {"entities": [{"name": "Redis", "bogus": 1}]})
        assert get_memory(db_conn, mem_id)["entities"] == [{"name": "Redis", "notes": "old"}]


# ---------------------------------------------------------------------------
# (3) a cwd inside the Claude config dir names no project
# ---------------------------------------------------------------------------

def _home_with_config(tmp_path):
    home = tmp_path / "home"
    config = home / ".claude"
    (config / "plugins" / "cache").mkdir(parents=True)
    (config / "CLAUDE.md").write_text("# user memory\n", encoding="utf-8")
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
    return home, config, project


@pytest.fixture
def no_declared_project(monkeypatch, tmp_path):
    """Strategy 3 only: no env declaration, no git repo, the session record is
    inert under pytest, and the walk-up cannot see markers above tmp_path."""
    from tests.test_project_id import _isolate_walkup_to
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("scripts.memory_api.main_repo_root", lambda *a, **k: None)
    _isolate_walkup_to(monkeypatch, tmp_path)


class TestProjectIdNotTheConfigDir:

    def test_a_cwd_under_the_default_config_dir_names_no_project(
        self, tmp_path, monkeypatch, no_declared_project
    ):
        from scripts.memory_api import PACTMemory
        home, config, _ = _home_with_config(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.chdir(config / "plugins" / "cache")
        assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")

    def test_the_config_dir_itself_names_no_project(
        self, tmp_path, monkeypatch, no_declared_project
    ):
        from scripts.memory_api import PACTMemory
        home, config, _ = _home_with_config(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.chdir(config)
        assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")

    def test_a_relocated_config_dir_is_honoured(
        self, tmp_path, monkeypatch, no_declared_project
    ):
        from scripts.memory_api import PACTMemory
        home, _, _ = _home_with_config(tmp_path)
        relocated = tmp_path / "cfg"
        (relocated / "sub").mkdir(parents=True)
        (relocated / "CLAUDE.md").write_text("# relocated\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(relocated))
        monkeypatch.chdir(relocated / "sub")
        assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")

    def test_control_a_project_cwd_still_names_its_project(
        self, tmp_path, monkeypatch, no_declared_project
    ):
        from scripts.memory_api import PACTMemory
        home, _, project = _home_with_config(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.chdir(project)
        assert PACTMemory._detect_project_id_with_source() == ("proj", "cwd")
