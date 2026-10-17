"""hooks/doctor_brief.py: the SessionStart line reads only the doctor's cache,
says only where the judge's switch stands when green, fails open, and starts a background refresh when the cache is stale.

Every run points CLAUDE_PLUGIN_DATA, HOME and CLAUDE_CONFIG_DIR at tmp_path; nothing touches the
live ~/.claude.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "hooks" / "doctor_brief.py"
SUPPRESS = {"suppressOutput": True}


def load(name: str, path: Path):
    """Import a file by path (bin/ is not on the suite's sys.path, and path setup stays in conftest)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def now_iso(delta_hours: float = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=delta_hours)
    return moment.isoformat(timespec="seconds")


def check(check_id, state, summary="s", detail=()):
    return {"id": check_id, "name": check_id, "state": state, "summary": summary, "detail": list(detail), "ran_at": "x"}


GREEN = [check(c, "green") for c in ("links", "split", "index", "secrets", "stale_refs", "release", "mcp", "gates")] \
    + [check("actions", "green"), check("mcp", "skip")]
RED = [check("links", "green"), check("secrets", "red", "credential values in 2 place(s)"),
       check("release", "amber", "release at v1; main has 3 newer tag(s)"),
       check("actions", "red", "2 open", ["#7 ALERT: SECURITY: rotate the fixture token (team-a)", "n8n guard hardening"])]


@pytest.fixture
def env(tmp_path):
    (tmp_path / "proj" / ".git").mkdir(parents=True)   # the session's repo, a main checkout
    (tmp_path / "data").mkdir()
    (tmp_path / "xdg" / "repacted").mkdir(parents=True)
    (tmp_path / "xdg" / "repacted" / "profile.json").write_text(
        json.dumps({"paths": {"vault": str(tmp_path / "vault" / "05-Education" / "Coding-Notes")}}))
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "home" / ".claude"), "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "REPACT_DOCTOR_REPO": str(tmp_path / "no-repo"),
            "REPACT_DOCTOR_NO_REFRESH": "1", "REPACT_SCRATCH_ROOT": str(tmp_path / "scratch-root")}


def session_repo(env) -> Path:
    return (Path(env["HOME"]).parent / "proj").resolve()


def write_cache(env, checks, generated=None, repo="session"):
    """`repo`: the repo the doctor read ("session": the session's own; None: none recorded)."""
    recorded = str(session_repo(env)) if repo == "session" else repo
    Path(env["CLAUDE_PLUGIN_DATA"], "doctor.json").write_text(
        json.dumps({"version": 1, "generated": generated or now_iso(), "repo": recorded, "checks": checks}))


def brief(env, source="startup", **extra):
    proc = subprocess.run([sys.executable, str(HOOK)],
                          input=json.dumps({"source": source, "cwd": str(session_repo(env))}),
                          env={**env, **extra}, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_silent_when_every_check_is_green_or_skipped(env):
    write_cache(env, GREEN)
    assert brief(env) == SUPPRESS


def test_silent_when_there_is_no_cache_yet(env):
    assert brief(env) == SUPPRESS


def test_one_line_when_something_is_red(env):
    write_cache(env, RED)
    out = brief(env)
    line = out["systemMessage"]
    assert "hookSpecificOutput" not in out and "\n" not in line    # 1.4.1: each line on one channel
    assert line.startswith("doctor: 1 red (secrets: credential values in 2 place(s)); 1 amber (release:")
    assert "2 user action(s) open: #7 ALERT: SECURITY: rotate the fixture token (team-a) · n8n guard hardening" in line


def test_outside_the_harness_the_cache_is_found_under_the_config_dir(env):
    """No CLAUDE_PLUGIN_DATA: the path comes from shared.paths.get_claude_config_dir()."""
    fallback = Path(env["CLAUDE_CONFIG_DIR"], "plugins", "data", "repacted-repacted")
    fallback.mkdir(parents=True)
    no_data = {k: v for k, v in env.items() if k != "CLAUDE_PLUGIN_DATA"}
    write_cache({**env, "CLAUDE_PLUGIN_DATA": str(fallback)}, RED)
    assert brief(no_data)["systemMessage"].startswith("doctor: 1 red")


def test_user_actions_alone_still_speak(env):
    write_cache(env, GREEN[:-2] + [check("actions", "amber", "1 open", ["Rotate the fixture keys"])])
    assert "1 user action(s) open: Rotate the fixture keys" in brief(env)["systemMessage"]


def brief_as(env, agent_type):
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"source": "startup", "agent_type": agent_type}),
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("agent_type", ["repacted:pact-backend-coder", "PACT:pact-architect", "pact-secretary"])
def test_a_teammate_session_stays_silent(env, agent_type):
    write_cache(env, RED)
    assert brief_as(env, agent_type) == SUPPRESS


