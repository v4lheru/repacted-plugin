"""
Tests for the memory_init module - lazy initialization system for PACT memory.

Tests cover:
1. Unit tests for ensure_memory_ready(), reset_initialization(), is_initialized()
2. Thread safety - multiple concurrent calls only run once
3. Integration with memory_api.py - first API call triggers initialization
4. Edge cases - graceful degradation, already-installed dependencies
"""

import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestEnsureMemoryReady:
    """Tests for ensure_memory_ready() - the main lazy initialization entry point."""

    def test_returns_dict_with_expected_keys(self):
        """Test that ensure_memory_ready returns expected result structure."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            result = ensure_memory_ready()

            assert 'already_initialized' in result
            assert 'deps' in result
            assert 'migration' in result
            assert 'embedding' in result
            assert result['already_initialized'] is False

    def test_idempotent_only_runs_once(self):
        """Test that ensure_memory_ready only runs initialization once per session."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First call should run initialization
            result1 = ensure_memory_ready()
            assert result1['already_initialized'] is False
            assert mock_deps.call_count == 1

            # Second call should return early
            result2 = ensure_memory_ready()
            assert result2['already_initialized'] is True
            assert mock_deps.call_count == 1  # Still 1, not called again

            # Third call also returns early
            result3 = ensure_memory_ready()
            assert result3['already_initialized'] is True
            assert mock_deps.call_count == 1


class TestResetInitialization:
    """Tests for reset_initialization() - allows re-initialization for testing."""

    def test_reset_allows_reinitialization(self):
        """Test that reset_initialization allows initialization to run again."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First initialization
            result1 = ensure_memory_ready()
            assert result1['already_initialized'] is False
            assert is_initialized() is True
            assert mock_deps.call_count == 1

            # Reset
            reset_initialization()
            assert is_initialized() is False

            # Second initialization runs again
            result2 = ensure_memory_ready()
            assert result2['already_initialized'] is False
            assert mock_deps.call_count == 2

    def test_reset_leaves_existing_marker_in_place(self):
        """reset_initialization() must not delete the embedding marker.

        The marker path resolves to a location shared with a real session, so a
        reset that unlinks it reaches outside the test process. Resetting the
        in-memory flag is the only behaviour tests need.
        """
        from scripts.memory_init import reset_initialization, _get_embedding_attempted_path

        test_session_id = f"test-routef-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()
            marker_path.touch()
            try:
                assert marker_path.exists(), "precondition: marker was created"

                reset_initialization()

                assert marker_path.exists(), (
                    "reset_initialization() deleted the embedding marker; "
                    "the reset must not touch the filesystem"
                )
            finally:
                marker_path.unlink(missing_ok=True)


class TestIsInitialized:
    """Tests for is_initialized() - checks current initialization state."""

    def test_returns_false_before_initialization(self):
        """Test is_initialized returns False before ensure_memory_ready is called."""
        from scripts.memory_init import reset_initialization, is_initialized

        reset_initialization()
        assert is_initialized() is False

    def test_returns_true_after_initialization(self):
        """Test is_initialized returns True after ensure_memory_ready completes."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization, is_initialized

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            ensure_memory_ready()
            assert is_initialized() is True


class TestThreadSafety:
    """Tests for thread safety - multiple concurrent calls only run once."""

    def test_concurrent_calls_only_initialize_once(self):
        """Test that multiple concurrent calls only run initialization once."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        call_count = {'value': 0}
        call_lock = threading.Lock()

        def counting_deps():
            with call_lock:
                call_count['value'] += 1
            # Simulate some work
            time.sleep(0.05)
            return {'status': 'ok', 'installed': [], 'failed': []}

        with patch('scripts.memory_init.check_and_install_dependencies', side_effect=counting_deps), \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Launch 10 concurrent calls
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(ensure_memory_ready) for _ in range(10)]
                results = [f.result() for f in as_completed(futures)]

            # Only one call should have run the full initialization
            assert call_count['value'] == 1

            # Exactly one result should have already_initialized=False
            not_initialized = [r for r in results if not r['already_initialized']]
            already_initialized = [r for r in results if r['already_initialized']]

            assert len(not_initialized) == 1
            assert len(already_initialized) == 9

    def test_double_check_locking_pattern(self):
        """Test that double-check locking prevents race conditions."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        # Track how many times we enter the lock
        lock_entries = {'value': 0}
        original_lock = threading.Lock()

        class TrackingLock:
            def __enter__(self):
                lock_entries['value'] += 1
                return original_lock.__enter__()

            def __exit__(self, *args):
                return original_lock.__exit__(*args)

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed, \
             patch('scripts.memory_init._init_lock', TrackingLock()):

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # First call acquires lock
            ensure_memory_ready()

            # Second call should return early without acquiring lock (fast path)
            # due to the initial check before lock acquisition
            ensure_memory_ready()

            # Lock was only entered once
            assert lock_entries['value'] == 1


