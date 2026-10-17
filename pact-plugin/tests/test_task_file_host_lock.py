"""Hook writes to a team task file take the Claude Code host's own task lock.

Location: pact-plugin/tests/test_task_file_host_lock.py
Summary: the host (2.1.288, bundled proper-lockfile) locks a task by mkdir
         `<id>.json.lock`, waits about 2.7 s on EEXIST, reclaims a directory
         whose mtime is 10 s old with rmdir, and rmdirs on release. 1.5.0's
         hook writer left a regular-file flock sidecar of that name, so every
         later host TaskUpdate failed (ELOCKED, then ENOTDIR on the reclaim).
         `host_update` below simulates the host; the pins: hook and host
         exclude each other, nothing is left behind, a 1.5.0 leftover is
         healed, a symlink or a non-empty file is never touched.
Used by: the suite.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import threading
import time
from pathlib import Path

import pytest

from shared import state_file
from shared.task_utils import update_task_file

TEAM = "lock-team"


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    directory = tmp_path / "tasks" / TEAM
    directory.mkdir(parents=True)
    return directory


def seed(tasks: Path, task_id: str = "7") -> Path:
    path = tasks / f"{task_id}.json"
    path.write_text(json.dumps({"id": task_id, "status": "pending", "metadata": {}}))
    return path


def meta(path: Path) -> dict:
    return json.loads(path.read_text())["metadata"]


def put(key, value=True):
    return lambda task: task["metadata"].update({key: value}) or True


def host_update(path: Path, mutate, retries: int = 30) -> None:
    """The host's updateTask lock, as bundled: mkdir; on EEXIST stat (following a link),
    reclaim with rmdir when 10 s stale, else retry 5 ms doubling to 100 ms; rmdir on release."""
    lock = str(path) + ".lock"
    delay = 0.005
    for _ in range(retries + 1):
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                held = os.stat(lock)
            except FileNotFoundError:                # released in between: try again at once
                continue
            if held.st_mtime < time.time() - 10:
                os.rmdir(lock)                       # NotADirectoryError on a file: the 1.5.0 defect
                continue
        time.sleep(delay)
        delay = min(delay * 2, 0.1)
    else:
        raise OSError(errno.EEXIST, "Lock file is already being held", lock)
    try:
        task = json.loads(path.read_text())
        mutate(task)
        path.write_text(json.dumps(task))
    finally:
        os.rmdir(lock)


def age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then), follow_symlinks=False)


def test_the_simulated_host_fails_on_the_1_5_0_sidecar(tasks):
    """Fidelity: the old flock writer's leftover blocks the host exactly as seen live."""
    path = seed(tasks)
    state_file.locked_update(path, lambda text: (text, True, None), tasks.parent)
    sidecar = Path(str(path) + ".lock")
    assert sidecar.is_file()
    age(sidecar, 30)
    with pytest.raises(NotADirectoryError):
        host_update(path, lambda t: None)


def test_a_hook_write_leaves_nothing_and_the_host_writes_after_it(tasks):
    path = seed(tasks)
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert sorted(p.name for p in tasks.iterdir()) == ["7.json"]
    host_update(path, lambda t: t["metadata"].update(host=True))
    assert meta(path) == {"hook": True, "host": True}


def test_a_held_host_lock_makes_the_hook_wait_then_write(tasks):
    path = seed(tasks)
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    threading.Timer(0.3, lock.rmdir).start()
    started = time.monotonic()
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert time.monotonic() - started >= 0.25
    assert meta(path) == {"hook": True} and not lock.exists()


def test_a_lock_held_past_the_wait_gives_up_without_writing(tasks, monkeypatch):
    monkeypatch.setattr(state_file, "HOST_LOCK_WAIT", 0.2)
    path = seed(tasks)
    before = path.read_bytes()
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    age(lock, 9)                                     # fresh by the host's 10 s rule
    assert update_task_file(TEAM, "7", put("hook")) is False
    assert path.read_bytes() == before and lock.is_dir()


def test_a_stale_host_directory_is_reclaimed(tasks):
    path = seed(tasks)
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    age(lock, 11)
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert meta(path) == {"hook": True} and not lock.exists()


def test_a_non_empty_stale_directory_is_never_emptied(tasks, monkeypatch):
    monkeypatch.setattr(state_file, "HOST_LOCK_WAIT", 0.2)
    path = seed(tasks)
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    (lock / "keep").write_text("x")
    age(lock, 60)
    assert update_task_file(TEAM, "7", put("hook")) is False
    assert (lock / "keep").read_text() == "x"


