"""
#927 HANDOFF + artifact durability — comprehensive durability tests.

Covers the four committed seams of the artifact_paths durability mechanism:
  - session_journal.py     — artifact_paths event schema + read_events_from
                             vs the off-lead-masked read_events (the masked-read
                             seam) + supersede-by-(workflow,feature) latest-ts.
  - task_lifecycle_gate.py — the lead-frame, fail-open emit BACKSTOP that nudges
                             when a PREPARE/ARCHITECT phase completes with no
                             artifact_paths event for its (workflow, feature).

DISCIPLINE (per the locked plan + this session's plan-mode strategy):
  - BOTH-MODES AXIS = agent_type AND session_id. The backstop keys on
    is_canonical_journal_frame, which reads BOTH: the role leg on agent_type
    (LEAD = 'repacted:pact-orchestrator' / 'pact-orchestrator'; TEAMMATE = a pact-*
    type), and the topology leg on session_id against the leadSessionId of the
    team config. The teammate-frame arms below assert the backstop is ABSENT,
    and that absence holds ONLY because those fixtures seed no leadSessionId
    team config, so the topology leg cannot resolve. A session_id-topology
    fixture DOES test a gate input this surface reads, and the arms it makes
    possible are BUILT BELOW: `_seed_lead_topology` writes the second half,
    and the two-arm matrix on agent_type drives the same agent_type value on
    both sides of a resolvable topology.
  - PHANTOM-GREEN GUARD: phase-task subjects + owners are seeded from THIS
    session's real on-disk task shapes ('PREPARE: handoff-artifact-durability',
    'ARCHITECT: handoff-artifact-durability', bare owner 'devops-engineer'),
    not a synthetic shape the code expects. A fixture using owner
    'pact-devops-engineer' (the agentType, not the owner) is the #1028 fake.
  - DETERMINISM: reproduce the END-STATE (direct evaluate_lifecycle / read drive
    + real on-disk journal file ops), never the async race. No sleeps, no
    SendMessage, no real GC timing — GC/worktree-teardown is a real file removal.
  - NON-MOCKED seam integration for the masked-read seam + the backstop journal
    read: both task_lifecycle_gate and agent_handoff_emitter are in
    SEAM_DEPENDENT_HOOKS, so these tests drive a REAL on-disk journal via
    append_event/read_events_from over a tmp-redirected HOME + pact_context —
    they never mock read_events / read_events_from / the gate.
  - NON-VACUITY: source-removal counter-tests (remove the journal SOURCE and
    prove recovery collapses) + a masked-read inversion (prove the off-lead leg
    is false-empty for the RIGHT reason, not a missing fixture).

Run with the 3.13.7 interpreter (default python3 has no pytest):
    /Users/alice/.pyenv/versions/3.13.7/bin/python3 -m pytest \
        pact-plugin/tests/test_artifact_paths_durability.py -rA
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.session_journal import (  # noqa: E402
    append_event,
    make_event,
    read_events,
    read_events_from,
    read_last_event_from,
)

# --- agent_type axis constants (the both-modes discriminator for this surface) ---
LEAD = "repacted:pact-orchestrator"
LEAD_BARE = "pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

# --- production-real seeds (this session's actual on-disk task shapes) ---
FEATURE = "handoff-artifact-durability"
PREPARE_SUBJECT = f"PREPARE: {FEATURE}"
ARCHITECT_SUBJECT = f"ARCHITECT: {FEATURE}"
# A real specialist owner is a BARE name; the pact-* token is the agentType
# (config.json members[].agentType), NOT the owner. Phantom-green ground truth.
BARE_OWNER = "devops-engineer"

TEAM = "session-artifact-dur"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
PROJECT_DIR = "/test/project"


def _artifact_event(workflow, feature=FEATURE, paths=None, task_id=None, ts=None):
    """Build a real artifact_paths event via make_event (auto-sets v/ts).

    ts override lets a test pin supersede ordering deterministically.
    """
    if paths is None:
        paths = [f"/abs/docs/{workflow}/{feature}.md"]
    fields = {"workflow": workflow, "feature": feature, "paths": paths}
    if task_id is not None:
        fields["task_id"] = task_id
    event = make_event("artifact_paths", **fields)
    if ts is not None:
        event["ts"] = ts
    return event


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Redirect HOME + session context to a tmp tree so append_event /
    read_events / read_events_from write+read a REAL on-disk session journal.

    Mirrors test_pr4_drain_survival_harvest.live_env — the canonical non-mocked
    seam harness. Returns (tmp_path, session_dir) where session_dir is the
    absolute on-disk session directory the explicit reader resolves.

    CLAUDE_CONFIG_DIR IS REMOVED, not merely unused. It takes precedence over
    home in get_claude_config_dir, so an operator who exports it would send
    the team-config read outside the temp tree. The topology arms below read
    that config, and the failure would look like a gate decision.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir=PROJECT_DIR)
    slug = Path(PROJECT_DIR).name
    session_dir = tmp_path / ".claude" / "pact-sessions" / slug / SID
    return tmp_path, str(session_dir)


def _journal_file(tmp_path: Path) -> Path:
    """Resolve the single on-disk journal file under the tmp sessions root —
    so a non-vacuity arm can remove the SOURCE and prove recovery collapses."""
    sessions_root = tmp_path / ".claude" / "pact-sessions"
    matches = list(sessions_root.rglob("session-journal.jsonl"))
    assert len(matches) == 1, f"expected one tmp journal, found {matches!r}"
    return matches[0]


def _payload(*, agent_type, subject, task_id="40", skipped=None, owner=None,
             session_id=None):
    """Build a TaskUpdate(status=completed) PostToolUse frame for
    evaluate_lifecycle. The both-modes axis is agent_type AND session_id:
    is_canonical_journal_frame reads the role leg on agent_type and the
    topology leg on session_id against the leadSessionId of the team config.

    A frame built with session_id=None carries NO topology half, so the leg
    cannot resolve whatever the team config says. Pass session_id AND call
    _seed_lead_topology to build the two halves together. Either one alone
    gives False, which is the answer the retired predicate gives.
    """
    metadata: dict = {}
    if skipped is not None:
        metadata["skipped"] = skipped
    task = {"id": task_id, "subject": subject, "owner": owner, "metadata": metadata}
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"task": task},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def _has_backstop_advisory(advisories) -> bool:
    return any(code == "artifact_paths_emit_missing" for code, _ in advisories)


def _seed_lead_topology(tmp_path: Path, lead_session_id: str = SID) -> Path:
    """Write the SECOND half of the topology leg: the team config carrying
    leadSessionId.

    THE TWO HALVES LIVE IN DIFFERENT FILES. The live_env fixture writes the
    CONTEXT file, which supplies get_team_name(). The leadSessionId lives in
    the TEAM CONFIG. A fixture that seeds only the first carries one half,
    and one half gives the SAME verdict as the retired predicate, so an arm
    built on it is green either way and reads as tested.
    """
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    config = teams_dir / "config.json"
    config.write_text(
        json.dumps({"leadSessionId": lead_session_id}), encoding="utf-8"
    )
    return config


# =============================================================================
# P2-6 SCHEMA — artifact_paths validates required {workflow,feature,paths:list}
#                + optional task_id; list is a first-class registered type.
# =============================================================================
class TestSchema:
    def test_valid_event_appends(self, live_env):
        _tmp, session_dir = live_env
        assert append_event(_artifact_event("prepare")) is True
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        assert events[0]["workflow"] == "prepare"
        assert events[0]["feature"] == FEATURE
        assert isinstance(events[0]["paths"], list)
        assert events[0]["v"] == 1
        assert "ts" in events[0]


# =============================================================================
# P0-2 ALWAYS-READ mechanics — read_events_from resolves the event;
#       supersede-by-(workflow,feature) latest-ts picks the latest.
# =============================================================================
class TestAlwaysReadAndSupersede:
    def test_read_events_from_resolves_event(self, live_env):
        _tmp, session_dir = live_env
        append_event(_artifact_event("architect", paths=["/abs/a.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        assert [e["paths"] for e in events] == [["/abs/a.md"]]


# =============================================================================
# P0-3 FAILURE-PATH recovery — with the HANDOFF absent, the artifact is STILL
#       resolvable via the artifact_paths pointer (HANDOFF-independent).
# P0-1 BOUNDARY SEAL is exercised by TestBackstopBothModes (teammate-frame leg).
# =============================================================================
class TestFailurePathRecovery:
    def test_artifact_resolvable_with_no_handoff_event(self, live_env):
        """The durability guarantee: artifact recovery does NOT depend on any
        agent_handoff event. Seed ONLY an artifact_paths event (no handoff) and
        prove the pointer + disk read recover the substance."""
        tmp_path, session_dir = live_env
        artifact = tmp_path / "docs" / "prepare" / f"{FEATURE}.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("PREPARE SUBSTANCE — recovered without a handoff",
                            encoding="utf-8")
        append_event(_artifact_event("prepare", paths=[str(artifact)]))

        # No agent_handoff events exist at all.
        assert read_events_from(session_dir, "agent_handoff") == []
        # Yet the artifact is fully resolvable via the pointer.
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        recovered = Path(events[0]["paths"][0]).read_text(encoding="utf-8")
        assert "recovered without a handoff" in recovered


# =============================================================================
# P2-7 MASKED-READ SEAM — read_events() off-lead returns false-empty while
#       read_events_from(session_dir) returns events. THE load-bearing dual-mode
#       constraint (the secretary harvests off-lead). Non-mocked seam.
# =============================================================================
class TestMaskedReadSeam:
    def test_implicit_read_resolves_when_context_present(self, live_env):
        """Positive control: with the session context resolvable (the lead-frame
        analogue — get_session_dir resolves), the implicit read_events() DOES
        see the event. Proves the false-empty below is the off-lead masking, not
        a missing fixture."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare"))
        assert len(read_events("artifact_paths")) == 1
        assert len(read_events_from(session_dir, "artifact_paths")) == 1


