"""
Tests for the session-record rung of the project-scope read contract, the
fail-closed env/record write refusal, and the home-scope warning.

Location: pact-plugin/tests/test_project_dir_resolution.py

Summary: Phase B of claude-project-dir-once. The read contract gains a
session-record rung (env -> session record -> git -> cwd -> home-with-warning)
shared by every consumer (memory_api._detect_project_id,
working_memory's two CLAUDE.md resolvers), and WRITES
(memory save, WM sync) refuse when CLAUDE_PROJECT_DIR and the
session record disagree.

Used by/with:
- skills/pact-memory/scripts/pact_session.py: the record reader + refusal
  channel under test.
- tests/fixtures/project_dir.py: umbrella factory, context writer, discovery
  enabler, subprocess env builder.
- tests/test_project_id.py: the replica/equivalence pins for the pre-existing
  strategies; this file owns the record-rung coverage the replica deliberately
  omits.

HOW THE RECORD ROUTE IS EXERCISED: in-process rows use
enable_record_discovery (the test_session_discovery_route pattern — the
refusal reads os.environ, so deleting PYTEST_CURRENT_TEST supplies the REAL
predicate a different input) plus a real context file written into the
redirected tmp config root. No getter is monkeypatched: every row exercises
the shipped discovery chain. The subprocess rows (R1/R2/R4/R5 + the CLI
envelope rows) cross the real process boundary with a constructed env
(child_env), because Path.home patching does not cross it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures.project_dir import (
    child_env,
    enable_record_discovery,
    git_flake_shim,
    make_umbrella,
    source_export_line,
    write_session_context,
)
# EVERY import here goes through the `scripts.` package route, deliberately.
# A bare `import working_memory` loads a SECOND module instance whose
# `from pact_session import ...` binds a second copy of pact_session — with
# its own discovery cache AND its own ProjectScopeDisagreementError class, so
# the cache reset would miss the live cache and pytest.raises would miss the
# raised class. The sibling files' bare-import convention is not safe for
# this file's cross-module refusal assertions.
from scripts import pact_session
from scripts import working_memory as wm
from scripts.memory_api import PACTMemory
from scripts.pact_session import ProjectScopeDisagreementError
from clock_shift.clock_shift_env import carry_clock_shift


_MEMORY_CLI = (
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts" / "cli.py"
)
_SESSION_INIT = Path(__file__).parent.parent / "hooks" / "session_init.py"
_PACT_MEMORY_ROOT = Path(__file__).parent.parent / "skills" / "pact-memory"
SID = "record-rung-session-0001"


def _seed_claude_md(root: Path) -> Path:
    """A minimal syncable project CLAUDE.md (Working Memory section present)."""
    claude_dir = root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n",
        encoding="utf-8",
    )
    return claude_md


def _arm_record(monkeypatch, tmp_path, record_dir) -> None:
    """Make the session record LIVE in-process: refusal off, env id set,
    context file written into the redirected tmp config root."""
    enable_record_discovery(monkeypatch, pact_session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    write_session_context(Path.home() / ".claude", SID, record_dir)


def _git_repo(path: Path) -> Path:
    """A fresh git repo (no commit needed for --git-common-dir)."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )
    return path


# ---------------------------------------------------------------------------
# The record reader's contract (pact_session.get_project_dir_from_session_record)
# ---------------------------------------------------------------------------

class TestSessionRecordReader:
    """The reader never raises and fails open to "" on every ambiguous shape."""

    def test_returns_the_recorded_absolute_project_dir(self, tmp_path, monkeypatch):
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        assert pact_session.get_project_dir_from_session_record() == str(umbrella.project)

    def test_missing_context_file_yields_empty(self, tmp_path, monkeypatch):
        enable_record_discovery(monkeypatch, pact_session)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
        assert pact_session.get_project_dir_from_session_record() == ""


# ---------------------------------------------------------------------------
# Precedence: _detect_project_id's Strategy 1.5 (record below env, above git)
# ---------------------------------------------------------------------------

class TestDetectProjectIdRecordRung:
    def test_env_wins_over_a_disagreeing_record_on_reads(self, tmp_path, monkeypatch):
        """READS FOLLOW ENV: a present CLAUDE_PROJECT_DIR declares the scope;
        the record never overrides it and nothing refuses on a read path."""
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        repo = _git_repo(tmp_path / "declared-repo")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        assert PACTMemory._detect_project_id() == "declared-repo"


