"""Tests for shared/teammate_mode.py -- effective teammateMode resolution and
the in-process startup-notice emit decision.

Background: the helper mirrors the FILE-READABLE portion of Claude Code's
settings precedence to answer "what is the effective teammateMode?" from a hook
subprocess (which cannot see the live `--teammate-mode` CLI override). It is
fail-open by construction -- every public function is total (never raises),
because the sole consumer runs on the SessionStart hot path where an uncaught
exception would break bootstrap.

Core invariant under test: NEVER FALSE-SUPPRESS. A false suppress silently
reinstates the in-process idle-stall the notice exists to warn about; a false
emit is one harmless extra startup line. Every fail-open / fail-safe decision
resolves toward EMIT.

(Accepted breach, NOT tested here because it is unreadable from a hook
subprocess: the in-memory `--teammate-mode` CLI override. It only ever errs
toward over-emitting -- the safe direction. The enterprise managed-settings
layer USED to be an untested blind spot too, but F1 now reads it at the TOP of
the precedence (see TestManagedSettingsPath / TestManagedPrecedence below), so
it is no longer a blind spot: a managed "in-process"/"auto" over a lower-layer
"tmux" now resolves correctly instead of false-suppressing.)

Coverage:

resolve_effective_teammate_mode() + should_emit_inprocess_notice() -- §9.1 matrix:
1.  user settings.json {in-process}                 -> "in-process" / emit
2.  user settings.json {tmux}                        -> "tmux"       / suppress
3.  user settings.json {auto}                         -> "auto"       / emit
4.  no settings files anywhere                        -> "auto"       / emit (default)
5.  user settings.json malformed JSON                 -> "auto"       / emit (skip)
6.  user settings.json {tmux} + ~/.claude.json {in-process}
                                                       -> "tmux"       / suppress (settings>legacy)
7.  only ~/.claude.json {in-process} (no settings)    -> "in-process" / emit (legacy fallback)
8.  project settings.local.json {in-process} OVER user settings.json {tmux}
                                                       -> "in-process" / emit (FALSE-SUPPRESS EDGE)
9.  {teammateMode:"banana"} (unrecognized)            -> "auto"       / emit (value skipped)
10. {teammateMode: 42} (non-string)                   -> "auto"       / emit (type guard skips)

Precedence layering (defense-in-depth beyond the architect's 10):
- project settings.json beats user settings.json
- project settings.local.json beats project settings.json
- empty-string teammateMode is skipped like any unrecognized value

Enterprise managed-settings precedence (F1):
- _managed_settings_path() returns the correct OS-specific absolute literal
- managed-settings is the HIGHEST precedence source (paths[0])
- ** breach-closure regression: managed "in-process" OVER user "tmux"
  resolves "in-process" / EMIT (the false-suppress F1 closes)
- symmetric: managed "tmux" OVER user "in-process" resolves "tmux" / SUPPRESS
  (managed truly drives resolution both ways; closes the OQ1 over-emit)
- managed-absent falls through to lower layers (dev-machine common case)
- malformed managed file fails open (skip -> fall through -> never raises)

VALID_TEAMMATE_MODES contract:
- exact membership + frozenset immutability

Fail-open guarantee (§9.3) -- helper never raises under any read/parse error:
- malformed JSON / non-dict JSON / unreadable (dir-in-place) / wrong-type value
- Path.home() itself raising (outer-guard defense in depth)
"""
import json
import sys
from pathlib import Path

import pytest

