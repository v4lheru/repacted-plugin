"""scripts/run-tests: the machine-wide suite lock (build plan §6, D11).

Every test points REPACTED_LOCK_DIR at tmp_path, so it never touches the real /private/tmp slots
(which the run that executes this file may itself be holding).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PLUGIN_ROOT.parent / "scripts" / "run-tests"
LOCKF = "/usr/bin/lockf"

pytestmark = pytest.mark.skipif(not Path(LOCKF).exists(), reason="macOS lockf(1) not present")


def run(args, lock_dir, timeout=60, **env_extra):
    env = {**os.environ, "REPACTED_LOCK_DIR": str(lock_dir), **env_extra}
    return subprocess.run([sys.executable, str(WRAPPER), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def hold(lock: Path, seconds: float) -> subprocess.Popen:
    """Hold lock with lockf(1) for `seconds`, and wait until it is really held."""
    proc = subprocess.Popen([LOCKF, "-k", str(lock), "sleep", str(seconds)])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        probe = subprocess.run([LOCKF, "-k", "-t", "0", str(lock), "true"])
        if probe.returncode == 75:
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("lockf never acquired the lock")


def raw(lock_dir, code, *extra, **env_extra):
    return run(["--raw", "--pkg", "t", *extra, "--", sys.executable, "-c", code], lock_dir, **env_extra)


def test_a_lockf_holder_of_the_suite_slot_blocks_a_full_run(tmp_path):
    holder = hold(tmp_path / "repacted-suite.lock", 5)
    try:
        result = run(["--full", "--wait", "0.5", "--pkg", "t"], tmp_path)
    finally:
        holder.kill()
    assert result.returncode == 75, result.stderr
    assert "suite slot not acquired" in result.stderr


def test_a_targeted_run_waits_only_for_a_worker_slot(tmp_path):
    """The suite slot being busy must not stop a targeted run: it never takes it."""
    holder = hold(tmp_path / "repacted-suite.lock", 5)
    try:
        result = raw(tmp_path, "print('ran')", "--wait", "0.5")
    finally:
        holder.kill()
    assert result.returncode == 0, result.stderr
    assert "ran" in result.stdout


def test_both_worker_slots_held_means_exit_75(tmp_path):
    holders = [hold(tmp_path / f"repacted-worker-{n}.lock", 5) for n in (1, 2)]
    try:
        result = raw(tmp_path, "print('ran')", "--wait", "0.5")
    finally:
        for h in holders:
            h.kill()
    assert result.returncode == 75, result.stderr
    assert "ran" not in result.stdout


def test_one_free_worker_slot_is_enough(tmp_path):
    holder = hold(tmp_path / "repacted-worker-1.lock", 5)
    try:
        result = raw(tmp_path, "print('ran')", "--wait", "0.5")
    finally:
        holder.kill()
    assert result.returncode == 0, result.stderr
    assert "repacted-worker-2.lock" in result.stderr


def test_the_wrapper_lock_excludes_lockf(tmp_path):
    """The other direction: while the wrapper runs, lockf(1) cannot take its slot."""
    code = ("import subprocess,sys; r=subprocess.run(['%s','-k','-t','0',sys.argv[1],'true']);"
            "print('lockf-exit', r.returncode)" % LOCKF)
    env = {**os.environ, "REPACTED_LOCK_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, str(WRAPPER), "--raw", "--pkg", "t", "--",
                             sys.executable, "-c", code, str(tmp_path / "repacted-worker-1.lock")],
                            env=env, capture_output=True, text=True, timeout=60)
    assert "lockf-exit 75" in result.stdout, result.stdout + result.stderr


def test_the_owner_sidecar_names_the_package_and_is_removed_after(tmp_path):
    code = "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())"
    env = {**os.environ, "REPACTED_LOCK_DIR": str(tmp_path)}
    sidecar = tmp_path / "repacted-worker-1.lock.owner"
    result = subprocess.run([sys.executable, str(WRAPPER), "--raw", "--pkg", "w0-infra", "--",
                             sys.executable, "-c", code, str(sidecar)],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "pkg=w0-infra" in result.stdout
    assert not sidecar.exists()


def _wrapper_module():
    """The wrapper imported in-process, without leaving a __pycache__ in scripts/."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("run_tests_quick", str(WRAPPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module




def run_clean(args, lock_dir, drop=(), timeout=120, **env_extra):
    """run(), without the variables in `drop` that the run executing this file may have set."""
    env = {key: value for key, value in os.environ.items() if key not in drop}
    env.update({"REPACTED_LOCK_DIR": str(lock_dir), **env_extra})
    return subprocess.run([sys.executable, str(WRAPPER), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