# ---------------------------------------------------------------------------
# Home scope warns (the last resort must not be silent)
# ---------------------------------------------------------------------------

class TestHomeScopeWarning:
    def _force_cwd_resolution(self, monkeypatch, resolved_root: Path):
        """Kill the env/record/git legs so Strategy 3 answers `resolved_root`.
        The walk itself is not the thing under test — the warning branch is —
        so _find_project_root is pinned to the answer directly."""
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        def _no_git(*args, **kwargs):
            raise FileNotFoundError("git unavailable in this arm")

        monkeypatch.setattr("subprocess.run", _no_git)
        monkeypatch.setattr(
            PACTMemory, "_find_project_root",
            staticmethod(lambda start: resolved_root),
        )

    def test_home_resolution_warns(self, tmp_path, monkeypatch, caplog):
        home = Path.home().resolve()  # autouse-redirected to tmp_path
        self._force_cwd_resolution(monkeypatch, home)
        with caplog.at_level(logging.WARNING):
            result = PACTMemory._detect_project_id()
        assert result == home.name
        assert any(
            "HOME directory" in r.message and "CLAUDE_PROJECT_DIR" in r.message
            for r in caplog.records
        ), f"home scope resolved silently: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Fail-closed writes: env vs record disagreement
# ---------------------------------------------------------------------------

class TestWriteRefusalOnDisagreement:
    def test_memory_save_refuses_naming_both_values(self, tmp_path, monkeypatch):
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))

        memory = PACTMemory()  # constructed AFTER the env manipulation
        with pytest.raises(ProjectScopeDisagreementError) as excinfo:
            memory.save({"context": "c", "goal": "g"})
        text = str(excinfo.value)
        assert str(other) in text, "the refusal does not name the env value"
        assert str(umbrella.project) in text, "the refusal does not name the record"
        assert "Nothing was written" in text
        assert "re-export CLAUDE_PROJECT_DIR" in text, "the refusal names no remedy"


# ---------------------------------------------------------------------------
# Working-memory resolvers: the record rung preserves the existence coupling
# ---------------------------------------------------------------------------

class TestWorkingMemoryRecordRung:
    def test_record_dir_is_probed_for_an_existing_claude_md(self, tmp_path, monkeypatch):
        umbrella = make_umbrella(tmp_path)
        claude_md = _seed_claude_md(umbrella.project)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert wm._get_claude_md_path() == claude_md
        path, base = wm._resolve_display_claude_md_with_base()
        assert path == claude_md
        assert base == umbrella.project, (
            "the containment anchor must be the base the resolver USED — the "
            "recorded dir — not a re-derivation"
        )


class TestWorkingMemoryDisagreementGuard:
    def test_ambient_sync_refuses_on_disagreement(self, tmp_path, monkeypatch):
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        other = tmp_path / "other"
        other.mkdir()
        env_file = _seed_claude_md(other)  # a REAL writable target, so the
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))  # refusal is non-vacuous

        with pytest.raises(ProjectScopeDisagreementError) as excinfo:
            wm.sync_to_claude_md({"context": "X", "goal": "g"}, None, "id")
        assert str(other) in str(excinfo.value)
        assert str(umbrella.project) in str(excinfo.value)
        assert "X" not in env_file.read_text(encoding="utf-8"), (
            "the refused sync still wrote to the env-resolved file"
        )


# ---------------------------------------------------------------------------
# Cross-process: the CLI envelope carries the refusal on stderr
# ---------------------------------------------------------------------------

