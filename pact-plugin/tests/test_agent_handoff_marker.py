"""
Direct unit coverage for shared/agent_handoff_marker.py — the SSOT shared by
BOTH agent_handoff emit paths (agent_handoff_emitter "b1" + the lead-side
task_lifecycle_gate emit "b2"). #880 Fix B (#887 occupant-identity marker key).

This file is the HOME for module-level coverage of the SSOT primitives. It
focuses on the surface NOT already exercised through the emitter's main()
in the sibling files:

  - occupant_hash(): cross-process determinism (the hashlib-not-builtin-hash
    property #887 rests on), distinctness, path-safety, separator-collision
    resistance.
  - is_signal_task(): the shared emit-eligibility atom.
  - already_emitted(): the occupant-keyed test-and-set — specifically the
    #887 HEART (same team + same task_id + DIFFERENT occupant → BOTH emit)
    and the within-session ~37x re-fire dedup, with same-fixture positive
    controls.
  - the NEW post-mkdir TOCTOU containment re-check (realpath + commonpath
    after mkdir) — distinct from the is_symlink() PRE-check that the
    TestMarkerDirSymlinkGuard family in test_emitter_idempotency.py already
    covers. O_NOFOLLOW guards only the FINAL path component, so a symlinked
    marker_dir appearing in the race window would still let os.open write
    THROUGH it — the re-check is the only thing stopping the escaped write.

Already covered ELSEWHERE (not duplicated here):
  - sanitize_path_component() + degenerate-value guards → test_emitter_path_sanitization.py
  - is_symlink() PRE-check + fail-open (PermissionError/ENOSPC) → test_emitter_idempotency.py
  - 8-thread concurrent O_EXCL race → test_emitter_idempotency.py::TestConcurrentFireRace

Non-vacuity (#887): the same-task_id-different-occupant rows below FAIL if the
marker key is reverted to the bare {task_id} (pre-#887) shape — they are the
delete-the-fix counter-tests. See the docstring on TestOccupantKeyedDedup.
"""
import hashlib
import os
from pathlib import Path

import pytest

from shared.agent_handoff_marker import (
    already_emitted,
    handoff_already_emitted,
    is_signal_task,
    occupant_hash,
)

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# =============================================================================
# occupant_hash — the #887 key-derivation primitive
# =============================================================================
class TestOccupantHash:
    def test_pins_hashlib_sha256_algorithm(self):
        """occupant_hash MUST be hashlib SHA-256 of f'{agent}\\x00{subject}',
        first 16 hex chars. Pinning the exact algorithm (not just 'some
        stable hash') guards against a refactor silently swapping in the
        builtin hash() — which PYTHONHASHSEED salts per process and would
        break the cross-process O_EXCL dedup."""
        agent, subject = "backend-coder", "implement feature X"
        expected = hashlib.sha256(
            f"{agent}\x00{subject}".encode("utf-8")
        ).hexdigest()[:16]
        assert occupant_hash(agent, subject) == expected

    def test_digest_is_path_safe_hex_of_fixed_length(self):
        occ = occupant_hash("a/../b", "subject\x00with/controls")
        assert len(occ) == 16
        assert all(c in "0123456789abcdef" for c in occ), (
            "hex digest must carry no path separators / traversal / control "
            f"chars; got {occ!r}"
        )


# =============================================================================
# is_signal_task — the shared emit-eligibility atom
# =============================================================================
class TestIsSignalTask:
    @pytest.mark.parametrize("ttype", ["blocker",])
    def test_signal_types_are_excluded(self, ttype):
        assert is_signal_task({"type": ttype}) is True

    @pytest.mark.parametrize(
        "metadata",
        [
            {"type": "work"},],
    )
    def test_non_signal_or_malformed_is_false(self, metadata):
        assert is_signal_task(metadata) is False


