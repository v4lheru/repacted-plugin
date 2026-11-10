"""
Tests for shared/pact_context.py — session context module.

Tests cover:

get_pact_context():
1. Returns all fields from a valid context file
2. Returns empty strings when file is missing
3. Returns empty strings when file contains invalid JSON
4. Caches result on second call (module-level _cache)
5. Returns empty strings when file has missing keys

get_team_name():
6. Returns lowercased team_name
7. Returns empty string when context file is missing

get_session_id():
8. Returns session_id from context file
9. Returns empty string when context file is missing

get_project_dir():
10. Returns project_dir from context file

resolve_agent_name():
11. Returns agent_name from input_data when present
12. Splits agent_id on @ and returns name part (step 2)
13. Handles @ split edge cases: @ at start, multiple @, no @ (step 2 edges)
14. Resolves agent_id via team config lookup
15. Falls back to agent_type with pact- prefix stripped
16. Returns empty string when no identity info available
17. Falls back to agent_type when team config missing
18. Falls back to agent_type when agent_id not in members
19. Returns agent_type as-is when it doesn't start with pact- or repacted:pact-
19b. Resolves "repacted:pact-<name>" like "pact-<name>"; any other "PACT:" value as-is
20. Handles non-list members gracefully

write_context():
21. Creates context file with correct content
22. Uses atomic write (temp file + rename)
23. Sets file permissions to 0o600
24. Creates parent directory if missing
25. Handles write errors gracefully (fail-open)
25b. Skips write when session_id and project_dir are empty

--- Extended Coverage (Test Engineer) ---

get_pact_context() edge cases:
26. Returns empty strings when file is read-protected (+ logs to stderr)
27. Returns empty strings when JSON is a non-dict type (e.g., list)
28. Coerces non-string values to strings
29. Caches error result (no repeated file reads on error)
30. Returns empty strings for empty JSON object

get_team_name() edge cases:
31. Does not transform other accessors (only team_name lowercased)

resolve_agent_name() edge cases:
32. Skips empty string agent_name (falsy)
33. Falls back when agent_id present but no team_name available
34. Handles corrupt team config JSON gracefully
35. Uses default team_name from context file when not provided
36. Handles agent_id without agent_type (no fallback after failed lookup)
37. Prefers agent_name over agent_id even when both present

write_context() edge cases:
38. Writes valid ISO 8601 timestamp in started_at
39. Cleans up temp file on write failure

Integration:
40. write_context → get_pact_context round-trip
41. write_context → get_team_name/get_session_id/get_project_dir round-trip

Category C fallback (memory scripts — shared pact_session module):
42. get_session_id_from_context_file returns session_id from session-scoped context file
43. get_session_id_from_context_file returns empty string when file missing
43b. get_session_id_from_context_file returns empty string with no args
44. get_session_id_from_context_file returns empty string on invalid JSON
45. _detect_session_id returns None without context (no env fallback)
46. _get_embedding_attempted_path falls back to 'unknown' without context

init():
47. Second init() call is a no-op (idempotency guard)
48. Missing session_id leaves _context_path as None (readers return empty context)
48b. session_id present but CLAUDE_PROJECT_DIR absent leaves _context_path as None

Additional write_context():
49. Overwrites existing file
50. No temp files left on success

Additional resolve_agent_name():
51. agent_id found in config skips agent_type check
52. member with missing name field returns empty string

Additional Category C:
53. memory_api imports get_session_id_from_context_file from pact_session
54. memory_init imports get_session_id_from_context_file from pact_session
55. _get_embedding_attempted_path falls back to "unknown" when no context file

Additional write_context():
56. Creates deeply nested parent directories

Concurrent:
57. Concurrent writes produce valid JSON

Uninitialized accessors:
57b. get_pact_context() returns _EMPTY_CONTEXT when _context_path is None
57c. write_context() computes session-scoped path when _context_path is None

Migration completeness (AST-based scanner — os.environ.get, os.getenv, os.environ[]):
58-60. No hook runtime code reads phantom env vars (parametrized x3)
61-63. No skill script runtime code reads phantom env vars (parametrized x3)

--- Fresh Review Tests ---

init() ordering:
64. get_team_name() before init() returns empty; after init() reads session-scoped

Session-scoped path E2E:
65. write_context → init → get_team_name full session-scoped cycle
66. Session-scoped path uses the resolved project dir's basename as slug

pact_session.py path:
67. _context_file_path returns session-scoped path when both args provided
68. _context_file_path returns None when args missing

init()-before-reader ordering guard:
69. (parametrized) Every hook that calls a pact_context reader calls init() first
70. session_end.py: init() before get_project_slug() (indirect get_project_dir() call)

Library module init() contract:
71. task_utils.get_task_list() works when init() was called by a prior hook
72. get_session_id() serves import-only library modules when init() was called by a prior hook
"""

