"""bin/_memdir.py and bin/repacted-memdir: the orchestrator memory-dir swap and its split merge.

Temp config roots only; nothing here touches the live ~/.claude. The launcher that drives
these operations around a session is tested in test_repact_launcher.py.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
MEMDIR_CLI = BIN_DIR / "repacted-memdir"
_spec = importlib.util.spec_from_file_location("_memdir", BIN_DIR / "_memdir.py")
_memdir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_memdir)  # by path: path setup is conftest-owned (test_path_setup_pin)

LEGACY, REPACTED = _memdir.LEGACY_NAME, _memdir.DEFAULT_NAME
NOW = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.timezone.utc)
STAMP = "20260926T120000Z"


def _memory(memory_dir: Path, name: str = LEGACY) -> Path:
    d = memory_dir / name
    (d / "sub").mkdir(parents=True)
    (d / "MEMORY.md").write_text("# head\n", encoding="utf-8")
    (d / "sub" / "note.md").write_bytes(b"\x00binary\xff")
    (d / "empty").mkdir()
    (d / "link.md").symlink_to("MEMORY.md")
    return d


def _names(memory_dir: Path) -> list:
    return sorted(p.name for p in memory_dir.iterdir())


@pytest.fixture
def memory_dir(tmp_path):
    d = tmp_path / "agent-memory"
    d.mkdir()
    return d


# ---------- ensure ----------

def test_ensure_round_trip_is_byte_identical(memory_dir):
    original = _memdir.tree_fingerprint(_memory(memory_dir))
    assert _memdir.ensure(memory_dir, "repacted", apply=True) == 0
    assert _names(memory_dir) == [REPACTED]
    assert _memdir.tree_fingerprint(memory_dir / REPACTED) == original
    assert _memdir.ensure(memory_dir, "pact", apply=True) == 0
    assert _names(memory_dir) == [LEGACY]
    assert _memdir.tree_fingerprint(memory_dir / LEGACY) == original


@pytest.mark.parametrize("side, present", [("repacted", REPACTED),])
def test_ensure_is_idempotent(memory_dir, side, present, capsys):
    original = _memdir.tree_fingerprint(_memory(memory_dir, present))
    assert _memdir.ensure(memory_dir, side, apply=True) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert _names(memory_dir) == [present]
    assert _memdir.tree_fingerprint(memory_dir / present) == original


def test_ensure_with_no_dir_at_all_is_a_no_op(memory_dir, capsys):
    assert _memdir.ensure(memory_dir, "repacted", apply=True) == 0
    assert "neither name exists" in capsys.readouterr().out
    assert _names(memory_dir) == []


def test_ensure_refuses_a_split_and_moves_nothing(memory_dir):
    a = _memdir.tree_fingerprint(_memory(memory_dir, LEGACY))
    b = _memdir.tree_fingerprint(_memory(memory_dir, REPACTED))
    for side in _memdir.SIDES:
        with pytest.raises(_memdir.Refusal, match="SPLIT.*--merge-split"):
            _memdir.ensure(memory_dir, side, apply=True)
    assert _memdir.tree_fingerprint(memory_dir / LEGACY) == a
    assert _memdir.tree_fingerprint(memory_dir / REPACTED) == b


@pytest.mark.parametrize("kind", ["symlink",])
def test_ensure_refuses_a_symlink_or_a_file_at_either_name(memory_dir, tmp_path, kind):
    _memory(memory_dir, LEGACY)
    if kind == "symlink":
        (memory_dir / REPACTED).symlink_to(_memory(tmp_path, "elsewhere"))
    else:
        (memory_dir / REPACTED).write_text("x", encoding="utf-8")
    with pytest.raises(_memdir.Refusal, match="symlink" if kind == "symlink" else "not a directory"):
        _memdir.ensure(memory_dir, "repacted", apply=True)
    assert (memory_dir / LEGACY).is_dir()


def test_the_dry_run_moves_nothing(memory_dir, capsys):
    _memory(memory_dir)
    assert _memdir.ensure(memory_dir, "repacted", apply=False) == 0
    assert "dry run: nothing moved" in capsys.readouterr().out
    assert _names(memory_dir) == [LEGACY]


def test_a_failed_post_move_check_names_the_undo(memory_dir):
    _memory(memory_dir)
    calls = []

    def drifting(path):
        calls.append(path)
        count, digest = _memdir.tree_fingerprint(path)
        return (count, digest) if len(calls) == 1 else (count, "0" * 64)

    with pytest.raises(_memdir.VerifyFailed, match="--ensure pact --confirm-live"):
        _memdir.ensure(memory_dir, "repacted", apply=True, fingerprint=drifting)


# ---------- merge_split ----------

def _split(memory_dir: Path):
    """The kill -9 shape: the repacted-named dir holds the real memory, and a later pact
    session wrote a fresh head plus one new lesson into a new PACT-named dir."""
    canonical = _memory(memory_dir, REPACTED)
    stray = memory_dir / LEGACY
    (stray / "sub").mkdir(parents=True)
    (stray / "MEMORY.md").write_text("# fresh head from the pact session\n", encoding="utf-8")
    (stray / "lesson.md").write_text("a new lesson\n", encoding="utf-8")
    (stray / "sub" / "note.md").write_bytes(b"\x00binary\xff")  # an identical twin still collides
    (stray / "new-empty").mkdir()
    (stray / "stray-link.md").symlink_to("lesson.md")
    return stray, canonical


# ---------- the CLI ----------

def _cli(tmp_path, *args, extra_env=None):
    """HOME is always a temp dir: the link steps write under $HOME/.claude/protocols."""
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path), "HOME": str(tmp_path.parent / "home"),
           "PATH": "/usr/bin:/bin", **(extra_env or {})}
    return subprocess.run([sys.executable, str(MEMDIR_CLI), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def _pact_install(config_root: Path) -> Path:
    """A fake PACT install, registered the way Claude Code records it."""
    root = config_root.parent / "pact-install"
    (root / "agents").mkdir(parents=True)
    (root / "protocols").mkdir()
    (root / "agents" / "pact-architect.md").write_text("PACT body\n", encoding="utf-8")
    registry = config_root / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"version": 2, "plugins": {"PACT@pact-marketplace": [
        {"scope": "user", "installPath": str(root)}]}}), encoding="utf-8")
    return root


