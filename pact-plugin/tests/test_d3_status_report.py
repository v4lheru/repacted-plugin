"""D3 STATUS: hooks/shared/transcripts.py, repact-status, repact-report, `repact report` and the
session ledger, on a synthetic session (entry shapes as measured on the platform; no real
content). The golden replays on a real session live in test_d3_golden_replay.py.
"""
import json
import os
import subprocess
import sys
import time
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
    return subprocess.run([sys.executable, str(BIN / script), *args], capture_output=True, text=True,
                          env=dict(os.environ), timeout=60)


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


def test_repact_status_without_a_session_exits_3(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    proc = run("repact-status", "--session", "nope")
    assert proc.returncode == 3


# --------------------------------------------------------------------------- report

def test_report_counts(session):
    r = tr.build_report(session)
    assert r["spend"]["lead"]["calls"] == 6 and r["spend"]["teammate"]["transcripts"] == 4
    assert r["spend"]["subagent"]["calls"] == 1
    w = r["wakes"]
    assert (w["idle_only_wakes"], w["double_wakes"], w["idle_only_calls"], w["idle_only_output"],
            w["idle_only_no_tool"]) == (2, 1, 3, 2 + 1 + 1, 1)
    assert r["delivery"] == {"messages": 1, "located": 1, "late": 0, "late_median_min": None}
    assert r["rejections"] == {"rejected_tasks": 1, "with_unaddressed_amend": 0, "other": 1}
    assert r["signals"] == {"psguard_denials": 1, "task_reminders": 2, "unflagged_alarms": 1}


def test_report_counts_the_write_checks_per_use(session, tmp_path):
    """The write-time checks' outcomes (checks/shadow.jsonl, kind write_gate) per use, for this
    team's lines and commit lines inside the window; other teams and other kinds are not counted."""
    write(tmp_path / "plugin-data" / "checks" / "shadow.jsonl", [
        {"ts": ts(55), "kind": "write_gate", "event": "note", "use": "handoff_evidence", "team": TEAM, "keys": ["a", "b"]},
        {"ts": ts(55), "kind": "write_gate", "event": "return", "use": "commit_rule", "team": "_commits", "keys": ["c"]},
        {"ts": ts(600), "kind": "write_gate", "event": "return", "use": "commit_rule", "team": "_commits", "keys": ["d"]},
        {"ts": ts(55), "kind": "write_gate", "event": "note", "use": "handoff_evidence", "team": "other-team", "keys": ["e"]},
        {"ts": ts(55), "kind": "amendment_gate", "event": "would_deny", "team": TEAM}])
    g = tr.build_report(session, team=TEAM)["gates"]
    assert g["total"] == {"returned": 1, "repeats": 0, "notes": 2, "noted": 1}
    assert list(g["uses"]) == ["handoff_evidence", "commit_rule"]
    text = "\n".join(tr.render_report(tr.build_report(session, team=TEAM)))
    assert "write checks         1 returned, 0 repeats passed, 2 notes, 1 noted" in text


def test_report_counts_note_events_apart_from_the_items_they_note(session, tmp_path):
    """`noted` counts the note events (writes that got a note); `notes` counts the items noted."""
    write(tmp_path / "plugin-data" / "checks" / "shadow.jsonl", [
        {"ts": ts(55), "kind": "write_gate", "event": "note", "use": "handoff_evidence", "team": TEAM, "keys": ["a", "b"]},
        {"ts": ts(56), "kind": "write_gate", "event": "note", "use": "handoff_evidence", "team": TEAM, "keys": ["c"]},
        {"ts": ts(57), "kind": "write_gate", "event": "return", "use": "handoff_evidence", "team": TEAM, "keys": ["d"]}])
    row = tr.build_report(session, team=TEAM)["gates"]["uses"]["handoff_evidence"]
    assert row == {"returned": 1, "repeats": 0, "notes": 3, "noted": 2}
    text = "\n".join(tr.render_report(tr.build_report(session, team=TEAM)))
    assert "  handoff_evidence         1 returned   0 repeats    3 notes   2 noted" in text


def test_report_sums_context_cost_over_two_session_journals(session, tmp_path):
    folder = tmp_path / "pact-sessions" / "proj"

    def counters(path, checks, frames):
        def total(rows):
            return {key: sum(r[key] for r in rows) for key in ("runs", "context", "system")}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "checks": checks, "frames": frames,
                                    "events": {"PreToolUse": total(list(checks.values()))}}))

    counters(folder / "sess" / "context-cost.json",
             {"gate_a": {"runs": 1, "context": 1000, "system": 0},
              "gate_b": {"runs": 1, "context": 300, "system": 20}},
             {"lead": {"runs": 1, "context": 1000, "system": 0},
              "teammate": {"runs": 1, "context": 300, "system": 20}})
    write(folder / "sess" / "session-journal.jsonl", [
        {"type": "metadata_write", "ts": ts(30), "task_id": "7", "key": "handoff", "agent": "worker-a", "bytes": 900},
        {"type": "gate_refusal", "ts": ts(30), "hook": "gate_a", "verdict": "deny", "rule": "x"}])
    counters(folder / "older" / "context-cost.json", {"gate_a": {"runs": 1, "context": 2000, "system": 0}},
             {"lead": {"runs": 1, "context": 2000, "system": 0}})
    write(folder / "older" / "session-journal.jsonl", [
        {"type": "metadata_write", "ts": ts(30), "task_id": "3", "key": "teachback_submit", "agent": "worker-a",
         "bytes": 400}])
    one = tr.build_report(session)["context_cost"]
    assert one["total"] == {"runs": 2, "context": 1300, "system": 20, "writes": 1, "written": 900}
    both = tr.build_report(session, cost_all=True)["context_cost"]
    assert both["total"] == {"runs": 3, "context": 3300, "system": 20, "writes": 2, "written": 1300}
    assert list(both["top_checks"]) == ["gate_a", "gate_b"] and both["top_checks"]["gate_a"]["runs"] == 2
    assert both["agents"]["worker-a"] == {"teachback_submit": 400, "handoff": 900, "writes": 2}
    assert both["frames"] == {"lead": 3000, "teammate": 320}
    assert set(both["sessions"]) == {"sess", "older"}
    text = run("repact-report", "--transcript", str(session), "--cost-all").stdout
    assert "context cost         3 runs injected 3.2 KB context, 0.0 KB system" in text
    assert len(text.splitlines()) <= 26


