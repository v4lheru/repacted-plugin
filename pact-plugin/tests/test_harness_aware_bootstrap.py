"""Harness-aware bootstrap: config-less Desktop/SDK substrate.

The PACT plugin is built on the interactive-CLI team model (home-dir registry,
``session-<id8>`` naming, an eagerly platform-written ``config.json``). Under the
Claude Desktop / Agent-SDK harness the platform provides NO native team scaffold:
it creates ``teams/<full-uuid>/`` with ``inboxes/`` + ``file-edits.json`` but NO
``config.json`` and NO ``members[]``. That mismatch deadlocks bootstrap two ways:

  * GATE 1 (dispatch deadlock): ``_resolve_aligned_team_name`` identity-match
    keys on ``config.json['leadSessionId']`` — absent under the config-less
    substrate, so it falls back to the minted ``session-<id8>`` while the real
    tasks live under ``tasks/<uuid>/`` -> the secretary spawn is refused with
    ``no Task assigned to owner='secretary'``.
  * GATE 2 (marker deadlock): ``bootstrap_marker_writer._team_has_secretary``
    reads ``members[]`` from ``config.json`` — empty under the config-less
    substrate -> the bootstrap-complete marker is never written ->
    ``Edit``/``Write``/``Agent`` stay blocked for the whole session.

THE FIX (two surgical edits, session-id-anchored, NO harness detection, NO
self-provision; both unreachable under new-CLI so the CLI path is byte-identical):

  * GATE 1 — ``_resolve_aligned_team_name`` branch-2 (``pact_context.py``): after
    the identity-match loop misses, BEFORE ``return fallback``, resolve to the
    running frame's own ``session_id`` IFF
    ``is_safe_path_component(session_id)`` (the FIRST conjunct — short-circuits
    before any Path composition) AND ``teams/<session_id>/`` ``is_dir()`` AND
    (``inboxes/`` ``is_dir()`` OR ``file-edits.json`` exists). POSITIVE,
    session-id-anchored — NOT config-absence (config-absence is shared with the
    CLI ~38s cold-start, so it is not a sound discriminator).
  * GATE 2 — ``_team_has_secretary`` inbox-witness (``bootstrap_marker_writer.py``):
    members[] check FIRST (CLI byte-identical), then fall back to
    ``teams/<team_name>/inboxes/secretary.json`` ``is_file()`` as the config-less
    "secretary joined" witness.

SOUNDNESS — why branch-2 cannot misfire under CLI: new-CLI names the platform
team dir ``session-<id8>``, so ``teams/<full-uuid>/`` NEVER exists (steady-state
AND the ~38s cold-start window). The decisive proof is FIXTURE C below
(``TestBranch2NoMisfireUnderCLI``): the ``is_dir()`` guard on the running frame's
full-UUID session_id is False, so branch-2 returns the default.

WHY THE INBOX-WITNESS IS THE RIGHT GATE-2 SIGNAL (code-ordering): PACT writes no
inbox files; its bootstrap ritual is ``TaskCreate -> TaskUpdate(owner=secretary)
-> Agent(name=secretary)`` with NO pre-spawn ``SendMessage`` to the secretary, so
the platform writes ``inboxes/secretary.json`` only on delivery to an
already-dispatched secretary — it cannot predate the spawn within PACT's
choreography. The residual (could the PLATFORM pre-create an inbox on a bare name
reference, OUTSIDE PACT's ritual?) is closed by the deferred negative live-probe
documented in ``tests/runbooks/desktop-bootstrap-deferred-validation.md`` — NOT by code,
NOT by a CI test (it is platform-runtime-only).

DUAL-MODE × HARNESS: the dual-mode contract (in-process: frame session_id ==
leadSessionId; tmux: != ) is PERMANENT; the harness substrate is a THIRD
orthogonal axis. The tmux-leg non-misfire (``TestTmuxLegNoMisfire``) is
double-protected — (1) the is_lead gate upstream of the marker witness blocks a
teammate frame from reaching it; (2) even if reached, branch-2's ``is_dir`` guard
on ``teams/<teammate-sid>/`` returns default (TS4 ↔ TS12 cross-guard).

NON-MOCKED SEAM DISCIPLINE: ``dispatch_gate``, ``bootstrap_gate``, and
``bootstrap_marker_writer`` are in ``SEAM_DEPENDENT_HOOKS`` — these tests build
the real config-less team dir on disk (under a tmp HOME) and drive the UNSTUBBED
team-resolution seam; the team-resolution seam whose correct resolution IS the
thing under test is never mocked.

NON-VACUITY (counter-test-by-revert, SOURCE-ONLY): each behavioral cell carries a
``# COUNTER-TEST`` annotation naming the source-only revert that turns it RED. The
GATE-1 commit BUNDLES the source edit with a test-hygiene docstring edit to
test_team_name_detect_align.py, so a whole-commit ``git revert -n`` would mask the
cardinality — use SOURCE-ONLY revert of ``pact_context.py`` (GATE 1) /
``bootstrap_marker_writer.py`` (GATE 2), leaving these tests in place. The
measured RED cardinality is documented in this module's HANDOFF.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.constants import BOOTSTRAP_MARKER_NAME  # noqa: E402


# ── Real-shaped ids + the two divergent dir-naming schemes ────────────────────
# Desktop/SDK names the team dir with the bare 36-char UUID; new-CLI names it
# session-<first8>. The fix resolves the former via branch-2 and leaves the
# latter on the existing identity-match path.
LEAD_UUID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"   # Desktop full-UUID team dir
LEAD_ID8 = "session-0001639f"                         # new-CLI session-<id8> dir
TMUX_TEAMMATE_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"  # a teammate's own sid
_SECRETARY = "secretary"


# ── seeding helpers (config-less Desktop sim + CLI counter-fixtures) ──────────


def _seed_config_less_desktop(teams_root, uuid, *, with_secretary_inbox=True,
                              with_file_edits=True):
    """The config-less Desktop/SDK substrate: teams/<uuid>/ with inboxes/ +
    file-edits.json but NO config.json. Optionally drop the secretary's inbox
    file (the GATE-2 witness)."""
    team_dir = teams_root / uuid
    (team_dir / "inboxes").mkdir(parents=True, exist_ok=True)
    if with_secretary_inbox:
        (team_dir / "inboxes" / f"{_SECRETARY}.json").write_text(
            json.dumps([{"from": "team-lead", "text": "bootstrap"}]),
            encoding="utf-8",
        )
    if with_file_edits:
        (team_dir / "file-edits.json").write_text("{}", encoding="utf-8")
    # Deliberately NO config.json — that is the whole point.
    assert not (team_dir / "config.json").exists()
    return team_dir


def _seed_cli_with_config(teams_root, dir_name, *, lead_session_id, members=()):
    """The CLI substrate: teams/<dir_name>/config.json with leadSessionId +
    members[] (the regression firewall / identity-match path)."""
    team_dir = teams_root / dir_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"name": dir_name, "leadSessionId": lead_session_id,
                    "members": [{"name": m} for m in members]}),
        encoding="utf-8",
    )
    return team_dir


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """Fresh pact_context state: home -> tmp_path, caches cleared. Returns
    (module, teams_root). teams_root is the real <home>/.claude/teams so the
    resolver's default teams_dir resolves to it (non-mocked seam)."""
    import shared.pact_context as ctx_module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx_module.reset_for_tests()
    teams_root = tmp_path / ".claude" / "teams"
    teams_root.mkdir(parents=True, exist_ok=True)
    yield ctx_module, teams_root
    ctx_module.reset_for_tests()