import shared.teammate_mode as teammate_mode
from shared.teammate_mode import (
    VALID_TEAMMATE_MODES,
    _managed_settings_path,
    _read_teammate_mode,
    _settings_source_paths,
    resolve_effective_teammate_mode,
    should_emit_inprocess_notice,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _ModeEnv:
    """Filesystem-isolated settings-source writer for teammate_mode tests.

    Pins Path.home() -> tmp_path/home and CLAUDE_PROJECT_DIR -> tmp_path/project
    so the helper reads ONLY the temp tree, never the real machine's settings
    (devops flagged this determinism risk). Exposes one writer per settings
    source so tests construct exactly the precedence they intend.
    """

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.project = tmp_path / "project"
        self.managed = tmp_path / "managed-settings.json"
        self.home.mkdir()
        self.project.mkdir()
        self._monkeypatch = monkeypatch
        monkeypatch.setattr(Path, "home", lambda: self.home)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.project))
        # Default-neutralize the enterprise managed-settings source so a test
        # that does NOT opt into write_managed() does not read the REAL OS
        # managed path as the highest-precedence source[0]. Without this, every
        # resolve/should_emit test silently reads
        # /Library/Application Support/ClaudeCode/managed-settings.json (or the
        # OS equivalent): green on a dev machine only because that file is
        # absent, but non-deterministic on a managed fleet -- exactly the
        # environment the managed-settings precedence targets. Mirrors the
        # Path.home() / CLAUDE_PROJECT_DIR isolation above. Tests that WANT a
        # managed file still opt in explicitly via write_managed(); the
        # managed-absent case opts in via point_managed_at_absent().
        self._default_absent_managed = tmp_path / "default-absent-managed-settings.json"
        monkeypatch.setattr(
            teammate_mode, "_managed_settings_path",
            lambda: self._default_absent_managed,
        )

    @staticmethod
    def _write(path: Path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        # dict -> JSON; str written raw (for malformed-JSON / non-dict cases)
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
        return path

    # Settings sources, highest precedence first.
    def write_project_local(self, content):
        return self._write(self.project / ".claude" / "settings.local.json", content)

    def write_project(self, content):
        return self._write(self.project / ".claude" / "settings.json", content)

    def write_user(self, content):
        return self._write(self.home / ".claude" / "settings.json", content)

    def write_legacy(self, content):
        return self._write(self.home / ".claude.json", content)

    # Enterprise managed-settings (F1) -- highest precedence. Isolation seam:
    # monkeypatch teammate_mode._managed_settings_path so the test owns the
    # highest source WITHOUT touching a real OS managed-settings path.
    def write_managed(self, content):
        """Write a managed-settings.json under tmp and point the helper at it."""
        self._write(self.managed, content)
        self._monkeypatch.setattr(
            teammate_mode, "_managed_settings_path", lambda: self.managed
        )
        return self.managed

    def point_managed_at_absent(self):
        """Point _managed_settings_path at an absent tmp file (managed-absent case).

        Deterministic stand-in for the dev-machine common case where the real
        OS managed path does not exist -- avoids depending on the host actually
        lacking /Library/Application Support/ClaudeCode/managed-settings.json.
        """
        absent = self.root / "absent-managed-settings.json"
        self._monkeypatch.setattr(
            teammate_mode, "_managed_settings_path", lambda: absent
        )
        return absent


@pytest.fixture
def mode_env(tmp_path, monkeypatch):
    """Isolated settings tree for resolve/should_emit tests."""
    return _ModeEnv(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# §9.1 matrix: resolve_effective_teammate_mode + should_emit_inprocess_notice
# ---------------------------------------------------------------------------

class TestResolveAndEmitMatrix:
    """The architect's §9.1 matrix: resolution value AND the emit decision.

    should_emit is asserted alongside resolve in every row because the emit
    decision is the load-bearing output (#864). Testing them together pins the
    'never false-suppress' invariant at the boundary the safeguard defends.
    """

    def test_user_in_process_emits(self, mode_env):
        """Row 1: user settings in-process -> resolve in-process, EMIT."""
        mode_env.write_user({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_user_tmux_suppresses(self, mode_env):
        """Row 2: user settings tmux -> resolve tmux, SUPPRESS (the only suppress case)."""
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False


# ---------------------------------------------------------------------------
# Precedence layering -- defense-in-depth beyond the architect's 10 rows
# ---------------------------------------------------------------------------

class TestPrecedenceLayering:
    """Each higher-priority source must shadow the lower one.

    Row 8 pins layer-1 (project-local) over layer-3 (user). These pin the
    intermediate edges so a future re-ordering of any single layer fails loudly.
    """

    def test_project_settings_beats_user_settings(self, mode_env):
        """project settings.json (layer 2) outranks user settings.json (layer 3)."""
        mode_env.write_project({"teammateMode": "in-process"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True

    def test_project_local_beats_project_settings(self, mode_env):
        """project settings.local.json (layer 1) outranks project settings.json (layer 2)."""
        mode_env.write_project_local({"teammateMode": "tmux"})
        mode_env.write_project({"teammateMode": "in-process"})
        assert resolve_effective_teammate_mode() == "tmux"
        assert should_emit_inprocess_notice() is False


# ---------------------------------------------------------------------------
# VALID_TEAMMATE_MODES contract
# ---------------------------------------------------------------------------

class TestValidModesContract:
    """Pin the allowed value set and its immutability.

    If Claude Code adds/renames a teammateMode value, this test is the canary:
    the helper resolves unknown values to 'auto' (safe / emit), so a schema
    drift degrades gracefully, but the pinned set documents the known contract.
    """

    def test_valid_modes_exact_membership(self):
        assert VALID_TEAMMATE_MODES == frozenset({"auto", "tmux", "in-process"})

    def test_valid_modes_is_frozenset(self):
        """Immutable so it cannot be mutated by an importer at runtime."""
        assert isinstance(VALID_TEAMMATE_MODES, frozenset)
        with pytest.raises(AttributeError):
            VALID_TEAMMATE_MODES.add("new-mode")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# §9.3 fail-open guarantee -- the helper is TOTAL (never raises)
# ---------------------------------------------------------------------------

class TestReadTeammateModeFailOpen:
    """Per-file reader contract (§4.1 _read_teammate_mode): any missing /
    unreadable / parse-error / wrong-type / unrecognized condition returns
    None and NEVER raises.
    """

    def test_missing_file_returns_none(self, tmp_path):
        assert _read_teammate_mode(tmp_path / "nope.json") is None

    def test_malformed_json_returns_none(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{ not json", encoding="utf-8")
        assert _read_teammate_mode(p) is None


class TestResolveFailOpen:
    """resolve_effective_teammate_mode + should_emit are TOTAL: any failure
    degrades to 'auto' / EMIT, never an exception out of the hot path.
    """

    def test_unreadable_source_degrades_to_auto_emit(self, mode_env):
        """A settings.json that is a directory (read raises) -> auto / EMIT."""
        (mode_env.home / ".claude").mkdir(parents=True)
        (mode_env.home / ".claude" / "settings.json").mkdir()
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True

    def test_path_home_raising_degrades_to_auto(self, mode_env, monkeypatch):
        """If Path.home() itself raises, the outer guard returns the default.

        _settings_source_paths() calls Path.home(); if that throws, the whole
        source-list construction raises and resolve's outer try/except returns
        _DEFAULT_MODE. Defense-in-depth beyond per-file fail-open.
        """
        def boom():
            raise RuntimeError("home unavailable")

        monkeypatch.setattr(Path, "home", boom)
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# _settings_source_paths -- precedence-order contract
# ---------------------------------------------------------------------------

class TestSettingsSourcePaths:
    """Pin the file-readable precedence order and the CLAUDE_PROJECT_DIR seam.

    F1: the enterprise managed-settings path is prepended as the HIGHEST
    precedence source (paths[0]); the file-readable list is now 4 entries:
    managed -> project-local -> project -> user.
    """

    def test_paths_in_precedence_order(self, mode_env):
        """4 paths, managed highest. Asserts against the SAME managed-path
        getter the resolver uses (same-function compare) so it is
        platform-agnostic -- no hardcoded OS literal in the order assertion.

        Reads teammate_mode._managed_settings_path (the MODULE attribute) rather
        than the from-imported name: the mode_env fixture default-neutralizes
        the module attribute (see _ModeEnv.__init__), and _settings_source_paths
        resolves the getter through the module namespace at call time. Comparing
        against the module attribute keeps both sides referring to the same
        (neutralized) getter; the bare from-imported name would still point at
        the original real-OS getter and spuriously mismatch.
        """
        paths = _settings_source_paths()
        assert paths == [
            teammate_mode._managed_settings_path(),
            mode_env.project / ".claude" / "settings.local.json",
            mode_env.project / ".claude" / "settings.json",
            mode_env.home / ".claude" / "settings.json",
        ]


# ---------------------------------------------------------------------------
# Enterprise managed-settings (F1): OS-specific path + highest-precedence reads
# ---------------------------------------------------------------------------

class TestManagedSettingsPath:
    """_managed_settings_path() returns the correct OS-specific absolute literal.

    Pure path construction -- no I/O, no Path.home()/expanduser -- so it does
    not perturb the home-monkeypatch seam. The literals are verified live
    against Claude Code 2.1.156 (architect OQ1 / F1).
    """

    @pytest.mark.parametrize(
        "platform,expected",
        [
            ("darwin", Path("/Library/Application Support/ClaudeCode/managed-settings.json")),],
    )
    def test_managed_path_per_os(self, monkeypatch, platform, expected):
        """Each platform maps to its documented absolute managed-settings path."""
        monkeypatch.setattr(sys, "platform", platform)
        assert _managed_settings_path() == expected


class TestManagedPrecedence:
    """Managed-settings is the highest-precedence file source (F1).

    Isolation: mode_env.write_managed()/point_managed_at_absent() monkeypatch
    teammate_mode._managed_settings_path to a tmp file the test owns, so these
    NEVER read or write a real OS managed-settings path.
    """

    def test_managed_in_process_over_user_tmux_breach_closed(self, mode_env):
        """** MANAGED-SETTINGS BREACH-CLOSURE REGRESSION (F1). **

        managed-settings 'in-process' layered OVER a user 'tmux' MUST resolve
        'in-process' / EMIT. This is the exact false-suppress F1 closes: before
        F1 the helper never read managed-settings, so a managed fleet pinning
        'in-process' over a user 'tmux' was wrongly SUPPRESSED (the
        #864-reinstating direction). Managed now sits at precedence [0], read
        first -> 'in-process' -> EMIT.

        If this ever inverts (resolve 'tmux' / suppress), the managed layer has
        stopped being read at the top of the precedence and the breach is open
        again.
        """
        mode_env.write_managed({"teammateMode": "in-process"})
        mode_env.write_user({"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "in-process"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# Anti-normalization regression guard (documentation-in-code)
# ---------------------------------------------------------------------------

class TestAntiNormalizationVariants:
    """Pin case-variant and whitespace-padded values to EMIT -- on purpose.

    The helper does an EXACT check: a value is recognized only if it is
    byte-identical to a member of VALID_TEAMMATE_MODES, and should_emit
    suppresses only on resolved == "tmux". So every case-variant ("Tmux",
    "TMUX") and whitespace-padded (" tmux", "tmux ") value is NOT recognized
    -> the source is skipped -> resolution falls through to "auto" -> the
    notice EMITS. That EMIT is the fail-open-SAFE direction: a tmux user who
    typed "Tmux" gets one harmless extra startup line.

    These tests exist to FAIL LOUDLY if a future "be lenient" refactor adds
    case/whitespace normalization (e.g. `value.strip().lower() in
    VALID_TEAMMATE_MODES`) to _read_teammate_mode. Such a refactor would
    silently flip these variants to the SUPPRESS direction -- a FALSE-SUPPRESS,
    the exact #864-reinstating outcome the core invariant forbids -- while
    passing every other test in this file. The test IS the guard: do NOT
    "fix" these to suppress.
    """

    @pytest.mark.parametrize("variant", ["Tmux",])
    def test_case_variant_emits(self, mode_env, variant):
        """A case-variant of 'tmux' is not byte-equal -> skipped -> auto -> EMIT.

        Failure here (resolve 'tmux' / suppress) means someone added
        case-normalization that introduced a false-suppress. See class docstring.
        """
        mode_env.write_user({"teammateMode": variant})
        assert resolve_effective_teammate_mode() == "auto"
        assert should_emit_inprocess_notice() is True


# ---------------------------------------------------------------------------
# Broad-except CONTRACT: the breadth of the per-file handler is load-bearing
# ---------------------------------------------------------------------------

class TestPerFileBroadExceptContract:
    """Pin the BREADTH of _read_teammate_mode's `except Exception` -- not just
    that it catches *something*.

    The per-file reader wraps its body in a broad `except Exception` so ANY
    read/parse failure degrades to None (fail-open). The existing fail-open
    tests only exercise exceptions a NARROW tuple would STILL catch:
    IsADirectoryError / PermissionError (OSError) and malformed JSON
    (json.JSONDecodeError). None of them would fail if someone "tightened" the
    handler to `except (OSError, json.JSONDecodeError)`.

    This closes that gap with an input whose exception is OUTSIDE that narrow
    tuple: a file whose bytes are not valid UTF-8. read_text(encoding="utf-8")
    then raises UnicodeDecodeError -- a subclass of ValueError, and NOT a
    subclass of OSError or json.JSONDecodeError (verified on CPython 3.14).
    Only the broad `except Exception` catches it; a narrowed handler would let
    it propagate and FAIL test_invalid_utf8_returns_none_via_broad_except --
    which is exactly the regression signal we want.

    NOTE: an embedded-NUL path is deliberately NOT used here. On CPython 3.12+
    Path.exists() returns False for a NUL path (it does not raise), so
    _read_teammate_mode short-circuits at its `if not path.exists()` guard
    WITHOUT entering the try/except body -- a NUL test would be phantom-green
    (it passes whether or not the except is narrowed). Invalid UTF-8 is the
    live discriminator on this interpreter.
    """

    def test_invalid_utf8_returns_none_via_broad_except(self, tmp_path):
        """Invalid-UTF-8 bytes -> UnicodeDecodeError (ValueError, not OSError /
        JSONDecodeError) -> only the broad except returns None. THE discriminator.
        """
        p = tmp_path / "settings.json"
        # Lone 0xFF/0xFE bytes inside an otherwise-JSON document: read_text
        # with strict utf-8 raises UnicodeDecodeError before json.loads runs.
        p.write_bytes(b'{"teammateMode": "\xff\xfetmux"}')
        assert _read_teammate_mode(p) is None


class TestConfigDirSettingsAndLegacy:
    """C6 (#926): the user settings.json source (:120) and the legacy
    ~/.claude.json fallback (:147) follow CLAUDE_CONFIG_DIR. The legacy file is
    a SIBLING-asymmetry special case: $HOME/.claude.json when unset (NOT
    $HOME/.claude/.claude.json), $CONFIG_DIR/.claude.json when set."""

    def test_user_settings_follows_config_dir(self, mode_env, monkeypatch):
        # :120 — env-set: the user settings source is $CONFIG/settings.json.
        # NON-VACUOUS: if it still read $HOME/.claude/settings.json, the file
        # below wouldn't be found and resolution would fall through to "auto".
        config_dir = mode_env.root / "config-kimi"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        mode_env._write(config_dir / "settings.json", {"teammateMode": "tmux"})
        assert resolve_effective_teammate_mode() == "tmux"