def test_a_1_5_0_empty_sidecar_is_healed(tasks):
    path = seed(tasks)
    sidecar = Path(str(path) + ".lock")
    sidecar.touch()
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert meta(path) == {"hook": True} and not os.path.lexists(sidecar)
    host_update(path, lambda t: None)


def test_a_sidecar_flocked_by_a_1_5_0_writer_is_healed_after_it_finishes(tasks):
    path = seed(tasks)
    sidecar = Path(str(path) + ".lock")
    fd = os.open(sidecar, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)

    def finish():
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    threading.Timer(0.3, finish).start()
    started = time.monotonic()
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert time.monotonic() - started >= 0.25
    assert not os.path.lexists(sidecar)


def test_a_non_empty_regular_file_is_left_alone(tasks):
    path = seed(tasks)
    other = Path(str(path) + ".lock")
    other.write_text("not ours")
    assert update_task_file(TEAM, "7", put("hook")) is False
    assert other.read_text() == "not ours" and meta(path) == {}


@pytest.mark.parametrize("target_kind", ["dir", "file", "dangling"])
def test_a_symlink_lock_is_never_followed_or_deleted(tasks, tmp_path, target_kind):
    path = seed(tasks)
    target = tmp_path / "elsewhere"
    if target_kind == "dir":
        target.mkdir()
        age(target, 60)
    elif target_kind == "file":
        target.touch()
    link = Path(str(path) + ".lock")
    link.symlink_to(target)
    assert update_task_file(TEAM, "7", put("hook")) is False
    assert link.is_symlink() and os.readlink(link) == str(target)
    assert target.exists() == (target_kind != "dangling") and meta(path) == {}


def test_concurrent_hook_and_host_writers_lose_nothing(tasks):
    path = seed(tasks)
    workers = [threading.Thread(target=update_task_file, args=(TEAM, "7", put(f"hook{i}")))
               for i in range(8)]
    workers += [threading.Thread(target=host_update, args=(path, lambda t, i=i: t["metadata"].update(
        {f"host{i}": True}))) for i in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert set(meta(path)) == {f"hook{i}" for i in range(8)} | {f"host{i}" for i in range(4)}
    assert sorted(p.name for p in tasks.iterdir()) == ["7.json"]


@pytest.mark.parametrize("odd_id", ["ünï", "a b", "x" * 200])
def test_odd_task_ids_leave_nothing(tasks, odd_id):
    seed(tasks, odd_id)
    assert update_task_file(TEAM, odd_id, put("hook")) is True
    assert sorted(p.name for p in tasks.iterdir()) == [f"{odd_id}.json"]


def test_a_host_reclaim_between_observation_and_rmdir_survives(tasks, monkeypatch):
    """rv4 MED-1: the hook saw a stale dir, a host reclaimed it (rmdir + mkdir) before the hook's
    rmdir; the hook must not remove the host's fresh lock."""
    monkeypatch.setattr(state_file, "HOST_LOCK_WAIT", 0.3)
    path = seed(tasks)
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    age(lock, 60)
    real_lstat, host = os.lstat, {}

    def lstat(target, *args, **kwargs):
        result = real_lstat(target, *args, **kwargs)
        if str(target) == str(lock) and not host and result.st_mtime < time.time() - 10:
            os.rmdir(lock)
            os.mkdir(lock)                           # the host's fresh lock
            host["ino"] = real_lstat(lock).st_ino
        return result

    monkeypatch.setattr(state_file.os, "lstat", lstat)
    assert update_task_file(TEAM, "7", put("hook")) is False
    assert lock.is_dir() and real_lstat(lock).st_ino == host["ino"] and meta(path) == {}


def test_a_crash_leftover_is_waited_out_then_reclaimed(tasks):
    """rv4 MED-2/LOW-3: a hook killed holding the lock leaves a fresh dir; the 11 s wait outlasts
    the host's 10 s stale threshold, so the next write heals it instead of failing."""
    assert state_file.HOST_LOCK_WAIT > state_file.HOST_LOCK_STALE
    path = seed(tasks)
    lock = Path(str(path) + ".lock")
    lock.mkdir()
    age(lock, 9.6)
    assert update_task_file(TEAM, "7", put("hook")) is True
    assert meta(path) == {"hook": True} and not lock.exists()
