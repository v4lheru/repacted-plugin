"""tests/test_context_ledger.py: per-agent attribution and the once-per-tier advisory
(hooks/shared/context_ledger.py), end to end through dispatch, plus bin/repact-cost and
repact-explain --agent. Attribution sums SIZES and keeps no text; nothing is said under the
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

    def test_a_failed_call_counts_its_error_and_a_huge_response_is_capped(self):
        text, _ = _run("", "PostToolUseFailure", {"error": "Exit code 1\nboom"})
        assert json.loads(text)["agents"]["bob"]["out_bytes"] == _json_bytes("Exit code 1\nboom")
        text, _ = _run("", "PostToolUse", {"tool_response": {"stdout": "z" * (ledger.OUT_CAP + 5)}})
        assert json.loads(text)["agents"]["bob"]["out_bytes"] == ledger.OUT_CAP

    def test_base_is_the_sessions_measured_start_per_run(self):
        counters = {"checks": {"session_init": {"runs": 2, "context": 200, "system": 20},
                               "peer_inject": {"runs": 1, "context": 50, "system": 0}}}
        assert ledger.base_bytes(counters, "lead") == 110
        assert ledger.base_bytes(counters, "teammate") == 160

    def test_a_non_tool_event_with_nothing_injected_writes_nothing(self):
        text, changed, rows = ledger.add_run("", "Stop", "lead", {}, "", {}, LIMITS)
        assert not changed and rows == []


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

    def test_the_band_is_said_once_between_stop_and_hard_then_the_hard_line_once(self):
        text = ""
        for _ in range(7):                                             # the 7th crosses 2,000
            text, _ = _run(text, "PreToolUse", {"tool_input": BIG})
        text, _, (_, [band]) = ledger.consume(text, "bob", "teammate")
        assert "start no new work" in band and "run the tests for what you have already" in band
        assert "`remaining:`" in band and "hard line (~2 KB)" in band   # 2,500 prints as 2 KB
        text, _ = _run(text, "PreToolUse", {"tool_input": {"c": "x"}})  # still in the band
        assert json.loads(text)["agents"]["bob"]["tier"] == "stop"
        assert ledger.consume(text, "bob", "teammate")[2] == ([], [])  # the band is said once
        text, _ = _run(text, "PreToolUse", {"tool_input": BIG})        # the 9th crosses 2,500
        assert json.loads(text)["agents"]["bob"]["tier"] == "hard"
        text, _, (_, [hard]) = ledger.consume(text, "bob", "teammate")
        assert hard.startswith("your context is past the hard line (~3 KB); write your HANDOFF now")
        text, _, (system, _) = ledger.consume(text, "lead", "lead")
        assert [line.split(" (")[0] for line in system[1:]] == [
            "context: bob is past the stop line", "context: bob is past the hard line"]
        text, _ = _run(text, "PreToolUse", {"tool_input": BIG})
        assert ledger.consume(text, "bob", "teammate")[2] == ([], [])  # the hard line once

    def test_the_session_line_is_spoken_once_and_the_budget_row_names_the_top(self):
        limits = {"advise": 10 ** 9, "stop": 10 ** 9, "session": 500}
        text = ""
        for agent in ("a", "b", "a", "b"):
            text, _ = _run(text, "PreToolUse", {"tool_input": {"c": "x" * 100}}, agent=agent,
                           limits=limits)
        [line] = json.loads(text)["pending"]
        assert line.startswith("session spend ~0 MB-calls across 2 agents; top: ")
        assert json.loads(text)["session_told"] is True
        text, _, rows = ledger.add_run(text, "SessionEnd", "lead", {}, "", {}, limits)
        [(kind, row)] = rows
        assert kind == "context_budget" and row["agents"] == 2
        assert [item["agent"] for item in row["top"]] == ["a", "b"]
        assert row["spend"] == sum(item["spend"] for item in row["top"])

    def test_profile_overrides_the_lines_it_names_with_positive_ints(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        (tmp_path / "repacted").mkdir()
        (tmp_path / "repacted" / "profile.json").write_text(json.dumps(
            {"context_lines": {"advise": 5, "stop": True, "session": -1, "other": 9}}))
        assert ledger.lines() == {"advise": 5, "stop": ledger.LINES["stop"],
                                  "session": ledger.LINES["session"]}
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
        assert ledger.lines() == ledger.LINES


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

    def test_large_by_scope(self):
        assert self._lines(_task(variety={"scope": 3, "risk": 1})) == self.LARGE

    def test_large_by_risk(self):
        assert self._lines(_task(variety={"scope": 1, "risk": 4})) == self.LARGE

    def test_explicit_budget_wins_over_the_stamp_and_is_clamped(self):
        assert self._lines(_task(context_budget_kb=700, variety={"scope": 4})) == (420_000, 700_000)
        assert self._lines(_task(context_budget_kb=350.5)) == (210_300, 350_500)
        for low in (0, -5, 299):
            assert self._lines(_task(context_budget_kb=low)) == (180_000, 300_000)
        for high in (1001, 10 ** 400):
            assert self._lines(_task(context_budget_kb=high)) == (600_000, 1_000_000)

    @pytest.mark.parametrize("metadata", [
        {"variety": "large"}, {"variety": ["scope", 4]}, {"variety": {"scope": "4", "risk": None}},
        {"variety": {"scope": True, "risk": float("nan")}}, {"context_budget_kb": "900"},
        {"context_budget_kb": True}, {"context_budget_kb": float("nan")},
        {"context_budget_kb": float("inf")}, {"context_budget_kb": {"kb": 900}}])
    def test_a_malformed_stamp_or_budget_falls_back_to_the_default(self, metadata):
        assert self._lines(_task(**metadata)) == self.DEFAULT
        assert self._lines({"id": "7", "metadata": "x"}) == self.DEFAULT
        assert self._lines(["not", "a", "task"]) == self.DEFAULT

    def test_a_malformed_budget_does_not_hide_a_large_stamp(self):
        assert self._lines(_task(context_budget_kb="900", variety={"risk": 4})) == self.LARGE

    def test_the_actors_task_is_its_highest_in_progress_one_and_the_lead_keeps_the_default(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        team = tmp_path / "tasks" / "team-a"
        team.mkdir(parents=True)
        for task in (_task("9", context_budget_kb=900), _task("10", variety={"scope": 3}),
                     _task("11", status="completed", context_budget_kb=400),
                     _task("12", owner="eve", context_budget_kb=400)):
            (team / f"{task['id']}.json").write_text(json.dumps(task))
        (team / "13.json").write_text("{not json")                     # unreadable: skipped
        assert ledger.current_task("team-a", "bob")["id"] == "10"      # 10 over 9, not "9" > "10"
        assert ledger.current_task("team-a", "nobody") is None
        assert ledger.current_task("no-such-team", "bob") is None
        assert ledger.current_task("../team-a", "bob") is None
        monkeypatch.setattr(ledger, "actor_team", lambda payload: "team-a")
        assert ledger.agent_limits({}, "teammate", "bob", ledger.LINES)["stop"] == 800_000
        assert ledger.agent_limits({}, "teammate", "eve", ledger.LINES)["stop"] == 400_000
        assert ledger.agent_limits({}, "lead", "lead", ledger.LINES) == ledger.LINES
        assert ledger.agent_limits({}, "teammate", "unknown", ledger.LINES) == ledger.LINES
        monkeypatch.setattr(ledger, "current_task", lambda team, agent: 1 / 0)
        assert ledger.agent_limits({}, "teammate", "bob", ledger.LINES) == ledger.LINES

    def test_the_budget_is_read_only_from_the_lowest_line_up_and_moves_the_tiers(self):
        calls = []

        def budget():
            calls.append(1)
            return {**LIMITS, "advise": 5_000, "stop": 8_000}

        limits = {**LIMITS, "advise": 600}                             # lowest line: 600
        text, _, _ = ledger.add_run("", "PreToolUse", "teammate", {}, "bob",
                                    ledger.frame_sizes("PreToolUse", {"tool_input": BIG}),
                                    limits, budget)
        assert calls == [] and json.loads(text)["agents"]["bob"]["tier"] == ""
        for _ in range(8):                                             # ~2.8 KB: past LIMITS' hard
            text, _, _ = ledger.add_run(text, "PreToolUse", "teammate", {}, "bob",
                                        ledger.frame_sizes("PreToolUse", {"tool_input": BIG}),
                                        limits, budget)
        assert len(calls) == 8 and json.loads(text)["agents"]["bob"]["tier"] == ""


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


def test_the_lead_hears_each_tier_once_on_the_frame_after_the_crossing(session):
    session_dir, frame = session
    payload = {"tool_name": "Bash", "session_id": "s1", "tool_input": BIG}
    envelopes = [frame(payload) for _ in range(9)]
    said = [env.get("systemMessage", "") for env in envelopes]
    told = [env.get("hookSpecificOutput", {}).get("additionalContext", "") for env in envelopes]
    assert all(line == "" for line in said[:4])                        # under the line: silence
    assert said[4].startswith("context: lead ~1 KB after 4 calls; consider a fresh session")
    assert said[5] == ""                                               # not repeated
    assert said[7].startswith("context: lead is past the stop line (~2 KB after 7 calls)")
    # The lead's own wording: its work goes on in fresh agents, and the user runs the refresh.
    assert "do no new work in THIS context" in told[7] and told[7].startswith("ran\n")
    assert "give it to a fresh agent" in told[7] and "/repacted:refresh then /compact" in told[7]
    assert "start no new work" not in told[7] and "write your HANDOFF" not in told[7]
    assert said[8].startswith("context: lead is past the hard line (~3 KB after 8 calls)")
    assert "write your HANDOFF now" in told[8]
    counters = json.loads((session_dir / ledger.COST_FILE).read_text("utf-8"))
    assert counters["agents"]["lead"]["calls"] == 9
    assert counters["agents"]["lead"]["tier"] == "hard" and counters["pending"] == []
    assert counters["checks"]["allow_ctx"]["runs"] == 9


# --- the CLIs -------------------------------------------------------------------------------

def _cli(name, *args):
    return subprocess.run([sys.executable, str(PLUGIN_ROOT / "bin" / name), *args],
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


def test_repact_explain_filters_refusals_by_agent(tmp_path):
    def row(agent):
        base = {"v": 1, "type": "gate_refusal", "ts": "2026-10-04T10:00:00Z", "hook": "deny_a",
                "verdict": "deny", "rule": "deny_a.exit_2", "code": "abc123abc123", "target": "",
                "frame": "teammate", "event": "PreToolUse"}
        return json.dumps({**base, **({"agent": agent} if agent else {})})
    (tmp_path / "session-journal.jsonl").write_text(row("bob") + "\n" + row("") + "\n")
    done = _cli("repact-explain", "--agent", "bob", "--session-dir", str(tmp_path), "--json")
    assert done.returncode == 0 and [r["agent"] for r in json.loads(done.stdout)] == ["bob"]
    done = _cli("repact-explain", "--agent", "bob", "--session-dir", str(tmp_path))
    assert "agent bob" in done.stdout
    assert _cli("repact-explain", "--agent", "alice", "--session-dir", str(tmp_path)).returncode == 1
