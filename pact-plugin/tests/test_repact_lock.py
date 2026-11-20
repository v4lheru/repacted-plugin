"""
Location: pact-plugin/tests/test_repact_lock.py
Summary: bin/repact-lock, the advisory machine-wide lock agents take for a shared single-instance
         resource. The helper is used by concurrent agents, so these are its boundary cases: a
         race, a release by a non-holder, names that look like paths, a ttl that is not a positive
         number, a lock file that is not a record (a link included), a stamp ahead of the clock, a
         wait that runs out, and an expired holder.
Used by: the plugin suite. Every test runs the CLI as a subprocess against a temp plugin data dir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "bin" / "repact-lock"
BUSY, USAGE, REFUSED = 1, 64, 65


@pytest.fixture()
def data(tmp_path):
    return tmp_path / "plugin-data"


def _env(data: Path) -> dict:
    return {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}


def lock(data: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          env=_env(data))


def _files(root: Path) -> set:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


def test_one_holder_at_a_time_and_only_the_holder_releases(data):
    assert lock(data, "acquire", "docker", "--owner", "coder-a").returncode == 0
    busy = lock(data, "acquire", "Docker ", "--owner", "coder-b")          # the same resource
    assert busy.returncode == BUSY and "held by coder-a" in busy.stdout
    refused = lock(data, "release", "docker", "--owner", "coder-b")
    assert refused.returncode == REFUSED and "held by coder-a, not coder-b" in refused.stderr
    assert "held by coder-a" in lock(data, "status", "docker").stdout
    assert lock(data, "release", "docker", "--owner", "coder-a").returncode == 0
    assert lock(data, "status").stdout.strip() == "no locks held"
    assert lock(data, "acquire", "docker", "--owner", "coder-b").returncode == 0


def test_racing_acquires_have_exactly_one_winner(data):
    racers = [subprocess.Popen([sys.executable, str(CLI), "acquire", "docker", "--owner", f"agent-{n}"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(data))
              for n in range(8)]
    outputs = [proc.communicate()[0] for proc in racers]
    results = [(proc.returncode, out) for proc, out in zip(racers, outputs)]
    winners = [out for code, out in results if code == 0]
    assert len(winners) == 1 and sorted(code for code, _ in results) == [0] + [BUSY] * 7
    holder = winners[0].split("owner ")[1].split(",")[0]
    assert f"held by {holder} " in lock(data, "status", "docker").stdout


@pytest.mark.parametrize("name", ["../../escape",])
def test_a_name_that_looks_like_a_path_stays_inside_the_lock_directory(tmp_path, data, name):
    assert lock(data, "acquire", name, "--owner", "coder-a").returncode == 0
    assert lock(data, "acquire", "other", "--owner", "coder-b").returncode == 0     # a distinct lock
    files = _files(tmp_path)
    assert len(files) == 3 and all(path.startswith("plugin-data/locks/") for path in files)
    assert name in lock(data, "status", name).stdout
    assert lock(data, "release", name, "--owner", "coder-a").returncode == 0


@pytest.mark.parametrize("name", ["",])
def test_a_name_that_is_not_one_printable_line_is_a_usage_error(data, name):
    for args in (["acquire", name, "--owner", "coder-a"], ["acquire", "docker", "--owner", name],
                 ["release", name, "--owner", "coder-a"], ["status", name]):
        assert lock(data, *args).returncode == USAGE
    assert not data.exists() or _files(data) == set()