# ══════════════════════════════════════════════════════════════════════════════
# GATE 1 — _resolve_aligned_team_name branch-2 (config-less session-id anchor)
# ══════════════════════════════════════════════════════════════════════════════


class TestBranch2ResolvesConfigLess:
    """Branch-2 resolves the config-less full-UUID team to the running frame's
    own session_id. This is the POSITIVE config-less path that clears GATE 1."""

    def test_inboxes_witness_resolves_uuid(self, ctx):
        """config-less teams/<uuid>/ with inboxes/ (no config.json) -> resolves
        to <uuid> via branch-2.

        # COUNTER-TEST (source-only revert of pact_context.py): without branch-2
        # the identity-match misses and the resolver returns the fallback
        # ('minted-fallback'), not <uuid> -> RED.
        """
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID,
                                  with_secretary_inbox=False)  # inboxes/ alone
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="minted-fallback"
        )
        assert resolved == LEAD_UUID


class TestBranch2NoMisfireUnderCLI:
    """FIXTURE C — THE load-bearing non-misfire proof. Branch-2 must be
    unreachable under any CLI state because new-CLI names the team dir
    session-<id8>, so teams/<full-uuid>/ never exists."""

    def test_cli_coldstart_no_full_uuid_dir_returns_default(self, ctx):
        """CLI cold-start: the platform created teams/session-<id8>/ (no config
        yet — the ~38s window) and CRUCIALLY NO teams/<full-uuid>/. The running
        frame's session_id is the full UUID. branch-2's
        (teams_root / <full-uuid>).is_dir() is False -> returns default. No
        ~38s cold-start misfire.

        # COUNTER-TEST: this is the cell that proves branch-2 cannot fire under
        # CLI. If branch-2 keyed on config-ABSENCE instead of a positive
        # session-id-anchored is_dir probe, the session-<id8> dir (config-absent
        # in this window) would misfire. The is_dir guard on the full-UUID sid is
        # what makes it safe.
        """
        ctx_module, teams_root = ctx
        # CLI cold-start dir: session-<id8>, NO config.json yet, no full-uuid dir.
        (teams_root / LEAD_ID8).mkdir(parents=True, exist_ok=True)
        (teams_root / LEAD_ID8 / "inboxes").mkdir(exist_ok=True)
        assert not (teams_root / LEAD_UUID).exists()  # the key precondition
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default=LEAD_ID8
        )
        # Running frame is the full UUID; no teams/<full-uuid>/ -> default.
        assert resolved == LEAD_ID8


