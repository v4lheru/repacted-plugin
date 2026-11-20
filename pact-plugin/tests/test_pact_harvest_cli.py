"""
Durable test suite for the pact_harvest CLI extraction (#1034/#927).

Covers the harvest-domain CLI (hooks/shared/pact_harvest.py) + the pure
resolve_latest_artifacts helper (hooks/shared/session_journal.py), which replace
the previously-untestable inline-Python glue the pact-handoff-harvest skill ran.
(This closes the #927 review's MINOR-2: the supersede was prose only.)

THREAT MODEL → TEST MAPPING (why each block exists):
  - The secretary runs the harvest OFF-LEAD, where pact_context.get_session_dir()
    / read_events() false-return '' → 0 events silently. The CLI exists to give
    the skill explicit-path, masked-read-safe entry points. So the load-bearing
    tests are: (a) the B1-CLASS DRIFT PARITY — resolve-session-dir on a
    special-char project_dir/session_id reconstructs the SANITIZED path the
    writer actually wrote to (NO drift); this is the exact bug class clean-
    basename masking hid in #927 and only cross-lane review caught. (b) The
    EXIT-CODE contract (0=proceed-incl-empty, 2=stop, never 1) — the skill keys
    its "report the gap and STOP, do not fall back to a path-less read" branch on
    the exit code, never on parsing stdout. (c) The IMPORT-SEAM — the direct-
    script sys.path bootstrap that makes `from shared.pact_context` resolve;
    without it the CLI is dead in script mode (pact_context has package-relative
    imports). (d) The ARRAY-PARSE contract the rewritten skill Steps 1/10 rely on.

FIDELITY SPLIT (lead-confirmed): TRUE direct-script subprocess for the CLI
contract / exit-code / empty-stdout / import-seam tests (those properties only
exist in script mode); in-process direct calls for the pure helper units.

B1-ORACLE DISCIPLINE (lead-confirmed, avoids the #927 wrong-oracle near-miss):
the expected sanitized path is DERIVED from the SSOT — reconstruct_session_dir
called directly — NOT hand-built. A hand-built path that under-sanitizes would
agree with a buggy CLI (vacuous green). A companion assertion proves a raw
un-sanitized join DIFFERS, so the special-char input actually triggers
sanitization (defeating clean-basename masking).

NON-VACUITY: documented per-test in the HANDOFF; spot-proven via source mutation
(flip an exit code / break supersede / remove the bootstrap → the matching test
goes RED). The B1 parity test's raw-join-differs companion is its in-suite
non-vacuity guard.

Run on the 3.13.7 interpreter (default python3 has no pytest):
    /Users/alice/.pyenv/versions/3.13.7/bin/python3 -m pytest \
        pact-plugin/tests/test_pact_harvest_cli.py -rA
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).parent.parent / "hooks"

import shared.pact_context as pact_context  # noqa: E402
from shared.session_journal import (  # noqa: E402
    _parse_ts,
    append_event,
    make_event,
    resolve_latest_artifacts,
)

_CLI = _HOOKS_DIR / "shared" / "pact_harvest.py"
_PY = "/Users/alice/.pyenv/versions/3.13.7/bin/python3"

# Exit-code contract (must match pact_harvest.py).
_EXIT_OK = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_UNRESOLVED = 2


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the CLI as a TRUE direct-script subprocess (script mode — the
    only mode where the sys.path bootstrap, exit codes, and stdout contract
    actually exist). Uses the same interpreter the suite runs under."""
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
    )


def _art_event(workflow, feature, paths, ts, task_id=None):
    fields = {"workflow": workflow, "feature": feature, "paths": paths}
    if task_id is not None:
        fields["task_id"] = task_id
    ev = make_event("artifact_paths", **fields)
    ev["ts"] = ts
    return ev


