"""Edge-path coverage for the working_memory CLAUDE.md-sync lock:
lock-release-on-exception, and the CLAUDE_PROJECT_DIR-divergence residual.

Location: pact-plugin/tests/test_working_memory_resolver_and_lock_release.py

Summary: Two edge paths that the primary concurrency suites
(test_working_memory_concurrency.py + ..._comprehensive.py) do not cover,
grouped in this sibling file to keep the comprehensive file focused (and under
the file-size advisory threshold):

  1. Lock-release-on-exception — an exception raised mid-window (inside the
     `with file_lock(...)` block at a sync site) must still RELEASE the lock via
     the contextmanager's ``finally``, so the next writer can acquire it. A lock
     that leaked on the error path would wedge every subsequent sync until the
     5s timeout, repeatedly. We also confirm fail-open (return False) and that
     no partial/torn write persists.

  2. CLAUDE_PROJECT_DIR-divergence residual — the lock only serializes when all
     writers resolve the SAME .claude/CLAUDE.md (hence the same sidecar inode).
     When CLAUDE_PROJECT_DIR is unset AND the git-root/cwd fallbacks resolve
     DIFFERENT roots between two processes, the resolvers return different paths
     → different sidecars → the lock does NOT serialize. This is a DEMONSTRATION
     / known-limitation test: it documents the unprotected residual; it does
     NOT claim the divergence path is safe. It is out-of-contract in practice
     because the plugin sets CLAUDE_PROJECT_DIR every session, so every writer
     hits the env-var branch first and converges — see the caveat in each
     divergence test's docstring.

Used by: pytest (the working_memory edge-path gate).
"""

import os
from pathlib import Path