def test_every_lead_spelling_gets_the_line(env):
    """Parity with shared.pact_context.LEAD_AGENT_TYPES, the set is_lead tests against."""
    from shared.pact_context import LEAD_AGENT_TYPES
    write_cache(env, RED)
    for agent_type in sorted(LEAD_AGENT_TYPES):
        assert "systemMessage" in brief_as(env, agent_type), agent_type


def test_a_teammate_start_may_still_refresh_a_stale_cache(env):
    write_cache(env, GREEN, generated=now_iso(delta_hours=30))
    cache = Path(env["CLAUDE_PLUGIN_DATA"], "doctor.json")
    assert brief_as({**env, "REPACT_DOCTOR_NO_REFRESH": ""}, "repacted:pact-backend-coder") == SUPPRESS
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if json.loads(cache.read_text()).get("generated", "") > now_iso(delta_hours=1):
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the teammate's start did not refresh the stale cache")


def test_a_compaction_restart_stays_silent(env):
    write_cache(env, RED)
    assert brief(env, source="compact") == SUPPRESS


@pytest.mark.parametrize("content", ["{not json", json.dumps({"checks": "nope"}), json.dumps([1, 2])])
def test_a_broken_cache_fails_open(env, content):
    Path(env["CLAUDE_PLUGIN_DATA"], "doctor.json").write_text(content)
    assert brief(env) == SUPPRESS


def test_a_completed_task_is_never_listed_and_an_old_cache_says_it_is_stale(env):
    """Regression (2026-09-29): a 45 h old cache listed HALT tasks completed an hour after it."""
    team = Path(env["CLAUDE_CONFIG_DIR"], "tasks", "team-a")
    team.mkdir(parents=True)
    (team / "7.json").write_text(json.dumps({"id": "7", "status": "completed"}))
    write_cache(env, RED)
    line = brief(env)["systemMessage"]
    assert "#7" not in line and "1 user action(s) open: n8n guard hardening" in line
    write_cache(env, RED, generated=now_iso(delta_hours=45))
    line = brief(env)["systemMessage"]
    assert "STALE result (45h old" in line and "user action" not in line and "2 place(s)" not in line
    assert "secrets, release" in line


def test_the_closed_statuses_match_the_shared_predicate():
    actions = load("user_actions_pin", PLUGIN_ROOT / "hooks" / "shared" / "user_actions.py")
    assert set(load("doctor_brief_closed_pin", HOOK).CLOSED_STATUSES) == set(actions.CLOSED_STATUSES)


def test_a_stale_cache_starts_a_background_refresh(env):
    write_cache(env, GREEN, generated=now_iso(delta_hours=30))
    cache = Path(env["CLAUDE_PLUGIN_DATA"], "doctor.json")
    started = time.monotonic()
    assert brief(env, REPACT_DOCTOR_NO_REFRESH="") == SUPPRESS
    assert time.monotonic() - started < 5, "the hook must not wait for the refresh"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        data = json.loads(cache.read_text()) if cache.exists() else {}
        if data.get("generated", "") > now_iso(delta_hours=1):
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the detached --quick refresh never rewrote the cache")
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["secrets"]["state"] == "skip", "a --quick refresh never runs the slow checks"