class TestCliRefusalEnvelope:
    def test_memory_save_cli_envelopes_the_refusal_on_stderr(self, tmp_path):
        """The full boundary: a child process with a constructed env discovers
        the record itself (no pytest marker crosses), and cmd_save's envelope
        lands on stderr with both values. Asserting the envelope type and both
        dir names — not the exit code alone — is what pins the refusal TEXT."""
        umbrella = make_umbrella(tmp_path)
        write_session_context(umbrella.config_root, SID, umbrella.project)
        other = tmp_path / "other"
        other.mkdir()
        store = tmp_path / "memory.db"
        env = child_env(
            umbrella.config_root,
            home=tmp_path,
            session_id=SID,
            project_dir=other,
        )
        setup = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "setup", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(tmp_path), timeout=120,
        )
        assert setup.returncode == 0, f"store setup failed: {setup.stderr[:400]!r}"

        proc = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "save", "--db-path", str(store),
             json.dumps({"context": "cross-process", "goal": "g"})],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(tmp_path), timeout=120,
        )
        assert proc.returncode != 0, f"a disagreeing save exited 0: {proc.stdout!r}"
        assert "SCOPE_DISAGREEMENT" in proc.stderr, (
            f"the envelope type is missing from stderr: {proc.stderr!r}"
        )
        # The envelope is home-scrubbed (~), so assert on the distinguishing
        # basenames rather than the absolute paths.
        assert other.name in proc.stderr and umbrella.project.name in proc.stderr, (
            f"the refusal must name both values; stderr: {proc.stderr!r}"
        )
        assert not store.exists() or "cross-process" not in store.read_bytes().decode(
            "utf-8", errors="ignore"
        ), "a refused save left the row behind"


# ---------------------------------------------------------------------------
# R1 — #1613 acceptance: an umbrella session writes unprefixed (subprocess)
# ---------------------------------------------------------------------------

def _memory_store_scopes(store: Path) -> list:
    """The project_id of every row in the store — the scope the writer USED,
    read from the DB rather than inferred from an exit code."""
    with sqlite3.connect(str(store)) as conn:
        return [row[0] for row in conn.execute("SELECT project_id FROM memories")]


class TestR1UmbrellaAcceptance:
    """The #1613 acceptance shape end-to-end: env var ABSENT, session record
    armed, cwd = the umbrella. memory save and WM sync both run
    unprefixed and land under the recorded scope. Every leg is a real
    subprocess with a constructed env — the boundary the issue measured."""

    def _env(self, umbrella, tmp_path, memory_dir):
        return child_env(
            umbrella.config_root,
            home=tmp_path,
            session_id=SID,
            memory_dir=memory_dir,
        )


    def test_r1_memory_save_and_sync_unprefixed_scope_the_record(self, tmp_path):
        """The acceptance shape faithfully: the DEFAULT store (a real session
        passes no --db-path), so the store lands under the child's tmp HOME
        with origin 'home' — and the ambient-sync guard's redirected-store
        refusal does not fire. (That guard is the incident class of the
        scratch-store incident, not a defect in this fix.)"""
        umbrella = make_umbrella(tmp_path)
        claude_md = _seed_claude_md(umbrella.project)
        write_session_context(umbrella.config_root, SID, umbrella.project)
        env = self._env(umbrella, tmp_path, None)
        default_store = tmp_path / ".claude" / "pact-memory" / "memory.db"

        save = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "save",
             json.dumps({"context": "R1-UMBRELLA-SAVE", "goal": "g"})],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(umbrella.project),
            timeout=120,
        )
        assert save.returncode == 0, f"unprefixed save failed: {save.stderr[:400]!r}"
        save_envelope = json.loads(save.stdout)
        assert save_envelope["ok"] is True
        assert save_envelope["result"]["sync_status"] == "wrote", (
            f"save's WM sync leg did not write: {save_envelope}"
        )
        assert default_store.exists(), "save did not create the default store"
        assert _memory_store_scopes(default_store) == ["umbrella"], (
            f"the save did not scope to the recorded project: "
            f"{_memory_store_scopes(default_store)}"
        )
        assert "R1-UMBRELLA-SAVE" in claude_md.read_text(encoding="utf-8"), (
            "save's sync leg did not land in the umbrella CLAUDE.md"
        )

        # The standalone sync surface, unprefixed (a rebuild over the same
        # record keeps the section intact).
        sync = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "sync"],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(umbrella.project),
            timeout=120,
        )
        assert sync.returncode == 0, f"unprefixed sync failed: {sync.stderr[:400]!r}"
        sync_envelope = json.loads(sync.stdout)
        assert sync_envelope["ok"] is True, f"sync envelope: {sync_envelope}"
        assert sync_envelope["result"]["project_id"] == "umbrella"
        assert "R1-UMBRELLA-SAVE" in claude_md.read_text(encoding="utf-8"), (
            "the standalone sync did not project the umbrella scope's record"
        )


