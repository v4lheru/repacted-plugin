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

import json
import os
import subprocess
import sys
import time
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


@pytest.mark.parametrize("name", ["../../escape", "a/b", "..", ".", "/etc/hosts", "döcker-端口", "x.lock"])
def test_a_name_that_looks_like_a_path_stays_inside_the_lock_directory(tmp_path, data, name):
    assert lock(data, "acquire", name, "--owner", "coder-a").returncode == 0
    assert lock(data, "acquire", "other", "--owner", "coder-b").returncode == 0     # a distinct lock
    files = _files(tmp_path)
    assert len(files) == 3 and all(path.startswith("plugin-data/locks/") for path in files)
    assert name in lock(data, "status", name).stdout
    assert lock(data, "release", name, "--owner", "coder-a").returncode == 0


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "a\x1b[2Jb", "a\u2028b", "a\u202eb", "x" * 101])
def test_a_name_that_is_not_one_printable_line_is_a_usage_error(data, name):
    for args in (["acquire", name, "--owner", "coder-a"], ["acquire", "docker", "--owner", name],
                 ["release", name, "--owner", "coder-a"], ["status", name]):
        assert lock(data, *args).returncode == USAGE
    assert not data.exists() or _files(data) == set()


@pytest.mark.parametrize("ttl", ["0", "-5", "nan", "inf", "1441", "soon"])
def test_a_ttl_that_is_not_a_positive_number_of_minutes_is_a_usage_error(data, ttl):
    result = lock(data, "acquire", "docker", "--owner", "coder-a", f"--ttl={ttl}")
    assert result.returncode == USAGE and "repact-lock acquire docker" in result.stderr
    assert lock(data, "status", "docker").stdout.strip() == "docker  free"


@pytest.mark.parametrize("content", [
    b"", b"   \n", b"{not json", b"[]", b"\xff\xfe",
    json.dumps({"name": "docker", "owner": "coder-a", "acquired": "now", "ttl_s": 60}).encode(),
    json.dumps({"name": "docker", "owner": "a\nb", "acquired": 1.0, "ttl_s": 60}).encode(),
    json.dumps({"name": "docker", "owner": "coder-a", "acquired": 1.0, "ttl_s": 1e300}).encode(),
    json.dumps({"name": "docker", "owner": "coder-a", "acquired": 9e12, "ttl_s": 60}).encode(),
    # Each of the next three ended all three commands in a traceback.
    json.dumps({"name": "docker", "owner": "coder-a", "acquired": 10 ** 400, "ttl_s": 60}).encode(),
    json.dumps({"name": "docker", "owner": "coder-a", "acquired": 1.0, "ttl_s": 10 ** 400}).encode(),
    b"[" * 100_000,
])
def test_a_lock_file_that_is_not_a_record_is_replaced_and_reported(data, content):
    assert lock(data, "acquire", "docker", "--owner", "coder-a").returncode == 0
    (path,) = (data / "locks").glob("*.lock")
    path.write_bytes(content)
    assert "unreadable lock file" in lock(data, "status").stdout
    assert lock(data, "release", "docker", "--owner", "coder-a").returncode == REFUSED
    taken = lock(data, "acquire", "docker", "--owner", "coder-b")
    assert taken.returncode == 0 and "replaced an unreadable lock file" in taken.stdout
    assert "held by coder-b" in lock(data, "status", "docker").stdout


@pytest.mark.parametrize("target", ["absent", "keep.txt"])
def test_a_link_in_place_of_a_lock_file_is_damaged_for_every_command_and_never_followed(
        tmp_path, data, target):
    """A link to nothing read as free to status and release while acquire answered 'taken a moment
    ago' for ever: nobody could take the lock."""
    (tmp_path / "keep.txt").write_text("KEEP\n")
    assert lock(data, "acquire", "docker", "--owner", "coder-a").returncode == 0
    (path,) = (data / "locks").glob("*.lock")
    path.unlink()
    path.symlink_to(tmp_path / target)
    assert "unreadable lock file (a symbolic link)" in lock(data, "status", "docker").stdout
    assert lock(data, "release", "docker", "--owner", "coder-a").returncode == REFUSED
    taken = lock(data, "acquire", "docker", "--owner", "coder-b")
    assert taken.returncode == 0 and "replaced an unreadable lock file" in taken.stdout
    assert not path.is_symlink() and "held by coder-b" in lock(data, "status", "docker").stdout
    assert (tmp_path / "keep.txt").read_text() == "KEEP\n" and not (tmp_path / "absent").exists()


@pytest.mark.parametrize("ahead", [61, 300, 590])
def test_a_lock_stamped_ahead_of_the_clock_is_still_held(data, ahead):
    """After the clock was set back by more than a minute the holder's lock read as unreadable,
    and the next acquire took it from under a live holder."""
    assert lock(data, "acquire", "docker", "--owner", "coder-a").returncode == 0
    (path,) = (data / "locks").glob("*.lock")
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "acquired": time.time() + ahead}))
    assert "held by coder-a" in lock(data, "status", "docker").stdout
    busy = lock(data, "acquire", "docker", "--owner", "coder-b")
    assert busy.returncode == BUSY and "held by coder-a" in busy.stdout
    assert lock(data, "release", "docker", "--owner", "coder-a").returncode == 0


@pytest.mark.parametrize("ahead", [630, 3600, 23 * 3600])
def test_a_lock_stamped_past_the_clock_window_is_replaced(data, ahead):
    """A record stamped 23 hours ahead with a 24 hour ttl was held for 47 hours, and nobody but its
    owner could release it. The window ends at 600 s; 630 leaves room for the stamp to age while
    the commands start."""
    assert lock(data, "acquire", "docker", "--owner", "coder-a", "--ttl", "1440").returncode == 0
    (path,) = (data / "locks").glob("*.lock")
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "acquired": time.time() + ahead}))
    shown = lock(data, "status", "docker").stdout
    assert "unreadable lock file (acquired more than 10m00s in the future)" in shown
    taken = lock(data, "acquire", "docker", "--owner", "coder-b")
    assert taken.returncode == 0 and "replaced an unreadable lock file" in taken.stdout
    assert "held by coder-b" in lock(data, "status", "docker").stdout


def test_a_wait_that_runs_out_reports_busy(data):
    assert lock(data, "acquire", "docker", "--owner", "coder-a").returncode == 0
    started = time.monotonic()
    result = lock(data, "acquire", "docker", "--owner", "coder-b", "--wait", "1.2")
    assert result.returncode == BUSY and "held by coder-a" in result.stdout
    assert "gave up after 1s" in result.stdout and time.monotonic() - started >= 1.2
    assert lock(data, "acquire", "docker", "--owner", "coder-b", "--wait=-1").returncode == USAGE


def test_an_expired_lock_is_reclaimed_by_a_waiter_and_names_the_previous_owner(data):
    assert lock(data, "acquire", "docker", "--owner", "coder-a", "--ttl", "0.01").returncode == 0
    taken = lock(data, "acquire", "docker", "--owner", "coder-b", "--wait", "10")
    assert taken.returncode == 0 and "reclaimed from coder-a" in taken.stdout
    stale = lock(data, "release", "docker", "--owner", "coder-a")
    assert stale.returncode == REFUSED and "held by coder-b" in stale.stderr
    again = lock(data, "acquire", "docker", "--owner", "coder-b")           # the holder's own retry
    assert again.returncode == 0 and "already yours" in again.stdout