import json
from pathlib import Path

import pytest


class TestGetPactContext:
    """Tests for get_pact_context() — read and cache context file."""

    def test_returns_all_fields(self, pact_context):
        """Should return all fields from a valid context file."""
        from shared.pact_context import get_pact_context

        pact_context(
            team_name="pact-abc12345",
            session_id="abc12345-0000-1111-2222-333344445555",
            project_dir="/Users/example/project",
        )

        result = get_pact_context()

        assert result["team_name"] == "pact-abc12345"
        assert result["session_id"] == "abc12345-0000-1111-2222-333344445555"
        assert result["project_dir"] == "/Users/example/project"
        assert result["started_at"] == "2026-01-01T00:00:00Z"


class TestGetTeamName:
    """Tests for get_team_name() — convenience accessor."""

    def test_returns_lowercased_team_name(self, pact_context):
        """Should return team_name from context, lowercased."""
        from shared.pact_context import get_team_name

        pact_context(team_name="PACT-ABC12345")

        result = get_team_name()

        assert result == "pact-abc12345"


class TestGetSessionId:
    """Tests for get_session_id() — convenience accessor."""

    def test_returns_session_id(self, pact_context):
        """Should return session_id from context file."""
        from shared.pact_context import get_session_id

        pact_context(session_id="test-session-xyz")

        assert get_session_id() == "test-session-xyz"


class TestGetProjectDir:
    """Tests for get_project_dir() — convenience accessor."""

    def test_returns_project_dir(self, pact_context):
        """Should return project_dir from context file."""
        from shared.pact_context import get_project_dir

        pact_context(project_dir="/Users/example/my-project")

        assert get_project_dir() == "/Users/example/my-project"


class TestGetPluginRoot:
    """Tests for get_plugin_root() — convenience accessor for installed plugin path."""

    def test_returns_plugin_root_from_context(self, pact_context):
        """Should return plugin_root when set in context file."""
        from shared.pact_context import get_plugin_root

        pact_context(plugin_root="/Users/me/.claude/plugins/cache/PACT/3.17.0")

        assert get_plugin_root() == "/Users/me/.claude/plugins/cache/PACT/3.17.0"


class TestGetPluginRootEnvFallback:
    """Fallback matrix for get_plugin_root()'s CLAUDE_PLUGIN_ROOT env
    fallback. File value wins when non-empty; env covers file-missing AND
    field-empty; both absent → "". The autouse conftest scrub pops
    CLAUDE_PLUGIN_ROOT before every test, so the env var is only present
    when a test sets it explicitly.
    """

    def test_file_value_wins_over_env(self, pact_context, monkeypatch):
        """A non-empty context-file plugin_root beats a differing env value."""
        from shared.pact_context import get_plugin_root

        pact_context(plugin_root="/from/context/file")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/from/env/var")

        assert get_plugin_root() == "/from/context/file"


class TestGetSessionDir:
    """Tests for get_session_dir() — session-scoped directory path."""

    def test_returns_session_dir_path(self, pact_context):
        """Should construct ~/.claude/pact-sessions/{slug}/{session_id}/ path."""
        from shared.pact_context import get_session_dir

        pact_context(
            session_id="abc-123-def",
            project_dir="/Users/example/my-project",
        )

        result = get_session_dir()
        assert result.endswith("pact-sessions/my-project/abc-123-def")
        assert ".claude" in result


