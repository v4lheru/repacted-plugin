"""D3 STATUS: hooks/shared/transcripts.py, repact-status, repact-report, `repact report` and the
session ledger, on a synthetic session (entry shapes as measured on the platform; no real
content).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
BIN = PLUGIN / "bin"
def _load_transcripts():
    """hooks/shared/transcripts.py by file path (tests never edit sys.path; conftest owns path setup)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_transcripts", Path(__file__).resolve().parent.parent / "hooks" / "shared" / "transcripts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tr = _load_transcripts()


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
HEX = "0123456789abcdef"
TEAM = "d3-team"
MARKER = "PLANTEDMARKER7Q9Z"          # inert: planted in every content field, must never be printed
REDACT_ME = "REPACT_TEST_SECRET_ABCDEFGH12"


def ts(minutes_ago, seconds=0):
    return (NOW - timedelta(minutes=minutes_ago, seconds=seconds)).isoformat().replace("+00:00", "Z")


def assistant(mid, t, *, blocks=(), stop=None, usage=None):
    msg = {"id": mid, "role": "assistant", "content": list(blocks), "stop_reason": stop}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": t, "message": msg}


def use(inp=10, cache=1000, out=5):
    return {"input_tokens": inp, "cache_read_input_tokens": cache,
            "cache_creation_input_tokens": 0, "output_tokens": out}


def tool_use(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, t, content="", bg=None):
    entry = {"type": "user", "timestamp": t,
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": tid, "content": content}]}}
    if bg:
        entry["toolUseResult"] = {"backgroundTaskId": bg, "stdout": MARKER}
    return entry


def lead_text(t, body):
    return {"type": "user", "timestamp": t, "message": {"role": "user", "content": body}}


def tm(sender, body):
    return f'<teammate-message teammate_id="{sender}">{body}</teammate-message>'


def idle(sender):
    return tm(sender, json.dumps({"type": "idle_notification", "result": MARKER}))


def write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A lead, two teammates and one subagent, plus the team's config and tasks."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    lead = tmp_path / "projects" / "-proj" / "sess.jsonl"
    sub = lead.with_suffix("") / "subagents"
    long_message = f"Please re-run the suite and report {MARKER} with the counts, then idle please."
    write(lead, [
        lead_text(ts(60), f"user prompt {MARKER}"),
        assistant("L1", ts(59), blocks=[{"type": "text", "text": MARKER}], stop="end_turn", usage=use(out=7)),
        assistant("L2", ts(58), blocks=[tool_use("s1", "SendMessage", to="worker-a", message=long_message)],
                  stop="tool_use", usage=use(out=3)),
        assistant("L2", ts(58), blocks=[], stop="tool_use", usage=use(out=3)),       # same message streamed
        tool_result("s1", ts(58)),
        lead_text(ts(50), tm("worker-a", f"done {MARKER}")),                             # text from worker-a
        assistant("L3", ts(49), stop="end_turn", usage=use(out=4)),                    # lead answers it
        lead_text(ts(49, 30), idle("worker-a")),                                       # idle-only, 30 s later: double wake
        assistant("L4", ts(49, 20), stop="end_turn", usage=use(cache=2000, out=2)),
        lead_text(ts(30), idle("worker-b")),                                           # idle-only, not double
        assistant("L5", ts(29), blocks=[tool_use("r1", "Read", file_path=MARKER)], stop="tool_use", usage=use(out=1)),
        tool_result("r1", ts(29), content=MARKER),
        assistant("L6", ts(28), stop="end_turn", usage=use(out=1)),
        {"type": "attachment", "timestamp": ts(20), "attachment": {
            "type": "hook_additional_context", "content": ["UNFLAGGED BACKGROUND WORK — x"]}},
        {"type": "attachment", "timestamp": ts(20), "attachment": {"type": "task_reminder", "content": []}},
    ])
    write(sub / f"agent-aworker-a-{HEX}.jsonl", [
        lead_text(ts(57), tm("team-lead", long_message)),                              # delivered 1 min late
        assistant("A1", ts(56), blocks=[tool_use("b1", command=f"run {MARKER}", description="Run the suite",
                                                 run_in_background=True)], stop="tool_use", usage=use(out=9)),
        tool_result("b1", ts(56), content=MARKER, bg="bgjob1"),
        assistant("A2", ts(3), blocks=[tool_use("w1", command=f"sleep 590 {MARKER}",
                                                 description=f"Wait for the suite {REDACT_ME}")],
                  stop="tool_use", usage=use(inp=1, cache=612_000, out=2)),
    ])
    write(sub / f"agent-aworker-b-{HEX}.jsonl", [
        assistant("B1", ts(45), blocks=[tool_use("b2", command="make", description="Build it",
                                                 run_in_background=True)], stop="tool_use", usage=use()),
        tool_result("b2", ts(45), bg="bgjob2"),
        {"type": "attachment", "timestamp": ts(40), "attachment": {
            "type": "queued_command", "commandMode": "task-notification",
            "prompt": "<task-notification><task-id>bgjob2</task-id><status>completed</status></task-notification>"}},
        {"type": "user", "timestamp": ts(39), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "zz", "content": f"{tr.PSGUARD_PREFIX} can expose tokens"}]}},
        assistant("B2", ts(38), blocks=[{"type": "text", "text": MARKER}], stop="end_turn", usage=use(out=4)),
        {"type": "attachment", "timestamp": ts(38), "attachment": {"type": "task_reminder", "content": []}},
    ])
    write(sub / f"agent-a{HEX}.jsonl", [assistant("S1", ts(20), stop="end_turn", usage=use(out=1))])
    # R1: a task body and an MCP call carry a `description` that is content, not a summary.
    write(sub / f"agent-aworker-c-{HEX}.jsonl", [
        assistant("C1", ts(10), blocks=[tool_use("c1", "TaskCreate", subject="x", description=MARKER)],
                  stop="tool_use", usage=use()),
        tool_result("c1", ts(10)),
        assistant("C2", ts(9), stop="end_turn", usage=use()),
    ])
    write(sub / f"agent-aworker-d-{HEX}.jsonl", [
        assistant("D1", ts(10), blocks=[tool_use("d1", "mcp__srv__create", description=MARKER)],
                  stop="tool_use", usage=use()),
        tool_result("d1", ts(10)),
        assistant("D2", ts(9), stop="end_turn", usage=use()),
    ])
    for name in ("worker-a", "worker-b", "worker-c", "worker-d"):
        (sub / f"agent-a{name}-{HEX}.meta.json").write_text(json.dumps({"name": name, "teamName": TEAM}))
    (tmp_path / "teams" / TEAM).mkdir(parents=True)
    (tmp_path / "teams" / TEAM / "config.json").write_text(json.dumps(
        {"members": [{"name": "team-lead"}] + [{"name": n} for n in ("worker-a", "worker-b", "worker-c", "worker-d")]}))
    tasks = tmp_path / "tasks" / TEAM
    tasks.mkdir(parents=True)
    for task in [
        {"id": "7", "owner": "worker-a", "status": "in_progress", "subject": "build",
         "metadata": {"amend_1": {"text": MARKER, "at": ts(5), "from": "team-lead", "kind": "ruling"}}},
        {"id": "8", "owner": "worker-b", "status": "completed", "subject": "done",
         "metadata": {"handoff_rejection": {"reason": MARKER}, "handoff": {}}},
        {"id": "9", "owner": "team-lead", "status": "pending", "subject": "BLOCKER: rotate the token"},
    ]:
        (tasks / f"{task['id']}.json").write_text(json.dumps(task))
    return lead


