"""Under `claude -p --plugin-dir` the hooks and the Bash tools read one run ledger.

The harness gives hooks CLAUDE_PLUGIN_DATA=<cfg>/plugins/data/repacted-inline but gives the Bash
tool no CLAUDE_PLUGIN_DATA, so tcount fell back to repacted-repacted-local and the run-ledger hook
never found its row. SessionStart now exports REPACTED_PLUGIN_DATA through $CLAUDE_ENV_FILE (shown
live to reach the Bash tool and a subagent's Bash under -p), and plugin_paths.plugin_data_dir()
reads CLAUDE_PLUGIN_DATA, then REPACTED_PLUGIN_DATA, then the default.

Location: pact-plugin/tests/test_run_ledger_plugin_dir.py. Drives the real session_init
main() (through test_session_init_env_file._run_main), the real bin/tcount and the real
hooks/run_ledger.py, in the shape of the reviewer's probe_d4.sh.
Run: tcount pytest -q tests/test_run_ledger_plugin_dir.py (from the plugin root)
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


from shared import plugin_paths
from test_session_init_env_file import _run_main

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FALLBACK = plugin_paths.PLUGIN_DATA_DIRNAME


def _rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _export():
    from shared.session_init_common import _persist_plugin_data_env
    _persist_plugin_data_env()


def _sourced(env_file: Path, name: str) -> str:
    """The value a shell gets for `name` after sourcing the env file, as the Bash tool does."""
    out = subprocess.run(["sh", "-c", f'. "$1"; printf %s "${{{name}}}"', "sh", str(env_file)],
                         capture_output=True, text=True, timeout=30, env={"PATH": os.environ["PATH"]})
    return out.stdout


def test_tcount_from_bash_and_the_run_ledger_hook_write_one_ledger(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg dir"                       # a config path with a space
    inline = cfg / "plugins" / "data" / "repacted-inline"   # not created yet: the writers make it
    env_file = tmp_path / "session-env.sh"
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    env_file.touch()                                 # the harness's per-hook env file
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(inline))
    _run_main(monkeypatch, tmp_path)                 # the real SessionStart main()

    # The Bash tool: no CLAUDE_PLUGIN_DATA, the env file sourced, then the real tcount.
    bash_env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_DATA", "REPACTED_PLUGIN_DATA",
                                                                 "CLAUDE_ENV_FILE", "TCOUNT_LOG_DIR")}
    bash_env.update(CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_CODE_SESSION_ID="sid-d4")
    script = f'. {shlex.quote(str(env_file))} && exec {shlex.quote(sys.executable)} ' \
             f'{shlex.quote(str(PLUGIN_ROOT / "bin" / "tcount"))} {shlex.quote(sys.executable)} ' \
             f'-m pytest -q -p no:cacheprovider test_ok.py'
    ran = subprocess.run(["sh", "-c", script], cwd=str(proj), capture_output=True, text=True,
                         timeout=120, env=bash_env)
    output = ran.stdout + ran.stderr
    assert "passed 1" in output, output

    # The hook: the harness's CLAUDE_PLUGIN_DATA, the same Bash call as its payload.
    payload = {"hook_event_name": "PostToolUse", "session_id": "sid-d4", "cwd": str(proj),
               "tool_name": "Bash", "tool_input": {"command": f"cd {proj} && tcount python -m pytest -q test_ok.py"},
               "tool_response": {"stdout": output, "stderr": "", "interrupted": False}}
    hook_env = {**bash_env, "CLAUDE_PLUGIN_DATA": str(inline), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    subprocess.run([sys.executable, str(PLUGIN_ROOT / "hooks" / "run_ledger.py")], input=json.dumps(payload),
                   capture_output=True, text=True, timeout=60, env=hook_env)

    rows = _rows(inline / "runs" / "ledger.jsonl")
    assert {row.get("source") for row in rows} == {"tcount", "hook"}
    hooked = [row.get("run_dir") for row in rows if row.get("source") == "hook"]
    assert len(hooked) == 1 and hooked[0] in (str(proj), str(proj.resolve()))
    assert not (cfg / "plugins" / "data" / FALLBACK).exists()


# ---------------------------------------------------------------------------------------------
# Boundary rows: the resolver and the SessionStart export
# ---------------------------------------------------------------------------------------------