def test_report_cli_text_and_json(session):
    text = run("repact-report", "--transcript", str(session))
    assert text.returncode == 0 and "idle-only" in text.stdout and len(text.stdout.splitlines()) <= 22
    data = run("repact-report", "--transcript", str(session), "--json")
    assert json.loads(data.stdout)["wakes"]["double_wakes"] == 1


def test_repact_report_dispatch(session):
    proc = subprocess.run(["bash", str(BIN / "repact"), "report", "--transcript", str(session), "--json"],
                          capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert proc.returncode == 0 and json.loads(proc.stdout)["session"] == "sess"


# --------------------------------------------------------------------------- ledger

def _ledger_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("session_ledger", PLUGIN / "hooks" / "session_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ledger_row(session, tmp_path):
    ledger = _ledger_module()
    ledger.write_row(str(session), "/some/where/proj")
    rows = (tmp_path / "plugin-data" / "sessions.jsonl").read_text().splitlines()
    row = json.loads(rows[0])
    assert len(rows) == 1 and row["session"] == "sess" and row["project"] == "proj" and row["team"] == TEAM
    assert row["open_user_actions"] == [{"id": "9", "subject": "BLOCKER: rotate the token"}]
    assert row["report"]["wakes"]["idle_only_wakes"] == 2


def test_ledger_hook_detaches_and_writes_one_row(session, tmp_path):
    payload = json.dumps({"hook_event_name": "SessionEnd", "transcript_path": str(session), "cwd": "/x/proj"})
    start = time.monotonic()
    proc = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "session_ledger.py")], input=payload,
                          capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"suppressOutput": True}
    ledger = tmp_path / "plugin-data" / "sessions.jsonl"
    while not ledger.exists() and time.monotonic() - start < 30:
        time.sleep(0.1)
    time.sleep(0.2)
    assert len(ledger.read_text().splitlines()) == 1