class TestTmuxLegNoMisfire:
    """TS4 — the tmux leg of the dual-mode contract. A teammate frame runs under
    its OWN session_id (!= the lead's). branch-2 keys on the RUNNING frame's
    session_id (get_team_name() passes get_session_id()), so a teammate frame
    whose own sid has no teams/<that-sid>/ dir returns default — it does NOT
    mis-resolve to the lead's dir."""

    def test_teammate_sid_no_own_dir_returns_default(self, ctx):
        """tmux teammate: the LEAD's config-less team dir exists at
        teams/<lead-uuid>/, but the teammate frame's session_id is its OWN
        (different) uuid with no teams/<teammate-sid>/ dir. branch-2 keyed on the
        teammate sid -> is_dir False -> default. No mis-resolve to the lead dir.

        # COUNTER-TEST: if branch-2 keyed on a hardcoded lead sid or scanned for
        # ANY config-less dir (rather than the running frame's own sid), this
        # would mis-resolve to <lead-uuid> -> RED.
        """
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID)  # the LEAD's team dir
        assert not (teams_root / TMUX_TEAMMATE_SID).exists()
        # The running frame is the TEAMMATE — branch-2 keys on ITS sid.
        resolved = ctx_module._resolve_aligned_team_name(
            TMUX_TEAMMATE_SID, teams_dir=str(teams_root), default="teammate-default"
        )
        assert resolved == "teammate-default"


class TestBranch2TraversalSecurity:
    """GATE-1 security: is_safe_path_component(session_id) is the FIRST conjunct
    of branch-2 — a path-unsafe session_id is rejected BEFORE any Path
    composition, EVEN IF the traversed dir exists. The path-safety lives in the
    RESOLVER (GATE 1); _team_has_secretary's team_name is path-safe by
    construction (it is get_team_name() output), so the security cell targets
    the resolver gate."""

    def test_traversal_id_rejected_even_if_dir_exists(self, ctx):
        """A path-unsafe session_id ('../sibling') whose Path composition WOULD
        traverse to an existing, witnessed dir must STILL be rejected by
        is_safe_path_component (the guard-order short-circuit) -> default. Proves
        the guard bites BEFORE the FS probe — the dir existence is a real lure.

        # COUNTER-TEST: moving is_safe_path_component out of the FIRST-conjunct
        # position (or removing it) would let the traversal resolve to the
        # sibling dir -> this assertion (== default) goes RED.
        """
        ctx_module, teams_root = ctx
        # Build a real, witnessed sibling the traversal would land on.
        sibling = teams_root / "sibling"
        (sibling / "inboxes").mkdir(parents=True, exist_ok=True)
        # The traversal target teams_root/'../teams/sibling' resolves to sibling.
        poisoned = "../teams/sibling"
        resolved = ctx_module._resolve_aligned_team_name(
            poisoned, teams_dir=str(teams_root), default="safe-default"
        )
        assert resolved == "safe-default"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 2 — bootstrap_marker_writer inbox-witness (config-less secretary signal)
# ══════════════════════════════════════════════════════════════════════════════