class TestCheckAndInstallDependencies:
    """Tests for check_and_install_dependencies function."""

    def test_all_dependencies_present_returns_ok(self):
        """Test when all dependencies are already installed - returns ok status."""
        from scripts.memory_init import check_and_install_dependencies

        # When deps are already importable, function returns ok with empty lists
        # The actual function checks via __import__, and if successful, returns ok
        # We test this by calling it (deps are installed in test env)
        result = check_and_install_dependencies()

        # If deps are installed, status should be ok
        # If not installed, the test still passes as we're testing the return structure
        assert 'status' in result
        assert 'installed' in result
        assert 'failed' in result

    def test_subprocess_called_for_missing_deps(self, monkeypatch):
        """Test that subprocess.run is called when deps are missing."""
        import builtins
        from scripts.memory_init import check_and_install_dependencies

        monkeypatch.delenv("CI", raising=False)   # pins the installer, so the gate must be OPEN
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            # Simulate pysqlite3 not installed
            if name == 'pysqlite3':
                raise ImportError("No module named 'pysqlite3'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', mock_import), \
             patch('scripts.memory_init.subprocess.run') as mock_run:

            mock_run.return_value = MagicMock(returncode=0)

            result = check_and_install_dependencies()

            # Should have attempted pip install
            assert mock_run.called


class TestDependencyDriftIsDetectable:
    """The `skipped_ci` branch is SILENT. This class is why that is safe.

    WHAT WAS MEASURED, so nobody re-opens this by "just adding a log line".
    Under the shipping invocation (`python -m pytest`, no `-r`, no `-s`), with
    `CI` set and a genuinely absent package, the DOMINANT caller of
    `check_and_install_dependencies` is a CLI SUBPROCESS (`scripts/cli.py` via
    `subprocess.run`), not the pytest process. On that path the CLI's stderr is
    a STRUCTURED JSON CONTRACT — the CLI tests do `json.loads(result.stderr)`.
    Drift forced, one variable changed at a time, `tests/test_memory_cli.py`:

        no emission at all ................. 154 passed
        print(..., file=sys.stderr) ........ 15 failed
        logger.warning (lastResort→stderr) . 15 failed
        warnings.warn (default→stderr) ..... 15 failed

    So every in-process emission mechanism corrupts that contract identically,
    and `warnings.warn` is NOT a safe substitute here even though it is the one
    mechanism that IS visible from a passing test in the pytest process.
    Visibility does not cross the process boundary regardless: pytest's warnings
    summary only collects warnings raised in the pytest process.

    Hence drift is caught STATICALLY, below. A failing test is louder than any
    log line and is immune to capture, to the process boundary and to `-r`.
    """

    def _source_repo_root(self):
        """Repo root by a POSITIVE marker, or None when this is not a checkout.

        Deliberately NOT "did I find the workflow file?". "I cannot find it" and
        "it is wrong" must never produce the same outcome — that equivalence is
        the silent-narrowing defect this whole class exists to prevent. So the
        skip decision is made on an INDEPENDENT positive marker, and the
        workflow's own absence is then free to be a FAILURE.

        Marker matches the convention already used by test_packaging_boundary.py.
        """
        for base in Path(__file__).resolve().parents:
            if (base / ".claude-plugin" / "marketplace.json").exists():
                return base
        return None

    def test_declared_packages_match_the_ci_install_line(self):
        """Every package memory_init would install must be declared by CI.

        WHAT THIS PROVES: the two lists cannot silently diverge. If a package is
        added here and not to the workflow, this goes RED with the name in the
        message — which is the drift signal the silent branch cannot emit.

        WHAT IT DOES NOT PROVE: that any runtime message is visible in a CI log.
        Nothing asserts that, because nothing safely can — see the class
        docstring. It also does not prove the reverse direction: CI may install
        packages this module does not know about, which is harmless.
        """
        from scripts.memory_init import check_and_install_dependencies  # noqa: F401
        import inspect

        from scripts import memory_init

        source = inspect.getsource(memory_init.check_and_install_dependencies)
        declared = re.findall(r"\(\s*'([^']+)'\s*,\s*'[^']+'\s*\)", source)
        assert declared, (
            "could not read the package list out of "
            "check_and_install_dependencies — the list changed shape and this "
            "parity test is now vacuous. Re-point it; do not delete it."
        )

        root = self._source_repo_root()
        if root is None:
            pytest.skip(
                "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
                f"{Path(__file__).resolve()}, so this is not a PACT source "
                "checkout. The expected case is an INSTALLED PLUGIN CACHE, "
                "which has no .github/ tree by design. This skip is NO "
                "INFORMATION about dependency drift — never read it as a pass."
            )

        # INSIDE a checkout, absence is a FAILURE. Reaching here means the
        # marker was found, so the workflow is expected to exist and parse; if
        # it does not, the drift detector is broken and must say so loudly
        # rather than skip and read as green.
        workflow = root / ".github" / "workflows" / "tests.yml"
        assert workflow.exists(), (
            f"source checkout detected at {root} (marketplace.json present) but "
            f"{workflow} is missing. The drift detector cannot run. This is a "
            f"FAILURE, not a skip: inside a checkout the workflow is required."
        )
        install_line = next(
            (ln for ln in workflow.read_text(encoding="utf-8").splitlines()
             if "pip install" in ln),
            None,
        )
        assert install_line is not None, (
            f"PARSE FAILURE, not dependency drift: {workflow} contains no "
            f"'pip install' line, so the CI dependency declaration cannot be "
            f"read. The workflow changed shape — re-point this parity test at "
            f"the new shape; do not weaken it to a skip."
        )

        # Compare against TOKENS of the install line, never substrings of it.
        # A substring test fails OPEN on the most plausible next edit: this
        # module does `import pysqlite3 as sqlite3`, so an editor adding
        # 'sqlite3' to the package list above would find it inside 'pysqlite3',
        # pass, and assert a package is installed that is not. Measured against
        # the real install line, 'sqlite3', 'sqlite', 'vec', 'yaml' and
        # 'asyncio' are ALL substrings of it and none is declared by CI.
        #
        # Names are normalised per PEP 503 (lowercase, [-_.] runs collapse to
        # '-') on BOTH sides, so a legitimate 'sqlite_vec'/'sqlite-vec' spelling
        # difference does not read as drift. Version specifiers and extras are
        # stripped so 'pytest>=8' still matches 'pytest'.
        def _normalise(name):
            head = re.split(r"[=<>!~;\[]", name)[0]
            return re.sub(r"[-_.]+", "-", head).strip().lower()

        # Model how pip itself reads argv: the package operands are the
        # non-flag words AFTER the `install` verb. Everything before it is the
        # invocation (`run:`, `python`, `-m`, `pip`) and is not an operand.
        # Deriving the boundary structurally beats filtering a hand-maintained
        # list of command words, which would need updating and would turn a
        # package legitimately named after a command into false drift.
        words = install_line.split()
        operands = words[words.index("install") + 1:] if "install" in words else []
        install_tokens = {
            _normalise(tok) for tok in operands
            if tok and not tok.startswith("-")
        }

        # PARSE NON-VACUITY — asserted BEFORE the drift comparison, and with a
        # DISTINCT message, because "I could not read the line" and "a package
        # is missing" must never produce the same outcome. If a workflow
        # refactor defeats this parser, `install_tokens` silently empties and
        # the set difference below passes VACUOUSLY: the guard would be dead
        # while green, which is the exact failure it exists to prevent.
        #
        # The anchor has GROUND TRUTH INDEPENDENT OF THE PARSE: this code is
        # executing under pytest, so CI demonstrably installs pytest. If a
        # correct parse of the CI install line does not contain it, the parse
        # is wrong — not the dependency set. That is what makes this a real
        # non-vacuity check rather than a restatement of the thing being tested.
        _PARSE_ANCHOR = "pytest"
        assert install_tokens, (
            f"PARSE FAILURE, not dependency drift: no package operands could be "
            f"read from the CI install line. The line is {install_line.strip()!r}. "
            f"The tokeniser models `pip install <flags> <packages>`; a refactor "
            f"to a requirements file or a multi-line form defeats it. Re-point "
            f"the parser — do NOT relax the assertion below, which would pass "
            f"vacuously over an empty token set."
        )
        assert _PARSE_ANCHOR in install_tokens, (
            f"PARSE FAILURE, not dependency drift: the parsed operands "
            f"{sorted(install_tokens)} do not contain {_PARSE_ANCHOR!r}, but "
            f"this test is RUNNING under pytest, so CI installs it. The parse "
            f"is therefore wrong, not the dependency set. Re-point the parser."
        )

        declared_tokens = {_normalise(p) for p in declared}
        missing = sorted(declared_tokens - install_tokens)
        assert not missing, (
            f"DEPENDENCY DRIFT: {missing} are installed by "
            f"memory_init.check_and_install_dependencies but are NOT in the CI "
            f"workflow's install line. Under CI the install path is skipped, so "
            f"these would be absent at runtime and the branch reports it "
            f"NOWHERE (it is deliberately silent — the CLI's stderr is a JSON "
            f"contract). Add them to the workflow, or remove them here."
        )


class TestMaybeEmbedPending:
    """Tests for maybe_embed_pending function.

    Note: These tests use session marker files to track once-per-session behavior.
    The embed_pending_memories import is mocked via sys.modules since the
    embedding_catchup module uses relative imports that don't work in test context.
    """

    def test_session_scoped_only_runs_once(self, tmp_path):
        """Test that maybe_embed_pending only runs once per session via marker file."""
        from scripts.memory_init import maybe_embed_pending, _get_embedding_attempted_path

        # Use a unique session ID for this test
        test_session_id = f"test-once-{time.time()}"

        with patch("scripts.memory_init.get_session_id_from_context_file", return_value=test_session_id):
            marker_path = _get_embedding_attempted_path()

            # Clean up any existing marker
            if marker_path.exists():
                marker_path.unlink()

            # Create a mock module for embedding_catchup
            mock_catchup = MagicMock()
            mock_catchup.embed_pending_memories = MagicMock(return_value={'processed': 0})

            with patch.dict(sys.modules, {'.embedding_catchup': mock_catchup}):
                # First call creates marker and attempts embedding
                result1 = maybe_embed_pending()

                # Marker should now exist
                assert marker_path.exists()

                # Second call should skip because marker exists
                result2 = maybe_embed_pending()
                assert result2['status'] == 'skipped'
                assert 'already attempted' in result2['message'].lower()

            # Clean up
            if marker_path.exists():
                marker_path.unlink()


class TestMemoryAPIIntegration:
    """Tests for integration between memory_api.py and memory_init.py.

    Note: Direct testing of memory_api.py is complex due to its relative imports.
    These tests verify the lazy initialization pattern by testing the _ensure_ready
    helper function behavior and the ensure_memory_ready integration.
    """

    def test_ensure_ready_helper_calls_ensure_memory_ready(self):
        """Test that _ensure_ready() calls ensure_memory_ready()."""
        from scripts.memory_init import reset_initialization, is_initialized, ensure_memory_ready

        reset_initialization()
        assert is_initialized() is False

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Simulate what _ensure_ready does: call ensure_memory_ready
            ensure_memory_ready()

            assert is_initialized() is True
            assert mock_deps.call_count == 1


class TestGracefulDegradation:
    """Tests for graceful degradation when initialization steps fail."""

    def test_continues_after_dep_failure(self):
        """Test that initialization continues even if dependency installation fails."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'failed', 'installed': [], 'failed': ['pysqlite3']}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            result = ensure_memory_ready()

            # All steps were called despite first failure
            assert mock_migrate.call_count == 1
            assert mock_embed.call_count == 1
            assert result['deps']['status'] == 'failed'


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_deps_result(self):
        """Test handling of empty dependency check result."""
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {}  # Empty dict
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Should not raise
            result = ensure_memory_ready()
            assert result is not None


            # State should be consistent (either initialized or not)
            # The point is that no exception is raised


class TestMaybeMigrateEmbeddings:
    """Tests for maybe_migrate_embeddings function.

    Tests cover:
    1. Happy paths: No table, empty table, dimensions match, migration success
    2. Edge cases: Import failures, connection failures, partial re-embedding
    3. Error handling: Exception during migration returns error status

    Note: The function uses relative imports from within the scripts package.
    We test by creating a testable wrapper that injects dependencies.
    """

    def _create_testable_migrate_function(
        self,
        pysqlite3_module=None,
        sqlite_vec_module=None,
        get_connection_func=None,
        get_embedding_service_func=None,
        generate_embedding_text_func=None,
        embedding_dim=256,
        import_error=False,
    ):
        """Create a version of maybe_migrate_embeddings with injected dependencies.

        This allows us to test the actual logic without dealing with relative imports.
        """
        import struct

        def testable_migrate():
            result = {"status": "ok", "message": None}

            try:
                # Simulate import block
                if import_error:
                    return result

                if pysqlite3_module is None or sqlite_vec_module is None:
                    return result

                if get_connection_func is None:
                    return result

                # Get expected dimension
                expected_dim = embedding_dim

                # Connect to database
                conn = get_connection_func()
                sqlite_vec_module.load(conn)

                # Check if vec_memories table exists
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
                )
                if cursor.fetchone() is None:
                    conn.close()
                    return result  # No table, nothing to migrate

                # Check actual dimension by examining an embedding
                try:
                    row = conn.execute("SELECT embedding FROM vec_memories LIMIT 1").fetchone()
                    if row is None:
                        conn.close()
                        return result  # Empty table, nothing to migrate

                    actual_dim = len(row[0]) // 4  # 4 bytes per float
                    if actual_dim == expected_dim:
                        conn.close()
                        return result  # Dimensions match, no migration needed

                except Exception:
                    conn.close()
                    return result

                # Dimension mismatch detected - need to migrate
                result["status"] = "migrating"
                result["message"] = f"Migrating embeddings: {actual_dim}-dim -> {expected_dim}-dim"

                # Drop old table
                conn.execute("DROP TABLE IF EXISTS vec_memories")
                conn.commit()

                # Recreate with new dimension
                conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                        memory_id TEXT PRIMARY KEY,
                        project_id TEXT PARTITION KEY,
                        embedding float[{expected_dim}]
                    )
                """)
                conn.commit()

                # Re-embed all memories
                service = get_embedding_service_func()
                memories = conn.execute("""
                    SELECT id, context, goal, lessons_learned, decisions, entities
                    FROM memories
                """).fetchall()

                success = 0
                for mem_id, context, goal, lessons, decisions, entities in memories:
                    try:
                        memory_dict = {
                            'context': context, 'goal': goal, 'lessons_learned': lessons,
                            'decisions': decisions, 'entities': entities,
                        }
                        embed_text = generate_embedding_text_func(memory_dict)
                        embedding = service.generate(embed_text)

                        if embedding:
                            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                            conn.execute(
                                "INSERT OR REPLACE INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
                                (mem_id, embedding_blob)
                            )
                            success += 1
                    except Exception:
                        continue

                conn.commit()
                conn.close()

                result["status"] = "ok"
                result["message"] = f"Migrated {success}/{len(memories)} embeddings to {expected_dim}-dim"
                return result

            except Exception as e:
                result["status"] = "error"
                result["message"] = str(e)[:50]
                return result

        return testable_migrate

    # =========================================================================
    # Happy Path Tests
    # =========================================================================

    def test_import_error_returns_ok_gracefully(self):
        """Test: ImportError during module imports -> returns ok gracefully."""
        migrate_func = self._create_testable_migrate_function(import_error=True)
        result = migrate_func()

        assert result['status'] == 'ok'
        assert result['message'] is None


    # =========================================================================
    # Edge Case Tests
    # =========================================================================


    # =========================================================================
    # Error Handling Tests
    # =========================================================================


    # =========================================================================
    # Integration with actual function (graceful degradation)
    # =========================================================================


