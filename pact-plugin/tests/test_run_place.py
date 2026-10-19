"""Where a Bash call started its test run: shared/run_place.run_dir reads the
command in the shell's order, and the run-ledger hook records it as run_dir on its row.

Location: pact-plugin/tests/test_run_place.py. The table holds the hostile spellings at
the parser's boundary (a cd in a subshell, the background, a comment, a heredoc, quoted text, a
pipeline); the last test runs a real tcount and the real hook, so the recorded shape is live.
Run: tcount pytest -q tests/test_run_place.py (from the plugin root)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import evidence_check as ec
from shared.ledger import read_runs
from shared.run_place import run_dir

PLUGIN = Path(__file__).resolve().parent.parent

PLACES = [
    ("pytest -q", "/c"),
    ("cd /s && pytest -q", "/s"),
    ("cd sub && pytest -q", "/c/sub"),
    ("git -C /wt status; cd /s && pytest -q", "/s"),
    ("cd /wt; (cd /s && pytest -q)", "/s"),
    ("cd /s; (cd /wt); pytest -q", "/s"),
    ("cd /s; x=$(cd /wt; pwd); pytest -q", "/s"),
    ("cd /s; cd /wt & pytest -q", "/s"),
    ("cd /s; echo 'x; cd /wt'; pytest -q", "/s"),
    ('cd /s; echo "x; cd /wt"; pytest -q', "/s"),
    ("cd /s # ; cd /wt\npytest -q", "/s"),
    ("cd /s\ncat <<EOF\ncd /wt\nEOF\npytest -q", "/s"),
    ("cd /s\n   cd /wt && pytest -q", "/wt"),
    ('W=/wt; cd "$W" && pytest -q', "/wt"),
    ("{ cd /wt; } && pytest -q", "/wt"),
    ("2>/dev/null cd /wt && pytest -q", "/wt"),
    ("cd /wt 2>&1 && pytest -q", "/wt"),
    ("cd -P /wt && nice -n 19 tcount pytest -q", "/wt"),
    ('cd "$(mktemp -d)" && pytest -q', None),
    ("cd $NEVER_SET && pytest -q", None),
    ("cd - && pytest -q", None),
    ("cd && pytest -q", None),
    ("pushd /wt; popd; pytest -q", None),
    ("true | cd /wt; pytest -q", None),
    ("cd /a && pytest -q; cd /b && pytest -q", None),
    ("cd /s; echo 'never closed && pytest -q", None),
    ("cd /s && ls", None),
]


@pytest.mark.parametrize("command, expected", PLACES)
def test_run_dir_is_the_directory_in_force_at_the_runner(command, expected, monkeypatch):
    monkeypatch.delenv("CDPATH", raising=False)
    assert run_dir(command, "/c") == expected


def test_a_relative_cd_under_cdpath_is_not_settled(monkeypatch):
    monkeypatch.setenv("CDPATH", "/elsewhere")
    assert run_dir("cd sub && pytest -q", "/c") is None
    assert run_dir("cd ./sub && pytest -q", "/c") == "/c/sub"


def tcount_then_hook(command: str, cwd: Path, main: Path, env: dict) -> None:
    """Run tcount for real in `cwd`, then hand its output to the run-ledger hook as the Bash
    call `command`, made from the session cwd `main`."""
    done = subprocess.run([sys.executable, str(PLUGIN / "bin" / "tcount"), sys.executable, "-m", "pytest", "-q",
                           "-p", "no:cacheprovider", "test_x.py"], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=120)
    assert "log:" in done.stdout, done.stdout + done.stderr
    payload = {"session_id": "s-h1", "cwd": str(main), "hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, "description": "run"},
               "tool_response": {"stdout": done.stdout, "stderr": "", "interrupted": False}}
    hooked = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "run_ledger.py")], input=json.dumps(payload),
                            env=env, capture_output=True, text=True, timeout=60)
    assert hooked.returncode == 0, hooked.stderr


def test_a_real_tcount_run_records_where_it_ran_not_the_worktree_the_call_names(tmp_path):
    main, tree, scratch = tmp_path / "main", tmp_path / "wt", tmp_path / "scratch"
    for place in (main, tree, scratch):
        place.mkdir()
        (place / "test_x.py").write_text("def test_x():\n    pass\n")
    (tree / ".git").write_text("gitdir: /nowhere\n")
    data = tmp_path / "data"
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_CODE_SESSION_ID": "s-h1",
           "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "REPACT_SCRUB_DENYLIST": str(tmp_path / "none"),
           "REPACT_SCRUB_DENYLIST_WORDS": str(tmp_path / "none-words")}
    argv = f"tcount {sys.executable} -m pytest -q -p no:cacheprovider test_x.py"
    tcount_then_hook(f"git -C {tree} status; cd {scratch} && {argv}", scratch, main, env)
    tcount_then_hook(f"cd {tree} && {argv}", tree, main, env)
    runs = read_runs(data / "runs" / "ledger.jsonl")[0]
    outside, inside = [run for run in runs if run.get("source") == "hook"]
    assert outside["run_dir"] == os.path.realpath(scratch) and str(tree) in outside["call_dirs"]
    assert ec._where(outside) == os.path.realpath(scratch)            # the evidence check's place: tcount's cwd
    assert inside["run_dir"] == ec._where(inside) == os.path.realpath(tree)
