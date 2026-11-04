"""tests/test_context_ledger.py: per-agent attribution and the once-per-tier advisory
(hooks/shared/context_ledger.py), end to end through dispatch, plus bin/repact-cost and
`repact inspect --refusals --agent`. Attribution sums SIZES and keeps no text; nothing is said under the
advise line, each tier once per actor, a new name starts at zero; the transcript is measured
by os.stat alone. The noise rule runs with the lines lowered through profile.json
(`context_lines`), the tunable the module ships."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

from shared import context_ledger as ledger  # noqa: E402  (hooks/ is on the path: tests/conftest.py)
from shared.pact_context import reconstruct_session_dir  # noqa: E402

LIMITS = {"advise": 1_000, "stop": 2_000, "session": 10 ** 12}
BIG = {"command": "x" * 300}          # one frame of ~316 input bytes
CHECK = ("import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'hookSpecificOutput': "
         "{'hookEventName': 'PreToolUse', 'additionalContext': 'ran'}}))\n")


def _run(text, event, payload, agent="bob", frame="teammate", sizes=None, limits=LIMITS):
    text, _, rows = ledger.add_run(text, event, frame, sizes or {}, agent,
                                   ledger.frame_sizes(event, payload), limits)
    return text, rows


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class TestAttribution:
    def test_sums_sizes_only_and_keeps_no_text(self):
        secret = {"command": "echo secret-token-xyz"}
        response = {"stdout": "y" * 100}
        text, _ = _run("", "PreToolUse", {"tool_input": secret}, sizes={"allow_ctx": (10, 0)})
        text, _ = _run(text, "PostToolUse", {"tool_response": response})
        counters = json.loads(text)
        n_in, n_out = _json_bytes(secret), _json_bytes(response)
        assert counters["agents"]["bob"] == {
            "calls": 1, "in_bytes": n_in, "out_bytes": n_out, "injected": 10,
            "est_context": n_in + n_out + 10, "est_spend": n_in + 10, "tier": ""}
        assert "secret-token" not in text and "yyy" not in text
        assert counters["checks"] == {"allow_ctx": {"runs": 1, "context": 10, "system": 0}}
        assert counters["frames"] == {"teammate": {"runs": 1, "context": 10, "system": 0}}


class TestTiers:
    def test_silent_under_the_line_one_line_per_tier_and_a_new_name_starts_at_zero(self):
        text = ""
        for _ in range(3):
            text, _ = _run(text, "PreToolUse", {"tool_input": BIG})
        assert "pending" not in json.loads(text)                       # 3 frames under: silence
        text, _ = _run(text, "PreToolUse", {"tool_input": BIG})        # the 4th crosses 1,000
        [line] = json.loads(text)["pending"]
        assert line.startswith("context: bob ~1 KB after 4 calls; consider replacing it")
        text, _ = _run(text, "PreToolUse", {"tool_input": BIG})        # a repeat: nothing new
        assert len(json.loads(text)["pending"]) == 1
        assert ledger.consume(text, "bob", "teammate")[2] == ([], [])  # not the teammate's line
        text, changed, (system, context) = ledger.consume(text, "lead", "lead")
        assert changed and system == [line] and context == []
        assert ledger.consume(text, "lead", "lead")[2] == ([], [])    # spoken once
        for _ in range(2):
            text, _ = _run(text, "PreToolUse", {"tool_input": BIG})    # the 7th crosses 2,000
        row = json.loads(text)["agents"]["bob"]
        assert row["tier"] == "stop" and row["stop_pending"] == "stop"
        text, _, (system, context) = ledger.consume(text, "bob", "teammate")
        assert system == [] and len(context) == 1
        assert context[0].startswith("your context is past the stop line (~2 KB): start no new work")
        assert ledger.consume(text, "bob", "teammate")[2] == ([], [])
        text, _, (system, _) = ledger.consume(text, "lead", "lead")
        assert len(system) == 1 and "bob is past the stop line" in system[0]
        text, _ = _run(text, "PreToolUse", {"tool_input": BIG}, agent="bob2")
        counters = json.loads(text)
        assert counters["agents"]["bob2"]["tier"] == "" and counters["pending"] == []


def _task(task_id="7", owner="bob", status="in_progress", **metadata) -> dict:
    return {"id": task_id, "owner": owner, "status": status, "metadata": metadata}


class TestBudget:
    """The per-task lines: default, large by stamp, explicit, and the fallbacks."""

    DEFAULT = (300_000, 500_000)
    LARGE = (500_000, 800_000)

    @staticmethod
    def _lines(task) -> tuple:
        budget = ledger.task_budget(task, ledger.LINES)
        assert budget["session"] == ledger.LINES["session"]
        return budget["advise"], budget["stop"]

    def test_default_without_a_task_a_stamp_or_with_a_small_stamp(self):
        assert self._lines(None) == self.DEFAULT
        assert self._lines(_task()) == self.DEFAULT
        assert self._lines(_task(variety={"scope": 2, "risk": 3, "total": 9})) == self.DEFAULT
        assert ledger.hard_line(ledger.LINES) == 625_000


class TestInject:
    def test_lines_join_an_existing_envelope_and_make_one_when_absent(self):
        made = json.loads(ledger.inject(b"", "PreToolUse", ["L"], ["C"]))
        assert made == {"systemMessage": "L", "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": "C"}}
        existing = json.dumps({"systemMessage": "m", "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow",
            "additionalContext": "c"}}).encode("utf-8")
        joined = json.loads(ledger.inject(existing, "PreToolUse", ["L"], ["C"]))
        assert joined == {"systemMessage": "m\nL", "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow",
            "additionalContext": "c\nC"}}
        assert ledger.inject(b"", "PreToolUse", [], []) == b""
        assert ledger.inject(b"not json", "PreToolUse", ["L"], []) == b"not json"


class TestTranscript:
    def test_the_transcript_is_measured_by_stat_and_never_opened(self, tmp_path, monkeypatch):
        transcript = tmp_path / "t.jsonl"
        transcript.write_bytes(b"x" * 123)

        def refuse(*_args, **_kwargs):
            raise AssertionError("the transcript was opened")

        monkeypatch.setattr(builtins, "open", refuse)
        monkeypatch.setattr(os, "open", refuse)
        assert ledger.transcript_size({"transcript_path": str(transcript)}) == 123
        assert ledger.transcript_size({"agent_transcript_path": str(tmp_path / "gone"),
                                       "transcript_path": str(transcript)}) == 123
        assert ledger.transcript_size({"transcript_path": 7}) == 0


# --- through dispatch ------------------------------------------------------------------------

DRIVER = ("import sys; sys.path.insert(0, {hooks!r}); import dispatch, dispatch_registry as r; "
          "sys.exit(dispatch.dispatch('PreToolUse', sys.stdin.buffer.read(), "
          "checks=[r.Check(name='allow_ctx', script=sys.argv[1])]))").format(
    hooks=str(PLUGIN_ROOT / "hooks"))


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A lead session s1 under a temp config root, the lines lowered by profile.json; the
    frames run as the host runs them, one dispatch process each."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"), "CLAUDE_PROJECT_DIR": str(project),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    (tmp_path / "xdg" / "repacted").mkdir(parents=True)
    (tmp_path / "xdg" / "repacted" / "profile.json").write_text(json.dumps({"context_lines": LIMITS}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", env["CLAUDE_CONFIG_DIR"])
    session_dir = Path(reconstruct_session_dir(str(project), "s1"))
    session_dir.mkdir(parents=True)
    script = tmp_path / "allow_ctx.py"
    script.write_text(CHECK)

    def frame(payload) -> dict:
        done = subprocess.run([sys.executable, "-c", DRIVER, str(script)], capture_output=True,
                              input=json.dumps(payload).encode("utf-8"), env=env, timeout=60)
        assert done.returncode == 0 and done.stderr == b"", done.stderr
        return json.loads(done.stdout) if done.stdout.strip() else {}

    return session_dir, frame


@pytest.mark.slow
def test_the_lead_is_never_told_about_its_own_context(session):
    session_dir, frame = session
    payload = {"tool_name": "Bash", "session_id": "s1", "tool_input": BIG}
    envelopes = [frame(payload) for _ in range(9)]
    assert all(env.get("systemMessage", "") == "" for env in envelopes)
    told = [env.get("hookSpecificOutput", {}).get("additionalContext", "") for env in envelopes]
    assert all("compact" not in line and "stop line" not in line and "hard line" not in line
               for line in told)
    counters = json.loads((session_dir / ledger.COST_FILE).read_text("utf-8"))
    assert counters["agents"]["lead"]["calls"] == 9
    assert counters["agents"]["lead"]["tier"] == "hard" and not counters.get("pending")
    assert not counters["agents"]["lead"].get("stop_pending")
    assert counters["checks"]["allow_ctx"]["runs"] == 9


# --- the CLIs -------------------------------------------------------------------------------

def _cli(name, *args):
    argv = ["bash", str(PLUGIN_ROOT / "bin" / "repact"), "inspect", "--refusals"] if name == "repact inspect --refusals" \
        else [sys.executable, str(PLUGIN_ROOT / "bin" / name)]
    return subprocess.run([*argv, *args],
                          capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CODE_SESSION_ID": ""}, timeout=30)


def test_repact_cost_prints_the_agents_by_spend(tmp_path):
    assert _cli("repact-cost", "--session-dir", str(tmp_path)).returncode == 3
    cost = tmp_path / "context-cost.json"
    cost.write_text(json.dumps({"checks": {}}))
    assert _cli("repact-cost", "--session-dir", str(tmp_path)).returncode == 1
    cost.write_text(json.dumps({"agents": {
        "lead": {"calls": 4, "est_context": 900, "est_spend": 2_000_000, "tier": "advise"},
        "bob": {"calls": 9, "est_context": 2_100, "est_spend": 9_000_000, "in_bytes": 1_000,
                "out_bytes": 1_000, "injected": 100, "transcript_bytes": 50_000, "tier": "stop"}}}))
    done = _cli("repact-cost", "--session-dir", str(tmp_path))
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[1].startswith("bob") and lines[2].startswith("lead") and lines[3].startswith("total")
    assert "9.0 MB-c" in lines[1] and "stop" in lines[1]
    rows = json.loads(_cli("repact-cost", "--session-dir", str(tmp_path), "--top", "1", "--json").stdout)
    assert [row["agent"] for row in rows] == ["bob"] and rows[0]["transcript"] == 50_000


@pytest.mark.slow
def test_repact_explain_filters_refusals_by_agent(tmp_path):
    def row(agent):
        base = {"v": 1, "type": "gate_refusal", "ts": "2026-10-04T10:00:00Z", "hook": "deny_a",
                "verdict": "deny", "rule": "deny_a.exit_2", "code": "abc123abc123", "target": "",
                "frame": "teammate", "event": "PreToolUse"}
        return json.dumps({**base, **({"agent": agent} if agent else {})})
    (tmp_path / "session-journal.jsonl").write_text(row("bob") + "\n" + row("") + "\n")
    done = _cli("repact inspect --refusals", "--agent", "bob", "--session-dir", str(tmp_path), "--json")
    assert done.returncode == 0 and [r["agent"] for r in json.loads(done.stdout)] == ["bob"]
    done = _cli("repact inspect --refusals", "--agent", "bob", "--session-dir", str(tmp_path))
    assert "agent bob" in done.stdout
    assert _cli("repact inspect --refusals", "--agent", "alice", "--session-dir", str(tmp_path)).returncode == 1