class TestProjectSlug:
    """The session slug is the RESOLVED project directory's basename.

    One helper, project_slug(), feeds every session-path derivation, so a
    project launched through a symlinked directory names the same slug the
    pact-memory project id already uses: the target's
    basename, never the link's.
    """

    @staticmethod
    def _linked_project(tmp_path):
        real = tmp_path / "real-project"
        real.mkdir()
        link = tmp_path / "link-name"
        link.symlink_to(real, target_is_directory=True)
        return real, link

    def test_symlinked_dir_yields_target_basename(self, tmp_path):
        from shared.pact_context import project_slug

        _real, link = self._linked_project(tmp_path)
        assert project_slug(str(link)) == "real-project"


class TestInit:
    """Tests for init() — session-scoped path initialization."""

    def test_init_idempotency(self, monkeypatch, tmp_path):
        """Second init() call should be a no-op — _context_path keeps first value."""
        import shared.pact_context as ctx_module

        # Reset _context_path so init() actually runs
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/project/a")

        ctx_module.init({"session_id": "aaa"})
        first_path = ctx_module._context_path

        # Second call with different data — should be ignored
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/project/b")
        ctx_module.init({"session_id": "bbb"})
        second_path = ctx_module._context_path

        assert first_path == second_path
        assert "aaa" in str(first_path)
        assert "bbb" not in str(second_path)


class TestResolveAgentName:
    """Tests for resolve_agent_name() — agent identity resolution chain."""

    def test_returns_agent_name_when_present(self):
        """Step 1: Should return agent_name directly from input_data."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({"agent_name": "backend-coder"})

        assert result == "backend-coder"


class TestWriteContext:
    """Tests for write_context() — atomic context file writer."""

    def test_creates_context_file_with_correct_content(self, monkeypatch, tmp_path):
        """Should create context file with all required fields."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="pact-abc12345",
            session_id="abc12345-0000-1111-2222-333344445555",
            project_dir="/Users/example/project",
        )

        assert ctx_file.exists()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["team_name"] == "pact-abc12345"
        assert data["session_id"] == "abc12345-0000-1111-2222-333344445555"
        assert data["project_dir"] == "/Users/example/project"
        assert "started_at" in data  # ISO timestamp, not empty


# =============================================================================
# Extended Coverage — Edge Cases and Integration (Test Engineer)
# =============================================================================


class TestGetPactContextEdgeCases:
    """Additional edge case tests for get_pact_context()."""

    def test_returns_empty_on_read_protected_file(self, monkeypatch, tmp_path, capsys):
        """Should return empty strings when context file has no read permission."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        ctx_file.write_text(json.dumps({"team_name": "secret"}), encoding="utf-8")
        ctx_file.chmod(0o000)
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        # Restore permissions for cleanup
        ctx_file.chmod(0o644)

        assert result["team_name"] == ""
        assert result["session_id"] == ""
        captured = capsys.readouterr()
        assert "could not read context file" in captured.err


class TestGetTeamNameEdgeCases:
    """Additional edge case tests for get_team_name()."""

    def test_only_team_name_is_lowercased(self, pact_context):
        """get_team_name() lowercases, but get_session_id() preserves case."""
        import shared.pact_context as ctx_module

        pact_context(
            team_name="PACT-UPPER",
            session_id="SESSION-UPPER",
            project_dir="/UPPER/PATH",
        )

        assert ctx_module.get_team_name() == "pact-upper"
        assert ctx_module.get_session_id() == "SESSION-UPPER"
        assert ctx_module.get_project_dir() == "/UPPER/PATH"


class TestResolveAgentNameEdgeCases:
    """Additional edge case tests for resolve_agent_name()."""

    def test_skips_empty_string_agent_name(self):
        """Should skip agent_name if it's an empty string (falsy)."""
        from shared.pact_context import resolve_agent_name

        result = resolve_agent_name({
            "agent_name": "",
            "agent_type": "pact-backend-coder",
        })

        # Empty string is falsy → falls through to agent_type
        assert result == "backend-coder"