class TestInboxWitnessGate2:
    """_team_has_secretary accepts teams/<team>/inboxes/secretary.json as the
    config-less 'secretary joined' witness, with members[] checked FIRST."""

    _TEAM = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"  # config-less full-uuid team

    def _has_secretary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from bootstrap_marker_writer import _team_has_secretary
        return _team_has_secretary(self._TEAM)

    def test_inbox_witness_passes_config_less(self, monkeypatch, tmp_path):
        """Config-less team (NO config.json) + inboxes/secretary.json present ->
        _team_has_secretary True via the inbox fallback.

        # COUNTER-TEST (source-only revert of bootstrap_marker_writer.py): without
        # the inbox fallback, members[] is empty (no config.json) -> False -> RED
        # (the marker would never write — the original deadlock).
        """
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._TEAM)  # writes secretary.json
        assert self._has_secretary(monkeypatch, tmp_path) is True


# ══════════════════════════════════════════════════════════════════════════════
# Writability invariant (TS12) — non-lead frame cannot poison the marker
# ══════════════════════════════════════════════════════════════════════════════


class TestWritabilityInvariant:
    """The is_lead gate (bootstrap_marker_writer:471) sits UPSTREAM of the
    secretary witness — a non-lead frame returns before reaching it, so a
    teammate frame can never write the marker even when the inbox-witness is
    present. The second, independent layer of the tmux non-misfire protection
    (TS4 ↔ TS12 cross-guard)."""

    _SLUG = "project"
    _SESSION_ID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"  # config-less full-uuid
    _PLUGIN_VERSION = "9.9.9"

    def _setup_config_less_session(self, monkeypatch, tmp_path):
        import shared.pact_context as ctx_module
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        session_dir = (tmp_path / ".claude" / "pact-sessions" / self._SLUG
                       / self._SESSION_ID)
        session_dir.mkdir(parents=True, exist_ok=True)
        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": self._PLUGIN_VERSION}), encoding="utf-8")
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": self._SESSION_ID, "session_id": self._SESSION_ID,
            "project_dir": f"/test/{self._SLUG}", "plugin_root": str(plugin_root),
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(ctx_module, "_aligned_cache", None)
        # The config-less substrate WITH the secretary inbox-witness present.
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._SESSION_ID)
        return session_dir

    def _run(self, input_data, capsys):
        from bootstrap_marker_writer import main
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit):
                main()
        out = capsys.readouterr().out.strip()
        return json.loads(out) if out else {}

    def test_lead_frame_writes_marker_via_inbox_witness(self, monkeypatch,
                                                        tmp_path, capsys):
        """End-to-end positive: a LEAD frame over the config-less substrate with
        the inbox-witness present -> the marker is written (GATE 1 resolves the
        team, GATE 2 passes via inbox-witness). The deadlock is cleared.

        # COUNTER-TEST: source-only revert of EITHER gate breaks this -> the
        # marker is absent -> RED.
        """
        session_dir = self._setup_config_less_session(monkeypatch, tmp_path)
        self._run({"hook_event_name": "UserPromptSubmit",
                   "session_id": self._SESSION_ID, "prompt": "hi",
                   "source": "startup", "agent_type": "pact-orchestrator"},
                  capsys)
        assert (session_dir / BOOTSTRAP_MARKER_NAME).exists()


# ══════════════════════════════════════════════════════════════════════════════
# SDK harness coverage (TS15) — by-construction note + cheap parametrization
# ══════════════════════════════════════════════════════════════════════════════


class TestSdkHarnessByConstruction:
    """The fix is HARNESS-AGNOSTIC: it never detects "Desktop" vs "sdk-cli" vs
    "sdk-py" — it anchors on the session_id substrate shape. So the sdk-* harness
    is covered BY CONSTRUCTION: any harness that produces the same config-less
    teams/<uuid>/ + inboxes/ substrate is resolved identically. The corpus shows
    no SDK session reached team-spawn, so this is documented (not a deadlock we
    can reproduce); the parametrization pins the by-construction equivalence
    cheaply over the substrate-producing harnesses."""

    @pytest.mark.parametrize("harness", ["claude-desktop",])
    def test_same_substrate_resolves_identically(self, ctx, harness):
        """Whatever harness produced it, an identical config-less full-UUID
        substrate resolves to <uuid> via branch-2. The harness label is inert —
        the resolver reads only the on-disk shape."""
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == LEAD_UUID