# ---------------------------------------------------------------------------
# R2 — #1485: cwd in a git sub-repo of the umbrella
# ---------------------------------------------------------------------------

class TestR2SubRepoCwd:
    """The exact #1485 frame: cwd inside the umbrella's git sub-repo (no
    CLAUDE.md of its own), env absent, record naming the umbrella. Without the
    record rung the git strategy answers the SUB-repo's basename."""

    def test_r2_record_outranks_the_subrepo_git_root(self, tmp_path, monkeypatch):
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(umbrella.subrepo)
        assert PACTMemory._detect_project_id() == "umbrella"


# ---------------------------------------------------------------------------
# R3 — #1005: ambiguous cwd; the record outranks the home/user fallback
# ---------------------------------------------------------------------------

class TestR3AmbiguousCwd:
    def test_r3_record_outranks_an_ambiguous_cwd(self, tmp_path, monkeypatch):
        """cwd in a bare directory whose marker walk would land on the tmp HOME
        config dir (answering the tmp basename): without the record rung the
        answer is the walk's; with it, the record's. The no-record/no-env
        negative half is pinned by TestHomeScopeWarning (the last resort warns
        rather than silently scoping home)."""
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.chdir(bare)
        assert PACTMemory._detect_project_id() == "umbrella"


# ---------------------------------------------------------------------------
# R4 — #1600: bimodal git cannot split parent and child
# ---------------------------------------------------------------------------

class TestR4BimodalGit:
    """The git-flake shim makes every git subprocess fail. The CONTROL arm
    proves the shim is live (git's death changes the worktree answer); the
    INVARIANT arms prove the record rung's answer does not depend on git at
    all, in-process or across the process boundary."""

    def _worktree_pair(self, tmp_path):
        main = _git_repo(tmp_path / "main-proj")
        linked = tmp_path / "wt"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", "-q", str(linked), "-b", "wt"],
            check=True, capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
        )
        return main, linked

    def test_r4_control_git_death_changes_the_worktree_answer(self, tmp_path, monkeypatch):
        """CONTROL, not a result row: cwd in a linked worktree, NO record. Git
        alive answers the MAIN repo (Strategy 2); git dead falls to the marker
        walk, which names the WORKTREE. The two answers differing is the proof
        the shim took effect — and the bimodal shape #1600 recorded."""
        main, linked = self._worktree_pair(tmp_path)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(linked)
        assert PACTMemory._detect_project_id() == "main-proj"  # git alive

        shim = git_flake_shim(tmp_path)
        monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
        assert PACTMemory._detect_project_id() == "wt", (
            "git's death did NOT change the answer — the shim is not live and "
            "the invariant arms below prove nothing"
        )


# ---------------------------------------------------------------------------
# R5 — the env-file channel: real session_init writes BOTH halves over the
# real process boundary, and a spawned-Bash child inherits the recorded value
# ---------------------------------------------------------------------------

