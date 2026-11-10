"""
Tests for pact-memory/scripts/models.py — data models for memory objects.

Tests cover:
1. TaskItem: from_dict (dict + string), to_dict, priority handling
2. Decision: from_dict (dict + string), to_dict, rationale/alternatives
3. Entity: from_dict (dict + string), to_dict, type/notes
4. _parse_string_list: None, list, JSON string, plain string, edge cases
5. _parse_datetime: None, datetime obj, ISO string, common formats
6. MemoryObject: from_dict (all field types), to_dict, to_storage_dict,
   get_searchable_text, __repr__, JSON string handling
7. memory_from_db_row: basic conversion, file injection
"""
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# TaskItem
# ---------------------------------------------------------------------------

class TestTaskItem:
    def test_from_dict(self):
        from scripts.models import TaskItem
        t = TaskItem.from_dict({"task": "Write tests", "status": "in_progress", "priority": "high"})
        assert t.task == "Write tests"
        assert t.status == "in_progress"
        assert t.priority == "high"

    def test_from_string(self):
        from scripts.models import TaskItem
        t = TaskItem.from_dict("Write tests")
        assert t.task == "Write tests"
        assert t.status == "pending"
        assert t.priority is None

    def test_to_dict_with_priority(self):
        from scripts.models import TaskItem
        t = TaskItem(task="Do thing", status="completed", priority="low")
        d = t.to_dict()
        assert d == {"task": "Do thing", "status": "completed", "priority": "low"}


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

class TestDecision:
    def test_from_dict(self):
        from scripts.models import Decision
        d = Decision.from_dict({
            "decision": "Use factory pattern",
            "rationale": "Flexibility",
            "alternatives": ["Singleton", "Builder"]
        })
        assert d.decision == "Use factory pattern"
        assert d.rationale == "Flexibility"
        assert d.alternatives == ["Singleton", "Builder"]

    def test_from_string(self):
        from scripts.models import Decision
        d = Decision.from_dict("Use factory pattern")
        assert d.decision == "Use factory pattern"
        assert d.rationale is None

    def test_to_dict_full(self):
        from scripts.models import Decision
        d = Decision(decision="X", rationale="Y", alternatives=["A", "B"])
        result = d.to_dict()
        assert result == {"decision": "X", "rationale": "Y", "alternatives": ["A", "B"]}


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class TestEntity:
    def test_from_dict(self):
        from scripts.models import Entity
        e = Entity.from_dict({"name": "AuthService", "type": "service", "notes": "Core auth"})
        assert e.name == "AuthService"
        assert e.type == "service"
        assert e.notes == "Core auth"

    def test_from_string(self):
        from scripts.models import Entity
        e = Entity.from_dict("AuthService")
        assert e.name == "AuthService"
        assert e.type is None

    def test_to_dict_full(self):
        from scripts.models import Entity
        e = Entity(name="X", type="module", notes="note")
        assert e.to_dict() == {"name": "X", "type": "module", "notes": "note"}


# ---------------------------------------------------------------------------
# _parse_string_list
# ---------------------------------------------------------------------------

