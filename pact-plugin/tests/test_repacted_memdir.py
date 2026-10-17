"""bin/_memdir.py and bin/repacted-memdir: the orchestrator memory-dir swap and its split merge.

Temp config roots only; nothing here touches the live ~/.claude. The launcher that drives
these operations around a session is tested in test_repact_launcher.py.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
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


@pytest.mark.parametrize("side, present", [("repacted", REPACTED), ("pact", LEGACY)])
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


@pytest.mark.parametrize("kind", ["symlink", "file"])
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


def test_a_custom_name_is_honoured(memory_dir):
    _memory(memory_dir)
    assert _memdir.ensure(memory_dir, "repacted", "observed-name", apply=True) == 0
    assert _names(memory_dir) == ["observed-name"]


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


def test_merge_split_moves_everything_and_suffixes_every_collision(memory_dir, capsys):
    stray, canonical = _split(memory_dir)
    before = _memdir.content_fingerprint([stray, canonical])
    canonical_head = (canonical / "MEMORY.md").read_text(encoding="utf-8")

    assert _memdir.merge_split(memory_dir, apply=True, now=NOW) == 0

    assert _names(memory_dir) == [LEGACY]
    merged = memory_dir / LEGACY
    assert _memdir.content_fingerprint([merged]) == before
    assert (merged / "MEMORY.md").read_text(encoding="utf-8") == canonical_head
    assert (merged / f"MEMORY.md.split-{STAMP}").read_text(encoding="utf-8").startswith("# fresh")
    assert (merged / f"sub/note.md.split-{STAMP}").read_bytes() == b"\x00binary\xff"
    assert (merged / "lesson.md").read_text(encoding="utf-8") == "a new lesson\n"
    assert os.readlink(merged / "stray-link.md") == "lesson.md"
    assert (merged / "new-empty").is_dir() and (merged / "empty").is_dir()
    out = capsys.readouterr().out
    assert "merged the split: 2 file(s) moved, 2 collision(s)" in out
    assert _memdir.split_files(memory_dir) == sorted(
        [merged / f"MEMORY.md.split-{STAMP}", merged / f"sub/note.md.split-{STAMP}"])


def test_merge_split_never_overwrites_an_existing_suffixed_name(memory_dir):
    stray, canonical = _split(memory_dir)
    (canonical / f"MEMORY.md.split-{STAMP}").write_text("an earlier split\n", encoding="utf-8")
    (stray / f"MEMORY.md.split-{STAMP}").write_text("stray's own suffixed file\n", encoding="utf-8")
    before = _memdir.content_fingerprint([stray, canonical])

    assert _memdir.merge_split(memory_dir, apply=True, now=NOW) == 0

    merged = memory_dir / LEGACY
    assert _memdir.content_fingerprint([merged]) == before
    texts = {p.name: p.read_text(encoding="utf-8") for p in merged.glob("MEMORY.md.split-*")}
    assert texts[f"MEMORY.md.split-{STAMP}"] == "an earlier split\n"
    assert sorted(texts.values()) == sorted(["an earlier split\n", "stray's own suffixed file\n",
                                            "# fresh head from the pact session\n"])


def test_a_stray_dir_where_the_canonical_has_a_file_is_suffixed_whole(memory_dir):
    canonical = _memory(memory_dir, REPACTED)
    (canonical / "topic").write_text("a file\n", encoding="utf-8")
    stray = memory_dir / LEGACY
    (stray / "topic").mkdir(parents=True)
    (stray / "topic" / "a.md").write_text("a\n", encoding="utf-8")
    (stray / "topic" / "b.md").write_text("b\n", encoding="utf-8")
    before = _memdir.content_fingerprint([stray, canonical])

    assert _memdir.merge_split(memory_dir, apply=True, now=NOW) == 0

    merged = memory_dir / LEGACY
    assert (merged / "topic").read_text(encoding="utf-8") == "a file\n"
    assert sorted(p.name for p in (merged / f"topic.split-{STAMP}").iterdir()) == ["a.md", "b.md"]
    assert _memdir.content_fingerprint([merged]) == before


def test_merge_split_dry_run_lists_collisions_and_changes_nothing(memory_dir, capsys):
    stray, canonical = _split(memory_dir)
    a, b = _memdir.tree_fingerprint(stray), _memdir.tree_fingerprint(canonical)
    assert _memdir.merge_split(memory_dir, apply=False, now=NOW) == 0
    out = capsys.readouterr().out
    assert "collision: MEMORY.md -> MEMORY.md.split-" in out and "dry run" in out
    assert (_memdir.tree_fingerprint(stray), _memdir.tree_fingerprint(canonical)) == (a, b)


def test_merge_split_without_a_split_is_a_no_op(memory_dir, capsys):
    original = _memdir.tree_fingerprint(_memory(memory_dir))
    assert _memdir.merge_split(memory_dir, apply=True) == 0
    assert "no split" in capsys.readouterr().out
    assert _memdir.tree_fingerprint(memory_dir / LEGACY) == original


def test_a_destination_that_appears_mid_merge_is_never_overwritten(memory_dir, monkeypatch):
    """Sabotage shape: the planned free name is taken between plan and move. The move must
    fail loudly and every byte must still exist in one of the two dirs."""
    stray, canonical = _split(memory_dir)
    before = _memdir.content_fingerprint([stray, canonical])
    real_place = _memdir._Placer.place

    def place_then_squat(self, parts, is_leaf):
        dest, collided = real_place(self, parts, is_leaf)
        if parts == ("lesson.md",):
            dest.write_text("squatter\n", encoding="utf-8")
        return dest, collided

    monkeypatch.setattr(_memdir._Placer, "place", place_then_squat)
    with pytest.raises(FileExistsError):
        _memdir.merge_split(memory_dir, apply=True, now=NOW)
    (canonical / "lesson.md").unlink()  # remove the squatter before counting
    assert _memdir.content_fingerprint([stray, canonical]) == before


def test_status_reports_split_and_split_files(memory_dir):
    _split(memory_dir)
    lines = "\n".join(_memdir.status(memory_dir))
    assert "split: YES" in lines and "*.split-* files: 0" in lines
    _memdir.merge_split(memory_dir, apply=True, now=NOW)
    lines = "\n".join(_memdir.status(memory_dir))
    assert "split: no" in lines and "*.split-* files: 2" in lines
    assert "2 *.split-* file(s)" in _memdir.split_reminder(memory_dir)


# ---------- the CLI ----------

def _cli(tmp_path, *args, extra_env=None):
    """HOME is always a temp dir: the link steps write under $HOME/.claude/protocols."""
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path), "HOME": str(tmp_path.parent / "home"),
           "PATH": "/usr/bin:/bin", **(extra_env or {})}
    return subprocess.run([sys.executable, str(MEMDIR_CLI), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_ensure_is_a_dry_run_unless_confirmed(tmp_path):
    _memory(tmp_path / "agent-memory")
    assert _cli(tmp_path, "--ensure", "repacted").returncode == 0
    assert _names(tmp_path / "agent-memory") == [LEGACY]
    assert _cli(tmp_path, "--ensure", "repacted", "--confirm-live").returncode == 0
    assert _names(tmp_path / "agent-memory") == [REPACTED]


def test_cli_refuses_to_move_inside_a_repact_session(tmp_path):
    _memory(tmp_path / "agent-memory")
    result = _cli(tmp_path, "--ensure", "repacted", "--confirm-live",
                  extra_env={"REPACTED_LAUNCH": "alternate"})
    assert result.returncode == 2 and "inside a repact session" in result.stderr
    assert _names(tmp_path / "agent-memory") == [LEGACY]
    assert _cli(tmp_path, "--status", extra_env={"REPACTED_LAUNCH": "alternate"}).returncode == 0


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


def test_cli_split_refusal_and_merge(tmp_path):
    config = tmp_path / "config"
    _split(config / "agent-memory")
    pact_root = _pact_install(config)
    stale = config / "agents" / "pact-architect.md"
    stale.parent.mkdir()
    stale.symlink_to(BIN_DIR.parent / "agents" / "pact-architect.md")  # left by a kill -9
    refused = _cli(config, "--ensure", "repacted", "--confirm-live", "--quiet")
    assert refused.returncode == 2 and "SPLIT" in refused.stderr
    merged = _cli(config, "--merge-split", "--confirm-live")
    assert merged.returncode == 0, merged.stderr
    assert "merged the split:" in merged.stdout
    assert os.path.realpath(stale) == os.path.realpath(pact_root / "agents" / "pact-architect.md")
    reminded = _cli(config, "--ensure", "repacted", "--confirm-live", "--quiet", "--remind-splits")
    assert reminded.returncode == 0 and reminded.stdout == ""
    assert reminded.stderr.count("\n") == 1 and "2 *.split-* file(s)" in reminded.stderr


@pytest.mark.parametrize("argv", [[], ["--ensure", "both"], ["--status", "--merge-split"],
                                  ["--status", "--to", "a/b"], ["--status", "--to", LEGACY]])
def test_cli_usage_errors_exit_64(tmp_path, argv):
    assert _cli(tmp_path, *argv).returncode == 64