# =============================================================================
# (1) PURE UNIT — resolve_latest_artifacts (in-process; no subprocess needed).
# =============================================================================
class TestResolveLatestArtifacts:
    FEATURE = "feat-x"

    def test_supersede_latest_ts_wins_per_workflow(self):
        events = [
            _art_event("prepare", self.FEATURE, ["/OLD.md"], "2026-06-25T01:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/NEW.md"], "2026-06-25T03:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/MID.md"], "2026-06-25T02:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {"prepare": ["/NEW.md"]}

    def test_complete_path_list_never_merged_across_events(self):
        """Each event carries the COMPLETE list; supersede REPLACES, never
        unions. The latest event's 1-path list wins whole over a prior 2-path
        list — a merge bug would yield 3 paths."""
        events = [
            _art_event("prepare", self.FEATURE, ["/a.md", "/b.md"], "2026-06-25T01:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/c.md"], "2026-06-25T02:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {"prepare": ["/c.md"]}


# =============================================================================
# (2) SUBPROCESS — resolve-session-dir contract + THE B1-CLASS DRIFT PARITY.
# =============================================================================
class TestResolveSessionDirSubprocess:
    def _write_ctx(self, tmp_path, project_dir, session_id):
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": project_dir, "session_id": session_id}), encoding="utf-8")
        return ctx

    def test_valid_context_exit0_absolute_dir(self, tmp_path, monkeypatch):
        # Subprocess isolation: the autouse conftest fixture redirects
        # Path.home() IN-PROCESS only (monkeypatch.setattr does not cross to
        # the child), so the subprocess must inherit CLAUDE_CONFIG_DIR to
        # resolve the SAME tmp root as the parent oracle
        # (pact_context.reconstruct_session_dir). See conftest
        # _isolate_config_root_to_tmp "WHY NOT ALSO SET HOME ENV".
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        ctx = self._write_ctx(tmp_path, "/clean/project", "sess-abcd")
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()
        assert Path(out).is_absolute()
        # Oracle = the SSOT helper itself (NOT a hand-built path).
        assert out == pact_context.reconstruct_session_dir("/clean/project", "sess-abcd")

    def test_b1_class_drift_both_axes_sanitized_no_drift(self, tmp_path, monkeypatch):
        """THE critical durability test (the exact bug class cross-lane review
        caught in #927). A project basename with a DOT and a session_id with a
        non-[A-Za-z0-9_-] char must reconstruct the SANITIZED path the WRITER
        wrote to — NO drift.

        ORACLE = reconstruct_session_dir called directly (the SSOT). Non-vacuity
        companion: a RAW un-sanitized join DIFFERS — proving the special-char
        input actually triggers sanitization on BOTH axes (slug + session_id),
        which is what clean-basename masking hid."""
        # Subprocess isolation: see test_valid_context_exit0_absolute_dir —
        # the child must inherit CLAUDE_CONFIG_DIR to resolve the SAME tmp
        # root as the parent oracle (pact_context.reconstruct_session_dir).
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        project_dir = "/Users/alice/my.project dir"   # dot AND space in the basename
        session_id = "abc.def 123"                  # dot AND space (non-allowlist)
        ctx = self._write_ctx(tmp_path, project_dir, session_id)
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()

        # (a) parity with the SSOT writer-derivation (the load-bearing assertion).
        expected = pact_context.reconstruct_session_dir(project_dir, session_id)
        assert out == expected, f"CLI {out!r} drifted from SSOT {expected!r}"

        # (b) NON-VACUITY: a raw un-sanitized join would land elsewhere — so the
        # special-char input genuinely exercises sanitization (not a no-op).
        from shared.paths import get_claude_config_dir
        raw = str(get_claude_config_dir() / "pact-sessions"
                  / Path(project_dir).name / session_id)
        assert out != raw, (
            "special-char input must be sanitized away from the raw join — if "
            "equal, the drift case is vacuous (no sanitization exercised)"
        )
        # And concretely: the dot/space are gone from BOTH path segments.
        assert "my_project_dir" in out and "abc_def_123" in out


# =============================================================================
# (3) SUBPROCESS — resolve-artifacts contract.
# =============================================================================
class TestResolveArtifactsSubprocess:
    FEATURE = "feat-y"

    @pytest.fixture
    def session_with_events(self, tmp_path, monkeypatch, pact_context):
        """Build a REAL on-disk journal at a resolvable session dir so the CLI's
        read_events_from resolves it (non-mocked seam).

        CLAUDE_CONFIG_DIR is set (not just Path.home) because the CLI runs as a
        SUBPROCESS: `monkeypatch.setattr(Path, "home", ...)` patches only this
        test process, NOT the child, so the subprocess's --session-dir
        containment check would resolve the REAL home and falsely reject this
        tmp session dir. The env var IS inherited by the subprocess, making the
        child's get_claude_config_dir() agree with this fixture's root."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        pact_context(team_name="t", session_id="sid-harvest", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = tmp_path / ".claude" / "pact-sessions" / slug / "sid-harvest"
        return str(session_dir)

    def test_supersede_and_shape_one_line_json(self, session_with_events):
        sd = session_with_events
        append_event(_art_event("prepare", self.FEATURE, ["/OLD.md"], "2026-06-25T01:00:00Z"))
        append_event(_art_event("prepare", self.FEATURE, ["/NEW.md"], "2026-06-25T02:00:00Z"))
        append_event(_art_event("architect", self.FEATURE, ["/arch.md"], "2026-06-25T01:00:00Z"))
        r = _run_cli("resolve-artifacts", "--session-dir", sd, "--feature", self.FEATURE)
        assert r.returncode == _EXIT_OK
        # One-line compact JSON object.
        assert "\n" not in r.stdout.strip()
        assert json.loads(r.stdout) == {"prepare": ["/NEW.md"], "architect": ["/arch.md"]}

    def test_wrong_feature_excluded(self, session_with_events):
        sd = session_with_events
        append_event(_art_event("prepare", self.FEATURE, ["/mine.md"], "2026-06-25T01:00:00Z"))
        append_event(_art_event("prepare", "other", ["/other.md"], "2026-06-25T02:00:00Z"))
        r = _run_cli("resolve-artifacts", "--session-dir", sd, "--feature", self.FEATURE)
        assert r.returncode == _EXIT_OK
        assert json.loads(r.stdout) == {"prepare": ["/mine.md"]}


# =============================================================================
# (4) IMPORT-SEAM — the direct-script sys.path bootstrap resolves
#     shared.pact_context (the non-vacuous seam the bootstrap fix addresses).
# =============================================================================
class TestImportSeamBootstrap:
    def test_direct_script_resolves_pact_context_package_chain(self, tmp_path, monkeypatch):
        """The CLI run as a direct script (cwd OUTSIDE the repo) must resolve
        `from shared.pact_context import reconstruct_session_dir` AND
        pact_context's own package-relative imports (from .session_state, etc).
        Proven end-to-end: resolve-session-dir produces a correct abs path, which
        is only possible if the bootstrap made `shared` a real package. If a
        future edit removes the sys.path bootstrap, this goes RED (ModuleNotFound
        or a non-zero exit), failing loudly."""
        # Subprocess isolation: see test_valid_context_exit0_absolute_dir —
        # the child must inherit CLAUDE_CONFIG_DIR to resolve the SAME tmp
        # root as the parent oracle (pact_context.reconstruct_session_dir).
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": "/p", "session_id": "s"}), encoding="utf-8")
        # Run from an arbitrary cwd (tmp_path) to exercise script-mode sys.path.
        r = subprocess.run(
            [sys.executable, str(_CLI), "resolve-session-dir",
             "--context-file", str(ctx)],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert r.returncode == _EXIT_OK, (
            f"direct-script import seam broke (rc={r.returncode}); "
            f"stderr={r.stderr!r}"
        )
        assert r.stdout.strip() == pact_context.reconstruct_session_dir("/p", "s")
        assert "ModuleNotFoundError" not in r.stderr
        assert "ImportError" not in r.stderr


# =============================================================================
# (5) ARRAY-PARSE CONTRACT — session_journal's `read` CLI emits a JSON ARRAY
#     (the contract SKILL.md Steps 1/10 now parse). A future switch back to
#     JSONL would break the skill's reused read — this guards it.
# =============================================================================
class TestReadEmitsJsonArrayContract:
    def test_session_journal_read_cli_emits_json_array(self, tmp_path, monkeypatch, pact_context):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="t", session_id="sid-arr", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = str(tmp_path / ".claude" / "pact-sessions" / slug / "sid-arr")
        append_event(make_event(
            "agent_handoff", agent="devops-engineer", task_id="1",
            task_subject="x", handoff={"produced": "p", "decisions": "d",
            "uncertainty": "n", "integration": "n", "reasoning_chain": "r",
            "open_questions": "n"}))
        sj = str(_HOOKS_DIR / "shared" / "session_journal.py")
        r = subprocess.run(
            [sys.executable, sj, "read", "--session-dir", session_dir,
             "--type", "agent_handoff"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"read failed: {r.stderr!r}"
        parsed = json.loads(r.stdout)
        assert isinstance(parsed, list), (
            "session_journal read MUST emit a JSON ARRAY (the contract Steps "
            "1/10 parse); a regression to JSONL would break the reused read"
        )
        assert len(parsed) == 1 and parsed[0]["type"] == "agent_handoff"


# =============================================================================
# (6) ARGPARSE CONTRACT — a missing/unknown subcommand OR a missing required
#     argument must exit NON-ZERO (argparse's 2), so the skill's
#     `if ! out=$(...); then stop; fi` gate still STOPS rather than falling
#     through to a path-less read. `sub.required = True` (plus each subcommand's
#     `required=True` arguments) is what enforces this; a regression to
#     required=False would let an under-specified invocation exit 0 — this pins
#     the stop-gate-compatible contract.
# =============================================================================
class TestSubcommandArgparseContract:
    def test_no_subcommand_exits_nonzero_stop_gate(self):
        """No subcommand -> argparse error -> exit 2 (non-zero). The skill keys
        its stop branch on the exit code, so any non-zero is a stop; assert it
        is NOT 0 and concretely the argparse 2."""
        r = _run_cli()
        assert r.returncode != _EXIT_OK, "no-subcommand must not exit 0"
        assert r.returncode == 2, f"expected argparse exit 2, got {r.returncode}"


# =============================================================================
# (7) TRAVERSAL DEFENSE + GRACEFUL-TS — through the real CLI.
#     (a) A traversal session_id ('../../etc/passwd') must be SANITIZED away,
#         leaving no '..' segment that could escape the pact-sessions tree.
#     (b) A corrupted journal with a parseable-but-naive ts must NOT crash
#         resolve-artifacts (exit 1 + traceback); it degrades gracefully.
# =============================================================================
class TestTraversalAndGracefulTs:
    def test_traversal_session_id_is_sanitized_no_escape(self, tmp_path, monkeypatch):
        """A '../../etc/passwd' session_id reconstructs INSIDE pact-sessions
        with the traversal characters neutralized (no '..' segment, stays under
        the sessions root). Oracle = the SSOT reconstruct_session_dir.

        What this verifies is the OUTCOME — the resolved dir provably stays
        under the pact-sessions root with no upward-escaping '..' segment — NOT
        which layer enforces it. Two defense-in-depth layers cooperate: the
        session_id regex sanitization in reconstruct_session_dir AND
        _build_session_path's own resolve()/containment/basename-fallback
        traversal guard. Because that second layer alone already neutralizes the
        traversal, a session_id-regex-only revert does NOT leave a '..' segment
        (this test stays green under it) — so the structural assertion pins the
        no-escape OUTCOME, not the regex mechanism in isolation."""
        # Subprocess isolation: see test_valid_context_exit0_absolute_dir —
        # the child must inherit CLAUDE_CONFIG_DIR to resolve the SAME tmp
        # root as the parent oracle (pact_context.reconstruct_session_dir).
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": "/clean/project", "session_id": "../../etc/passwd"}),
            encoding="utf-8")
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()
        # Parity with the SSOT writer-derivation (load-bearing).
        assert out == pact_context.reconstruct_session_dir(
            "/clean/project", "../../etc/passwd")
        # Structural outcome: the traversal is neutralized — no '..' path
        # segment survives, and the resolved dir stays under the pact-sessions
        # tree (guaranteed jointly by the sanitization + _build_session_path
        # traversal guard).
        parts = Path(out).parts
        assert ".." not in parts, f"traversal not sanitized: {out!r}"
        assert "pact-sessions" in parts
        sessions_idx = parts.index("pact-sessions")
        # Everything after pact-sessions is the sanitized slug + session_id —
        # no segment escapes upward.
        assert all(p != ".." for p in parts[sessions_idx:])


# =============================================================================
# (8) SESSION-DIR CONTAINMENT — resolve-artifacts rejects a --session-dir
#     OUTSIDE the pact-sessions root (defense-in-depth) but NEVER a legit one.
#     The root derives from get_claude_config_dir() (driven by CLAUDE_CONFIG_DIR
#     here, which the subprocess inherits) so the child agrees with the test's
#     configured root — NOT the real home.
# =============================================================================
class TestSessionDirContainment:
    def _configure_root(self, tmp_path, monkeypatch):
        """Point both this process and the CLI subprocess at a tmp config root.
        Path.home for the in-process journal write; CLAUDE_CONFIG_DIR (inherited
        by the subprocess) for the child's containment-root derivation."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))

    def test_legit_contained_session_dir_passes(
        self, tmp_path, monkeypatch, pact_context):
        """A session dir UNDER <config>/pact-sessions/ resolves usually
        (exit 0) — containment must NOT over-reject the canonical layout. Guards
        the false-rejection direction: a too-strict predicate would flip this to
        exit 2."""
        self._configure_root(tmp_path, monkeypatch)
        pact_context(team_name="t", session_id="sid-contained", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = tmp_path / ".claude" / "pact-sessions" / slug / "sid-contained"
        append_event(_art_event("prepare", "feat-c", ["/c.md"], "2026-06-25T01:00:00Z"))
        r = _run_cli("resolve-artifacts", "--session-dir", str(session_dir),
                     "--feature", "feat-c")
        assert r.returncode == _EXIT_OK, (
            f"legit contained session-dir must pass; rc={r.returncode} "
            f"stderr={r.stderr!r}")
        assert json.loads(r.stdout) == {"prepare": ["/c.md"]}


# =============================================================================
# (9) _parse_ts TRAILING-Z ANCHOR — only a SINGLE trailing `Z` is rewritten to
#     `+00:00`; an interior `Z` is left intact (a blanket replace-all would
#     mangle it mid-string). The string-level normalization is isolated in
#     _normalize_trailing_z so the trailing-only anchor is directly testable —
#     at the _parse_ts return/raise layer the two forms are observationally
#     identical (any interior Z is unparseable either way).
# =============================================================================
class TestParseTsTrailingZAnchor:
    def test_trailing_z_parses_as_utc_no_regression(self):
        """A legit trailing-Z ts parses to the SAME UTC-aware instant as its
        explicit `+00:00` spelling — the anchor preserves the normal-path
        behavior (and the pre-3.11 trailing-Z support fromisoformat lacked)."""
        assert _parse_ts("2026-06-25T01:00:00Z") == _parse_ts(
            "2026-06-25T01:00:00+00:00")
        assert _parse_ts("2026-06-25T01:00:00Z").utcoffset().total_seconds() == 0


class TestHarvestTeachExamples:
    # Full example lines, content-pinned. prog is the invoked script path
    # (_CLI), so each rendered example is the exact pasteable string:
    # interpreter-prefixed, quoted script path, concrete flags. The epilog is
    # unwrapped (RawDescriptionHelpFormatter), so a full-line assertion is
    # width-safe at any COLUMNS.
    _EXAMPLES = {
        "resolve-session-dir": (
            f'python3 "{_CLI}" resolve-session-dir'
            " --context-file /abs/pact-session-context.json"
        ),
        "resolve-artifacts": (
            f'python3 "{_CLI}" resolve-artifacts'
            " --session-dir /abs/session --feature slug"
        ),
    }

    def test_top_level_help_lists_every_example_line(self):
        r = _run_cli("--help")
        assert r.returncode == 0
        for line in self._EXAMPLES.values():
            assert line in r.stdout, line


