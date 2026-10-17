"""scripts/run-tests: the machine-wide suite lock (build plan §6, D11).

Every test points REPACTED_LOCK_DIR at tmp_path, so it never touches the real /private/tmp slots
(which the run that executes this file may itself be holding).
"""
from __future__ import annotations

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


def test_status_reads_the_sidecar_not_the_process_table(tmp_path):
    (tmp_path / "repacted-suite.lock.owner").write_text("pkg=someone pid=1 since=x\n")
    holder = hold(tmp_path / "repacted-suite.lock", 5)
    try:
        result = run(["--status"], tmp_path)
    finally:
        holder.kill()
    assert "repacted-suite.lock: HELD (pkg=someone" in result.stdout
    assert "repacted-worker-1.lock: free" in result.stdout


def test_the_child_environment_is_isolated_and_niced(tmp_path):
    code = ("import json, os, sys; env = dict(os.environ); keys = ('HOME', 'CLAUDE_CONFIG_DIR', "
            "'CLAUDE_PLUGIN_ROOT', 'PYTHONDONTWRITEBYTECODE'); seen = {k: env.get(k) for k in keys}; "
            "seen['NICE'] = os.getpriority(os.PRIO_PROCESS, 0); seen['CWD'] = os.getcwd(); "
            "sys.stdout.write(json.dumps(seen))")
    result = raw(tmp_path, code, CLAUDE_CONFIG_DIR="/nowhere", CLAUDE_PLUGIN_ROOT="/nowhere",
                 HOME=str(tmp_path / "real-home"))
    out = json.loads(result.stdout)
    assert out["HOME"] != str(tmp_path / "real-home")
    assert out["CLAUDE_CONFIG_DIR"] is None and out["CLAUDE_PLUGIN_ROOT"] is None
    assert out["PYTHONDONTWRITEBYTECODE"] == "1"
    assert out["NICE"] >= 19
    assert Path(out["CWD"]).resolve() == PLUGIN_ROOT.resolve()


def test_the_temp_home_is_removed_and_a_given_home_is_kept(tmp_path):
    code = "import os, sys; sys.stdout.write(os.path.expanduser('~'))"
    temp_home = raw(tmp_path, code).stdout.strip()
    assert temp_home and not Path(temp_home).exists()
    kept = tmp_path / "kept-home"
    kept.mkdir()
    assert raw(tmp_path, code, "--home", str(kept)).stdout.strip() == str(kept)
    assert kept.is_dir()


def test_a_term_reaches_the_grandchild_and_frees_the_slot(tmp_path):
    """A signal to the wrapper must stop the whole tree (tcount -> pytest), not orphan pytest."""
    marker = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time; "
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "open(sys.argv[1], 'w').write(str(g.pid)); time.sleep(60)")
    env = {**os.environ, "REPACTED_LOCK_DIR": str(tmp_path)}
    wrapper = subprocess.Popen([sys.executable, str(WRAPPER), "--raw", "--pkg", "t", "--",
                                sys.executable, "-c", code, str(marker)], env=env)
    deadline = time.monotonic() + 20
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    grandchild = int(marker.read_text())
    wrapper.terminate()
    wrapper.wait(timeout=20)
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)
    probe = subprocess.run([LOCKF, "-k", "-t", "0", str(tmp_path / "repacted-worker-1.lock"), "true"])
    assert probe.returncode == 0


@pytest.mark.parametrize("args", [["-n", "2", "tests/"], ["-n2", "tests/"], ["--numprocesses=2", "tests/"],
                                  ["--full", "--raw", "--", "true"]],
                         ids=["xdist-n", "xdist-n2", "xdist-long", "full-and-raw"])   # no args = QUICK (#254)
def test_usage_errors_exit_64(tmp_path, args):
    assert run(args, tmp_path).returncode == 64


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


def test_quick_is_the_default_and_selects_the_tests_that_name_a_changed_file():
    """LEAN-DEFAULTS (#254): with no args, run-tests runs only the tests that import or name
    what changed, plus SMOKE, and never the whole suite."""
    module = _wrapper_module()
    plugin, root = module.quick_selection(["pact-plugin/bin/_tasks/views.py"])
    assert "tests/test_project_tasks_views.py" in plugin
    assert set(module.SMOKE) <= set(plugin)
    assert len(plugin) < 40, "a QUICK run must stay small"




def test_the_default_interpreter_is_the_venv_when_present(tmp_path, monkeypatch):
    """T-9de96f0e: one interpreter for every builder: --python, then $REPACTED_PYTHON, then a
    venv, and only then python3 from PATH."""
    module = _wrapper_module()
    monkeypatch.delenv("REPACTED_PYTHON", raising=False)
    monkeypatch.setattr(module, "REPO", tmp_path / "my repo")
    monkeypatch.setattr(module, "main_checkout", lambda: None)
    assert module.default_python() == "python3"
    venv = tmp_path / "my repo" / ".venv" / "bin" / "python3"
    venv.parent.mkdir(parents=True)
    venv.write_text("#!/bin/sh\n")
    venv.chmod(0o755)
    assert module.default_python() == str(venv)
    monkeypatch.setenv("REPACTED_PYTHON", "/opt/py/bin/python3")
    assert module.default_python() == "/opt/py/bin/python3"
    assert module.default_python("/given/python3") == "/given/python3"