def run(script, *args):
    """repact-status is Agent Teams only, so its runs set the Teams mode and the host flag."""
    env = dict(os.environ)
    if script == "repact-status":
        env.update(REPACT_EXECUTION="teams", CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="1")
    return subprocess.run([sys.executable, str(BIN / script), *args], capture_output=True, text=True,
                          env=env, timeout=60)


# --------------------------------------------------------------------------- reader

def test_usage_counts_a_streamed_message_once(session):
    u = tr.usage(tr.iter_entries(session))
    assert u["calls"] == 6 and u["output"] == 7 + 3 + 4 + 2 + 1 + 1


def test_until_drops_later_entries(session):
    until = NOW - timedelta(minutes=50)
    assert tr.usage(tr.iter_entries(session, until))["calls"] == 2


def test_roles_come_from_the_file_names(session):
    roles = {f["name"]: f["role"] for f in tr.session_files(session)}
    assert roles == {"lead": "lead", "worker-a": "teammate", "worker-b": "teammate", "worker-c": "teammate",
                     "worker-d": "teammate", f"agent-a{HEX}": "subagent"}


# --------------------------------------------------------------------------- status

def test_status_shows_a_waiting_teammate_as_busy_with_its_description(session):
    lines = dict(line.split("  ", 1) for line in tr.status_lines(session, now=NOW))
    assert lines["worker-a"].startswith('busy  Bash fg "Wait for the suite [REDACTED:test-secret]" 3m  ctx 612k  #7  bg:1  amend:1')


def test_status_shows_a_finished_teammate_as_idle_with_no_job(session):
    line = dict(line.split("  ", 1) for line in tr.status_lines(session, now=NOW))["worker-b"]
    assert line.startswith("idle  Bash bg \"Build it\" 45m") and "bg:" not in line


def test_status_replay_reads_only_what_was_written(session):
    until = NOW - timedelta(minutes=50)
    lines = dict(line.split("  ", 1) for line in tr.status_lines(session, until=until))
    assert lines["worker-a"].startswith('busy  Bash bg "Run the suite"') and "bg:1" in lines["worker-a"]
    assert "worker-b" not in lines          # its transcript starts later


def test_status_names_only_the_tool_for_calls_outside_the_description_allowlist(session):
    lines = dict(line.split("  ", 1) for line in tr.status_lines(session, now=NOW))
    assert lines["worker-c"].startswith("idle  TaskCreate 10m") and '"' not in lines["worker-c"]
    assert lines["worker-d"].startswith("idle  mcp__srv__create 10m") and '"' not in lines["worker-d"]


def test_repact_status_cli(session):
    proc = run("repact-status", "--transcript", str(session))
    assert proc.returncode == 0 and proc.stdout.count("\n") == 4


# --------------------------------------------------------------------------- report


# --------------------------------------------------------------------------- ledger

def _ledger_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_ledger", PLUGIN / "hooks" / "session_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- privacy


# --------------------------------------------------------------------------- open user actions (S1)

def _user_actions():
    import importlib.util
    spec = importlib.util.spec_from_file_location("user_actions", PLUGIN / "hooks" / "shared" / "user_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- ledger scope (S2)

