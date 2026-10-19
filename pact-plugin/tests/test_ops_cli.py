"""
Location: pact-plugin/tests/test_ops_cli.py
Summary: The workflow operations through the real launcher, `bin/repact <op>` (routed to
bin/_repact_ops.py): dispatch, then the coder's teachback submit; a coder's accept refused with
the way through and the rule id; and the strict reader refusing NaN and a bigint in a file.
Used by: pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from fixtures.ops_env import CODER, SESSION_ID, TEAM, World, ops_env, teachback  # noqa: F401  # fixture

REPACT = Path(__file__).resolve().parent.parent / "bin" / "repact"


def _repact(world: World, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items()
           if key not in ("REPACT_AGENT_NAME", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
    env["CLAUDE_CONFIG_DIR"] = str(world.root / "config")
    where = ["--team", TEAM, "--session-dir", str(world.session_dir), "--session-id", SESSION_ID]
    return subprocess.run(["bash", str(REPACT), *args, *where], capture_output=True, text=True,
                          env=env, cwd=str(world.repo), timeout=120)


def _write(world: World, name: str, text) -> str:
    path = world.root / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return str(path)


def test_dispatch_submit_and_a_refusal_through_bin_repact(ops_env: World):
    mission = _write(ops_env, "mission.md", "Fix the empty-input case in parser.py.\n")
    done = _repact(ops_env, "dispatch", "--name", CODER, "--agent", "pact-backend-coder",
                   "--subject", "Fix the parser", "--brief-file", mission, "--variety", "1,1,1,1",
                   "--kind", "fix", "--branch", "fix/parser")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("Dispatched #1 to coder-1") and len(done.stdout.splitlines()) <= 6

    tb = _write(ops_env, "tb.json", json.dumps(teachback()))
    done = _repact(ops_env, "submit", "1", "--as", CODER, "--teachback", tb)
    assert done.returncode == 0 and "Accepted automatically" in done.stdout, done.stderr

    done = _repact(ops_env, "accept", "1", "--as", CODER)
    lines = done.stderr.splitlines()
    assert done.returncode == 2 and done.stdout == ""
    assert lines[0].startswith("refused: `repact accept` is the lead's")
    assert lines[-1].startswith("→ ") and lines[-1].endswith("[rule: ops.lead_only]")


def test_the_cli_reads_input_files_strictly(ops_env: World):
    ops_env.dispatch()
    for name, text in (("nan.json", '{"produced": [NaN]}'), ("big.json", '{"rev": ' + "9" * 5000 + "}"),
                       ("cut.json", "[1, 2"), ("latin1.json", b"\xff\xfe{}")):
        done = _repact(ops_env, "submit", "1", "--as", CODER, "--handoff", _write(ops_env, name, text))
        assert done.returncode == 2, (name, done.stdout, done.stderr)
        assert done.stderr.splitlines()[-1].endswith("[rule: ops.invalid_input]"), name
    assert "handoff" not in ops_env.task("1")["metadata"]


def test_a_usage_error_prints_the_subcommands_example(ops_env: World):
    done = _repact(ops_env, "reject", "1")
    assert done.returncode == 2
    assert done.stderr.rstrip().endswith("repact reject 12 --reason \"The fix does not cover the empty input\"")
