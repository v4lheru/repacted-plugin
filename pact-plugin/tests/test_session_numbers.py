"""repact-session-numbers: the six session numbers wrap-up and pause print, read from a
fixture ledger, a temporary session directory and a fixture transcript in a temporary
config root (never the live ones)."""
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
COMMAND = PLUGIN / "bin" / "repact-session-numbers"
SESSION = "11111111-2222-3333-4444-555555555555"
MARKER = "PLANTEDMARKER7Q9Z"          # inert: planted in every text field, must never be printed
LABELS = ["lead calls", "per-call lead input", "teammate calls", "idle-only cache-read",
          "rejections", "check returns"]


def report(lead_calls=4, lead_input=800_000, teammate_calls=9, cache_read=120_000, rejected=2, returned=3):
    return {"session": SESSION, "team": MARKER,
            "spend": {"lead": {"calls": lead_calls, "input_side": lead_input, "output": 7},
                      "teammate": {"calls": teammate_calls, "input_side": 5}},
            "wakes": {"idle_only_cache_read": cache_read, "idle_only_calls": 1},
            "rejections": {"rejected_tasks": rejected},
            "gates": {"total": {"returned": returned}}}


def row(session=SESSION, date="2026-01-02T03:04:05Z", **numbers):
    return {"date": date, "session": session, "project": MARKER, "team": MARKER,
            "report": report(**numbers), "open_user_actions": [{"id": "9", "subject": MARKER}]}


def run(tmp_path, rows=None, ledger_exists=True, extra=()):
    """The command over a config root, a plugin data dir and a HOME that are all `tmp_path`."""
    ledger = tmp_path / "sessions.jsonl"
    if ledger_exists:
        ledger.write_text("".join(json.dumps(r) + "\n" for r in rows or []))
    session_dir = tmp_path / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path), CLAUDE_PLUGIN_DATA=str(tmp_path), HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, str(COMMAND), "--ledger", str(ledger), "--session-dir", str(session_dir),
                           *extra], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    header, *body = proc.stdout.splitlines()
    assert [line[:22].strip() for line in body] == LABELS
    return header, [line[22:] for line in body], proc.stdout + proc.stderr


def call(mid, cache_read):
    return {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {
        "id": mid, "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": MARKER}],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": cache_read, "output_tokens": 1}}}


def transcripts(tmp_path, lead_cache_read=990, mtime=None):
    """A lead with two calls, the second after an idle-only wake, one teammate with one call,
    and a rejected task. MARKER sits in every text field."""
    lead = tmp_path / "projects" / "-proj" / f"{SESSION}.jsonl"
    idle = json.dumps({"type": "idle_notification", "result": MARKER})
    entries = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": MARKER}},
        call("L1", lead_cache_read),
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {
            "role": "user", "content": f'<teammate-message teammate_id="{MARKER}">{idle}</teammate-message>'}},
        call("L2", 2_990),
    ]
    teammate = lead.with_suffix("") / "subagents" / "agent-aworker-0123456789abcdef.jsonl"
    teammate.parent.mkdir(parents=True)
    lead.write_text("".join(json.dumps(e) + "\n" for e in entries))
    teammate.write_text(json.dumps(call("T1", 5)) + "\n")
    teammate.with_suffix(".meta.json").write_text(json.dumps({"name": "worker", "teamName": "fixture-team"}))
    tasks = tmp_path / "tasks" / "fixture-team"
    tasks.mkdir(parents=True)
    (tasks / "1.json").write_text(json.dumps({"id": "1", "subject": MARKER, "metadata": {
        "handoff_rejection": {"reason": MARKER}, "handoff": {"produced": [MARKER]}}}))
    if mtime is not None:
        os.utime(lead, (mtime, mtime))
        os.utime(teammate, (mtime, mtime))


COMPUTED = ["2", "2,000 input-side tokens", "1", "2,990 tokens", "1 tasks", "0 items"]
ROW_TIME = 1_767_323_045              # 2026-01-02T03:04:05Z, the date of row()