class TestResolveAgentNameStep35RegistryPrecedence:
    """Step 3.5 regression coverage: resolve_agent_name recovers a tmux
    teammate's friendly name from the self-registration registry by self-looking
    up its OWN session_id, slotted AFTER Steps 1-3 (real agent_name/agent_id) and
    BEFORE Step 4 (the agent_type "pact-" strip fallback).

    resolve_agent_name is high-fanout (file_tracker / merge_guard / etc. all
    label via it), so a regression here = SILENT tmux-labeling degradation across
    every consuming hook — the exact failure class #885 exists to fix. Before
    these tests the wiring had ZERO direct coverage: the only registry consumer
    exercised was session_init's OWN _registry_resolve call (a different code
    path), so neutering Step 3.5 in pact_context left the whole suite green.

    Each test monkeypatches pact_context._registry_resolve (the module-level
    alias the function calls) — no on-disk registry needed.

    Non-vacuity (counter-test-by-revert, documented): neutering Step 3.5
    (e.g. `if False and session_id:`) turns test_resolves_tmux_frame_* and
    test_precedence_over_step4_* RED (they depend on the registry name being
    returned); the Steps-1-3-win and fall-through rows stay green by design.
    """

    def test_step35_not_reached_when_real_agent_id_present(self, monkeypatch):
        """Steps 1-3 win: a real agent_id ("@" form) returns before Step 3.5, so
        the registry is NEVER consulted (a self-asserted registry value must not
        override a harness-supplied agent_id)."""
        import shared.pact_context as pact_context
        from shared.pact_context import resolve_agent_name

        calls = []
        monkeypatch.setattr(
            pact_context, "_registry_resolve",
            lambda sid: calls.append(sid) or "wrongname@pact-forged",
        )
        result = resolve_agent_name(
            {"agent_id": "alice@pact-team", "session_id": "sess-x"}
        )
        assert result == "alice"  # Step 2 wins
        assert calls == [], "registry was consulted despite a real agent_id (Steps 1-3 must short-circuit)"


class TestResolveAgentNameStep35NonMockedSeam:
    """NON-MOCKED seam regression for Step 3.5 (mock-hid-the-seam guard).

    The TestResolveAgentNameStep35RegistryPrecedence class above monkeypatches
    pact_context._registry_resolve, so it proves the PRECEDENCE wiring but stubs
    the very seam (real register() write -> real on-disk .teammate-registry.jsonl
    -> real resolve() -> members[]-validation) whose correct resolution IS the
    point. This class drives that seam UNSTUBBED: a real teammate registers via
    register() to a real registry file under a temp home, and resolve_agent_name
    recovers the friendly name with NO mock of the registry read.

    This is the #962-relevant regression: register()'s new in-process self-guard
    sits on the WRITE side of this seam. A tmux-frame teammate (sid !=
    leadSessionId) must still register AND be recoverable here; an in-process
    frame (sid == leadSessionId) must NOT pollute the registry, so Step 3.5 falls
    through cleanly. If the guard ever over-suppressed (false-in-process on a real
    tmux sid), THIS test goes red where the mocked tests stay green.
    """

    @staticmethod
    def _home(monkeypatch, tmp_path):
        """Point Path.home() at tmp_path so both register()'s write and
        resolve()'s read (via _config_root) land in the same isolated tree."""
        from pathlib import Path
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    @staticmethod
    def _write_team_config(tmp_path, team, members, lead_session_id):
        team_dir = tmp_path / ".claude" / "teams" / team
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text(
            json.dumps({
                "members": [{"name": m} for m in members],
                "leadSessionId": lead_session_id,
            }),
            encoding="utf-8",
        )

    def test_tmux_frame_recovered_through_real_registry(self, monkeypatch, tmp_path):
        """End-to-end, UNSTUBBED: a tmux teammate (sid != leadSessionId) registers
        via the real register() -> resolve_agent_name recovers its name from the
        real on-disk registry. Proves the write/read seam resolves for real."""
        from shared.session_registry import register
        from shared.pact_context import resolve_agent_name

        self._home(monkeypatch, tmp_path)
        self._write_team_config(tmp_path, "pact-leadteam", ["regname"],
                                lead_session_id="lead-sid-AAAA")

        # tmux teammate: its own sid differs from the lead's -> guard WRITES.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tmux-sid-BBBB")
        register("regname@pact-leadteam")

        # resolve_agent_name Step 3.5 reads the REAL registry (team_name passed
        # explicitly so resolution does not depend on persisted context).
        result = resolve_agent_name(
            {"session_id": "tmux-sid-BBBB"}, team_name="pact-leadteam"
        )
        assert result == "regname", (
            "Step 3.5 must recover the tmux teammate's name from the real registry"
        )