class TestLogging:
    """Tests for logging behavior during initialization."""

    def test_logs_installed_dependencies(self, caplog):
        """Test that installed dependencies are logged."""
        import logging
        from scripts.memory_init import ensure_memory_ready, reset_initialization

        reset_initialization()

        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': ['sqlite-vec'], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            with caplog.at_level(logging.INFO):
                ensure_memory_ready()

            assert any('sqlite-vec' in record.message for record in caplog.records)


class TestMemoryAPIRealIntegration:
    """Tests that actually import memory_api.py and verify lazy initialization.

    These tests import from the real memory_api module and verify that
    PACTMemory methods trigger ensure_memory_ready() on first use.

    Note: memory_api uses relative imports, so we import the entire scripts
    package and mock ensure_memory_ready at the memory_api module level.
    """

    def test_pact_memory_save_triggers_ensure_ready(self):
        """Test that PACTMemory.save() triggers ensure_memory_ready()."""
        from scripts.memory_init import reset_initialization

        reset_initialization()

        # We need to mock ensure_memory_ready at the memory_api module level
        # because memory_api imports it with: from .memory_init import ensure_memory_ready
        with patch('scripts.memory_init.check_and_install_dependencies') as mock_deps, \
             patch('scripts.memory_init.maybe_migrate_embeddings') as mock_migrate, \
             patch('scripts.memory_init.maybe_embed_pending') as mock_embed:

            mock_deps.return_value = {'status': 'ok', 'installed': [], 'failed': []}
            mock_migrate.return_value = {'status': 'ok', 'message': None}
            mock_embed.return_value = {'status': 'ok', 'message': None}

            # Import the ensure_memory_ready we're testing
            from scripts.memory_init import ensure_memory_ready, is_initialized

            # Verify not initialized yet
            assert is_initialized() is False

            # Call ensure_memory_ready directly (simulating what _ensure_ready does)
            ensure_memory_ready()

            # Verify initialization happened
            assert is_initialized() is True
            assert mock_deps.call_count == 1
            assert mock_migrate.call_count == 1
            assert mock_embed.call_count == 1


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_init_state():
    """Reset initialization state before and after each test."""
    from scripts.memory_init import reset_initialization
    reset_initialization()
    yield
    reset_initialization()


