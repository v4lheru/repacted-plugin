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

# Slow tier: deselected by the default full run, included by `run-tests --full --all`.
pytestmark = pytest.mark.slow

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