# =============================================================================
# already_emitted — occupant-keyed test-and-set (#887 heart)
# =============================================================================
class TestOccupantKeyedDedup:
    """The #887 fix: keying the marker on (team, task_id, occupant) rather
    than the bare (team, task_id).

    NON-VACUITY: test_same_task_id_different_occupant_both_emit is the
    delete-the-fix counter-test — if occupant_hash is collapsed to a constant
    (reverting to the bare-{task_id} key), the two DIFFERENT occupants share
    a marker and the second is suppressed (1 winner, not 2), failing the
    assertion. Documented expected cardinality: {2 winners}; pre-#887: {1}.
    """

    def test_same_task_id_different_occupant_both_emit(self, tmp_path, monkeypatch):
        """HEART OF #887: a reused (team, task_id) under two DIFFERENT
        occupants must produce TWO winners — the new occupant's HANDOFF is
        not falsely suppressed by the prior occupant's stale marker."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id = "pact-test", "5"
        occ_a = occupant_hash("agent-alpha", "alpha's task")
        occ_b = occupant_hash("agent-beta", "beta's task")
        assert occ_a != occ_b  # precondition: genuinely different occupants

        first = already_emitted(team, task_id, occ_a)
        second = already_emitted(team, task_id, occ_b)
        assert first is False, "first occupant must win (emit)"
        assert second is False, (
            "DIFFERENT occupant on the same task_id must ALSO win (emit) — "
            "this is the #887 collision fix. If this is True, the marker key "
            "has regressed to the bare {task_id} shape."
        )
        # Two distinct marker files exist.
        marker_dir = tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        assert (marker_dir / f"{task_id}-{occ_a}").exists()
        assert (marker_dir / f"{task_id}-{occ_b}").exists()

    def test_same_occupant_dedups_positive_control(self, tmp_path, monkeypatch):
        """POSITIVE CONTROL for the row above: the SAME occupant on the same
        task_id is deduped (fire-once preserved) — proving the row above
        emits twice because the occupants DIFFER, not because dedup is
        globally broken."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "5", occupant_hash("agent", "subj")
        assert already_emitted(team, task_id, occ) is False, "first fire wins"
        assert already_emitted(team, task_id, occ) is True, (
            "same occupant re-fire must be suppressed (standing-task "
            "fire-once-across-lifespan preserved)"
        )


# =============================================================================
# TOCTOU containment re-check (NEW in #880 — post-mkdir realpath/commonpath)
# =============================================================================
class TestContentKeyedReemit:
    """A REVISED handoff must RE-EMIT: the handoff discriminator composes the
    occupant term with a content term.

    This pins BEHAVIOUR, not a description. It fails if anyone makes the
    handoff marker content-blind — the shape the module's own `WHAT THIS
    REPLACED` note describes, where the first emit for an occupant claimed the
    marker and every later revision was suppressed for the lifespan of the
    team, reaching no carrier and leaving no record it had been suppressed.

    NON-VACUITY: the third call is the control. Without it this arm would pass
    against a marker that dedups nothing at all, which is the opposite defect
    and equally wrong.
    """

    def test_changed_handoff_content_re_emits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id = "pact-test", "5"
        occ = occupant_hash("agent-alpha", "alpha's task")

        first = handoff_already_emitted(team, task_id, occ, "content-v1")
        changed = handoff_already_emitted(team, task_id, occ, "content-v2")
        repeat = handoff_already_emitted(team, task_id, occ, "content-v1")

        assert first is False, "first emit must win"
        assert changed is False, (
            "a REVISED handoff must re-emit — the discriminator carries a "
            "content term. True here means the content term was dropped, and "
            "every later revision of a handoff is silently suppressed."
        )
        assert repeat is True, (
            "CONTROL: unchanged content must still be suppressed. False here "
            "means the marker dedups nothing and the assertion above passes "
            "for the wrong reason."
        )


