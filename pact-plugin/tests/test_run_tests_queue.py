"""scripts/run-tests: the suite slot is granted in arrival order (FIFO ticket queue).

Every test points REPACTED_LOCK_DIR at tmp_path, so it never touches the real /private/tmp slots
or queue. A --full run's child is a stub "python" that records its package name in arrival order
and sleeps briefly, so no real suite runs.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PLUGIN_ROOT.parent / "scripts" / "run-tests"
LOCKF = "/usr/bin/lockf"
REPO = PLUGIN_ROOT.parent.parent

pytestmark = pytest.mark.skipif(not Path(LOCKF).exists(), reason="macOS lockf(1) not present")

# Invoked as `stub <tcount> -- stub -m pytest -q -p no:cacheprovider <pkg>`: the last argument
# is the package name the test passed as the pytest argument.
STUB = """#!/bin/sh
# run-tests asks its interpreter `-c "import site; ..."` for the user base (user_base); a
# python answers that without running a suite, so the stub does too, recording nothing.
case "$1" in -c) exit 0 ;; esac
for last; do :; done
echo "$last" >> "$ORDER_FILE"
echo $$ > "$ORDER_FILE.$last.pid"
exec sleep "${STUB_SLEEP:-0.3}"
"""


def load_wrapper():
    loader = importlib.machinery.SourceFileLoader("run_tests_under_test", str(WRAPPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # no __pycache__ in scripts/
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


@pytest.fixture
def rig(tmp_path):
    stub = tmp_path / "stub-python"
    stub.write_text(STUB)
    stub.chmod(0o755)
    locks = tmp_path / "locks"
    locks.mkdir()
    order = tmp_path / "order.txt"
    env = {**os.environ, "REPACTED_LOCK_DIR": str(locks), "ORDER_FILE": str(order)}
    return {"stub": stub, "locks": locks, "order": order, "env": env, "tmp": tmp_path}


def full(rig, pkg, wait=60, script=WRAPPER, **env_extra) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(script), "--full", "--pkg", pkg, "--wait", str(wait),
                             "--python", str(rig["stub"]), "--", pkg],
                            env={**rig["env"], **env_extra}, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def tickets(rig) -> list[dict]:
    queue = rig["locks"] / "repacted-suite.queue"
    return [json.loads(p.read_text()) for p in sorted(queue.glob("*.ticket"))] if queue.is_dir() else []


def wait_for(predicate, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def hold(lock: Path, seconds: float) -> subprocess.Popen:
    """Hold lock with lockf(1), an old-style flock-only holder, until it is really held."""
    proc = subprocess.Popen([LOCKF, "-k", str(lock), "sleep", str(seconds)])
    wait_for(lambda: subprocess.run([LOCKF, "-k", "-t", "0", str(lock), "true"]).returncode == 75,
             what="lockf to take the lock")
    return proc


def finish(procs, timeout=60):
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=timeout)
        results.append((proc.returncode, out, err))
    return results


def order(rig) -> list[str]:
    return rig["order"].read_text().split() if rig["order"].exists() else []


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def plant_ticket(rig, seq: int, pid: int, lstart: str, pkg: str) -> Path:
    queue = rig["locks"] / "repacted-suite.queue"
    queue.mkdir(exist_ok=True)
    path = queue / f"{seq:012d}.ticket"
    path.write_text(json.dumps({"seq": seq, "pid": pid, "lstart": lstart, "pkg": pkg, "since": "x"}))
    return path


def test_three_waiters_get_the_suite_slot_in_arrival_order(rig):
    holder = hold(rig["locks"] / "repacted-suite.lock", 60)
    waiters = []
    try:
        for n, pkg in enumerate(("first", "second", "third"), 1):
            waiters.append(full(rig, pkg))
            wait_for(lambda: len(tickets(rig)) == n, what=f"ticket {n}")
            time.sleep(0.3)
    finally:
        holder.kill()
    results = finish(waiters)
    assert [r[0] for r in results] == [0, 0, 0], results
    assert order(rig) == ["first", "second", "third"]
    assert tickets(rig) == []


def test_only_the_lowest_live_ticket_may_take_the_flock(rig, monkeypatch):
    """The ordering rule itself, deterministic: a live waiter ahead blocks every later ticket."""
    monkeypatch.setenv("REPACTED_LOCK_DIR", str(rig["locks"]))
    module = load_wrapper()
    ahead = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wait_for(lambda: module.process_start(ahead.pid) is not None, what="the sleeper to exist")
        plant_ticket(rig, 1, ahead.pid, module.process_start(ahead.pid), "ahead")
        own, ticket = module.take_ticket("me")
        assert ticket["seq"] == 2
        assert module.head_of_queue(own, ticket) is False
        ahead.kill()
        ahead.wait()
        assert module.head_of_queue(own, ticket) is True
    finally:
        ahead.kill()


def test_a_dead_holder_is_reaped_and_does_not_block_the_queue(rig):
    """kill -9 of the wrapper that holds the slot: the kernel frees the flock, and its ticket
    (pid gone) must be reaped so the next waiter is not stuck behind it."""
    first = full(rig, "doomed", STUB_SLEEP="30")
    wait_for(lambda: order(rig) == ["doomed"], what="the first run to start")
    doomed_pid = tickets(rig)[0]["pid"]
    first.kill()
    first.wait()
    os.kill(int(Path(f"{rig['order']}.doomed.pid").read_text()), 9)  # the orphaned stub child
    assert [t["pid"] for t in tickets(rig)] == [doomed_pid]
    started = time.monotonic()
    rc, _, err = finish([full(rig, "next", wait=15)])[0]
    assert rc == 0, err
    assert order(rig) == ["doomed", "next"]
    assert time.monotonic() - started < 10
    assert tickets(rig) == []


def test_a_dead_waiter_is_reaped(rig):
    holder = hold(rig["locks"] / "repacted-suite.lock", 60)
    try:
        ghost = full(rig, "ghost")
        wait_for(lambda: len(tickets(rig)) == 1, what="the ghost's ticket")
        ghost.kill()
        ghost.wait()
        survivor = full(rig, "survivor")
        # The survivor reaps the ghost on its first poll, while the holder still has the lock.
        wait_for(lambda: [t["pkg"] for t in tickets(rig)] == ["survivor"], what="the ghost to be reaped")
    finally:
        holder.kill()
    rc, _, err = finish([survivor])[0]
    assert rc == 0, err
    assert order(rig) == ["survivor"]
    assert tickets(rig) == []