class TestR5EnvFileSeam:
    """R5's two forms, per the probe outcome: the LITERAL os.environ leg is
    claimed for the standard spawned-Bash path only (the dogfood probe passed
    there with control-leg causation — tests/runbooks/claude-env-file-probe.md);
    this row drives the real writer and the platform's source step is played
    by source_export_line. Teammate-mode (in-process/tmux) propagation is
    UNVERIFIED at the platform layer and NOT claimed here — those sessions are
    covered by the rung-2 resolution-equality rows (R1/R2), and the
    distinction is recorded, not erased."""

    def test_r5_session_init_writes_record_and_export_and_a_bash_child_inherits(
        self, tmp_path
    ):
        umbrella = make_umbrella(tmp_path)
        env_file = tmp_path / "session-env.sh"
        sid5 = "r5-env-file-session-0001"
        env = child_env(
            umbrella.config_root, home=tmp_path, session_id=sid5,
            project_dir=umbrella.project, memory_dir=tmp_path / "memdir",
        )
        env["CLAUDE_ENV_FILE"] = str(env_file)
        frame = {
            "source": "startup",
            "session_id": sid5,
            "agent_type": "pact-orchestrator",  # lead frame: persist is lead-gated
        }
        proc = subprocess.run(
            [sys.executable, str(_SESSION_INIT)],
            input=json.dumps(frame),
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(umbrella.project),
            timeout=180,
        )
        assert proc.returncode == 0, (
            f"session_init failed in the stripped frame: {proc.stderr[:600]!r}"
        )

        # The RECORD half, written by the real writer (Tier 2 — no stubbed
        # persist_context).
        ctx_path = (
            umbrella.config_root / "pact-sessions" / "umbrella" / sid5
            / "pact-session-context.json"
        )
        assert ctx_path.exists(), (
            f"the real writer left no record; config root holds "
            f"{sorted(str(p) for p in umbrella.config_root.rglob('*'))}"
        )
        recorded = json.loads(ctx_path.read_text(encoding="utf-8"))["project_dir"]
        assert recorded == str(umbrella.project)

        # The EXPORT half — and the exported == recorded invariant.
        exported = source_export_line(env_file, "CLAUDE_PROJECT_DIR")
        assert exported is not None, (
            f"no CLAUDE_PROJECT_DIR export in the env file: "
            f"{env_file.read_text(encoding='utf-8')!r}"
        )
        assert exported == recorded

        # The spawned-Bash leg: the platform sources the env file, so the
        # child sees os.environ['CLAUDE_PROJECT_DIR'] == the recorded value —
        # and a write under it agrees with the record (no refusal).
        bash_env = child_env(
            umbrella.config_root, home=tmp_path, session_id=sid5,
            project_dir=exported, memory_dir=tmp_path / "memdir2",
        )
        probe = subprocess.run(
            [sys.executable, "-c",
             "import os; v = os.environ['CLAUDE_PROJECT_DIR']; print(v)"],
            capture_output=True, text=True, env=carry_clock_shift(bash_env), cwd=str(umbrella.project),
        )
        assert probe.stdout.strip() == recorded, (
            "the env-file value a spawned Bash inherits differs from the "
            "session record — the exported == recorded invariant is broken"
        )
        store = tmp_path / "memory.db"
        setup = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "setup", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(bash_env), cwd=str(umbrella.project),
            timeout=120,
        )
        assert setup.returncode == 0, f"store setup failed: {setup.stderr[:400]!r}"
        save = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "save", "--db-path", str(store),
             json.dumps({"context": "R5-EXPORTED", "goal": "g"})],
            capture_output=True, text=True, env=carry_clock_shift(bash_env), cwd=str(umbrella.project),
            timeout=120,
        )
        assert save.returncode == 0, (
            f"a write under the exported value refused despite record agreement: "
            f"{save.stderr[:400]!r}"
        )
        assert _memory_store_scopes(store) == ["umbrella"]


# ---------------------------------------------------------------------------
# Edge: symlinked spellings — slug equality on reads, verbatim refusal on writes
# ---------------------------------------------------------------------------

class TestSymlinkEdges:
    def test_symlinked_record_names_the_target(self, tmp_path, monkeypatch):
        """A recorded SYMLINK path resolves to the target's basename for the
        project name — one project, one key, however the session was launched."""
        umbrella = make_umbrella(tmp_path)
        link = tmp_path / "linked-umbrella"
        link.symlink_to(umbrella.project)
        _arm_record(monkeypatch, tmp_path, link)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert pact_session.get_project_dir_from_session_record() == str(link), (
            "the reader must return the recorded value VERBATIM"
        )
        assert PACTMemory._detect_project_id() == "umbrella"


# ---------------------------------------------------------------------------
# Edge: the sync CLI envelopes the refusal exactly like save
# ---------------------------------------------------------------------------