def test_a_fresh_cache_starts_no_refresh(env):
    write_cache(env, GREEN)
    cache = Path(env["CLAUDE_PLUGIN_DATA"], "doctor.json")
    before = cache.read_text()
    brief(env, REPACT_DOCTOR_NO_REFRESH="")
    time.sleep(3)
    assert cache.read_text() == before


def test_the_brief_adds_no_measurable_latency(env, tmp_path):
    """Median wall time of the hook reading a red cache vs a do-nothing hook, interleaved."""
    write_cache(env, RED)
    noop = tmp_path / "noop.py"
    noop.write_text("import json, sys\nsys.stdin.read()\nprint(json.dumps({'suppressOutput': True}))\n")

    def wall(script):
        start = time.perf_counter()
        subprocess.run([sys.executable, str(script)], input=b"{}", env=env, capture_output=True, timeout=30)
        return time.perf_counter() - start

    pairs = [(wall(noop), wall(HOOK)) for _ in range(15)]
    delta_ms = (statistics.median(h for _, h in pairs) - statistics.median(n for n, _ in pairs)) * 1000
    print(f"doctor_brief median delta over a no-op hook: {delta_ms:.1f} ms")
    assert delta_ms < 25


def test_another_repos_backlog_and_release_never_reach_this_session(env):
    """1.4.1 (aud-bugs item 10): the doctor read the plugin's own repo, and its backlog's
    USER-OWNED lines and release position reached every project's session start."""
    write_cache(env, RED, repo="/elsewhere/plugin-repo")
    line = brief(env)["systemMessage"]
    assert "n8n guard hardening" not in line and "release" not in line
    assert line.startswith("doctor: 1 red (secrets:") and "1 user action(s) open: #7 ALERT" in line
    write_cache(env, RED, repo=None)                       # a cache from before 1.4.1
    assert "n8n guard hardening" not in brief(env)["systemMessage"]


def test_the_doctor_reads_the_repo_of_its_working_directory(tmp_path):
    doctor = load("doctor_repo_pin", PLUGIN_ROOT / "bin" / "_doctor.py")
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "src").mkdir()
    env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert doctor.context_from_env(env, cwd=main / "src").repo_root == main.resolve()
    assert doctor.context_from_env(env, cwd=tmp_path).repo_root is None


# --- the resume comparison of task files against the journal (refactor, state-ownership.md) ----

def plant_session(env, team="session-ab12cd34", session_id="lead-session-1", rows=2) -> Path:
    config = Path(env["CLAUDE_CONFIG_DIR"])
    session = config / "pact-sessions" / "proj" / session_id
    session.mkdir(parents=True)
    (session / "pact-session-context.json").write_text(json.dumps({"team_name": team, "session_id": session_id}))
    (session / "session-journal.jsonl").write_text("".join(
        json.dumps({"type": "agent_dispatch", "task_id": str(n), "agent": "x", "phase": "CODE"}) + "\n"
        for n in range(1, rows + 1)) + json.dumps({"type": "task_metadata_snapshot", "task_id": "1",
                                                   "metadata": {}}) + "\n")
    (config / "tasks" / team).mkdir(parents=True)                      # stands, holds no task file
    return config / "tasks" / team


def brief_event(env, **event):
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": str(session_repo(env)), **event}),
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_resume_names_a_wiped_task_store_and_is_silent_when_the_two_agree(env):
    write_cache(env, GREEN)
    tasks = plant_session(env)
    line = "task store: no files for session-ab12cd34 (journal recorded 2 tasks)"
    assert brief_event(env, source="resume", session_id="lead-session-1") == {"systemMessage": line}
    assert brief_event(env, source="startup", session_id="lead-session-1") == SUPPRESS   # a resume only
    assert brief_event(env, source="resume", session_id="../lead-session-1") == SUPPRESS  # not a path
    (tasks / "1.json").write_text("{}")
    assert brief_event(env, source="resume", session_id="lead-session-1") == SUPPRESS   # they agree
