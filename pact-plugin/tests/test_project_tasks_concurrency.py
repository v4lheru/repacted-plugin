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
import time
from pathlib import Path

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
        print("appended 50")
    elif mode == "init":
        for _ in range(50):
            W.init(sys.argv[2])
        print("initialised 50")
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
    # Write the script once: rewriting it truncates the file while the worker spawned just before
    # may still be starting, and an interpreter that reads it empty exits 0 having done nothing.
    script = tmp_path / "worker.py"
    if not script.exists():
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
        assert out.strip() == "appended 50", err      # a worker that ran no adds fails here, by name
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


def test_spawning_a_worker_never_rewrites_the_script_a_starting_worker_reads(tmp_path, monkeypatch):
    """Regression (a macOS Python 3.9 CI run: 350 of 400, every worker exit 0). `_spawn` used
    to rewrite worker.py for each worker; a rewrite truncates first, and a worker whose interpreter
    opened the file in that gap ran an empty program: exit 0, no adds. Here every rewrite after the
    first holds the truncated file for 1 s, so the old `_spawn` loses the first worker every time."""
    real_write = Path.write_text
    writes = []

    def slow_rewrite(self, data, *args, **kwargs):
        if self.name == "worker.py" and writes:
            with open(self, "w") as handle:
                handle.flush()
                time.sleep(1.0)
                handle.write(data)
            return len(data)
        writes.append(self)
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", slow_rewrite)
    repo = new_repo(tmp_path / "repo")
    writer().init(repo)
    procs = [_spawn(tmp_path, repo, "append", str(n)) for n in range(2)]
    (tmp_path / "go").write_text("")
    outs = [proc.communicate(timeout=120) for proc in procs]
    assert [out.strip() for out, _ in outs] == ["appended 50", "appended 50"], outs
    assert len((repo / ".repacted" / "tasks.jsonl").read_text().splitlines()) == 100


def test_init_racing_appends_loses_no_line(tmp_path):
    """init is run again by setup and the importers; it only creates a missing log, so a concurrent
    init never truncates or replaces the file two writers are appending to."""
    repo = new_repo(tmp_path / "repo")
    writer().init(repo)
    procs = [_spawn(tmp_path, repo, "append", "0"), _spawn(tmp_path, repo, "init"),
             _spawn(tmp_path, repo, "append", "1")]
    (tmp_path / "go").write_text("")
    outs = [proc.communicate(timeout=240) for proc in procs]
    assert [out.strip() for out, _ in outs] == ["appended 50", "initialised 50", "appended 50"], outs
    assert len((repo / ".repacted" / "tasks.jsonl").read_text().splitlines()) == 100


def test_writers_naming_the_repo_by_a_symlink_and_by_its_real_path_share_one_lock(tmp_path):
    """The store resolves the main checkout, so two spellings of one repo (a symlink, or /var and
    /private/var on macOS) lock and append to the same files."""
    repo = new_repo(tmp_path / "repo")
    writer().init(repo)
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    procs = [_spawn(tmp_path, repo, "append", "0"), _spawn(tmp_path, alias, "append", "1")]
    (tmp_path / "go").write_text("")
    outs = [proc.communicate(timeout=240) for proc in procs]
    assert [out.strip() for out, _ in outs] == ["appended 50", "appended 50"], outs
    assert len((repo / ".repacted" / "tasks.jsonl").read_text().splitlines()) == 100
    assert not (alias / ".repacted" / "tasks.lock").is_symlink()
    assert writer().Store.locate(alias).lock == writer().Store.locate(repo).lock


def test_a_deleted_lock_is_recreated_and_an_unopenable_lock_refuses_the_write(tmp_path):
    """`git clean -X` removes the ignored lock: the next write recreates it. A lock that cannot be
    opened raises before anything is appended, so an add never reports success unlocked."""
    repo = new_repo(tmp_path / "repo")
    store = writer().init(repo)
    store.add("first")
    store.lock.unlink()
    store.add("second")
    assert store.lock.is_file()
    if os.geteuid() == 0:
        pytest.skip("root opens a read-only file anyway")
    store.lock.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            store.add("third")
    finally:
        store.lock.chmod(0o644)
    titles = [json.loads(line)["title"] for line in store.log.read_text().splitlines()]
    assert titles == ["first", "second"]


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