def _seed_claude_md(home: Path) -> Path:
    """Create a minimal project CLAUDE.md with an empty Working Memory section
    under ``home``/.claude/."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n",
        encoding="utf-8",
    )
    return claude_md


# ---------------------------------------------------------------------------
# 1. Lock-release-on-exception / chmod-mid-window.
# ---------------------------------------------------------------------------

class TestLockReleaseOnException:
    """An exception raised mid-window must release the lock (the contextmanager
    ``finally: LOCK_UN + os.close``), fail open, and leave no torn write."""

    def test_chmod_failure_mid_window_releases_lock_and_fails_open(
        self, tmp_path, monkeypatch
    ):
        """If setting the temp file's mode raises INSIDE the `with file_lock`
        block, sync_to_claude_md must (a) report `failed` (fail-open via the
        outer try/except) and (b) leave the lock RE-ACQUIRABLE — proving the
        lock was released on the exception path, not leaked.

        Mid-window failure SITE: _atomic_write_text sets the mode on the TEMP
        file before publishing it, so a boom there is a reachable mid-window
        exception — it fires inside _atomic_write_text, propagates out through
        the file_lock contextmanager's `finally`, and is caught by the outer
        try/except. The assertions below (fail-open + lock re-acquirable) are
        what this test pins; the SITE has moved twice now (target-after-write ->
        os.chmod on the temp -> os.fchmod on the open handle) while the
        exception path did not. Inject at whichever primitive currently sets the
        mode; do not treat the primitive as the thing under test.

        A leaked lock would force the next acquirer to block until the 5s
        timeout (then fail open), degrading every subsequent sync. We prove
        release by re-acquiring with a SHRUNK timeout: success means the lock
        is free; a TimeoutError would mean it leaked.
        """
        from scripts import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Raise at the os.fchmod inside _atomic_write_text (mode-set on the open
        # temp handle, before the rename), exercising the mid-window path.
        def boom_fchmod(*args, **kwargs):
            raise OSError("simulated mode-set failure mid-window")

        monkeypatch.setattr(wm.os, "fchmod", boom_fchmod)

        result = wm.sync_to_claude_md(
            {"context": "MID-WINDOW-BOOM", "goal": "g"}, None, "id"
        )
        # (a) fail-open. `failed` is the load-bearing part: it says the write
        # was entered and the exception was swallowed. A merely falsy result
        # would also hold if the target had never resolved, in which case the
        # lock was never taken and assertion (b) below would prove nothing.
        assert result.reason == wm.SyncResult.FAILED

        # (b) lock released → re-acquirable fast (NOT the 5s timeout). Shrink
        # the timeout so a leak would surface as a quick TimeoutError, not a
        # 5s hang.
        target = wm._get_claude_md_path()
        assert target is not None
        monkeypatch.setattr(wm, "_LOCK_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(wm, "_LOCK_POLL_INTERVAL", 0.05)
        with wm.file_lock(target):
            pass  # acquisition must succeed — if the lock had leaked this raises


# ---------------------------------------------------------------------------
# 1b. Read-only target directory — atomic-write dir-permission fail-safe.
# ---------------------------------------------------------------------------

class TestReadOnlyDirectoryFailSafe:
    """The atomic write requires write permission on the target's DIRECTORY —
    _atomic_write_text creates its temp there, relative to a descriptor it holds
    on that directory — where the old bare write_text needed only permission on
    the file. On a read-only directory holding a writable CLAUDE.md the sync
    REFUSES the write (fails safe) instead of truncating. Narrow config (bind
    mounts, restrictive umask) but real."""

    def test_readonly_dir_refuses_atomic_write_at_write_site_and_leaves_file_intact(
        self, tmp_path, monkeypatch
    ):
        """A 0o500 directory holding a writable CLAUDE.md makes the atomic write
        fail; sync returns False and the file is byte-unchanged.

        The FAILURE SITE is asserted, not just the outcome — because two distinct
        steps create a file in the target directory and they fail at DIFFERENT
        sites: file_lock creates its `.CLAUDE.md.lock` sidecar (os.open O_CREAT),
        and _atomic_write_text creates its temp (mkstemp). On a read-only dir with
        NO sidecar the LOCK fails first and the atomic write is never reached — a
        test that stopped at "returns False" would certify the lock's permission
        check, not the atomic-write behaviour change it names. So this test
        PRE-CREATES the sidecar, making the lock acquirable (opening an existing
        file RDWR needs file-write + dir-search, not dir-create), so the failure
        lands at mkstemp inside _atomic_write_text. Empirically verified: without
        the pre-created sidecar the PermissionError comes from file_lock; with it,
        from mkstemp.
        """
        from scripts import working_memory as wm

        claude_md = _seed_claude_md(tmp_path)
        before = claude_md.read_text(encoding="utf-8")
        claude_dir = claude_md.parent

        # Pre-create the lock sidecar so file_lock acquires without needing to
        # CREATE a file in the soon-to-be read-only directory.
        sidecar = claude_dir / f".{claude_md.name}.lock"
        sidecar.write_text("", encoding="utf-8")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Instrument the SITE: record that _atomic_write_text was reached (proving
        # the lock was passed) and let the REAL read-only dir make its mkstemp
        # raise. The failure is a genuine filesystem PermissionError, not a stub.
        reached = {"atomic_write": False}
        real_atomic = wm._atomic_write_text

        def spy_atomic(target, content, project_root):
            reached["atomic_write"] = True
            return real_atomic(target, content, project_root)

        monkeypatch.setattr(wm, "_atomic_write_text", spy_atomic)

        os.chmod(str(claude_dir), 0o500)
        try:
            result = wm.sync_to_claude_md(
                {"context": "READONLY-DIR", "goal": "g"}, None, "id"
            )
        finally:
            os.chmod(str(claude_dir), 0o700)  # restore so tmp cleanup can run

        # Fail-safe: the sync declined and the always-loaded file is untouched.
        assert result.reason == wm.SyncResult.FAILED, (
            "read-only dir must make the sync fail open as `failed`"
        )
        assert claude_md.read_text(encoding="utf-8") == before, (
            "target was mutated on a read-only dir — the write did not fail safe"
        )
        # SITE: execution reached _atomic_write_text (past the lock), so the
        # failure is the atomic-write dir-write requirement, not the lock's.
        assert reached["atomic_write"], (
            "sync failed before reaching _atomic_write_text — the sidecar "
            "precondition is not holding, so this exercises the lock's permission "
            "check, not the dir-write behaviour change it exists to pin"
        )


# ---------------------------------------------------------------------------
# 2. CLAUDE_PROJECT_DIR-divergence residual (DEMONSTRATION / known-limitation).
# ---------------------------------------------------------------------------

