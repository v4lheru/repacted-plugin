"""`repact-report --since` (bin/_report_since.py) on synthetic journals and a synthetic
session ledger: the window filter, the documented JSON schema, malformed input counted, and
no content in the output.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPORT = PLUGIN / "bin" / "repact-report"
SCHEMA_DOC = PLUGIN / "reference" / "since-counts.md"
MARKER = "PLANTEDMARKER7Q9Z"          # inert: planted in every content field, must never be printed
NOW = datetime.now(timezone.utc)


def stamp(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def journal(config, project, session, events, raw_lines=()):
    folder = config / "pact-sessions" / project / session
    folder.mkdir(parents=True)
    lines = [json.dumps(e) for e in events] + list(raw_lines)
    (folder / "session-journal.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def refusal(days_ago, rule="env.read.operand"):
    return {"type": "gate_refusal", "ts": stamp(days_ago), "hook": "env_guard", "verdict": "deny",
            "rule": rule, "target": f"/secret/{MARKER}", "tool_name": "Bash", "agent": f"agent-{MARKER}"}


def session_events(days_ago):
    return [
        {"type": "session_start", "ts": stamp(days_ago), "session_id": "s", "project_dir": f"/p/{MARKER}"},
        refusal(days_ago),
        refusal(days_ago, rule="git_commit_check.exit_2"),
        {"type": "dispatch_decision", "ts": stamp(days_ago), "decision": "ALLOW", "name": f"coder-{MARKER}",
         "prompt_redacted": f"do {MARKER}", "rule": None, "subagent_type": "x", "team_name": "t"},
        {"type": "dispatch_decision", "ts": stamp(days_ago), "decision": "DENY", "name": "c2",
         "rule": "dispatch.no_task", "reason": MARKER},
        {"type": "agent_handoff", "ts": stamp(days_ago), "agent": "a", "task_id": "7",
         "task_subject": MARKER, "handoff": {"produced": [MARKER]}},
        {"type": "metadata_write", "ts": stamp(days_ago), "agent": "a", "task_id": "7",
         "key": "teachback_submit", "bytes": 900},
        {"type": "task_metadata_snapshot", "ts": stamp(days_ago), "task_id": "7", "occupant": "a",
         "subject": MARKER, "metadata": {"handoff_rejection": {"reason": MARKER}}},
        {"type": "task_metadata_snapshot", "ts": stamp(days_ago), "task_id": "8", "occupant": "a",
         "subject": MARKER, "metadata": {"teachback_submit": {"understanding": MARKER}}},
        {"type": "compaction_attributed", "ts": stamp(days_ago), "verdict": "x", "basis": MARKER},
    ]


def ledger_row(session, days_ago, project="proj"):
    return {"date": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"), "session": session,
            "project": project, "team": MARKER, "open_user_actions": [{"subject": MARKER}],
            "report": {"spend": {"lead": {"calls": 10, "input_side": 1000}, "teammate": {"calls": 4}},
                       "wakes": {"idle_only_cache_read": 50}, "rejections": {"rejected_tasks": 1},
                       "gates": {"total": {"returned": 2}}}}


def run(tmp_path, *args):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    return subprocess.run([sys.executable, str(REPORT), *args], capture_output=True, text=True,
                          env=env, timeout=60)


def setup(tmp_path):
    config, data = tmp_path / "config", tmp_path / "data"
    data.mkdir()
    journal(config, "proj", "s-new", session_events(1))
    journal(config, "proj", "s-old", session_events(30))
    journal(config, "other", "s-other", session_events(1))
    rows = [ledger_row("s-new", 3), ledger_row("s-new", 1), ledger_row("s-old", 30),
            ledger_row("s-other", 1, project="other")]
    (data / "sessions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_window_filter_counts_only_sessions_in_the_window(tmp_path):
    setup(tmp_path)
    proc = run(tmp_path, "--since", "7d", "--project", "proj", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["sessions"] == 1                       # s-old (30 days) and the other project are out
    assert report["refusals"] == {"total": 2, "by_rule": {"env.read.operand": 1, "git_commit_check.exit_2": 1}}
    assert report["dispatches"] == {"allowed": 1, "denied": 1} and report["teammates_dispatched"] == 1
    assert report["tasks"] == {"accepted": 2, "handoff_rejected": 1, "teachback_rejected": 0}
    assert (report["teachbacks"], report["handoffs"], report["compactions"]) == (1, 1, 1)
    # the latest row of s-new only: never the superseded row, the old session or the other project
    assert report["ledger"]["sessions"] == 1 and report["ledger"]["lead_calls"] == 10
    assert report["ledger"]["per_call_lead_input"] == 100
    wide = json.loads(run(tmp_path, "--since", (NOW - timedelta(days=60)).date().isoformat(),
                          "--all-projects", "--json").stdout)
    assert wide["sessions"] == 3 and wide["sessions_by_project"] == {"other": 1, "proj": 2}


def test_json_carries_the_documented_schema(tmp_path):
    setup(tmp_path)
    report = json.loads(run(tmp_path, "--since", "7d", "--project", "proj", "--json").stdout)
    assert report["schema"] == "repact-report.since/1"
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    assert report["schema"] in doc
    undocumented = [key for key in report if f"`{key}`" not in doc]
    undocumented += [f"{part}.{key}" for part in ("ledger", "skipped", "tasks", "dispatches", "window")
                     for key in report[part] if f"`{key}`" not in doc]
    assert not undocumented, f"keys missing from reference/since-counts.md: {undocumented}"


def test_malformed_and_unreadable_input_is_skipped_and_counted(tmp_path):
    setup(tmp_path)
    config = tmp_path / "config"
    journal(config, "proj", "s-junk", [], raw_lines=["{not json", "[1, 2]"])
    journal(config, "proj", "s-mixed", session_events(1)[:2], raw_lines=["garbage",
            json.dumps({"type": "gate_refusal", "ts": "yesterday", "rule": "x.y"})])
    with open(tmp_path / "data" / "sessions.jsonl", "a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    report = json.loads(run(tmp_path, "--since", "7d", "--project", "proj", "--json").stdout)
    assert report["skipped"]["malformed_journals"] == 1
    assert report["skipped"]["malformed_lines"] == 3
    assert report["skipped"]["undated_events"] == 1
    assert report["skipped"]["malformed_ledger_lines"] == 1
    assert report["sessions"] == 2                        # s-new and s-mixed; s-junk counts nothing


def test_no_content_reaches_the_output(tmp_path):
    setup(tmp_path)
    journal(tmp_path / "config", "proj", "s-hostile", [refusal(1, rule=f"rm -rf /{MARKER}"),
                                                      refusal(1, rule={"nested": MARKER})])
    for args in (("--json",), ()):
        proc = run(tmp_path, "--since", "7d", "--project", "proj", *args)
        assert proc.returncode == 0, proc.stderr
        assert MARKER not in proc.stdout + proc.stderr
        assert "/secret" not in proc.stdout and "s-new" not in proc.stdout
    report = json.loads(run(tmp_path, "--since", "7d", "--project", "proj", "--json").stdout)
    assert report["refusals"]["by_rule"]["(unrecognised)"] == 2
