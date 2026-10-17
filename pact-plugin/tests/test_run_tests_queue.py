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


def test_a_reused_pid_with_a_different_start_time_counts_as_dead(rig):
    """The pid is alive, but it is not the process that took the ticket (amend_1)."""
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stale = plant_ticket(rig, 1, live.pid, "Thu Jan  1 00:00:00 1970", "reused")
        rc, _, err = finish([full(rig, "next", wait=15)])[0]
    finally:
        live.kill()
    assert rc == 0, err
    assert order(rig) == ["next"]
    assert not stale.exists()


def test_a_waiter_behind_a_live_ticket_keeps_waiting(rig, monkeypatch):
    """The live-with-matching-start-time control for the reuse test: that ticket is NOT reaped."""
    monkeypatch.setenv("REPACTED_LOCK_DIR", str(rig["locks"]))
    module = load_wrapper()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wait_for(lambda: module.process_start(live.pid) is not None, what="the sleeper to exist")
        ahead = plant_ticket(rig, 1, live.pid, module.process_start(live.pid), "ahead")
        rc, _, err = finish([full(rig, "behind", wait=3)])[0]
    finally:
        live.kill()
    assert rc == 75, err
    assert order(rig) == []
    assert ahead.exists()


def test_the_wait_timeout_exits_75_and_leaves_no_ticket(rig):
    holder = hold(rig["locks"] / "repacted-suite.lock", 60)
    try:
        rc, _, err = finish([full(rig, "impatient", wait=1)])[0]
    finally:
        holder.kill()
    assert rc == 75, err
    assert "suite slot not acquired" in err
    assert tickets(rig) == []


def test_a_flock_only_contender_cannot_deadlock_the_queue(rig):
    """An old-style holder that keeps retaking the flock (lockf, like a pre-queue copy) may win out
    of turn, but every queued waiter still finishes."""
    lock = rig["locks"] / "repacted-suite.lock"
    churn = subprocess.Popen(["/bin/sh", "-c", f"for i in 1 2 3 4 5 6; do {LOCKF} -k {lock} sleep 0.5; "
                              "sleep 0.1; done"])
    try:
        waiters = [full(rig, pkg, wait=60) for pkg in ("a", "b")]
        results = finish(waiters)
    finally:
        churn.kill()
    assert [r[0] for r in results] == [0, 0], results
    assert sorted(order(rig)) == ["a", "b"]
    assert tickets(rig) == []


def test_an_old_copy_of_the_script_and_new_waiters_all_finish(rig):
    """The real transition: the pre-queue run-tests from main's history contends with new copies."""
    old_source = subprocess.run(["git", "-C", str(REPO), "show", "9efceda9:scripts/run-tests"],
                                capture_output=True, text=True)
    if old_source.returncode != 0:
        pytest.skip("pre-queue revision not in this clone")
    # The old copy resolves its plugin dir from its own path, so give it the same layout in tmp.
    old = rig["tmp"] / "repacted" / "scripts" / "run-tests"
    old.parent.mkdir(parents=True)
    (rig["tmp"] / "repacted" / "pact-plugin").mkdir()
    old.write_text(old_source.stdout)
    holder = hold(rig["locks"] / "repacted-suite.lock", 60)
    try:
        procs = [full(rig, "new-1")]
        wait_for(lambda: len(tickets(rig)) == 1, what="the first new ticket")
        procs.append(full(rig, "old", script=old))
        procs.append(full(rig, "new-2"))
        wait_for(lambda: len(tickets(rig)) == 2, what="the second new ticket")
    finally:
        holder.kill()
    results = finish(procs)
    assert [r[0] for r in results] == [0, 0, 0], results
    assert sorted(order(rig)) == ["new-1", "new-2", "old"]
    new_only = [p for p in order(rig) if p != "old"]
    assert new_only == ["new-1", "new-2"]


def test_status_shows_the_queue_in_order(rig):
    holder = hold(rig["locks"] / "repacted-suite.lock", 60)
    waiters = []
    try:
        for n, pkg in enumerate(("q-one", "q-two"), 1):
            waiters.append(full(rig, pkg))
            wait_for(lambda: len(tickets(rig)) == n, what=f"ticket {n}")
        status = subprocess.run([sys.executable, str(WRAPPER), "--status"], env=rig["env"],
                                capture_output=True, text=True, timeout=30).stdout
    finally:
        holder.kill()
        for w in waiters:
            w.kill()
            w.wait()
    assert "suite queue: 2" in status
    lines = status.splitlines()
    one = next(i for i, l in enumerate(lines) if "pkg=q-one" in l)
    two = next(i for i, l in enumerate(lines) if "pkg=q-two" in l)
    assert one < two
    assert "(live)" in lines[one]


def test_status_with_no_queue_says_empty(rig):
    status = subprocess.run([sys.executable, str(WRAPPER), "--status"], env=rig["env"],
                            capture_output=True, text=True, timeout=30).stdout
    assert "suite queue: empty" in status


def test_quick_never_selects_a_slow_suite_even_when_it_names_the_changed_file():
    # tests/slow/ holds worst-case timings: --full's bare collection reaches it, QUICK never.
    module = load_wrapper()
    slow = module.PLUGIN / "tests" / "slow"
    assert any(p.name == "test_merge_guard_perf.py" for p in slow.glob("test_*.py"))
    assert not [p for p in module._test_files() if slow in p.parents]
    plugin, _root = module.quick_selection(["pact-plugin/hooks/shared/merge_guard_common.py"])
    assert plugin and not [p for p in plugin if p.startswith("tests/slow/")]