class TestParseStringList:
    def test_none_returns_empty(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list(None) == []

    def test_empty_string_returns_empty(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list("") == []


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_none_returns_none(self):
        from scripts.models import _parse_datetime
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self):
        from scripts.models import _parse_datetime
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _parse_datetime(dt) is dt


# ---------------------------------------------------------------------------
# MemoryObject
# ---------------------------------------------------------------------------

class TestMemoryObject:
    def test_from_dict_minimal(self):
        from scripts.models import MemoryObject
        m = MemoryObject.from_dict({"id": "abc", "context": "Working on auth"})
        assert m.id == "abc"
        assert m.context == "Working on auth"
        assert m.active_tasks == []
        assert m.lessons_learned == []

    def test_from_dict_full(self):
        from scripts.models import MemoryObject
        data = {
            "id": "abc",
            "context": "Auth module",
            "goal": "Implement OAuth",
            "active_tasks": [{"task": "Write tests", "status": "pending"}],
            "lessons_learned": ["Use mocks"],
            "decisions": [{"decision": "Factory pattern", "rationale": "Flexibility"}],
            "entities": [{"name": "AuthService", "type": "service"}],
            "reasoning_chains": ["X because Y"],
            "agreements_reached": ["Agreed on API"],
            "disagreements_resolved": ["Resolved caching approach"],
            "files": ["src/auth.py"],
            "project_id": "proj-1",
            "session_id": "sess-1",
            "created_at": "2024-01-15T10:00:00+00:00",
            "updated_at": "2024-01-15T11:00:00+00:00",
        }
        m = MemoryObject.from_dict(data)
        assert m.id == "abc"
        assert len(m.active_tasks) == 1
        assert m.active_tasks[0].task == "Write tests"
        assert m.lessons_learned == ["Use mocks"]
        assert len(m.decisions) == 1
        assert m.decisions[0].rationale == "Flexibility"
        assert len(m.entities) == 1
        assert m.entities[0].type == "service"
        assert m.reasoning_chains == ["X because Y"]
        assert m.files == ["src/auth.py"]


# ---------------------------------------------------------------------------
# memory_from_db_row
# ---------------------------------------------------------------------------

class TestMemoryFromDbRow:
    def test_basic_conversion(self):
        from scripts.models import memory_from_db_row
        row = {"id": "abc", "context": "test", "goal": "goal"}
        m = memory_from_db_row(row)
        assert m.id == "abc"
        assert m.context == "test"

    def test_injects_files(self):
        from scripts.models import memory_from_db_row
        row = {"id": "abc", "context": "test"}
        m = memory_from_db_row(row, files=["src/a.py", "src/b.py"])
        assert m.files == ["src/a.py", "src/b.py"]


# ---------------------------------------------------------------------------
# Bug 3 fix (#374) — strict-on-write / lenient-on-read key validation
# ---------------------------------------------------------------------------
#
# Before PR #374's commit 2, Entity/Decision/TaskItem.from_dict silently
# dropped unknown sub-object keys via data.get() on a hardcoded field list.
# A payload like `{"name": "X", "description": "Y"}` would construct an
# Entity with name="X" and lose `description` (the real field is `notes`).
#
# New contract:
#   - Write paths call from_dict(..., strict=True): unknown keys raise
#     ValueError with a message naming the bad keys and the allowed set.
#   - Read paths call from_dict(..., strict=False) (the default): unknown
#     keys are dropped with a logger.warning so legacy DB rows with stray
#     keys remain readable.


class TestFromDictStrictMode:
    """Bug 3 — strict=True raises on unknown sub-object keys."""

    def test_entity_strict_raises_on_unknown_key(self):
        from scripts.models import Entity
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            Entity.from_dict({"name": "Redis", "description": "cache"}, strict=True)

    def test_decision_strict_raises_on_unknown_key(self):
        from scripts.models import Decision
        with pytest.raises(ValueError, match="Unknown keys for Decision"):
            Decision.from_dict(
                {"decision": "Use Redis", "reason": "fast"},
                strict=True,
            )


class TestFromDictLenientMode:
    """Bug 3 — strict=False (default) drops unknown keys with a warning."""

    def test_entity_lenient_drops_unknown_key(self, caplog):
        from scripts.models import Entity
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        e = Entity.from_dict({"name": "X", "legacy_field": "keep"})
        assert e.name == "X"
        assert e.type is None
        assert e.notes is None
        assert "legacy_field" in caplog.text
        assert "Entity" in caplog.text

    def test_decision_lenient_drops_unknown_key(self, caplog):
        from scripts.models import Decision
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        d = Decision.from_dict({"decision": "Use X", "stray": "value"})
        assert d.decision == "Use X"
        assert "stray" in caplog.text


class TestAllowedKeysInvariant:
    """
    Cheap safety net: _ALLOWED_KEYS must match the dataclass fields exactly.

    If a future edit adds a dataclass field without updating _ALLOWED_KEYS,
    strict-mode validation will reject the new field as "unknown" — silently
    re-creating bug 3. This invariant fails loudly at test time instead.
    """

    def test_entity_allowed_keys_matches_fields(self):
        import dataclasses
        from scripts.models import Entity
        field_names = {f.name for f in dataclasses.fields(Entity)}
        assert Entity._ALLOWED_KEYS == field_names

    def test_decision_allowed_keys_matches_fields(self):
        import dataclasses
        from scripts.models import Decision
        field_names = {f.name for f in dataclasses.fields(Decision)}
        assert Decision._ALLOWED_KEYS == field_names