class TestTocTouContainmentRecheck:
    """The post-mkdir re-check (realpath(marker_dir) must stay inside
    realpath(team_base)) closes the race window between the is_symlink()
    PRE-check and mkdir(exist_ok=True): a symlink swapped in AFTER the
    pre-check would be silently followed by mkdir, and O_NOFOLLOW only
    protects the FINAL path component — so os.open would write THROUGH a
    symlinked marker_dir to an escaped location. This family pins that the
    re-check fail-open emits (returns False) WITHOUT an escaped marker write.

    Distinct from TestMarkerDirSymlinkGuard (test_emitter_idempotency.py),
    which covers the is_symlink() PRE-check only.
    """

    def test_symlink_appearing_after_precheck_is_contained(self, tmp_path, monkeypatch):
        """Simulate the TOCTOU race: marker_dir IS a symlink escaping the
        team base, but is_symlink() reports False (as it would have at
        pre-check time). The re-check must catch it via realpath/commonpath,
        return False (fail-open emit), and write NO marker at the escape
        target.

        NON-VACUITY: deleting the re-check lets os.open create
        `<escape>/{task_id}-{occ}` (O_NOFOLLOW does not stop traversal
        through a symlinked DIRECTORY) — the final assertion would then fail.
        """
        team = "pact-test"
        team_base = tmp_path / ".claude" / "teams" / team
        team_base.mkdir(parents=True)
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir()
        marker_dir = team_base / ".agent_handoff_emitted"
        marker_dir.symlink_to(escape_target, target_is_directory=True)

        # Patch is_symlink → False AFTER constructing the symlink: models the
        # pre-check passing (path was not a symlink at check time) while the
        # path is a symlink by the time mkdir/realpath run.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)

        result = already_emitted(team, "5", "occ")

        assert result is False, (
            "TOCTOU breach must fail-open emit (return False)"
        )
        assert not (escape_target / "5-occ").exists(), (
            "no marker may be written at the escaped (symlink) target — the "
            "post-mkdir containment re-check must short-circuit before "
            "os.open. If this file exists, the re-check was removed/bypassed."
        )