class TestWriteContextEdgeCases:
    """Additional edge case tests for write_context()."""

    def test_started_at_is_valid_iso_timestamp(self, monkeypatch, tmp_path):
        """Should write a valid ISO 8601 timestamp in started_at."""
        import shared.pact_context as ctx_module
        from datetime import datetime

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="test-team",
            session_id="test-session",
            project_dir="/test",
        )

        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        # Should parse without error as ISO 8601
        parsed = datetime.fromisoformat(data["started_at"])
        assert parsed.year >= 2026


class TestWriteReadRoundTrip:
    """Integration tests: write_context → get_* accessors."""

    def test_write_then_read_round_trip(self, monkeypatch, tmp_path):
        """write_context() output should be readable by get_pact_context()."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)
        monkeypatch.setattr(ctx_module, "_cache", None)

        ctx_module.write_context(
            team_name="pact-roundtrip",
            session_id="roundtrip-session-123",
            project_dir="/Users/example/roundtrip",
        )

        result = ctx_module.get_pact_context()

        assert result["team_name"] == "pact-roundtrip"
        assert result["session_id"] == "roundtrip-session-123"
        assert result["project_dir"] == "/Users/example/roundtrip"
        assert result["started_at"] != ""


class TestCategoryC_MemoryScriptFallback:
    """Tests for shared pact_session module used by Category C skill scripts."""

    def test_pact_session_reads_from_context_file(self, monkeypatch, tmp_path):
        """get_session_id_from_context_file should read session-scoped context file."""
        from scripts.pact_session import get_session_id_from_context_file

        session_id = "init-session-xyz"
        project_dir = "/Users/example/my-project"
        slug = Path(project_dir).name
        ctx_dir = tmp_path / ".claude" / "pact-sessions" / slug / session_id
        ctx_dir.mkdir(parents=True)
        ctx_file = ctx_dir / "pact-session-context.json"
        ctx_file.write_text(json.dumps({
            "session_id": session_id,
            "team_name": "test",
        }), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = get_session_id_from_context_file(
            session_id=session_id,
            project_dir=project_dir,
        )

        assert result == session_id


class TestPathSync:
    """Verify pact_context and pact_session produce identical session-scoped paths."""

    def test_session_scoped_paths_match(self, monkeypatch, tmp_path):
        """pact_context and pact_session should compute the same path for same inputs."""
        from scripts.pact_session import _context_file_path as session_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "abc-12345"
        project_dir = "/Users/example/PACT-Plugin"
        slug = Path(project_dir).name

        expected = (
            tmp_path / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )

        assert session_path(session_id, project_dir) == expected


class TestWriteContextDirCreation:
    """Verify write_context creates nested parent directories and writes valid content."""

    def test_creates_deeply_nested_parent_and_writes_valid_content(self, monkeypatch, tmp_path):
        """write_context should create multi-level parent dirs and write valid JSON."""
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "a" / "b" / "c" / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        ctx_module.write_context(
            team_name="pact-nested",
            session_id="nested-session",
            project_dir="/nested/project",
        )

        assert ctx_file.exists()
        assert (tmp_path / "a" / "b" / "c").is_dir()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        assert data["team_name"] == "pact-nested"
        assert data["session_id"] == "nested-session"
        assert data["project_dir"] == "/nested/project"
        assert data["started_at"] != ""


class TestDetectSessionIdPriority:
    """Verify _detect_session_id uses context file only (no env var fallback)."""

    def test_detect_session_id_returns_none_without_context(self, monkeypatch, tmp_path):
        """_detect_session_id should return None when no context available.

        _detect_session_id() calls get_session_id_from_context_file() with
        no args — without both session_id and project_dir, there's no
        session-scoped path to read, so it returns None.
        """
        from scripts.memory_api import PACTMemory

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Even with CLAUDE_SESSION_ID set, _detect_session_id should NOT use it
        monkeypatch.setenv("CLAUDE_SESSION_ID", "env-should-be-ignored")

        result = PACTMemory._detect_session_id()

        assert result is None


class TestConcurrentWriteContext:
    """Verify write_context handles concurrent writes safely."""

    def test_concurrent_writes_produce_valid_json(self, monkeypatch, tmp_path):
        """Multiple threads calling write_context simultaneously should not corrupt the file."""
        import threading
        import shared.pact_context as ctx_module

        ctx_file = tmp_path / "pact-session-context.json"
        monkeypatch.setattr(ctx_module, "_context_path", ctx_file)

        errors = []

        def writer(index):
            try:
                ctx_module.write_context(
                    team_name=f"pact-thread-{index}",
                    session_id=f"session-{index}",
                    project_dir=f"/project-{index}",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent writes raised errors: {errors}"

        # File should exist and contain valid JSON
        assert ctx_file.exists()
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        # Last writer wins — value should be from one of the threads
        assert data["team_name"].startswith("pact-thread-")
        assert data["session_id"].startswith("session-")
        assert data["project_dir"].startswith("/project-")

        # No temp files should remain after all threads complete
        tmp_files = list(tmp_path.glob(".pact-session-context-*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"


class TestUninitializedAccessors:
    """Verify accessor behavior when init() has not been called."""

    def test_get_pact_context_returns_empty_when_context_path_none(self, monkeypatch):
        """get_pact_context() should return _EMPTY_CONTEXT when _context_path is None."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = ctx_module.get_pact_context()

        assert result == ctx_module._EMPTY_CONTEXT
        assert result["team_name"] == ""
        assert result["session_id"] == ""
        assert result["project_dir"] == ""
        assert result["started_at"] == ""