# =============================================================================
# P0-1 BOUNDARY SEAL + P1-5 BACKSTOP — both-modes (frame-gate) matrix.
#   The backstop fires a FAIL-OPEN advisory when a PREPARE/ARCHITECT phase
#   completes with no artifact_paths event for its (workflow,feature).
#   The teammate-frame leg asserts the backstop is ABSENT, and that absence is
#   the frame gate acting on a fixture that seeds no leadSessionId team config.
# =============================================================================


# =============================================================================
# P1-5 BACKSTOP exemptions — skipped phases + non-artifact phases are exempt;
#       the advisory NEVER blocks.
# =============================================================================


# =============================================================================
# P2-6 unit — _phase_artifact_requirement maps subjects to (workflow, feature)
#       or None. Phantom-green-seeded from real subject shapes.
# =============================================================================


# =============================================================================
# P1-4 TIMING — the artifact_paths event/journal lives OUTSIDE the worktree
#       (survives worktree teardown); paths are full-absolute.
# =============================================================================
class TestWorktreeSurvivalAndAbsolutePaths:
    def test_journal_lives_outside_worktree(self, live_env):
        """The journal resolves under ~/.claude/pact-sessions/, NOT under any
        worktree — so a `git worktree remove` (modeled as removing a worktree
        subtree) cannot touch it.

        The LOAD-BEARING invariant is the PATH RELATIONSHIP: the journal abspath
        is NOT a descendant of the worktree dir. Asserted via os.path.commonpath
        (the journal/worktree common ancestor is NOT the worktree itself) — this
        is the assertion that fails if a regression ever resolves the journal
        INSIDE the worktree, at any nesting depth. Structural + real file ops."""
        import os
        tmp_path, session_dir = live_env
        append_event(_artifact_event("prepare"))
        journal = _journal_file(tmp_path).resolve()
        sessions_root = (tmp_path / ".claude" / "pact-sessions").resolve()
        worktree_dir = (tmp_path / "worktrees" / "feature-x").resolve()
        worktree_dir.mkdir(parents=True, exist_ok=True)

        # Path-relationship invariant: the journal is NOT under the worktree.
        # commonpath(journal, worktree) == worktree IFF journal is inside the
        # worktree — so the journal is outside exactly when it is NOT the
        # worktree. (Equivalent not-descendant form: no startswith of the
        # worktree dir + os.sep.)
        common = os.path.commonpath([str(journal), str(worktree_dir)])
        assert common != str(worktree_dir), (
            f"journal {journal} must NOT be a descendant of worktree "
            f"{worktree_dir} (commonpath={common})"
        )
        assert not str(journal).startswith(str(worktree_dir) + os.sep)
        # And it IS under the (worktree-independent) sessions root.
        assert os.path.commonpath([str(journal), str(sessions_root)]) == str(sessions_root)