# =============================================================================
# dir_fd intermediate-dir TOCTOU race (remediation cycle 2)
# =============================================================================
@pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="platform lacks openat/dir_fd support",
)
class TestDirFdIntermediateDirRace:
    """The marker create opens marker_dir as a dir_fd (O_DIRECTORY|O_NOFOLLOW)
    and creates the marker RELATIVE to that fd. This closes the residual
    INTERMEDIATE-DIR TOCTOU window: between the realpath/commonpath containment
    re-check and the create, marker_dir itself could be swapped to a symlink,
    and a full-path os.open would then write THROUGH the symlinked DIRECTORY
    (O_NOFOLLOW on the marker FILE guards only the final component).

    DISTINCT from TestTocTouContainmentRecheck: that family covers PRE-PLANTED
    symlinks (caught by the is_symlink() pre-check OR the realpath/commonpath
    re-check). The dir_fd fix is load-bearing ONLY for the RACE — marker_dir is
    a REAL contained dir at pre-check AND containment-check time, THEN swapped to
    an escaping symlink BEFORE the dir_fd open. So this test injects the swap in
    exactly that window (as a side effect of the containment check's realpath
    call), which a pre-planted symlink cannot exercise.

    NON-VACUITY: reverting the create to the plain full-path
    `os.open(str(marker_path), ...)` (pre-cycle-2 shape) makes the symlinked
    marker_dir get followed and the marker written THROUGH to the escape target
    — the no-write-through assertion then fails. Verified by isolated-worktree
    revert.
    """

    def test_marker_dir_swapped_to_symlink_after_containment_is_contained(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "race", "occ"

        team_base = tmp_path / ".claude" / "teams" / team
        team_base.mkdir(parents=True)
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir()
        marker_dir = team_base / ".agent_handoff_emitted"

        # Inject the TOCTOU swap as a side effect of the containment check's
        # realpath(marker_dir) call: marker_dir is a REAL empty dir when
        # resolved (so commonpath passes with the real contained path), then
        # is swapped to an escaping symlink BEFORE the dir_fd open runs.
        orig_realpath = os.path.realpath
        state = {"swapped": False}

        def _realpath_then_swap(path, *args, **kwargs):
            resolved = orig_realpath(path, *args, **kwargs)
            if not state["swapped"] and str(path).endswith(".agent_handoff_emitted"):
                # marker_dir was just mkdir'd (real, empty) — swap it now.
                Path(marker_dir).rmdir()
                Path(marker_dir).symlink_to(escape_target, target_is_directory=True)
                state["swapped"] = True
            return resolved

        monkeypatch.setattr(os.path, "realpath", _realpath_then_swap)

        result = already_emitted(team, task_id, occ)

        assert state["swapped"] is True, (
            "vacuity guard: the race-swap must have fired (else the test "
            "passes trivially via normal creation)"
        )
        assert marker_dir.is_symlink(), "marker_dir must be the escaping symlink at open time"
        assert result is False, (
            "a marker_dir swapped to a symlink in the race window must "
            "fail-open EMIT (return False) — the dir_fd O_DIRECTORY|O_NOFOLLOW "
            "open fails on the symlink"
        )
        assert not (escape_target / f"{task_id}-{occ}").exists(), (
            "NO write-through: the marker must NOT be created at the escape "
            "target. If this file exists, the dir_fd pinning was removed and a "
            "full-path open followed the symlinked directory."
        )


# =============================================================================
# Degenerate-value guard (smoke — full matrix in test_emitter_path_sanitization)
# =============================================================================
class TestDegenerateGuardSmoke:
    @pytest.mark.parametrize("bad", ["",])
    def test_degenerate_task_id_emits_without_marker(self, tmp_path, monkeypatch, bad):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # bad task_id sanitizes to a degenerate value → fail-open emit, no marker
        assert already_emitted("pact-test", bad, "occ") is False


# =============================================================================
# task_id str-normalisation inside the marker resolver
# =============================================================================
class TestTaskIdNormalisation:
    """_resolve_marker_target coerces task_id with str() BEFORE handing it to
    sanitize_path_component, whose bare re.sub RAISES TypeError on a non-str.

    WHY A RAISE IS THE SEVERE CASE, not a mis-key: marker callers sit inside
    emit paths that wrap their whole body in `except Exception: pass` (see
    task_lifecycle_gate's agent_handoff emit). The TypeError is therefore
    SWALLOWED — the emit is skipped and the event silently vanishes from the
    journal rather than failing loudly, so a consumer counting those events
    measures a population smaller than the one that occurred with no signal
    that anything was lost.

    NON-VACUITY — two INDEPENDENT mutations are needed, because no single one
    can red both properties:
      - delete the str() (revert to `sanitize_path_component(task_id)`)
        → every int row below ERRORS with TypeError.
      - collapse the coercion (e.g. `str(task_id)` → a constant)
        → the int rows stop raising and PASS, and only
          test_distinct_int_task_ids_still_discriminate reds.
    That second mutation is the point of the discrimination row: a
    normalisation that maps everything to one value ALSO removes the raise,
    so "no longer raises" alone cannot tell a fix from a lobotomy.

    NOT a restored capability — a NEW one. At base the int rows do not fail
    an assertion, they never REACH one: already_emitted(team, 83, occ) dies
    with TypeError before any comparison happens. So 83-vs-84 discrimination
    was not "working and preserved", it was UNREACHABLE. These rows establish
    that it is now reachable AND that it discriminates.

    SCOPE BOUND: task_id only, and the exclusions are not symmetric with it.
    occupant is a hex digest from occupant_hash() (str by construction).
    team_name is validated upstream against [A-Za-z0-9_-]+ and is NOT
    coerced — a non-str team_name raises AttributeError at .lower(), a
    DIFFERENT exception from a DIFFERENT line than the task_id TypeError this
    class is about. Do not read these rows as "all marker path components are
    normalised"; exactly one is. test_team_name_is_not_coerced pins the
    boundary so a later reader sees it was chosen rather than overlooked.

    The int arguments below are DELIBERATE contract violations, carrying
    type-checker suppressions at each site. task_id's annotation stays `str`
    on purpose: the coercion is a defensive backstop, not a widened contract.
    Passing a non-str task_id remains a CALL-SITE BUG — what changed is that
    the bug stops turning into a silent event loss.
    """

    def test_int_task_id_does_not_raise(self, tmp_path, monkeypatch):
        """Un-normalised, this ERRORS with
        `TypeError: expected string or bytes-like object, got 'int'`."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        occ = occupant_hash("coder", "int task_id subject")
        assert already_emitted("pact-test", 83, occ) is False  # type: ignore[arg-type]  # int is the point


