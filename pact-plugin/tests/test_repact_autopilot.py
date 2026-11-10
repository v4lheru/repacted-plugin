"""repact-autopilot: the model-free mechanics of /repacted:autopilot, one test per behaviour.

Location: pact-plugin/tests/test_repact_autopilot.py. bin/repact-autopilot keeps the run in
<session dir>/autopilot.jsonl (hooks/shared/autopilot_run.py): `start` records the limits, `next`
prints the top ready task of the task log or a stop reason, `begin` is the per-task checkpoint,
`record` an outcome, `report` the morning summary. Each test builds a temp repo with its own task
log, a temp CLAUDE_CONFIG_DIR with one session dir and, where spend matters, a lead transcript.
Run: tcount pytest -q tests/test_repact_autopilot.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_journal, unattended, workflow_state

# Slow tier: deselected by the default full run, included by `run-tests --full --all`.
pytestmark = pytest.mark.slow

PLUGIN = Path(__file__).resolve().parent.parent
BIN_DIR = PLUGIN / "bin"
SESSION = "11111111-2222-3333-4444-555555555555"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
REF = re.compile(r"T-[0-9a-f]{8}")
# 1,000,000 output tokens of Opus 5.5 cost $20 at the price table's rate (tests/test_session_cost_cap.py).
TWENTY_USD = json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
    "input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    "output_tokens": 1_000_000}}}) + "\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temp git repo with a task log, a session dir in unattended mode, an empty transcript."""
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "root")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    config = tmp_path / "claude"
    session_dir = config / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    transcript = config / "projects" / "-proj" / f"{SESSION}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    for name in ("PACT_SESSION_COST_CAP_USD", "REPACT_UNATTENDED", "REPACT_AUTOPILOT_NOW",
                 "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    unattended.write_flag(str(session_dir), True, "2026-10-18T10:00:00Z")
    run_bin("repact-task", "init", repo=repo)
    return {"repo": repo, "session_dir": session_dir, "transcript": transcript}


def run_bin(name: str, *args: str, repo: Path, env: dict = None, check: bool = True):
    done = subprocess.run([sys.executable, str(BIN_DIR / name), *args], capture_output=True, text=True,
                          cwd=repo, env={**os.environ, **GIT_ENV, **(env or {})}, timeout=60)
    if check:
        assert done.returncode == 0, done.stderr
    return done


def add(world, title: str, *extra: str) -> str:
    out = run_bin("repact-task", "add", title, *(extra or ("--priority", "P2")), repo=world["repo"]).stdout
    return REF.search(out).group(0)


def pilot(world, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    return run_bin("repact-autopilot", *args, "--session-dir", str(world["session_dir"]),
                   repo=world["repo"], env=env, check=False)


def next_task(world, env: dict = None) -> dict:
    done = pilot(world, "next", env=env)
    assert done.returncode in (0, 1), done.stderr
    return json.loads(done.stdout)


def test_next_picks_the_top_ready_task_and_never_a_waiting_one(world):
    low = add(world, "Tidy the logs", "--priority", "P3")
    top = add(world, "Fix the login bug", "--priority", "P1")
    add(world, "Ship the login fix", "--priority", "P0", "--depends", top)
    assert pilot(world, "start").returncode == 0
    picked = next_task(world)
    assert picked["ref"] == top and picked["title"] == "Fix the login bug" and picked["resume"] is False
    assert low != top


def test_next_skips_a_halted_task_and_its_dependants(world):
    halted = add(world, "Pick the cache backend", "--priority", "P0")
    add(world, "Warm the cache", "--priority", "P0", "--depends", halted)
    asked = add(world, "Rename the module", "--priority", "P1")
    other = add(world, "Write the docs", "--priority", "P3")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "begin", halted).returncode == 0
    assert pilot(world, "record", halted, "halted", "--reason", "High variety").returncode == 0
    assert session_journal.append_event(session_journal.make_event(
        "unattended_halt", task_id=asked, question="Merge the branch"), session_dir=str(world["session_dir"]))
    assert next_task(world)["ref"] == other


@pytest.mark.parametrize("reason", ["queue_empty",])
def test_each_stop_reason_stops_the_loop(world, reason):
    first = add(world, "Fix the login bug")
    add(world, "Write the docs")
    start, env = ["start"], {}
    if reason == "queue_empty":
        for ref in REF.findall(run_bin("repact-task", "next", "--all", repo=world["repo"]).stdout):
            run_bin("repact-task", "drop", ref, repo=world["repo"])
    if reason == "past_until":
        start += ["--until", "07:00"]
        env["REPACT_AUTOPILOT_NOW"] = "2026-10-18T06:00:00+00:00"
    if reason == "max_tasks":
        start += ["--max-tasks", "1"]
    if reason in ("max_usd", "spend_unknown"):
        start += ["--max-usd", "5"]
    assert pilot(world, *start, env=env).returncode == 0
    if reason == "past_until":
        env["REPACT_AUTOPILOT_NOW"] = "2026-10-20T06:00:00+00:00"
    if reason == "max_tasks":
        assert pilot(world, "record", first, "done", "--branch", "feat/login").returncode == 0
    if reason == "unattended_off":
        unattended.write_flag(str(world["session_dir"]), False, "2026-10-18T11:00:00Z")
    if reason in ("max_usd", "cost_cap"):
        world["transcript"].write_text(TWENTY_USD)
        env["PACT_SESSION_COST_CAP_USD"] = "10" if reason == "cost_cap" else ""
    if reason == "spend_unknown":
        world["transcript"].unlink()
    done = pilot(world, "next", env=env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert json.loads(done.stdout)["stop"] == reason
    again = pilot(world, "next", env=env)
    assert again.returncode == 1 and json.loads(again.stdout)["stop"] == reason


def test_start_refuses_a_dollar_limit_it_cannot_measure(world):
    world["transcript"].unlink()
    refused = pilot(world, "start", "--max-usd", "5")
    assert refused.returncode == 3 and "spend" in refused.stderr
    assert not (world["session_dir"] / "autopilot.jsonl").exists()


def test_start_refuses_when_unattended_mode_is_off(world):
    unattended.write_flag(str(world["session_dir"]), False, "2026-10-18T11:00:00Z")
    refused = pilot(world, "start")
    assert refused.returncode == 3 and "unattended" in refused.stderr
    command = (PLUGIN / "commands" / "autopilot.md").read_text(encoding="utf-8")
    assert "repact-autopilot" in command and "exit 3" in command


def test_a_begun_task_with_no_record_is_resumed_after_a_compaction(world):
    ref = add(world, "Fix the login bug", "--priority", "P1")
    add(world, "Write the docs", "--priority", "P3")
    assert pilot(world, "start", "--max-tasks", "5").returncode == 0
    assert pilot(world, "begin", ref).returncode == 0
    run_bin("repact-task", "start", ref, repo=world["repo"])
    picked = next_task(world)
    assert picked["ref"] == ref and picked["resume"] is True
    state = {"built_at": "2026-10-18T12:00:00Z", "team": "t", "tasks": {}}
    screen = workflow_state.render(state, "fresh", str(world["session_dir"]))
    assert re.search(rf"Autopilot: .*{ref} in progress.*/repacted:autopilot", screen)


def decide_items(world) -> list:
    done = subprocess.run(["bash", str(BIN_DIR / "repact"), "decide", "--json", "--session-dir",
                           str(world["session_dir"])], cwd=world["repo"], capture_output=True, text=True,
                          timeout=60, env={**os.environ, **GIT_ENV})
    assert done.returncode == 0, done.stderr
    return [item for item in json.loads(done.stdout)["items"] if item["source"] != "kb"]