class TestSyncCliEnvelope:
    def test_sync_cli_envelopes_the_refusal_on_stderr(self, tmp_path):
        """The second CLI write path's envelope: cmd_sync catches
        ProjectScopeDisagreementError into SCOPE_DISAGREEMENT on stderr,
        naming both values."""
        umbrella = make_umbrella(tmp_path)
        _seed_claude_md(umbrella.project)
        write_session_context(umbrella.config_root, SID, umbrella.project)
        other = tmp_path / "other"
        _seed_claude_md(other)  # a REAL writable target, so the refusal is non-vacuous
        store = tmp_path / "memory.db"
        env = child_env(
            umbrella.config_root, home=tmp_path, session_id=SID,
            project_dir=other, memory_dir=tmp_path / "memdir",
        )
        setup = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "setup", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(tmp_path), timeout=120,
        )
        assert setup.returncode == 0, f"store setup failed: {setup.stderr[:400]!r}"
        proc = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "sync", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(env), cwd=str(tmp_path), timeout=120,
        )
        assert proc.returncode != 0, f"a disagreeing sync exited 0: {proc.stdout!r}"
        assert "SCOPE_DISAGREEMENT" in proc.stderr, (
            f"the envelope type is missing from stderr: {proc.stderr!r}"
        )
        assert other.name in proc.stderr and umbrella.project.name in proc.stderr, (
            f"the refusal must name both values; stderr: {proc.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Edge: READS proceed under an env/record disagreement (the liberal half)
# ---------------------------------------------------------------------------

class TestLiberalReadsUnderDisagreement:
    def test_cli_reads_proceed_and_follow_the_env_scope(self, tmp_path):
        """Reads never refuse: a disagreeing env prefix is a deliberate
        per-command cross-scope inspection. The child lists and gets the
        ENV-scoped record while the record names a different project — exit 0,
        env-scoped answer, no refusal on stderr."""
        umbrella = make_umbrella(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        store = tmp_path / "memory.db"
        # Seed a record under the ENV scope with NO session behind it (no
        # record, no disagreement — the write proceeds).
        seed_env = child_env(
            umbrella.config_root, home=tmp_path, project_dir=other,
            memory_dir=tmp_path / "memdir",
        )
        setup = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "setup", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(seed_env), cwd=str(other), timeout=120,
        )
        assert setup.returncode == 0, f"store setup failed: {setup.stderr[:400]!r}"
        save = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "save", "--db-path", str(store),
             json.dumps({"context": "LIBERAL-READ-SEED", "goal": "g"})],
            capture_output=True, text=True, env=carry_clock_shift(seed_env), cwd=str(other), timeout=120,
        )
        assert save.returncode == 0, f"seed save failed: {save.stderr[:400]!r}"
        seed_id = json.loads(save.stdout)["result"]["memory_id"]
        assert _memory_store_scopes(store) == ["other"]

        # Now read with a DISAGREEING session record armed (record=umbrella,
        # env=other): both read verbs must proceed and follow the env scope.
        write_session_context(umbrella.config_root, SID, umbrella.project)
        read_env = child_env(
            umbrella.config_root, home=tmp_path, session_id=SID,
            project_dir=other, memory_dir=tmp_path / "memdir",
        )
        listed = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "list", "--db-path", str(store)],
            capture_output=True, text=True, env=carry_clock_shift(read_env), cwd=str(other), timeout=120,
        )
        assert listed.returncode == 0, f"list refused a read: {listed.stderr[:400]!r}"
        assert "SCOPE_DISAGREEMENT" not in listed.stderr
        assert "LIBERAL-READ-SEED" in listed.stdout, (
            f"list under a disagreeing env did not follow the env scope: "
            f"{listed.stdout[:400]!r}"
        )
        got = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "get", "--db-path", str(store), seed_id],
            capture_output=True, text=True, env=carry_clock_shift(read_env), cwd=str(other), timeout=120,
        )
        assert got.returncode == 0, f"get refused a read: {got.stderr[:400]!r}"
        assert "SCOPE_DISAGREEMENT" not in got.stderr
        assert "LIBERAL-READ-SEED" in got.stdout


# ---------------------------------------------------------------------------
# Cycle-2: the refusal surface extends to update()/delete() and the
# retrieved-sync projection warns through its swallow
# ---------------------------------------------------------------------------

