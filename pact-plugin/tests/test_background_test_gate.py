"""
Location: pact-plugin/tests/test_background_test_gate.py
Summary: hooks/background_test_gate.py refuses a teammate's test run in the background and
         lets everything else through: the lead, a dev server or watcher in the background, a
         foreground test run, and a frame whose role is unknown.
1.5.0 field report: teammates ran suites with run_in_background and idled on the finished job
three or more times in one night, though the brief said "foreground".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
HOOK = PLUGIN / "hooks" / "background_test_gate.py"
TEAMMATE = "repacted:pact-backend-coder"
LEAD = "repacted:pact-orchestrator"

TEST_RUNS = [
    "pytest -q tests/test_x.py",
    "python3 -m pytest -q",
    "python -m unittest discover",
    'cd "/w t/repo" && nice -n 19 python3 /w/repacted/scripts/run-tests -- tests/test_x.py 2>&1 | tail -5',
    "python3 scripts/run-tests -- tests/test_a.py",
    "tcount npm test",
    "tcount ./gate.sh",
    "env FOO=1 timeout 600 go test ./...",
    "make -C web test",
    "gmake check",
    "uv run pytest",
    'bash -c "pytest -q"',
    "pnpm vitest run",
    "npx jest --ci",
    "yarn test",
    "bun test",
    "npm run test:unit",
    "cargo test --workspace",
    "dotnet test App.sln --no-build",
    "rtk proxy pytest -x",
    "echo start; pytest",
]
NOT_TEST_RUNS = [
    "npm run dev",
    "pnpm dev --port 5173",
    "python3 -m http.server 8000",
    "make -C tests all",
    "make build",
    "vite",
    "echo pytest",
    "cat tests/test_x.py",
    "python3 -c 'import pytest'",
    "tail -f run-tests.log",
    "",
]


def _env(tmp_path: Path) -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"), "CLAUDE_PROJECT_DIR": str(tmp_path),
            # A Teams-only gate (shared/execution.py): the dispatcher runs it in teams mode.
            "REPACT_EXECUTION": "teams", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}


def _payload(command, agent_type=TEAMMATE, background=True) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s-bg-test",
               "cwd": "/tmp", "tool_input": {"command": command}}
    if background is not None:
        payload["tool_input"]["run_in_background"] = background
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def run_hook(payload, tmp_path, script=HOOK, *args) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(script), *args], input=data, text=True,
                          capture_output=True, env=_env(tmp_path), timeout=60)


def denied(done: subprocess.CompletedProcess) -> bool:
    out = json.loads(done.stdout) if done.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_a_teammate_test_run_in_the_background_is_refused_with_the_foreground_route(tmp_path):
    import background_test_gate as gate

    for command in TEST_RUNS:
        assert gate.runs_tests(command), command
    done = run_hook(_payload(TEST_RUNS[3]), tmp_path)
    assert denied(done) and done.returncode == 2
    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "without run_in_background" in reason and "600000" in reason


@pytest.mark.parametrize("command", NOT_TEST_RUNS)
def test_a_background_server_watcher_or_other_command_is_allowed(command, tmp_path):
    import background_test_gate as gate

    assert not gate.runs_tests(command)
    assert not denied(run_hook(_payload(command), tmp_path))


@pytest.mark.parametrize("flag", [True, "true", 1])
def test_every_spelling_of_the_flag_and_a_trailing_ampersand_count(flag, tmp_path):
    assert denied(run_hook(_payload("pytest -q", background=flag), tmp_path))
    assert denied(run_hook(_payload("pytest -q &", background=None), tmp_path))


@pytest.mark.parametrize("command", [
    "pytest -q & echo started", "(pytest -q &)", "( pytest -q & )", "pytest -q & disown",
    "setsid pytest -q &", "setsid -f pytest -q & disown", "nohup pytest -q > log 2>&1 &",
    "pytest -q 2>&1 | tee log &", "cd w && pytest -q &", "echo 'a & b'; pytest -q &",
    "(cd w; pytest -q) &", "make test-unit &", "npm run dev & pytest -q &", "pytest -q && echo ok &",
    "pytest&echo x",
])
def test_a_test_run_the_shell_backgrounds_anywhere_in_the_command_is_refused(command, tmp_path):
    """Second review F16: `&` before another command, in a subshell, or under setsid."""
    assert denied(run_hook(_payload(command, background=None), tmp_path)), command


@pytest.mark.parametrize("command", [
    "make test-server &", "make testdata &", "pytest -q; npm run dev &", "pytest -q\nnpm run dev &",
    "pytest -q & wait", "echo 'pytest &'", 'echo "pytest -q &"', "pytest -q 2>&1",
    "pytest -q &> log", "pytest -q |& tee log", "echo pytest &", "npm run dev & echo $!",
    "cat <<EOF\npytest -q &\nEOF", "pytest -q # &",
])
def test_a_foreground_test_run_or_a_background_non_test_is_allowed(command, tmp_path):
    """Second review F16: `make test-server &` was refused; redirections, quotes, heredocs,
    `& wait` and a test run before a backgrounded server are not a background test run."""
    assert not denied(run_hook(_payload(command, background=None), tmp_path)), command


@pytest.mark.parametrize("flag", [False, None, "false", 0, "yes"])
def test_a_foreground_test_run_is_allowed(flag, tmp_path):
    assert not denied(run_hook(_payload("pytest -q", background=flag), tmp_path))
    assert not denied(run_hook(_payload("pytest -q && echo done", background=flag), tmp_path))


def test_the_lead_may_background_a_test_run_and_an_unknown_frame_is_allowed_with_a_line(tmp_path):
    for agent_type in (LEAD, "pact-orchestrator"):
        assert not denied(run_hook(_payload("pytest -q", agent_type=agent_type), tmp_path))
    unknown = run_hook(_payload("pytest -q", agent_type=None), tmp_path)
    assert not denied(unknown) and unknown.returncode == 0
    assert "frame role unknown" in unknown.stderr


@pytest.mark.parametrize("raw", [
    "", "null", "[]", "{", json.dumps({"tool_name": "Bash", "tool_input": "pytest"}),
    json.dumps({"tool_name": "Bash", "agent_type": TEAMMATE, "tool_input": {"command": None,
                                                                            "run_in_background": True}}),
    json.dumps({"tool_name": "Bash", "agent_type": TEAMMATE, "tool_input": {"command": 7,
                                                                            "run_in_background": True}}),
    json.dumps({"tool_name": "Read", "agent_type": TEAMMATE, "tool_input": {"command": "pytest",
                                                                            "run_in_background": True}}),
])
def test_odd_stdin_is_allowed(raw, tmp_path):
    done = run_hook(raw, tmp_path)
    assert done.returncode == 0 and not denied(done)


def test_odd_commands_stay_bounded_and_never_raise(tmp_path):
    """Adversarial: unicode, an unbalanced quote, a huge command and a nested substitution."""
    import background_test_gate as gate

    for command in ("pytést -q", "pytest 'unclosed", "x" * 300_000 + " pytest", "$($($(pytest)))",
                    "p=pytest; $p", "\x00pytest", "ｐｙｔｅｓｔ"):
        assert gate.runs_tests(command) in (True, False)
    assert gate.runs_tests("echo $(pytest -q)")                   # a substitution runs too
    assert denied(run_hook(_payload("x" * 250_000 + "; pytest"), tmp_path)) in (True, False)
    for command in ("pytest 'unclosed &", ")))& pytest &((", "&&&&", "& & &", "ｐｙｔｅｓｔ &",
                    "\x00pytest &", "echo $(pytest &) &", "x &" * 20_000 + " pytest &"):
        assert isinstance(gate.backgrounded(command), list), command
    assert gate.backgrounded(")))& pytest &((") == [" pytest "]
    assert denied(run_hook(_payload("x &" * 20_000 + " pytest &", background=None), tmp_path))


def test_the_dispatcher_runs_the_gate_on_bash(tmp_path):
    dispatcher = PLUGIN / "hooks" / "dispatch.py"
    refused = run_hook(_payload("tcount python3 -m pytest"), tmp_path, dispatcher, "PreToolUse", "Bash")
    assert denied(refused), refused.stderr
    allowed = run_hook(_payload("npm run dev"), tmp_path, dispatcher, "PreToolUse", "Bash")
    assert not denied(allowed), allowed.stdout