def run_clean(args, lock_dir, drop=(), timeout=120, **env_extra):
    """run(), without the variables in `drop` that the run executing this file may have set."""
    env = {key: value for key, value in os.environ.items() if key not in drop}
    env.update({"REPACTED_LOCK_DIR": str(lock_dir), **env_extra})
    return subprocess.run([sys.executable, str(WRAPPER), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_the_user_base_survives_the_scratch_home(tmp_path):
    """T-60f7c7bf: with pytest installed in the user site, the scratch HOME hid it and the run
    printed NO TEST COUNT. The child keeps the interpreter's user base under the account's home;
    a PYTHONUSERBASE the caller set is kept as it is."""
    import pwd
    code = ("import json, os, site, sys; sys.stdout.write(json.dumps([os.environ.get('PYTHONUSERBASE'), "
            "site.getuserbase(), os.path.expanduser('~')]))")
    args = ["--raw", "--pkg", "t", "--python", sys.executable, "--", sys.executable, "-c", code]
    home = pwd.getpwuid(os.getuid()).pw_dir
    plain = {key: value for key, value in os.environ.items() if key != "PYTHONUSERBASE"}
    expected = subprocess.run([sys.executable, "-c", "import site; print(site.getuserbase())"],
                              env={**plain, "HOME": home}, capture_output=True, text=True).stdout.strip()
    from_env, user_base, scratch_home = json.loads(run_clean(args, tmp_path, drop=("PYTHONUSERBASE",)).stdout)
    assert from_env == user_base == expected and scratch_home != home
    assert not user_base.startswith(scratch_home)
    kept = json.loads(run_clean(args, tmp_path, PYTHONUSERBASE=str(tmp_path / "mine")).stdout)
    assert kept[0] == kept[1] == str(tmp_path / "mine")


def test_the_embedding_model_cache_survives_the_scratch_home(tmp_path):
    """The requires_embedding_backend tests read the model from huggingface's cache under the
    account's home; the scratch HOME hid it and they failed offline. A caller's HF_HOME is kept."""
    import pwd
    code = "import os, sys; sys.stdout.write(os.environ.get('HF_HOME', ''))"
    args = ["--raw", "--pkg", "t", "--python", sys.executable, "--", sys.executable, "-c", code]
    home = pwd.getpwuid(os.getuid()).pw_dir
    got = run_clean(args, tmp_path, drop=("HF_HOME", "HF_HUB_CACHE", "XDG_CACHE_HOME")).stdout
    assert got == os.path.join(home, ".cache", "huggingface")
    assert run_clean(args, tmp_path, HF_HOME=str(tmp_path / "mine")).stdout == str(tmp_path / "mine")


def test_in_a_session_the_log_sits_beside_the_live_ledger_and_outside_one_under_tmpdir(tmp_path):
    """A suite keeps its scratch data dir, but tcount's log of it goes where the run ledger is,
    resolved as the run-ledger hook resolves it (the caller's config dir), when the caller is a
    Claude Code session. The row this wrapper copies to the live ledger names that log."""
    suite, config, tmp = tmp_path / "test_fx.py", tmp_path / "claude", tmp_path / "tmp"
    suite.write_text("def test_a(): assert 1\n", encoding="utf-8")
    tmp.mkdir()
    drop = ("CLAUDE_PLUGIN_DATA", "TCOUNT_LOG_DIR", "CLAUDE_CODE_SESSION_ID")
    args = ["--pkg", "t", "--python", sys.executable, "--", str(suite)]

    def log_of(result) -> Path:
        assert "pytest: collected 1 · passed 1 " in result.stdout, result.stdout + result.stderr
        return Path(next(line for line in result.stdout.splitlines() if line.startswith("log: "))[5:])

    live = config / "plugins" / "data" / "repacted-repacted-local" / "runs"
    log = log_of(run_clean(args, tmp_path, drop, CLAUDE_CONFIG_DIR=str(config), TMPDIR=str(tmp),
                           CLAUDE_CODE_SESSION_ID="s-1"))
    assert log.parent.resolve() == live.resolve() and log.is_file()
    rows = [json.loads(line) for line in (live / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(row["source"], row["log_path"], row["passed"]) for row in rows] == [("tcount", str(log), 1)]

    outside = log_of(run_clean(args, tmp_path, drop, CLAUDE_CONFIG_DIR=str(config), TMPDIR=str(tmp)))
    assert outside.parent.resolve() == (tmp / "repacted-test-runs" / "runs").resolve()


def test_lock_dir_is_machine_wide_and_portable(monkeypatch):
    """M7 (G8 review): the lock dir was a fixed /private/tmp, which does not exist on Linux. It is
    now the first directory of /private/tmp, /tmp and the system temp dir, never a per-session
    TMPDIR first (that would split the machine-wide lock); REPACTED_LOCK_DIR still wins."""
    module = _wrapper_module()
    monkeypatch.delenv("REPACTED_LOCK_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", "/nonexistent-session-tmp")
    present = {"/private/tmp": False, "/tmp": True}
    monkeypatch.setattr(module.os.path, "isdir", lambda path: present.get(path, False))
    assert module.lock_dir() == Path("/tmp")                       # Linux
    present["/private/tmp"] = True
    assert module.lock_dir() == Path("/private/tmp")               # macOS
    present.update({"/private/tmp": False, "/tmp": False})
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: "/var/fallback")
    assert module.lock_dir() == Path("/var/fallback")
    monkeypatch.setenv("REPACTED_LOCK_DIR", "/custom/locks")
    assert module.lock_dir() == Path("/custom/locks")
