"""
Location: pact-plugin/tests/test_docker_lock_gate.py
Summary: hooks/docker_lock_gate.py, the Bash check that refuses a container test run unless the
         caller holds the 'docker' lock of bin/repact-lock. A parser on the command line plus a
         deny, so these are its boundary cases: the shapes a command that starts containers can
         take, the read-only and quoted look-alikes that must pass, the project scan, the lock
         states (free, another holder, expired, damaged, from the future), the caller that
         cannot be named, the advise and off switches, and the journal row.
Used by: the plugin suite. The end-to-end tests run the hook and the CLI as subprocesses
         against a temporary config root and plugin data dir, never the live lock file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
HOOK = PLUGIN / "hooks" / "docker_lock_gate.py"
CLI = PLUGIN / "bin" / "repact-lock"
import docker_lock_gate as gate  # noqa: E402  (hooks/ is on sys.path via conftest)

NEEDS_LOCK = [
    ("docker run -d postgres", "docker_run"),
    ("bash -lc 'cd a && docker compose up'", "compose_up"),
    ("while true; do docker compose up -d; done", "compose_up"),
    ("bash -c 'for i in 1; do docker run x; done'", "docker_run"),
    ("echo 'docker run été' | bash", "docker_run"),
]

NEVER = [
    "docker ps",
    'echo "docker run x"',
    "ls docker-run/",
    "gtimeout 5 docker info",
]


@pytest.mark.parametrize("command,code", NEEDS_LOCK)
def test_commands_that_start_containers_are_found(command, code):
    assert gate.detect(command).starts == code


@pytest.mark.parametrize("command", NEVER)
def test_read_only_quoted_and_commented_docker_words_never_need_the_lock(command):
    finding = gate.detect(command)
    assert finding.starts is None and finding.runner is None


@pytest.mark.parametrize("command,code,marker", [
    ("dotnet test --filter Category=Testcontainers", "dotnet_test", True),
])
def test_test_runners_and_the_command_line_marker(command, code, marker):
    finding = gate.detect(command)
    assert (finding.runner, finding.marker) == (code, marker)


@pytest.mark.parametrize("command", [
    "npm run build",
])
def test_commands_that_run_no_tests_are_not_runners(command):
    assert gate.detect(command).runner is None


def _nested(command: str, times: int) -> str:
    for _ in range(times):
        command = "bash -c " + shlex.quote(command)
    return command


@pytest.mark.parametrize("value,doubt", [
    (None, None),
])
def test_odd_input_is_read_or_doubted_never_raised(value, doubt):
    assert gate.detect(value).doubt == doubt


@pytest.mark.parametrize("command", [
    "do " * 50_000 + "docker run x",
])
def test_keyword_and_pipe_floods_are_read_in_linear_time(command):
    started = time.monotonic()
    assert gate.detect(command).starts == "docker_run"
    assert time.monotonic() - started < 2.0


def test_nesting_within_the_limit_is_read():
    assert gate.detect(_nested("docker run x", gate.MAX_DEPTH - 1)).starts == "docker_run"


def test_a_leading_cd_target_is_where_the_project_scan_starts(tmp_path):
    (tmp_path / "svc").mkdir()
    finding = gate.detect("cd svc && dotnet test")
    assert finding.cd_target == "svc"
    assert gate.scan_start(finding, str(tmp_path)) == str(tmp_path / "svc")
    assert gate.scan_start(gate.detect("cd $DIR && dotnet test"), str(tmp_path)) == str(tmp_path)
    assert gate.scan_start(finding, None) is None


# --- the project scan ------------------------------------------------------------------------

def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_project_scan_finds_a_reference_below_the_solution_root(tmp_path):
    root = _repo(tmp_path)
    tests = root / "tests" / "Api.Tests"
    tests.mkdir(parents=True)
    (tests / "Api.Tests.csproj").write_text('<PackageReference Include="Testcontainers.MsSql" />')
    (root / "node_modules" / "x").mkdir(parents=True)
    assert gate.project_marker(str(root)) == (True, None)
    assert gate.project_marker(str(tests)) == (True, None)


def test_project_scan_reads_manifests_only_and_stops_at_the_git_root(tmp_path):
    (tmp_path / "package.json").write_text('{"devDependencies": {"testcontainers": "10"}}')
    root = _repo(tmp_path)
    (root / "README.md").write_text("we use Testcontainers")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "package.json").write_text('"testcontainers"')
    assert gate.project_marker(str(root)) == (False, None)


def test_project_scan_error_is_no_marker(tmp_path):
    hit, error = gate.project_marker(str(tmp_path / "missing"))
    assert hit is False and error


# --- the decision ----------------------------------------------------------------------------

def _same(a, b):
    return a.casefold() == b.casefold()


def _record(owner, age_s, ttl_s=1800.0):
    return {"name": "docker", "owner": owner, "acquired": 1000.0 - age_s, "ttl_s": ttl_s}


@pytest.mark.parametrize("records,state", [
    ([], gate.FREE),
])
def test_hold_state(records, state):
    assert gate.hold_state(records, "lock1@t", 1000.0, _same).state == state


def test_decide_names_the_acquire_command_and_the_expired_hold():
    refused = gate.decide("docker_run", "lock1@t", gate.Hold(gate.FREE), gate.MODE_ENFORCE)
    assert refused.verdict == "deny" and "acquire docker --owner lock1@t --wait 600" in refused.text
    expired = gate.decide("docker_run", "lock1@t", gate.Hold(gate.EXPIRED, "lock1@t", 1900, -100),
                          gate.MODE_ENFORCE)
    assert "your hold expired" in expired.text
    busy = gate.decide("docker_run", "lock1@t", gate.Hold(gate.OTHER, "coder@t", 60, 1740),
                       gate.MODE_ADVISE)
    assert busy.verdict == "advise" and "coder@t holds" in busy.text
    assert gate.decide("docker_run", "lock1@t", gate.Hold(gate.HELD), gate.MODE_ENFORCE) is None
    assert gate.decide("docker_run", None, None, gate.MODE_ENFORCE).verdict == "allow"
    assert gate.decide(None, "lock1@t", gate.Hold(gate.FREE), gate.MODE_ENFORCE) is None


# --- end to end ------------------------------------------------------------------------------

SESSION = "11111111-2222-3333-4444-555555555555"
TEAM = "session-11111111"


@pytest.fixture()
def world(tmp_path):
    config = tmp_path / "config"
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    session_dir = config / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps(
        {"team_name": TEAM, "session_id": SESSION, "project_dir": str(project)}))
    (config / "teams" / TEAM).mkdir(parents=True)
    (config / "teams" / TEAM / "config.json").write_text(json.dumps(
        {"leadAgentId": f"team-lead@{TEAM}", "leadSessionId": SESSION, "members": []}))
    env = {k: v for k, v in os.environ.items() if k != "PACT_DOCKER_LOCK"}
    env.update(CLAUDE_CONFIG_DIR=str(config), CLAUDE_PROJECT_DIR=str(project),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    return {"env": env, "project": project, "session_dir": session_dir,
            "locks": tmp_path / "data" / "locks"}


def _hook(world, command, agent_type="lock1", **env):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": SESSION,
               "cwd": str(world["project"]), "agent_type": agent_type}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env={**world["env"], **env})


def _lock(world, *args, env=None):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          env={**world["env"], **(env or {})})


def _rows(world):
    path = world["session_dir"] / "session-journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_refused_without_the_lock_then_allowed_once_acquired(world):
    refused = _hook(world, "docker compose up -d")
    assert refused.returncode == 2
    reason = json.loads(refused.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert f"acquire docker --owner lock1@{TEAM} --wait 600" in reason
    rows = [row for row in _rows(world) if row.get("type") == "gate_refusal"]
    assert len(rows) == 1 and rows[0]["code"] == "compose_up" and rows[0]["rule"] == gate.FREE
    assert "compose up" not in json.dumps(rows[0])

    # Acquired from a Bash call that has no CLAUDE_PLUGIN_DATA: the default dir the hook reads too.
    env = {k: v for k, v in world["env"].items() if k != "CLAUDE_PLUGIN_DATA"}
    took = subprocess.run([sys.executable, str(CLI), "acquire", "docker", "--owner", f"Lock1@{TEAM}"],
                          capture_output=True, text=True, env=env)
    assert took.returncode == 0, took.stderr
    assert _hook(world, "docker compose up -d").returncode == 0
    assert _hook(world, "docker compose up -d", agent_type="coder").returncode == 2


def test_an_expired_hold_is_refused_as_expired(world):
    assert _lock(world, "acquire", "docker", "--owner", f"lock1@{TEAM}", "--ttl", "0.001").returncode == 0
    time.sleep(0.2)
    refused = _hook(world, "docker run x")
    assert refused.returncode == 2 and "your hold expired" in refused.stderr


@pytest.mark.parametrize("content", [
    "",
    "future",
])
def test_a_damaged_or_future_lock_file_is_not_a_hold(world, content):
    if content == "future":     # stamped from the clock here, not at collection, so the test id is stable
        content = json.dumps({"name": "docker", "owner": f"lock1@{TEAM}",
                              "acquired": time.time() + 7200, "ttl_s": 1800})
    world["locks"].mkdir(parents=True)
    name = hashlib.sha256(b"docker").hexdigest()[:32] + ".lock"     # the file repact-lock names
    (world["locks"] / name).write_text(content)
    assert _hook(world, "docker run x").returncode == 2


def test_a_project_that_references_testcontainers_needs_the_lock_for_a_plain_test_run(world):
    (world["project"] / "Api.Tests.csproj").write_text('<PackageReference Include="Testcontainers" />')
    assert _hook(world, "dotnet test").returncode == 2
    assert _hook(world, "dotnet build").returncode == 0


def test_advise_and_off_switches(world):
    advised = _hook(world, "docker run x", PACT_DOCKER_LOCK="advise")
    assert advised.returncode == 0 and "additionalContext" in advised.stdout
    assert [row["verdict"] for row in _rows(world) if row["type"] == "gate_refusal"] == ["advise"]
    off = _hook(world, "docker run x", PACT_DOCKER_LOCK="off")
    assert off.returncode == 0 and json.loads(off.stdout) == {"suppressOutput": True}


def test_a_caller_that_cannot_be_named_is_allowed_and_journaled(world):
    other_session = {"tool_name": "Bash", "tool_input": {"command": "docker run x"},
                     "session_id": "99999999-0000-0000-0000-000000000000", "cwd": "/"}
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(other_session), text=True,
                            capture_output=True, env=world["env"])
    assert result.returncode == 0
    lead = _hook(world, "docker run x", agent_type=None)
    assert lead.returncode == 2 and f"--owner team-lead@{TEAM}" in lead.stderr


@pytest.mark.parametrize("stdin", [
    "",
])
def test_malformed_payloads_allow(world, stdin):
    result = subprocess.run([sys.executable, str(HOOK)], input=stdin, text=True,
                            capture_output=True, env=world["env"])
    assert result.returncode == 0


def test_the_dispatcher_runs_the_gate_on_bash(world, tmp_path):
    """Registered in the dispatcher: a docker run with no lock held is refused, with the gate's
    own gate_refusal row only; PACT_DOCKER_LOCK=off lets it through."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "docker run x"},
                          "session_id": SESSION, "cwd": str(world["project"]), "agent_type": "lock1"})
    dispatcher = [sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse"]
    env = {**world["env"], "HOME": str(tmp_path)}
    refused = subprocess.run(dispatcher, input=payload, text=True, capture_output=True, env=env)
    assert refused.returncode == 2
    assert f"--owner lock1@{TEAM}" in json.loads(refused.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    rows = [row for row in _rows(world) if row.get("type") == "gate_refusal"]
    assert [(row["hook"], row["code"]) for row in rows] == [("docker_lock_gate", "docker_run")]

    allowed = subprocess.run(dispatcher, input=payload, text=True, capture_output=True,
                             env={**env, "PACT_DOCKER_LOCK": "off"})
    assert allowed.returncode == 0, allowed.stderr
    assert "deny" not in allowed.stdout
