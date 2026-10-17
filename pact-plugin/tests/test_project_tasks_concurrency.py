"""
D10 P1, test plan item 3 (the design notes §3.2, §12.1): 8 processes
append 50 events each to one log, two processes race opposite dependencies, and the same accept
fires twice at once (the bridge's double fire). Every writer is a separate OS process (run at nice 19) that loads the write module by path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from project_tasks_support import WRITER_PATH, new_repo, reader, writer

WORKER = textwrap.dedent("""
    import importlib.util, sys, time
    spec = importlib.util.spec_from_file_location("tasks_write_worker", sys.argv[1])
    W = importlib.util.module_from_spec(spec); sys.modules[spec.name] = W; spec.loader.exec_module(W)
    store = W.Store.locate(sys.argv[2])
    mode = sys.argv[3]
    go = sys.argv[4]
    while not __import__("os").path.exists(go):
        time.sleep(0.005)
    if mode == "append":
        for i in range(50):
            store.add(f"worker {sys.argv[5]} task {i}")
    elif mode == "accept":
        print(store.accept(sys.argv[5], "session-1/7", 1, []))
    elif mode == "reject":
        print(store.reject(sys.argv[5], "session-1/7", 1))
    else:
        try:
            store.dep(sys.argv[5], sys.argv[6])
            print("ok")
        except W.Refused as err:
            print("refused")
""")


def _spawn(tmp_path, repo, mode, *args):
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    return subprocess.Popen(["nice", "-n", "19", sys.executable, str(script), str(WRITER_PATH), str(repo),
                             mode, str(tmp_path / "go"), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "REPACT_TASKS_TEST_HOLD_S": os.environ.get("_HOLD", "0")})


def test_eight_processes_append_four_hundred_intact_lines(tmp_path):
    repo = new_repo(tmp_path / "repo")
    writer().init(repo)
    procs = [_spawn(tmp_path, repo, "append", str(n)) for n in range(8)]
    (tmp_path / "go").write_text("")
    for proc in procs:
        out, err = proc.communicate(timeout=240)
        assert proc.returncode == 0, err
    log = repo / ".repacted" / "tasks.jsonl"
    lines = log.read_text().splitlines()
    assert len(lines) == 400
    events = [json.loads(line) for line in lines]          # every line parses
    refs = [e["t"] for e in events if e["op"] == "create"]
    assert len(refs) == len(set(refs)) == 400
    assert len({e["eid"] for e in events}) == 400
    R = reader()
    assert R.read_log(log).corrupt == []
    assert len(R.load(repo).tasks) == 400


def test_two_processes_racing_opposite_dependencies_leave_exactly_one(tmp_path, monkeypatch):
    repo = new_repo(tmp_path / "repo")
    store = writer().init(repo)
    a, b = store.add("a"), store.add("b")
    monkeypatch.setenv("_HOLD", "0.4")        # each writer holds its validated state 0.4 s
    procs = [_spawn(tmp_path, repo, "race", a, b), _spawn(tmp_path, repo, "race", b, a)]
    (tmp_path / "go").write_text("")
    results = sorted(proc.communicate(timeout=120)[0].strip() for proc in procs)
    assert results == ["ok", "refused"]
    state = reader().load(repo)
    edges = {(ref, dep) for ref, task in state.tasks.items() for dep in task["depends_on"]}
    assert len(edges & {(a, b), (b, a)}) == 1


@pytest.mark.parametrize("op", ["accept", "reject"])
def test_the_same_accept_or_reject_fired_twice_at_once_writes_once(tmp_path, monkeypatch, op):
    """Idempotency is decided under the lock: both writers read the log before either appends,
    so a check made before taking the lock would let both through."""
    repo = new_repo(tmp_path / "repo")
    store = writer().init(repo)
    a = store.add("a")
    monkeypatch.setenv("_HOLD", "0.4")
    procs = [_spawn(tmp_path, repo, op, a), _spawn(tmp_path, repo, op, a)]
    (tmp_path / "go").write_text("")
    outs = [proc.communicate(timeout=120) for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0], [err for _, err in outs]
    assert sorted(out.strip() for out, _ in outs) == ["False", "True"]
    lines = (repo / ".repacted" / "tasks.jsonl").read_text().splitlines()
    assert [json.loads(line)["op"] for line in lines].count(op) == 1