def test_the_six_lines_come_from_the_sessions_latest_row(tmp_path):
    rows = [row(lead_calls=1, date="2026-01-01T00:00:00Z"),             # an earlier end of the same session
            row(),
            row(session="another-session", lead_calls=99)]
    header, values, _ = run(tmp_path, rows)
    assert SESSION in header and "ledger row of 2026-01-02T03:04:05Z" in header and "not billing" in header
    assert values == ["4", "200,000 input-side tokens", "9", "120,000 tokens", "2 tasks", "3 items"]


def test_a_number_with_no_source_prints_na_and_its_reason(tmp_path):
    _, values, _ = run(tmp_path, ledger_exists=False, extra=["--ledger-only"])
    assert values == ["n/a (no session ledger at the ledger path)"] * 6
    _, values, _ = run(tmp_path, ledger_exists=False)
    assert values == ["n/a (no session ledger at the ledger path, and no transcript of this session to compute from)"] * 6
    lean = row()
    del lean["report"]["spend"]["teammate"], lean["report"]["gates"]["total"]["returned"]
    (tmp_path / "second").mkdir()
    _, values, _ = run(tmp_path / "second", [row(session="another-session"), lean])
    assert values[2] == "n/a (the ledger row has no spend.teammate.calls)"
    assert values[5] == "n/a (the ledger row has no gates.total.returned)"
    assert [values[i] for i in (0, 1, 3, 4)] == ["4", "200,000 input-side tokens", "120,000 tokens", "2 tasks"]


def test_no_text_of_a_ledger_row_reaches_the_output(tmp_path):
    hostile = row(date=MARKER, lead_calls=True, teammate_calls=MARKER, cache_read=-1,
                  rejected={"n": MARKER}, returned=10 ** 400)
    hostile["report"]["spend"]["lead"]["input_side"] = float("inf")     # json writes Infinity
    header, values, output = run(tmp_path, [hostile])
    assert MARKER not in output and "Infinity" not in output
    assert "ledger row with no date" in header
    assert all(value.startswith("n/a (") and "not a count" in value for value in values)


def test_the_numbers_are_computed_now_when_the_session_has_no_ledger_row(tmp_path):
    transcripts(tmp_path)
    header, values, _ = run(tmp_path, [row(session="another-session")])
    assert "computed now" in header and "ledger row" not in header
    assert values == COMPUTED
    _, values, _ = run(tmp_path, [row(session="another-session")], extra=["--ledger-only"])
    assert all(value.startswith("n/a (no ledger row for this session") for value in values)


def test_the_ledger_row_is_the_source_while_it_is_as_new_as_the_transcripts(tmp_path):
    transcripts(tmp_path, mtime=ROW_TIME)
    header, values, _ = run(tmp_path, [row()])
    assert "ledger row of 2026-01-02T03:04:05Z" in header and values[0] == "4"
    transcripts_dir = tmp_path / "projects" / "-proj" / SESSION / "subagents"
    newer = ROW_TIME + 1
    os.utime(next(transcripts_dir.glob("*.jsonl")), (newer, newer))     # one transcript written after the row
    header, values, _ = run(tmp_path, [row()])
    assert "computed now" in header and values[0] == "2"


def test_no_text_of_a_transcript_reaches_the_output(tmp_path):
    transcripts(tmp_path)
    header, values, output = run(tmp_path, [])
    assert "computed now" in header and values == COMPUTED
    assert MARKER not in output and "fixture-team" not in output


def test_a_failed_computation_prints_na_lines_and_exits_0(tmp_path):
    transcripts(tmp_path, lead_cache_read=MARKER)                      # int(MARKER) raises inside the report
    header, values, output = run(tmp_path, [])
    assert "no ledger row" in header and MARKER not in output and "Traceback" not in output
    assert values == ["n/a (no ledger row for this session: the row is written when a session ends, "
                      "and computing the numbers now failed)"] * 6
    header, values, _ = run(tmp_path, [row(date="2020-01-01T00:00:00Z")])
    assert "ledger row of 2020-01-01T00:00:00Z, and computing the numbers now failed" in header and values[0] == "4"