def test_ledger_fails_open_on_a_missing_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pd"))
    _ledger_module().write_row(str(tmp_path / "absent.jsonl"), None)
    assert not (tmp_path / "pd" / "sessions.jsonl").exists()


# --------------------------------------------------------------------------- privacy

def test_planted_content_never_reaches_status_report_or_ledger(session, tmp_path):
    """MARKER sits in the user prompt, assistant text, a teammate message, an idle
    notification's result, tool inputs (a command, a Read path), tool outputs, a
    background result, an amendment and a rejection reason. None of it may be printed."""
    outputs = [
        run("repact-status", "--transcript", str(session)).stdout,
        run("repact-report", "--transcript", str(session)).stdout,
        run("repact-report", "--transcript", str(session), "--json").stdout,
    ]
    _ledger_module().write_row(str(session), "/x/proj")
    outputs.append((tmp_path / "plugin-data" / "sessions.jsonl").read_text())
    assert all(outputs) and not any(MARKER in text for text in outputs)
    assert not any(REDACT_ME in text for text in outputs)


def test_the_marker_is_really_in_the_fixture(session):
    """Control: the privacy test is not passing because nothing was planted."""
    raw = session.read_text() + "".join(p.read_text() for p in (session.with_suffix("") / "subagents").glob("*.jsonl"))
    assert raw.count(MARKER) >= 10


# --------------------------------------------------------------------------- open user actions (S1)

def _user_actions():
    import importlib.util
    spec = importlib.util.spec_from_file_location("user_actions", PLUGIN / "hooks" / "shared" / "user_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("task, is_open", [
    ({"status": "pending", "subject": "ALERT: SECURITY: tokens in args"}, True),
    ({"status": "in_progress", "subject": "HALT DATA: PII in logs"}, True),
    ({"status": "pending", "subject": "BLOCKER: sandbox token at its limit"}, True),
    ({"status": "pending", "subject": "RELEASE GATE: benchmark a local judge"}, True),
    ({"status": "pending", "subject": "quality breach", "metadata": {"type": "algedonic"}}, True),
    ({"status": "completed", "subject": "ALERT: done"}, False),
    ({"status": "deleted", "subject": "BLOCKER: withdrawn"}, False),
    ({"status": "pending", "subject": "ALERTING the lead about a build"}, False),
    ({"status": "pending", "subject": "fix the alert text"}, False),
    ({"status": "pending"}, False),
])
def test_one_open_user_action_predicate(task, is_open):
    assert _user_actions().is_open_user_action(task) is is_open


def test_ledger_lists_open_user_actions_with_the_shared_predicate(session, tmp_path):
    tasks = tmp_path / "tasks" / TEAM
    (tasks / "10.json").write_text(json.dumps({"id": "10", "status": "pending", "subject": "HALT QUALITY: x"}))
    (tasks / "11.json").write_text(json.dumps({"id": "11", "status": "deleted", "subject": "ALERT: gone"}))
    (tasks / "12.json").write_text(json.dumps({"id": "12", "status": "pending", "subject": "breach",
                                               "metadata": {"type": "algedonic"}}))
    _ledger_module().write_row(str(session), "/x/proj")
    row = json.loads((tmp_path / "plugin-data" / "sessions.jsonl").read_text().splitlines()[0])
    assert sorted(a["id"] for a in row["open_user_actions"]) == ["10", "12", "9"]


# --------------------------------------------------------------------------- ledger scope (S2)

def test_ledger_skips_an_in_process_teammates_session_end(session, tmp_path):
    teammate = session.with_suffix("") / "subagents" / f"agent-aworker-a-{HEX}.jsonl"
    ledger = _ledger_module()
    ledger.write_row(str(teammate), "/x/proj")
    payload = json.dumps({"hook_event_name": "SessionEnd", "transcript_path": str(teammate), "cwd": "/x"})
    proc = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "session_ledger.py")], input=payload,
                          capture_output=True, text=True, env=dict(os.environ), timeout=60)
    time.sleep(1.0)
    assert proc.returncode == 0 and not (tmp_path / "plugin-data" / "sessions.jsonl").exists()