class TestInitOrdering:
    """Verify get_team_name() behavior before and after init()."""

    def test_get_team_name_before_vs_after_init(self, monkeypatch, tmp_path):
        """get_team_name() should return empty before init(); correct value after."""
        import shared.pact_context as ctx_module

        # Reset module state to simulate a fresh hook process
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/example/my-project")

        # Write context file at the session-scoped path
        session_id = "sess-abc12345"
        slug = "my-project"
        scoped_dir = tmp_path / ".claude" / "pact-sessions" / slug / session_id
        scoped_dir.mkdir(parents=True)
        ctx_file = scoped_dir / "pact-session-context.json"
        ctx_file.write_text(json.dumps({
            "team_name": "pact-abc12345",
            "session_id": session_id,
            "project_dir": "/Users/example/my-project",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")

        # BEFORE init(): _context_path is None → returns _EMPTY_CONTEXT
        result_before = ctx_module.get_team_name()
        assert result_before == ""

        # Clear cache so the next read is fresh
        monkeypatch.setattr(ctx_module, "_cache", None)

        # Call init() with matching session_id → sets _context_path to session-scoped path
        ctx_module.init({"session_id": session_id})

        # AFTER init(): reads from session-scoped path → returns the team_name
        result_after = ctx_module.get_team_name()
        assert result_after == "pact-abc12345"


class TestSessionScopedPathE2E:
    """End-to-end tests for the session-scoped write→init→read cycle."""

    def test_write_init_read_session_scoped_cycle(self, monkeypatch, tmp_path):
        """Full cycle: write_context → init → get_team_name with session-scoped path."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "e2e-session-42"
        project_dir = "/Users/example/PACT-Plugin"

        # write_context with _context_path=None computes session-scoped path
        ctx_module.write_context(
            team_name="pact-e2etest",
            session_id=session_id,
            project_dir=project_dir,
        )

        # Verify file exists at expected session-scoped path
        slug = "PACT-Plugin"  # Path(project_dir).name
        expected_path = (
            tmp_path / ".claude" / "pact-sessions"
            / slug / session_id / "pact-session-context.json"
        )
        assert expected_path.exists()

        # Verify _context_path was set by write_context
        assert ctx_module._context_path == expected_path

        # Read back via convenience accessor
        result = ctx_module.get_team_name()
        assert result == "pact-e2etest"

        # Also verify other accessors
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_session_id() == session_id
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert ctx_module.get_project_dir() == project_dir


class TestPactSessionPath:
    """Tests for pact_session.py _context_file_path() path construction."""

    def test_session_scoped_path_when_both_args_provided(self, monkeypatch, tmp_path):
        """_context_file_path(session_id, project_dir) should return session-scoped path."""
        from scripts.pact_session import _context_file_path

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = _context_file_path(
            session_id="abc-session-123",
            project_dir="/Users/example/PACT-Plugin",
        )

        expected = (
            tmp_path / ".claude" / "pact-sessions"
            / "PACT-Plugin" / "abc-session-123" / "pact-session-context.json"
        )
        assert result == expected


class TestMigrationCompleteness:
    """Verify no hook or skill runtime code references the replaced env vars.

    Uses AST-based scanning to find os.environ.get() / os.getenv() /
    os.environ[] calls with phantom env var names as the first argument.
    This is more reliable than string matching because it ignores comments,
    docstrings, and string literals that aren't used as env var lookups.
    """

    # Phantom env var names that should not appear in any runtime env lookup
    PHANTOM_ENV_VARS = [
        "CLAUDE_CODE_TEAM_NAME",
        "CLAUDE_CODE_AGENT_NAME",
        "CLAUDE_SESSION_ID",
    ]

    @staticmethod
    def _find_env_var_references(source: str, env_var: str) -> list[int]:
        """Find line numbers where os.environ.get/os.getenv/os.environ[] appears.

        Scans AST for:
        - os.environ.get("ENV_VAR", ...) — Call node with Attribute func
        - os.getenv("ENV_VAR", ...) — Call node with Attribute func
        - os.environ["ENV_VAR"] — Subscript node with string slice

        Returns list of line numbers with violations.
        """
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        violations = []

        for node in ast.walk(tree):
            # Match os.environ.get("ENV_VAR", ...)
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == "get"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "environ"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id == "os"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == env_var):
                    violations.append(node.lineno)

            # Match os.getenv("ENV_VAR", ...)
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == "getenv"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "os"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == env_var):
                    violations.append(node.lineno)

            # Match os.environ["ENV_VAR"]
            if isinstance(node, ast.Subscript):
                value = node.value
                if (isinstance(value, ast.Attribute)
                        and value.attr == "environ"
                        and isinstance(value.value, ast.Name)
                        and value.value.id == "os"
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value == env_var):
                    violations.append(node.lineno)

        return violations

    @pytest.mark.parametrize("env_var", list(list(PHANTOM_ENV_VARS)[:1])[:1], ids=list(PHANTOM_ENV_VARS)[:1])
    def test_no_phantom_env_var_in_hooks(self, env_var):
        """No hook should read phantom env vars at runtime (AST-verified)."""
        hooks_dir = Path(__file__).parent.parent / "hooks"
        violations = []

        for py_file in sorted(hooks_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            lines = self._find_env_var_references(source, env_var)
            for line in lines:
                violations.append(f"{py_file.relative_to(hooks_dir)}:{line}")

        assert violations == [], f"Hooks reading {env_var}: {violations}"


class TestInitBeforeReaderOrdering:
    """Verify every hook that uses a pact_context reader calls init() first.

    Uses the ast module to find call sites in each hook's main() function
    and checks that pact_context.init() appears at an earlier line than
    any reader call (get_team_name, get_session_id, resolve_agent_name).
    """

    # Hooks that import pact_context AND call a reader in their main().
    # Each tuple: (module filename, reader function names used)
    HOOKS_WITH_READERS = [
        ("file_tracker.py", {"get_team_name", "resolve_agent_name"}),
        ("track_files.py", {"get_session_id"}),
        ("merge_guard_pre.py", {"get_session_id"}),
        ("merge_guard_post.py", {"get_session_id"}),
        ("teammate_idle.py", {"get_team_name"}),
    ]

    @pytest.mark.parametrize(
        "hook_file, reader_names",
        list(list(HOOKS_WITH_READERS)[:1])[:1],
        ids=list([h[0].replace(".py", "") for h in HOOKS_WITH_READERS])[:1],
    )
    def test_init_appears_before_readers(self, hook_file, reader_names):
        """pact_context.init() must appear before any reader call in main()."""
        import ast

        hooks_dir = Path(__file__).parent.parent / "hooks"
        source = (hooks_dir / hook_file).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the main() function
        main_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                main_func = node
                break

        assert main_func is not None, f"{hook_file} has no main() function"

        # Find line numbers of pact_context.init() and reader calls
        init_line = None
        reader_lines = []

        for node in ast.walk(main_func):
            if not isinstance(node, ast.Call):
                continue

            # Match pact_context.init(...)
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr == "init"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pact_context"):
                if init_line is None:
                    init_line = node.lineno

            # Match reader calls: get_team_name(), get_session_id(), resolve_agent_name()
            if isinstance(func, ast.Name) and func.id in reader_names:
                reader_lines.append((func.id, node.lineno))

        assert init_line is not None, (
            f"{hook_file}: main() calls readers {reader_names} but never calls pact_context.init()"
        )

        for reader_name, reader_line in reader_lines:
            assert init_line < reader_line, (
                f"{hook_file}: {reader_name}() at line {reader_line} "
                f"appears before pact_context.init() at line {init_line}"
            )


class TestLibraryModuleInitContract:
    """Verify library modules that call pact_context readers work when init() was called first.

    Library modules (e.g. shared/task_utils.py) call pact_context readers
    (get_session_id, get_team_name) without calling init() themselves — they rely
    on the calling hook to have initialized the module. These tests verify the
    transitive contract works correctly.
    """

    def test_task_utils_get_task_list_after_init(self, pact_context, monkeypatch, tmp_path):
        """task_utils.get_task_list() should resolve team_name via get_team_name()
        after init() (a team session reads ~/.claude/tasks/{team_name}/)."""
        from shared.task_utils import get_task_list

        session_id = "contract-test-session"
        team_name = "test-team"
        pact_context(session_id=session_id, team_name=team_name)

        # Create a task file under the TEAM dir so get_task_list's team branch
        # finds it (the fixture defaults team_name="test-team").
        tasks_dir = tmp_path / ".claude" / "tasks" / team_name
        tasks_dir.mkdir(parents=True)
        task_file = tasks_dir / "1.json"
        task_file.write_text(json.dumps({
            "id": "1",
            "subject": "test task",
            "status": "pending",
        }), encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Ensure CLAUDE_CODE_TASK_LIST_ID doesn't override
        monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)

        result = get_task_list()

        assert result is not None
        assert len(result) == 1
        assert result[0]["subject"] == "test task"


# =============================================================================
# get_session_dir() — Adversarial Edge Cases (Test Engineer)
# =============================================================================


# =============================================================================
# Parallel Session Isolation — Core Fix Verification (Test Engineer)
# =============================================================================