def _store_row_context(store: Path, memory_id: str) -> str:
    with sqlite3.connect(str(store)) as conn:
        return conn.execute(
            "SELECT context FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()[0]


class TestUpdateDeleteRefusalOnDisagreement:
    """update()/delete() are writes: under an env/record disagreement they
    refuse with both values + remedy BEFORE any store mutation; under
    agreement they proceed (the guard must not over-fire)."""

    def _seed_and_arm(self, tmp_path, monkeypatch):
        """A committed row scoped to the env project (written with NO session
        behind it), then the disagreement armed on top of it."""
        umbrella = make_umbrella(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
        memory = PACTMemory()  # constructed AFTER the env manipulation
        seed_id = memory.save({"context": "CYCLE2-SEED", "goal": "g"})
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        return umbrella, other, memory, seed_id

    def test_update_refuses_naming_both_values_before_any_mutation(
        self, tmp_path, monkeypatch
    ):
        umbrella, other, memory, seed_id = self._seed_and_arm(tmp_path, monkeypatch)
        store = tmp_path / ".claude" / "pact-memory" / "memory.db"
        before = _store_row_context(store, seed_id)

        with pytest.raises(ProjectScopeDisagreementError) as excinfo:
            memory.update(seed_id, {"context": "CYCLE2-MUTATED"})
        text = str(excinfo.value)
        assert str(other) in text, "the refusal does not name the env value"
        assert str(umbrella.project) in text, "the refusal does not name the record"
        assert "re-export CLAUDE_PROJECT_DIR" in text, "the refusal names no remedy"
        assert _store_row_context(store, seed_id) == before, (
            "a refused update still mutated the row"
        )


class TestRetrievedSyncWarnsUnderDisagreement:
    def test_search_swallow_warns_with_the_disagreement_and_does_not_raise(
        self, tmp_path, monkeypatch, caplog
    ):
        """The retrieved-context projection is a read side-effect: under a
        disagreement the projection's guard raises, search()'s swallow turns
        it into a WARNING that names the disagreement, and no exception
        escapes to the caller."""
        umbrella = make_umbrella(tmp_path)
        _arm_record(monkeypatch, tmp_path, umbrella.project)
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))

        fake_result = SimpleNamespace(
            id="fake-memory-id",
            to_dict=lambda: {"context": "CYCLE2-RETRIEVED", "goal": "g"},
        )
        monkeypatch.setattr(
            "scripts.memory_api.graph_enhanced_search",
            lambda *a, **kw: [fake_result],
        )
        memory = PACTMemory(project_id="proj")
        with caplog.at_level(logging.WARNING):
            results = memory.search("anything", sync_to_claude=True)
        assert results == [fake_result], "the read itself must not refuse"
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "disagrees" in w and str(other) in w and str(umbrella.project) in w
            for w in warnings
        ), (
            f"the swallow's warning does not name the disagreement: {warnings}"
        )


class TestWarrantedSyncProceedsUnderDisagreement:
    """The claude_md_root warrant on the public sync path: a caller that
    DECLARES the containment anchor has named its destination, so the ambient
    disagreement is moot — the write proceeds to the named root, NOT the
    record scope. The no-warrant path still refuses (TestWriteRefusalOn-
    Disagreement and the TestSyncCliEnvelope row are the counter arms)."""

    def test_sync_with_declared_root_proceeds_to_the_named_destination(
        self, tmp_path, monkeypatch
    ):
        """The declared root is a CONTAINMENT warrant, not a steering knob:
        the display resolver still resolves env-first (branch 1), so the
        honest shape is env == the named destination's root, with the warrant
        making the ambient disagreement (record names the umbrella) moot."""
        umbrella = make_umbrella(tmp_path)
        umbrella_md = _seed_claude_md(umbrella.project)
        other = tmp_path / "other"
        other_md = _seed_claude_md(other)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
        # A record committed under the env scope with NO session behind it.
        memory = PACTMemory()
        memory.save({"context": "WARRANT-TOKEN", "goal": "g"})
        # NOW the disagreement is armed (record=umbrella, env=other).
        _arm_record(monkeypatch, tmp_path, umbrella.project)

        written_ids = memory.sync(claude_md_root=other)
        assert memory.last_sync_status == wm.SyncResult.WROTE, (
            f"a warranted sync under disagreement must proceed; got "
            f"{memory.last_sync_status}"
        )
        assert written_ids, "the warranted sync projected no records"
        assert "WARRANT-TOKEN" in other_md.read_text(encoding="utf-8"), (
            "the projection did not land under the NAMED root"
        )
        assert "WARRANT-TOKEN" not in umbrella_md.read_text(encoding="utf-8"), (
            "the projection reached the record-scoped file despite the warrant"
        )