# =============================================================================
# P2-8 EDGES — missing artifact, malformed handoff coexistence, signal-task
#       independence.
# =============================================================================
class TestEdges:
    def test_missing_artifact_file_pointer_still_resolves(self, live_env):
        """The event resolves even when the pointed-at file is already gone (the
        accepted abnormal-teardown edge). The RESOLVER sees the pointer; the
        read-off-disk step is where the gap is noted + degrades to HANDOFF-only.
        This pins that the event layer is independent of file existence."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare", paths=["/abs/gone/missing.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        assert not Path(events[0]["paths"][0]).exists()  # file gone; pointer survives


# =============================================================================
# REMEDIATION REGRESSIONS — each must FAIL against the pre-fix code (non-vacuity)
# and PASS once the corresponding remediation fix lands.
# =============================================================================

class TestF1NonDictLineDoesNotPoisonWholeFile:
    """F1 regression (review FUTURE-1, promoted to in-PR fix): a single
    valid-JSON-but-NON-DICT journal line ('[1,2,3]', '"str"', 'null') must NOT
    drop the WHOLE file's events.

    PRE-FIX: _read_events_at calls event.get('type') on every parsed line; a
    non-dict parses fine, then .get() raises AttributeError, which escapes the
    per-line (JSONDecodeError, ValueError) catch and propagates to the outer
    except Exception → the entire file returns []. So a single non-dict line
    silently drops ALL artifact_paths events AND ALL HANDOFFs at harvest — the
    exact #927 silent-total-loss failure class.

    POST-FIX (devops isinstance(event, dict) guard in _read_events_at): the
    non-dict line is skipped per-line and the valid events survive.

    This test FAILS pre-fix (returns 0) and PASSES post-fix (returns the valid
    events) — non-vacuous by construction.
    """

    @pytest.mark.parametrize("bad_line", ["[1,2,3]",])
    def test_nondict_line_skipped_valid_events_survive(self, live_env, bad_line):
        tmp_path, session_dir = live_env
        # Append two valid events via the real writer so the journal exists.
        append_event(_artifact_event("prepare", paths=["/abs/p.md"]))
        append_event(_artifact_event("architect", paths=["/abs/a.md"]))
        # Inject a valid-JSON-but-non-dict line BETWEEN them on disk (real file).
        journal = _journal_file(tmp_path)
        lines = journal.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        journal.write_text(
            lines[0] + "\n" + bad_line + "\n" + lines[1] + "\n", encoding="utf-8"
        )
        # The two valid artifact_paths events MUST still be returned (not 0).
        events = read_events_from(session_dir, "artifact_paths")
        by_wf = {e.get("workflow"): e.get("paths") for e in events
                 if isinstance(e, dict)}
        assert by_wf == {"prepare": ["/abs/p.md"], "architect": ["/abs/a.md"]}, (
            f"a non-dict line ({bad_line!r}) must not poison the whole file; "
            f"got {events!r}"
        )


class TestF1SiblingScanPathLastEventSurvivesNonDictLine:
    """F1 sibling (the SECOND read path devops hardened in the same fix): the
    REVERSE-scan path `_scan_lines_for_event` — reached via `read_last_event_from`
    — must ALSO survive a non-dict line. F1 above covers the FORWARD read
    (`read_events_from` → `_read_events_at`); this covers the reverse read.

    PRE-FIX: the reverse scan calls `event.get(...)` on each parsed line; a
    non-dict line raises AttributeError, escapes the per-line
    (JSONDecodeError, ValueError) catch, propagates to the outer except, and the
    scan returns None — so `read_last_event_from` reports the target event as
    'not found' even though it is present. Distinct, concrete consequence (per
    the production docstring): `session_end` would conclude the session was
    NEVER paused.

    POST-FIX (devops `isinstance(event, dict)` guard in `_scan_lines_for_event`,
    parity with `_read_events_at`): the non-dict line is skipped and the target
    event is found.

    Non-vacuous: FAILS pre-fix (None), PASSES post-fix. Counter-test: revert
    session_journal.py → 4 RED (same fix file as the forward-read F1).
    """

    @pytest.mark.parametrize("bad_line", ["[1,2,3]",])
    def test_nondict_line_does_not_hide_last_event(self, live_env, bad_line):
        tmp_path, session_dir = live_env
        # A target artifact_paths event, then a non-dict line AFTER it on disk —
        # so the REVERSE scan hits the bad line BEFORE reaching the target.
        append_event(_artifact_event("prepare", paths=["/abs/p.md"]))
        journal = _journal_file(tmp_path)
        lines = journal.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        journal.write_text(lines[0] + "\n" + bad_line + "\n", encoding="utf-8")
        # The reverse scan must skip the trailing non-dict line and find the
        # target — not return None.
        ev = read_last_event_from(session_dir, "artifact_paths")
        assert ev is not None, (
            f"a trailing non-dict line ({bad_line!r}) must not hide the last "
            f"event in the reverse scan (read_last_event_from returned None)"
        )
        assert ev.get("paths") == ["/abs/p.md"]


class TestB1SessionDirReconstructSanitizationParity:
    """B1 regression: the harvest's session-dir reconstruction MUST apply the
    SAME path sanitization the writer applies, on BOTH axes (project-basename
    slug + session_id), so the reconstructed path equals the writer's stored
    path. Otherwise the harvest looks in the wrong directory and silently finds
    no journal.

    The writer (_build_session_path) runs the slug through
    _UNSAFE_SLUG_CHARS_RE (e.g. 'my.project' → 'my_project'). The PRE-FIX
    harvest reconstruction was a RAW join (Path(project_dir).name joined as-is),
    so a DOTTED basename diverged → path mismatch → recovery miss. The clean-slug
    suite cannot catch this because a no-special-char slug sanitizes to itself.

    POST-FIX (devops reconstruct_session_dir helper): reconstruction applies the
    same sanitization → paths match.

    Probe-confirmed divergence: writer 'my_project' vs raw-join 'my.project'.
    """

    DOTTED_PROJECT = "/Users/alice/my.project"
    # session_id carrying a non-allowlist '.' — exercises the SECOND
    # sanitization axis (the writer's init() sanitizes session_id too).
    SESSION_ID = "abc.def-123"

    def _writer_effective_path(self):
        """The path the REAL writer actually writes the journal to.

        ORACLE FIDELITY (load-bearing): the writer is init(), which sanitizes
        BOTH the slug AND the session_id via _UNSAFE_SLUG_CHARS_RE BEFORE
        calling _build_session_path. So the effective on-disk path uses the
        SANITIZED session_id, not the raw one. Building the oracle from
        _build_session_path(slug, RAW_session_id) would be a WRONG oracle (it
        skips init's session_id sanitization) and would produce a false
        mismatch — the parity bug is on the SLUG axis, and the helper must
        mirror init on BOTH axes. We reproduce init's exact construction here.
        """
        import shared.pact_context as pc
        from pathlib import Path
        slug = Path(self.DOTTED_PROJECT).name
        safe_slug = pc._UNSAFE_SLUG_CHARS_RE.sub("_", slug)
        safe_sid = pc._UNSAFE_SLUG_CHARS_RE.sub("_", self.SESSION_ID)
        return str(pc._build_session_path(safe_slug, safe_sid))

    def test_raw_join_diverges_from_writer_path_documents_the_bug(self):
        """Characterize the pre-fix bug directly: a RAW join over a dotted
        basename (Path(project_dir).name joined as-is, no sanitization) does NOT
        match the writer's sanitized path. This is the demonstration that the
        pre-fix harvest raw-join missed the journal; the parity test below is the
        regression guard that the helper fixes it."""
        import shared.paths as paths
        from pathlib import Path
        slug = Path(self.DOTTED_PROJECT).name  # 'my.project' — keeps the dot
        raw = str(paths.get_claude_config_dir()
                  / "pact-sessions" / slug / self.SESSION_ID)
        assert raw != self._writer_effective_path(), (
            "expected the raw-join ('my.project') to diverge from the writer's "
            "sanitized path ('my_project') on a dotted basename — if equal, the "
            "bug premise is gone"
        )

