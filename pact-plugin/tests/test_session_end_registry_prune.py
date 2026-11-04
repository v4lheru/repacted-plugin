"""Unit tests for session_end's self-registration registry prune
(_prune_registry_dead_teams).

Commit-F coverage: drops registry lines whose @team has no live directory under
~/.claude/teams/, keeps live-team lines, drops malformed/no-@ lines, is
idempotent (no needless rewrite when nothing is stale), and is fail-safe
(missing file / symlink / write race → 0, never raises). Preserves 0o600 on
rewrite.
"""

import inspect
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from session_end import _prune_registry_dead_teams


@pytest.fixture
def prune_env(tmp_path):
    """Build an isolated ~/.claude tree: a teams_dir with caller-chosen live
    teams + a registry file. Returns a helper namespace."""
    teams_dir = tmp_path / ".claude" / "teams"
    teams_dir.mkdir(parents=True)
    reg_path = tmp_path / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    reg_path.parent.mkdir(parents=True)

    class _Env:
        teams = teams_dir
        registry = reg_path

        @staticmethod
        def live_team(name):
            (teams_dir / name).mkdir(parents=True, exist_ok=True)

        @staticmethod
        def write_registry(lines):
            reg_path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")

        @staticmethod
        def line(session_id, value):
            return json.dumps({"session_id": session_id, "value": value})

        @staticmethod
        def prune():
            return _prune_registry_dead_teams(
                registry_path=reg_path, teams_dir=teams_dir
            )

        @staticmethod
        def remaining():
            return reg_path.read_text(encoding="utf-8").splitlines() if reg_path.exists() else []

    return _Env


def test_drops_dead_team_keeps_live_team(prune_env):
    prune_env.live_team("pact-live")
    prune_env.write_registry([
        prune_env.line("s1", "alice@pact-live"),
        prune_env.line("s2", "bob@pact-dead"),  # no dir under teams → dead
    ])
    pruned = prune_env.prune()
    assert pruned == 1
    assert prune_env.remaining() == [prune_env.line("s1", "alice@pact-live")]


def test_drops_malformed_and_no_at_lines(prune_env):
    prune_env.live_team("pact-live")
    prune_env.write_registry([
        prune_env.line("s1", "alice@pact-live"),
        prune_env.line("s2", "no-at-separator"),  # no @ → dropped
        "this-is-not-json",                        # garbage → dropped
    ])
    pruned = prune_env.prune()
    assert pruned == 2
    assert prune_env.remaining() == [prune_env.line("s1", "alice@pact-live")]


def test_unsafe_at_team_is_dropped_not_kept_and_never_raises(prune_env):
    """L1/M1 regression: an @team that is not a single safe path segment must be
    DROPPED — never KEPT via a traversal that happens to resolve to a real dir,
    and never raise.

    NON-VACUITY (lever works on the test runtime, Python 3.12+, where
    ``Path.is_dir()`` returns False on a NUL instead of raising): the ``.`` and
    ``pact-live/..`` cases both resolve to ``teams_dir`` itself, so PRE-fix
    ``(teams_dir / team).is_dir()`` was True and the bogus lines were KEPT
    (the L1 containment hole); POST-fix the segment validator rejects them, so
    they drop. Reverting the validation flips those lines back to KEPT → this
    assertion FAILS. The NUL case additionally guards the Python <=3.11 path
    where ``is_dir()`` propagated ``ValueError`` out of the prune (there the
    revert turns this into an ERROR). NUL is built with ``chr(0)`` so the test
    file holds no literal null byte.
    """
    prune_env.live_team("pact-live")
    nul_team = "pact-" + chr(0) + "evil"
    prune_env.write_registry([
        prune_env.line("s1", "alice@pact-live"),       # valid single segment → kept
        prune_env.line("s2", "bob@."),                 # '.' resolves to teams_dir → pre-fix KEPT; drop
        prune_env.line("s3", "eve@pact-live/.."),      # traversal → teams_dir → pre-fix KEPT; drop
        prune_env.line("s4", "mallory@" + nul_team),   # NUL → drop (guards <=3.11 raise)
    ])
    pruned = prune_env.prune()  # must NOT raise on any supported Python
    assert pruned == 3
    assert prune_env.remaining() == [prune_env.line("s1", "alice@pact-live")]


def test_idempotent_no_rewrite_when_nothing_stale(prune_env):
    prune_env.live_team("pact-live")
    prune_env.write_registry([prune_env.line("s1", "alice@pact-live")])
    mtime_before = prune_env.registry.stat().st_mtime_ns
    pruned = prune_env.prune()
    assert pruned == 0
    # no rewrite happened → mtime unchanged (the "no needless rewrite" guard)
    assert prune_env.registry.stat().st_mtime_ns == mtime_before


def test_all_dead_empties_the_file(prune_env):
    # no live teams written; every line is dead
    prune_env.write_registry([
        prune_env.line("s1", "alice@pact-gone"),
        prune_env.line("s2", "bob@pact-also-gone"),
    ])
    pruned = prune_env.prune()
    assert pruned == 2
    assert prune_env.remaining() == []


def test_missing_file_returns_zero_no_raise(prune_env):
    # registry never written
    assert prune_env.prune() == 0


def _make_root_absent(root):
    pass  # the path is simply never created


def _make_root_plain_file(root):
    root.write_text("not a directory", encoding="utf-8")


def _make_root_dangling_symlink(root):
    root.symlink_to(root.parent / "nonexistent-target")


def _make_root_unreadable(root):
    # A root with live teams INSIDE it, which the process cannot enumerate.
    root.mkdir()
    (root / "pact-live").mkdir()
    (root / "pact-also-live").mkdir()
    root.chmod(0o000)


# --------------------------------------------------------------------------
# Arms that separate a guarantee from its NEIGHBOUR.
#
# The arms above assert on return values and file contents. Six guard lines in
# this function were found deletable with nothing red, because that is the
# layer at which a deletable line is invisible: its neighbour reproduces the
# same return and the same bytes. Each arm below observes the one thing that
# differs.
# --------------------------------------------------------------------------


class _ReadCountingPath(type(Path())):
    """A Path that counts its own read_text calls."""

    reads = 0

    def read_text(self, *args, **kwargs):
        type(self).reads += 1
        return super().read_text(*args, **kwargs)


GUARANTEE_ARMS = {
    1: ["test_unobservable_teams_root_prunes_nothing"],
    2: ["test_unusable_root_is_rejected_before_the_registry_is_read"],
    3: ["test_registry_is_0600_even_when_the_umask_would_not_grant_it",
        "test_preserves_0o600_on_rewrite"],
    4: ["test_symlink_registry_is_skipped"],
    5: ["test_invalid_utf8_registry_returns_rather_than_propagating",
        "test_a_decodable_registry_still_prunes"],
    6: ["test_unobservable_teams_root_prunes_nothing",
        "test_unlistable_root_refuses_rather_than_pruning_a_dead_line"],
}