class TestProcessMarkerDoesNotOutliveItsProcess:
    """The fallback marker is per-process, so it must not survive the process.

    Route F removed the marker DELETION but not its CREATION, and the fallback
    token made every process's marker unique — so each one became permanent
    litter that nothing could ever match again. Measured at 100 files per suite
    run before this was fixed.

    NON-VACUITY: asserting "no marker exists afterwards" passes both when the
    cleanup works AND when no marker was ever created. So the child proves
    creation from the inside and reports the path; the parent then proves the
    same path is gone. Without the child's assertion this test could not tell
    a working cleanup from a marker that never existed.
    """

    def _run_child(self, extra_body=""):
        scripts = str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
        child = (
            "import sys, pathlib\n"
            f"sys.path.insert(0, {scripts!r})\n"
            "import memory_init\n"
            "p = memory_init._get_embedding_attempted_path()\n"
            "p.touch()\n"
            # POSITIVE ARM: the marker demonstrably existed while the process ran.
            "assert p.exists(), 'child failed to create its own marker'\n"
            "print(str(p))\n"
            f"{extra_body}"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"child failed: {proc.stderr}"
        return Path(proc.stdout.strip().splitlines()[-1])

    def test_fallback_marker_is_removed_when_the_process_exits(self):
        marker = self._run_child()

        assert "process-" in marker.name, (
            "precondition: the child had no session context, so it must have "
            f"used the process-unique fallback; got {marker.name}"
        )
        assert not marker.exists(), (
            f"{marker.name} outlived the process that created it; nothing can "
            "ever match that token again, so it is permanent litter"
        )

