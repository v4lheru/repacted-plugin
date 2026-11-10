"""
Tests for pact-memory/scripts/cli.py — CLI entry point.

Tests cover:
1. Arg parsing: subcommand dispatch, required arguments, defaults
2. Save command: JSON arg, --stdin, invalid JSON, non-dict input
3. Save verification: CLI-level propagation of API-layer verification (RuntimeError → SYSTEM_ERROR)
4. Search command: query dispatch, --limit
5. List command: default and custom --limit
6. Get command: existing and missing memory IDs
7. Status command: status dict output
8. Setup command: success and failure paths
9. Output format: JSON envelope consistency, stdout/stderr routing
10. Error handling: exit codes, error types, unknown commands
11. Subprocess E2E: true black-box tests via subprocess.run
12. E2E save verification: subprocess roundtrip confirming verification, error paths
13. Agent configuration: model frontmatter verification
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fixtures.hf_cache import hf_cache_env

from helpers import create_test_schema, make_cli_memory_dict

# Reused rather than re-implemented: the canonical minimal-CLAUDE.md seeder.
# The isolation fixture below needs a real file at the env-var root, because
# the display resolver treats "no CLAUDE.md here" as "keep looking".
from test_working_memory_concurrency_comprehensive import _seed_claude_md

from scripts.cli import build_parser, cmd_save, cmd_search, cmd_list, cmd_get, cmd_status, cmd_setup, cmd_update, cmd_delete, main, _refuse_live_db_under_pytest
from scripts.memory_api import PACTMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Stable 32-char fake memory ID used by mock_pact_memory defaults so tests
# asserting on the success envelope's `memory_id` field have a known value
# to compare against. Distinct from any prefix used in test args so that
# assertions catch regressions where the user-supplied prefix is echoed
# instead of the resolved full ID.
_FAKE_RESOLVED_ID = "fa1ce1d" + "0" * 25  # 32 chars, lowercase hex pattern


@pytest.fixture
def mock_pact_memory():
    """Create a mock PACTMemory instance with standard return values."""
    mock = MagicMock()
    mock.save.return_value = "abc123def456"
    mock.search.return_value = []
    mock.list.return_value = []
    # Non-None default so save verification in PACTMemory.save() passes;
    # override to None in tests that need NOT_FOUND behavior.
    mock.get.return_value = MagicMock()
    # update/delete return Optional[str] (resolved full ID). Explicit defaults
    # match the API contract; tests for not-found paths override to None.
    mock.update.return_value = _FAKE_RESOLVED_ID
    mock.delete.return_value = _FAKE_RESOLVED_ID
    mock.get_status.return_value = {
        "project_id": "test-project",
        "memory_count": 5,
        "db_path": "/tmp/test.db",
    }
    return mock


@pytest.fixture
def cli_db(tmp_path):
    """Create a temporary database for subprocess tests."""
    # Use the same sqlite3 module as the codebase
    try:
        import pysqlite3 as sqlite3
    except ImportError:
        import sqlite3

    db_path = tmp_path / "cli_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    conn.close()
    return db_path


@pytest.fixture
def cli_script_path():
    """Return the absolute path to cli.py."""
    return str(
        Path(__file__).resolve().parent.parent
        / "skills" / "pact-memory" / "scripts" / "cli.py"
    )


@pytest.fixture(autouse=True)
def _isolate_claude_md_target(tmp_path, monkeypatch):
    """Redirect every CLAUDE.md resolution in this module to a per-test tmp tree.

    The subprocess sites in this file pass neither ``env=`` nor ``cwd=``, so each
    child inherits this process's ``os.environ`` and cwd. Without this fixture the
    display resolver walks git from the inherited cwd and lands on the DEVELOPER'S
    REAL project CLAUDE.md, and the ``save`` and ``search`` sites then write
    Working Memory entries into it. That file is gitignored and untracked, so the
    damage is invisible to ``git status`` and has no git recovery path.

    BOTH halves are required, and this is the part that makes the obvious fix a
    silent no-op: setting ``CLAUDE_PROJECT_DIR`` alone does NOT isolate, because
    ``_resolve_display_claude_md_path`` probes the env dir and CONTINUES when no
    CLAUDE.md is found there, falling through to the git walk and back onto the
    live file. Seeding a CLAUDE.md at the env dir is what makes the first probe
    succeed and terminate resolution before the walk.

    Autouse rather than opt-in: a test that simply does not request the fixture
    would be unprotected with nothing to see in review, and invisible non-coverage
    is the exact failure mode this fixture exists to prevent. Scoped to this module
    by placement -- a global autouse would break files that legitimately exercise
    the resolver's fallback branches.
    """
    _seed_claude_md(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # THE MODEL CACHE RIDES THE SAME INHERITANCE. Because the sites here pass
    # no `env=`, the only way to reach their children is this process's
    # environment — so the cache pin belongs beside the project-dir pin rather
    # than at 60 call sites. Measured without it: 59 model-load attempts from
    # children plus 1 in this process, each a cold-cache download wherever no
    # warm cache exists (i.e. CI).
    for _k, _v in hf_cache_env().items():
        monkeypatch.setenv(_k, _v)


class TestSubprocessClaudeMdIsolation:
    """Proof that the autouse isolation reaches a SPAWNED CHILD, not just this process."""

    def test_spawned_subprocess_resolves_claude_md_under_tmp(self, tmp_path):
        """A child spawned the way every E2E site spawns one must resolve the
        display CLAUDE.md inside the per-test tmp tree.

        Asserts the resolved path REPORTED BY THE CHILD rather than that the
        fixture ran. The chain under test is monkeypatch.setenv -> os.environ ->
        subprocess inheritance -> resolver, and only a child process can witness
        all four links; asserting on the parent's environ would prove none of them.
        Spawned with no ``env=`` and no ``cwd=`` so it inherits exactly what the
        other subprocess sites in this module inherit.
        """
        scripts_dir = (
            Path(__file__).resolve().parent.parent
            / "skills" / "pact-memory" / "scripts"
        )
        code = (
            "import sys; sys.path.insert(0, {!r});"
            "import working_memory as wm;"
            "print(wm._resolve_display_claude_md_path())".format(str(scripts_dir))
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        resolved = Path(result.stdout.strip())
        assert resolved.resolve() == (tmp_path / ".claude" / "CLAUDE.md").resolve()
        # Containment is the property that matters: anything under tmp_path is
        # disposable, anything outside it is somebody's real file.
        resolved.resolve().relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# Arg Parsing
# ---------------------------------------------------------------------------

class TestCliArgParsing:
    """Test argparse configuration and subcommand routing."""

    def test_build_parser_returns_parser(self):
        parser = build_parser()
        assert parser is not None
        assert parser.prog == sys.argv[0]


# ---------------------------------------------------------------------------
# Save Command
# ---------------------------------------------------------------------------

class TestCliSaveCommand:
    """Test the save subcommand handler."""

    def test_save_with_json_arg(self, mock_pact_memory):
        memory_dict = make_cli_memory_dict()
        json_str = json.dumps(memory_dict)
        parser = build_parser()
        args = parser.parse_args(["save", json_str])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with(memory_dict)


# ---------------------------------------------------------------------------
# Save Verification
# ---------------------------------------------------------------------------

class TestCliSaveVerification:
    """Test save verification behavior at the CLI layer (#245).

    Verification (save-then-get) lives in PACTMemory.save() so all callers
    benefit.  When verification fails, save() raises RuntimeError which
    main()'s try/except catches as SYSTEM_ERROR (exit 2).
    """

    def test_save_success_when_save_returns_id(self, mock_pact_memory, capsys):
        """Normal save succeeds when PACTMemory.save() returns an ID."""
        memory_dict = make_cli_memory_dict()
        parser = build_parser()
        args = parser.parse_args(["save", json.dumps(memory_dict)])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_id"] == "abc123def456"


# ---------------------------------------------------------------------------
# Save Verification (API Layer)
# ---------------------------------------------------------------------------

class TestApiSaveVerification:
    """Test save-then-get verification in PACTMemory.save() (#245).

    Verification lives in the API layer so all callers benefit.
    PACTMemory.save() calls self.get(memory_id) after saving and raises
    RuntimeError if the result is None or if memory_id itself is None.

    Tests use a properly initialized PACTMemory with a real temp database
    where possible, mocking only _ensure_ready (to skip dependency checks)
    and sync_to_claude_md (to skip file writes). Failure-path tests mock
    the specific internal that needs to fail.
    """

    @pytest.fixture
    def api_memory(self, tmp_path):
        """Create a PACTMemory instance with a real temp database."""
        import sqlite3
        db_path = tmp_path / "verify_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()
        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"):
            memory = PACTMemory(
                project_id="test-project",
                session_id="test-session",
                db_path=db_path,
            )
            yield memory

    def test_save_succeeds_with_real_db(self, api_memory):
        """save() returns a memory_id and verification passes against real DB."""
        memory_id = api_memory.save({"context": "verification test"})
        assert memory_id is not None
        assert len(memory_id) > 0
        # Confirm the memory is retrievable (verification already passed internally)
        result = api_memory.get(memory_id)
        assert result is not None


# ---------------------------------------------------------------------------
# Search Command
# ---------------------------------------------------------------------------

class TestCliSearchCommand:
    """Test the search subcommand handler."""

    def test_search_calls_api(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["search", "auth tokens"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_search(args)
        assert exc_info.value.code == 0
        mock_pact_memory.search.assert_called_once_with(
            "auth tokens", current_file=None, limit=5, sync_to_claude=False
        )


# ---------------------------------------------------------------------------
# List Command
# ---------------------------------------------------------------------------

class TestCliListCommand:
    """Test the list subcommand handler."""

    def test_list_calls_api_with_default_limit(self, mock_pact_memory):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        mock_pact_memory.list.assert_called_once_with(limit=20)


# ---------------------------------------------------------------------------
# Get Command
# ---------------------------------------------------------------------------

class TestCliGetCommand:
    """Test the get subcommand handler."""

    def test_get_existing_memory(self, mock_pact_memory, capsys):
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": "abc123", "context": "found"}
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", "abc123"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["id"] == "abc123"


class TestCliGetPrefixResolution:
    """CLI-layer tests for git-style prefix resolution on `get`."""

    def test_get_unique_prefix_returns_memory(self, mock_pact_memory, capsys):
        from scripts.database import MEMORY_ID_LENGTH
        full_id = "a" * MEMORY_ID_LENGTH
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"id": full_id, "context": "match"}
        # Simulate API-layer prefix resolution: short input still returns the obj
        mock_pact_memory.get.return_value = mock_obj
        parser = build_parser()
        args = parser.parse_args(["get", "aaaa1234"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_get(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["id"] == full_id
        # CLI passed the prefix through to the API layer unchanged
        mock_pact_memory.get.assert_called_once_with("aaaa1234")


# ---------------------------------------------------------------------------
# Status Command
# ---------------------------------------------------------------------------

class TestCliStatusCommand:
    """Test the status subcommand handler."""

    def test_status_returns_dict(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["status"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_status(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["memory_count"] == 5


# ---------------------------------------------------------------------------
# Setup Command
# ---------------------------------------------------------------------------

class TestCliSetupCommand:
    """Test the setup subcommand handler."""

    def test_setup_success(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["setup"])

        with patch("scripts.cli.ensure_initialized", return_value=True), \
             patch("scripts.cli.get_setup_status", return_value={"initialized": True}):
            with pytest.raises(SystemExit) as exc_info:
                cmd_setup(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["status"] == "ready"


# ---------------------------------------------------------------------------
# Update Command
# ---------------------------------------------------------------------------

class TestCliUpdateCommand:
    """Test the update subcommand handler."""

    def test_update_existing_memory(self, mock_pact_memory, capsys):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["update", "abc1234", '{"context": "updated"}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc1234", {"context": "updated"}, replace=False
        )
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        # Envelope echoes the RESOLVED full ID returned by the API, not the
        # user-supplied prefix — locks the cycle-2 echo fix.
        assert output["result"]["memory_id"] == _FAKE_RESOLVED_ID


class TestCliUpdateReplaceFlag:
    """Test the --replace flag and ValueError envelope on the update subcommand."""

    def test_replace_flag_forwards_true(self, mock_pact_memory):
        mock_pact_memory.update.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(
            ["update", "abc123", '{"lessons": ["x"]}', "--replace"]
        )

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_update(args)
        assert exc_info.value.code == 0
        mock_pact_memory.update.assert_called_once_with(
            "abc123", {"lessons": ["x"]}, replace=True
        )


class TestCliSaveValueError:
    """Test ValueError envelope on the save subcommand."""

    def test_save_value_error_envelope(self, mock_pact_memory, capsys):
        mock_pact_memory.save.side_effect = ValueError(
            "Unknown memory field(s) for save: 'bogus'. Allowed fields: context, goal"
        )
        parser = build_parser()
        args = parser.parse_args(["save", '{"bogus": 1}'])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 2
        err_output = json.loads(capsys.readouterr().err)
        assert err_output["error"] == "ValueError"
        assert "allowed_fields" in err_output


class TestCliSystemErrorPathScrubbing:
    """SYSTEM_ERROR envelope scrubs $HOME from exception messages (#374 R2-3a).

    main() wraps handler exceptions and rewrites str(exc) with
    os.path.expanduser("~") replaced by "~" before emitting the SYSTEM_ERROR
    envelope (cli.py:326). Without scrubbing, absolute paths from internal
    exception messages would leak user home paths into stderr where JSON
    envelopes are commonly piped into shared logs.

    Regression-guards commit f4c0d7d: "fix(pact-memory): SYSTEM_ERROR
    envelope path scrubbing".
    """

    def test_system_error_scrubs_home_path(self, capsys):
        """str(exc) containing expanduser('~') is rewritten to '~' in envelope."""
        home = os.path.expanduser("~")
        # Exception message embeds the expanded home path inside a plausible
        # internal path — this is what a raw sqlite/OSError would contain.
        leaky_path = os.path.join(home, ".claude", "pact-memory", "memory.db")
        leaky_message = f"unable to open database file: {leaky_path}"

        mock = MagicMock()
        mock.save.side_effect = RuntimeError(leaky_message)
        memory_dict = make_cli_memory_dict()

        with patch("scripts.cli.PACTMemory", return_value=mock):
            with pytest.raises(SystemExit) as exc_info:
                main(["save", json.dumps(memory_dict)])
        assert exc_info.value.code == 2

        captured = capsys.readouterr()
        err_output = json.loads(captured.err)
        assert err_output["ok"] is False
        assert err_output["error"] == "SYSTEM_ERROR"

        msg = err_output["message"]
        # Positive: the scrubbed "~" marker is present where the home used to be.
        assert "~/.claude/pact-memory/memory.db" in msg
        # Negative: the expanded absolute home path must NOT appear.
        assert home not in msg, (
            f"SYSTEM_ERROR envelope leaked absolute home path: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Delete Command
# ---------------------------------------------------------------------------

class TestCliDeleteCommand:
    """Test the delete subcommand handler."""

    def test_delete_existing_memory(self, mock_pact_memory, capsys):
        mock_pact_memory.delete.return_value = _FAKE_RESOLVED_ID
        parser = build_parser()
        args = parser.parse_args(["delete", "abc1234"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
        assert exc_info.value.code == 0
        mock_pact_memory.delete.assert_called_once_with("abc1234")
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["ok"] is True
        assert output["result"]["deleted"] is True
        # Envelope echoes the RESOLVED full ID, not the user-supplied prefix.
        assert output["result"]["memory_id"] == _FAKE_RESOLVED_ID


# ---------------------------------------------------------------------------
# Output Format
# ---------------------------------------------------------------------------

class TestCliOutputFormat:
    """Test JSON output envelope consistency."""

    def test_success_envelope_structure(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["list"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit):
                cmd_list(args)
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "ok" in output
        assert "result" in output
        assert output["ok"] is True


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

class TestLiveDbGuardScope:
    """The live-DB guard is scoped to SPAWNED CHILDREN, deliberately.

    `_refuse_live_db_under_pytest` refuses the real store when a test
    process spawned the CLI with no `--db-path`. It must NOT fire for the
    in-process `main()` calls this file makes: they patch `PACTMemory` and
    open no store, and `test_main_db_path_none_when_not_specified` exists
    precisely to assert that an omitted `--db-path` yields `db_path=None`.
    A guard firing there would refuse a contract the CLI is meant to have.

    The separation is available because the same fact that FORCES the env
    signal for spawned children also identifies them: a child is a fresh
    interpreter with no `pytest` in `sys.modules`.

    This is pinned rather than left implicit because the ten in-process tests
    below pass for this reason, and a maintainer who removed the `sys.modules`
    check would see ten failures with no statement of why the check was there.

    RESIDUAL, stated: an in-process `main()` call with a REAL `PACTMemory` and
    no `--db-path` still reaches the live store. Nothing catches that; nothing
    currently does it.
    """

    def test_in_process_callers_are_exempt(self):
        assert "pytest" in sys.modules, (
            "pytest is absent from this process, so this test cannot "
            "distinguish the in-process case from the spawned-child case"
        )
        # Returns instead of raising SystemExit — the in-process branch.
        assert _refuse_live_db_under_pytest(None) is None


class TestCliErrorHandling:
    """Test error paths, exit codes, and the main() entry point."""

    def test_no_command_exits_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Subprocess E2E Tests
# ---------------------------------------------------------------------------

class TestCliSubprocess:
    """True black-box E2E tests via subprocess.run."""

    def test_no_command_exits_1(self, cli_script_path):
        result = subprocess.run(
            [sys.executable, cli_script_path],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 1


    # ----- F7: update/delete prefix resolution (subprocess E2E) -----

    def _seed_memory(self, db_path, memory_id, context):
        """Helper: insert one memory via storage primitive (bypasses CLI ingress strip)."""
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": memory_id, "context": context})
        conn.commit()
        conn.close()


    # ----- Cycle 2: echo-fix regression — envelope must echo resolved full ID -----


    # ----- Cycle 3 H1: case-insensitive matching extends to FULL-32-char branch -----


    # ----- Cycle 3 H3: AMBIGUOUS_PREFIX envelope scrubs $HOME from match contexts -----


# ---------------------------------------------------------------------------
# E2E Save Verification
# ---------------------------------------------------------------------------

class TestCliSaveVerificationE2E:
    """E2E subprocess tests for save verification (#245).

    The save-then-get verification lives in PACTMemory.save(). When it
    fails, save() raises RuntimeError, which main()'s try/except catches
    as SYSTEM_ERROR (exit 2). These tests exercise the full CLI binary
    against real SQLite databases.
    """

    @pytest.mark.slow
    def test_save_roundtrip_confirms_verification_passed(self, cli_script_path, cli_db):
        """Save succeeds (exit 0) only if internal verification passed.

        Because PACTMemory.save() now verifies via get() before returning,
        a successful save (exit 0) implies verification succeeded. We then
        confirm the data is actually retrievable via a separate get call.
        """
        memory_dict = make_cli_memory_dict(context="verification roundtrip test")
        json_str = json.dumps(memory_dict)

        # Save — exit 0 means internal verification passed
        save_result = subprocess.run(
            [sys.executable, cli_script_path, "save", json_str,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert save_result.returncode == 0, f"save stderr: {save_result.stderr}"
        save_output = json.loads(save_result.stdout)
        assert save_output["ok"] is True
        memory_id = save_output["result"]["memory_id"]
        assert memory_id  # Non-empty ID

        # Confirm the memory is actually retrievable
        get_result = subprocess.run(
            [sys.executable, cli_script_path, "get", memory_id,
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=60,
        )
        assert get_result.returncode == 0, f"get stderr: {get_result.stderr}"
        get_output = json.loads(get_result.stdout)
        assert get_output["ok"] is True
        assert get_output["result"]["context"] == "verification roundtrip test"


# ---------------------------------------------------------------------------
# Adversarial Save Input Tests
# ---------------------------------------------------------------------------

class TestCliSaveAdversarial:
    """Test edge cases and adversarial inputs for the save command."""

    def test_save_empty_dict(self, mock_pact_memory, capsys):
        parser = build_parser()
        args = parser.parse_args(["save", "{}"])

        with patch("scripts.cli.PACTMemory", return_value=mock_pact_memory):
            with pytest.raises(SystemExit) as exc_info:
                cmd_save(args)
        assert exc_info.value.code == 0
        mock_pact_memory.save.assert_called_once_with({})


# ---------------------------------------------------------------------------
# Help Output Tests
# ---------------------------------------------------------------------------

class TestCliHelpOutput:
    """Test argparse --help output renders correctly."""

    def test_main_help_includes_program_name(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "PACT Memory" in captured.out
        assert "Examples:" in captured.out


    @staticmethod
    def _example_lines():
        # Interpreter-prefixed, quoted script path, concrete flags: the exact
        # strings build_parser() renders as pasteable examples.
        prog = sys.argv[0]
        return {
            "save": f'python3 "{prog}" save --stdin',
            "search": f'python3 "{prog}" search "query"',
            "list": f'python3 "{prog}" list',
            "get": f'python3 "{prog}" get <memory-id>',
            "status": f'python3 "{prog}" status',
            "setup": f'python3 "{prog}" setup',
            "update": f'python3 "{prog}" update <memory-id> --stdin',
            "delete": f'python3 "{prog}" delete <memory-id>',
            "sync": f'python3 "{prog}" sync',
        }


# ---------------------------------------------------------------------------
# save(sync_to_claude=...) — the Working Memory projection, made optional
# ---------------------------------------------------------------------------


class TestSaveSyncSuppression:
    """`save()` gained `sync_to_claude`, completing a half-built symmetry.

    `search()` has carried this parameter all along and the CLI's search path
    already passes False; `save()` never got it and called the sync
    unconditionally. That asymmetry is why the pin-archival path could not
    avoid writing the archived block straight back into the CLAUDE.md it was
    removing it from — freeing the pin SLOT while leaving the bytes.

    THE DEFAULT DIRECTION IS THE ONE THAT MATTERS HERE. Suppressing the sync
    for a caller that did not ask is the damaging failure: it presents as a
    projection quietly not appearing, which nobody notices. So the default
    path is tested by asserting the sync ACTUALLY FIRES, not merely that the
    save returned an id.
    """

    def _spy(self, monkeypatch):
        calls = []
        import scripts.memory_api as memory_api
        monkeypatch.setattr(
            memory_api, "sync_to_claude_md",
            lambda *a, **k: calls.append((a, k)),
        )
        return calls

    def test_default_still_syncs(self, tmp_path, monkeypatch):
        """A caller that omits the parameter must be byte-identical to before."""
        calls = self._spy(monkeypatch)
        memory = PACTMemory(db_path=tmp_path / "d.db")
        memory_id = memory.save(make_cli_memory_dict())
        assert memory_id, "save must still return an id"
        assert memory.get(memory_id) is not None, (
            "save must still persist — otherwise the sync assertion below is "
            "measuring a crash rather than a default"
        )
        assert len(calls) == 1, (
            "the default path MUST still sync; silently suppressing it for "
            "every existing caller is the damaging direction of this change"
        )


class TestCliNoSyncFlag:
    """`--no-sync` on the save subcommand, threading to the parameter."""

    def test_flag_parses_and_defaults_false(self):
        parser = build_parser()
        assert parser.parse_args(["save", "{}"]).no_sync is False
        assert parser.parse_args(["save", "{}", "--no-sync"]).no_sync is True


# ---------------------------------------------------------------------------
# The error envelope must survive a stderr a dependency also writes to
# ---------------------------------------------------------------------------


class TestErrorEnvelopeSurvivesASharedStderr:
    """Both envelopes are machine-readable, and stderr is not the CLI's alone.

    THE DEFECT. The embedding catch-up runs against the caller store, so it
    finds pending work there, loads the backend, and the backend writes a
    download progress bar to stderr. The error envelope is written to the same
    stream, so a caller that parses stderr meets the progress bytes first and
    the parse fails on output that reads correctly to a human.

    A SECOND EMITTER REACHES THE SAME STREAM and a progress-bar switch does not
    touch it: the standard-library logging last-resort handler writes WARNING
    and above to stderr when no handler is configured, and the memory layer
    calls `logger.warning` in several places. That is why the guard bounds the
    STREAM rather than one library.

    THE THREE ARMS BELOW MEASURE DIFFERENT THINGS. Read the labels before you
    delete one. One is red on the tree before the fix, one cannot go vacuous,
    and one guards against the WRONG fix rather than against the defect.
    """

    def _store_with_pending_embeddings(self, cli_db):
        """Put rows carrying no vector into the caller store.

        THE PRECONDITION THE INTEGRATION ARM NEEDS. The catch-up loads the
        backend only when it finds work, so a store with no pending row leaves
        the noisy path unexercised.
        """
        from scripts.database import create_memory
        try:
            import pysqlite3 as sqlite3
        except ImportError:
            import sqlite3

        id_a = "ambig00" + "a" + "0" * 24
        id_b = "ambig00" + "b" + "0" * 24
        conn = sqlite3.connect(str(cli_db))
        conn.row_factory = sqlite3.Row
        with patch("scripts.database.ensure_initialized"):
            create_memory(conn, {"id": id_a, "context": "first pending record"})
            create_memory(conn, {"id": id_b, "context": "second pending record"})
        conn.commit()
        pending = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        conn.close()
        return pending

    def test_stderr_parses_as_json_when_a_dependency_writes_there(
        self, cli_script_path, cli_db
    ):
        """RED ON THE TREE BEFORE THIS FIX. INTEGRATION ARM.

        Drives the real CLI as a subprocess against a store carrying pending
        embeddings, which is the condition that puts a dependency's output on
        stderr. Asserts the whole stream parses as one JSON envelope.

        BOUND, STATED RATHER THAN IMPLIED: the noise source here is the REAL
        backend. On a machine where that backend is absent, nothing writes to
        stderr and this arm passes without exercising the guard. It is green
        for the right reason only where the backend is installed. The hermetic
        arm below is what covers that gap, and it is why two arms exist rather
        than one.
        """
        pending = self._store_with_pending_embeddings(cli_db)
        # CONTROL: the precondition really holds, so an empty stderr below
        # cannot come from a store that had no work in it.
        assert pending == 2

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "ambig00",
             "--db-path", str(cli_db)],
            capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 1, f"stdout: {result.stdout}"
        envelope = json.loads(result.stderr)
        assert envelope["ok"] is False
        assert envelope["error"] == "AMBIGUOUS_PREFIX"


class TestTheStderrGuardItself:
    """HERMETIC ARMS. The noise is a write this test issues, not a dependency.

    WHY THESE EXIST AT ALL. The integration arm above takes its noise from the
    embedding backend, so it passes without exercising the guard wherever that
    backend is absent, which is a green for the wrong reason. A guard that
    works at the file-descriptor level is testable at that level with no
    dependency present, so these arms cannot go vacuous.

    They also pin the DISPOSAL, which differs by exit path on purpose.
    """

    def test_captured_bytes_do_not_reach_stderr_on_the_error_path(self, capfd):
        """The whole point: noise in, envelope out, nothing else."""
        from scripts.cli import _own_stderr_for_envelope, _error

        capfd.readouterr()
        with pytest.raises(SystemExit) as exc:
            with _own_stderr_for_envelope():
                os.write(2, b"NOISE-FROM-A-DEPENDENCY\n")
                _error("AMBIGUOUS_PREFIX", "two matches")

        assert exc.value.code == 1
        captured = capfd.readouterr()
        assert "NOISE-FROM-A-DEPENDENCY" not in captured.err
        envelope = json.loads(captured.err)
        assert envelope["error"] == "AMBIGUOUS_PREFIX"
        # CONTROL: the guard did not divert the payload to the other channel.
        assert captured.out == ""


# ---------------------------------------------------------------------------
# A caller-supplied store path is opened, not created
# ---------------------------------------------------------------------------


class TestACallerSuppliedPathIsNotCreated:
    """A typo on `--db-path` must fail loudly, as it did before the resolver.

    THE REGRESSION THESE ARMS CLOSE. The directory side effect used to aim at
    the DEFAULT memory directory. When the resolver began returning a caller
    path, the side effect began building the tree for THAT path, so a mistyped
    `--db-path` stopped raising and started to succeed against an empty store.
    A write lands in a file the caller does not know about, and the command
    reports success.

    LOUD TO SILENT IS THE WRONG DIRECTION ON A CALLER-CONTROLLED VALUE. The
    derived route keeps its create, because production passes no path and must
    reach and build the real store.

    ⚠️ WHICH LAYER EACH ARM NOW MEASURES, BECAUSE ONE OF THEM MOVED. The CLI
    boundary in `cli.main` refuses an absent caller `--db-path` BEFORE the
    resolver runs, so the first arm below measures THE BOUNDARY REFUSAL and no
    longer reaches the directory-half guard it was written for. It stays here
    as a CLI-level statement of the same rule. The directory-half guard in
    `database.get_db_path` is pinned at the layer that still reaches it, in
    `tests/test_caller_path_is_not_created.py`, by the class named for the
    directory half below the boundary, through a library caller. Do NOT read a
    green result here as cover for that guard.
    """


    def test_an_absent_caller_parent_is_not_created(self, cli_script_path, tmp_path):
        """The CLI refuses a caller path that is absent, and creates nothing.

        THIS ARM MEASURES THE BOUNDARY REFUSAL, NOT THE RESOLVER. It reads the
        error NAME for that reason: a status alone cannot say WHICH layer
        answered, and this arm went green for a different layer once already.
        """
        absent = tmp_path / "typo-dir"
        db = absent / "x.db"
        assert not absent.exists()

        result = subprocess.run(
            [sys.executable, cli_script_path, "get", "abc1234", "--db-path", str(db)],
            capture_output=True, text=True, timeout=120,
        )

        assert not absent.exists(), (
            "a mistyped --db-path built its own directory tree, so the typo "
            "succeeded against an empty store instead of failing"
        )
        assert not db.exists()
        assert result.returncode != 0
        assert json.loads(result.stderr)["error"] == "DB_PATH_NOT_FOUND", (
            f"a non-zero status came from somewhere other than the boundary "
            f"refusal, so this arm no longer measures what it names. "
            f"stderr={result.stderr[:300]}"
        )


# ---------------------------------------------------------------------------
# A failed diagnostic write must not change the outcome
# ---------------------------------------------------------------------------


class TestAFailedReportDoesNotChangeTheOutcome:
    """`cmd 2>&1 | head` is an ordinary command line, not a constructed one.

    Once `head` stops reading, stderr is a pipe with no reader. The guard's
    replay then raised, the error envelope could not reach the same broken
    stream, and the process died with a status describing the REPORTING
    failure rather than the OPERATION. A successful command printed a success
    envelope on stdout and exited non-zero.
    """

    def test_a_broken_stderr_does_not_replace_a_success_exit(self):
        """RED BEFORE THE FIX: the replay raised and took the exit code with it.

        DETERMINISTIC BY CONSTRUCTION, and that is deliberate. The reviewer met
        this through a live dependency writing a progress bar into the capture.
        A noise source that depends on a backend being installed makes the arm
        pass for the wrong reason wherever that backend is absent, so this arm
        writes the captured bytes itself and points file descriptor 2 at a
        reader-less pipe by hand. It reproduces the mechanism rather than the
        occasion.
        """
        from scripts.cli import _own_stderr_for_envelope

        read_end, write_end = os.pipe()
        saved = os.dup(2)
        try:
            os.dup2(write_end, 2)
            # No reader from here on, so any write to descriptor 2 raises.
            os.close(read_end)

            with pytest.raises(SystemExit) as exc:
                with _own_stderr_for_envelope():
                    # Lands in the CAPTURE, because the guard holds descriptor
                    # 2 while the block runs. The replay meets the broken pipe
                    # after the guard restores it.
                    os.write(2, b"captured diagnostic line\n")
                    sys.exit(0)

            assert exc.value.code == 0, (
                "a failure in the reporting path replaced the outcome of an "
                "operation that succeeded"
            )
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(write_end)


# ---------------------------------------------------------------------------
# The exit STATUS a shell sees, measured in a child process
# ---------------------------------------------------------------------------


def _spawn_with_readerless_pipes(argv, env, break_stdout, break_stderr):
    """Run cli.py as a CHILD and return (returncode, stdout_text, stderr_text).

    THE CONDITION IS A MISSING READER, NOT A NUMBER OF BYTES. The parent closes
    the read end of each pipe immediately after the spawn, so the reader is gone
    BEFORE the child writes anything. That is what a consumer which stops early
    leaves behind.

    NO SIZE THRESHOLD APPEARS HERE, DELIBERATELY. Whether a given write raises
    at the call or survives in a buffer until interpreter shutdown depends on
    the stdio buffer size and on the pipe capacity of the system. Those move
    with the platform and the interpreter, and a number written here would decay
    with no event to show it. Removing the reader makes the condition hold at
    every size.

    A BROKEN STREAM RETURNS A SENTINEL rather than text, because its bytes went
    into a pipe nobody reads. Do not assert on a sentinel: a probe that merges
    a stream into the one it discards cannot report on that stream.
    """
    read_ends, write_ends, files = [], [], {}

    def target(broken, name):
        if broken:
            r, w = os.pipe()
            read_ends.append(r)
            write_ends.append(w)
            return w
        files[name] = tempfile.TemporaryFile()
        return files[name].fileno()

    out_target = target(break_stdout, "out")
    err_target = target(break_stderr, "err")

    proc = subprocess.Popen(
        [sys.executable] + argv,
        stdout=out_target,
        stderr=err_target,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    # The reader is gone from here on, and the child has not written yet.
    for fd in read_ends + write_ends:
        os.close(fd)
    returncode = proc.wait(timeout=120)

    def read_back(name):
        handle = files.get(name)
        if handle is None:
            return "<discarded: this stream was the reader-less pipe>"
        handle.seek(0)
        text = handle.read().decode("utf-8", "replace")
        handle.close()
        return text

    return returncode, read_back("out"), read_back("err")


class TestTheExitStatusSurvivesAReaderLessPipe:
    """A shell must read the OUTCOME of the operation, not of the report.

    WHY A CHILD PROCESS AND NOT A MONKEYPATCHED STREAM. An in-process arm can
    observe the ``SystemExit`` code, and only a child observes the exit STATUS.
    The two differ where interpreter finalization fails, which is where this
    defect lives, so an in-process arm cannot reach it by construction.

    ⚠️ 120 IS A SHARED STATUS. CPython uses it for any finalization failure, so
    a number alone cannot name a cause. Each arm below asserts the shutdown TEXT
    beside the number, and carries a control with no broken stream.
    """

    def test_a_broken_stdout_leaves_a_successful_run_at_zero(self, cli_script_path, tmp_path):
        """RED BEFORE THE FIX: exit 120, with the shutdown text on stderr.

        A success envelope is a REPORT. Once it is written the operation is
        finished, so a reader that has gone away costs the caller the text and
        must not change the outcome.
        """
        store = tmp_path / "derived" / "pact-memory"
        store.mkdir(parents=True)          # a redirect target that is present
        env = dict(os.environ)
        env["PACT_TEST_MEMORY_DIR"] = str(store)
        env.pop("PYTEST_CURRENT_TEST", None)

        rc, _out, err = _spawn_with_readerless_pipes(
            [cli_script_path, "list"], env, break_stdout=True, break_stderr=False
        )

        assert "Exception ignored" not in err, (
            "the interpreter reported a shutdown failure on the terminal of "
            f"the caller; stderr was: {err!r}"
        )
        assert rc == 0, (
            "a command that did what it was asked reported failure, because "
            f"the reader of its own success envelope had gone away (exit {rc})"
        )


# ---------------------------------------------------------------------------
# Sync Command
# ---------------------------------------------------------------------------

_WORKING_MEMORY_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n\n"
    "## Pinned Context\n\nkeep me\n"
)


def _backdate(db_path, memory_id, stamp):
    """Set a record's `created_at` directly, because a save cannot.

    The writer ignores a caller-supplied `created_at` and stamps its own,
    microsecond ISO, so saves land in an order these arms cannot choose and
    never tie. Writing the stamp here fixes the ordering in the store, which
    is the record of truth the projection reads. Note that the stamps written
    here are the space form, which is what the schema DEFAULT emits and not
    what the writer emits -- an arm that needs the production form must say
    so explicitly."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (stamp, memory_id))
    conn.commit()
    conn.close()


def _working_memory_headers(text):
    section = text.split("## Working Memory\n", 1)[1].split("\n## ", 1)[0]
    return [line[4:] for line in section.splitlines() if line.startswith("### ")]


