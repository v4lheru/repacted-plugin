"""tests/test_task_write_guard.py: the 2.1.0 write guards at their boundary.
task_write_guard refuses a reserved key in any
spelling or depth and a completion on a dispatched task, and denies input it cannot read, while
a lead todo task still writes. ops_frame_guard refuses a teammate's lead op by its host frame,
not by --as. subagent_stop relays a HANDOFF block, blocks once on none, and lets a two-phase
teachback end pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ops_frame_guard
import subagent_stop
import task_write_guard as guard

TEAM = "team-x"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A task store under a scratch config dir with one dispatched task (#7) and one lead todo (#8)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    tasks = tmp_path / "tasks" / TEAM
    tasks.mkdir(parents=True)
    dispatched = {"id": "7", "subject": "w", "status": "in_progress", "owner": "coder-1",
                  "metadata": {"dispatch": {"name": "coder-1", "agent": "pact-backend-coder"}}}
    todo = {"id": "8", "subject": "todo", "status": "pending", "metadata": {}}
    (tasks / "7.json").write_text(json.dumps(dispatched))
    (tasks / "8.json").write_text(json.dumps(todo))
    return tasks


def _update(tool_input: dict, raw: str = None):
    payload = {"tool_name": "TaskUpdate", "tool_input": tool_input}
    return guard.decide(payload, raw if raw is not None else json.dumps(payload), TEAM)


@pytest.mark.parametrize("raw_key", [
    '"\\u0068andoff"',          # a unicode-escaped key
    '"Handoff"',                # another case
    '"\\uff48andoff"',          # an NFKC lookalike (fullwidth h)
    '"hand\\u200boff"',         # a zero-width format character
])
def test_reserved_key_spellings_are_refused(store, raw_key):
    raw = '{"tool_name": "TaskUpdate", "tool_input": {"taskId": "7", "metadata": {%s: {}}}}' % raw_key
    reason, rule = guard.decide(json.loads(raw), raw, TEAM)
    assert rule == guard.RULE_RESERVED and "repact submit 7" in reason


def test_nested_and_duplicate_keys_are_refused(store):
    assert _update({"taskId": "7", "metadata": {"notes": {"teachback": {"x": 1}}}})[1] == guard.RULE_RESERVED
    raw = ('{"tool_name": "TaskUpdate", "tool_input": {"taskId": "7", '
           '"metadata": {"amend_1": {}, "amend_1": 1}}}')
    assert guard.decide(json.loads(raw), raw, TEAM)[1] == guard.RULE_RESERVED


def test_unparseable_input_is_denied(store):
    assert guard.decide({}, "{not json", TEAM)[1] == guard.RULE_UNREADABLE
    payload = {"tool_name": "TaskUpdate", "tool_input": {"taskId": 7, "status": "completed"}}
    assert guard.decide(payload, json.dumps(payload), TEAM)[1] == guard.RULE_UNREADABLE


def test_completion_of_a_dispatched_task_is_refused(store):
    reason, rule = _update({"taskId": "7", "status": "Completed"})
    assert rule == guard.RULE_COMPLETED and "repact accept 7" in reason


def test_a_lead_todo_task_still_writes(store):
    assert _update({"taskId": "8", "status": "completed", "metadata": {"note": "done"}}) == (None, None)
    assert _update({"taskId": "7", "metadata": {"note": "ok"}}) == (None, None)
    # a reserved key is the repact commands' on a todo task too
    assert _update({"taskId": "8", "metadata": {"handoff": {}}})[1] == guard.RULE_RESERVED


def test_task_create_cannot_forge_a_reserved_key(store):
    payload = {"tool_name": "TaskCreate", "tool_input": {"subject": "s", "metadata": {"dispatch": {}}}}
    assert guard.decide(payload, json.dumps(payload), TEAM)[1] == guard.RULE_RESERVED


class _Actor:
    def __init__(self, lead: bool, marked: bool, source: str = "unknown", name: str = ""):
        self.is_lead, self.marked, self.source, self.name = lead, marked, source, name

    def runs_lead_ops(self):
        return self.is_lead or (self.source == "unknown" and not self.marked)


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize("command", [
    "repact accept 7",
    "REPACT_AGENT_NAME= /x/bin/repact accept 7",
    "cd /tmp && python3 /x/bin/_repact_ops.py --team t accept 7",
    "bash -c 're''pact accept 7'",
    "echo $(/x/bin/rep?ct amend 7 --text hi)",
])
def test_a_teammate_calling_a_lead_op_without_as_is_refused(command):
    teammate = _Actor(lead=False, marked=True, source="host", name="coder-1")
    assert ops_frame_guard.decide(_bash(command), teammate).endswith("[rule: ops_frame_guard.lead_only]")


@pytest.mark.parametrize("command", [
    "env FOO=1 nice -n 5 repact accept 7",
    "sudo -u lead /x/bin/repact reject 7 --reason no",
    "uv run python3 /x/bin/_repact_ops.py amend 7 --text hi",
    "eval 'repact accept 7'",
    "find . -name x -exec repact accept 7 \\;",
    "bash <<'EOF'\nrepact accept 7\nEOF",
    "cat <(repact dispatch --name x)",
    "echo \"repact accept 7",
])
def test_a_lead_op_behind_a_runner_or_in_text_the_reader_cannot_read_is_refused(command):
    teammate = _Actor(lead=False, marked=True, source="host", name="coder-1")
    assert ops_frame_guard.decide(_bash(command), teammate).endswith("[rule: ops_frame_guard.lead_only]")


@pytest.mark.parametrize("command", [
    "grep -rn 'repact accept' hooks/",
    "echo repact accept 7",
    "git commit -m 'Make repact accept read the ledger.'",
    "rg -n \"repact (accept|reject)\" docs",
    "cat > notes.md <<'EOF'\nrepact accept 7\nEOF",
    "printf '%s\\n' \"run repact amend 7 later\"",
])
def test_a_lead_op_named_as_data_is_not_refused(command):
    teammate = _Actor(lead=False, marked=True, source="host", name="coder-1")
    assert ops_frame_guard.decide(_bash(command), teammate) is None


def test_the_lead_and_a_coder_submit_pass():
    assert ops_frame_guard.decide(_bash("repact accept 7"), _Actor(lead=True, marked=False)) is None
    teammate = _Actor(lead=False, marked=True, source="host")
    assert ops_frame_guard.decide(_bash("repact submit 7 --as coder-1"), teammate) is None
    assert ops_frame_guard.decide(_bash("repact-wait 7 --until completed,rejection,amend"), teammate) is None


def _transcript(tmp_path: Path, brief: Path, assistant_text: str) -> Path:
    path = tmp_path / "agent-a1.jsonl"
    rows = [{"type": "user", "message": {"role": "user", "content": f"Read {brief} and do what it says."}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": assistant_text}]}}]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


@pytest.fixture
def backstop(store, tmp_path, monkeypatch):
    """A session dir with its context file, and task #7 with an accepted teachback."""
    session = tmp_path / "pact-sessions" / "proj" / "sid-1"
    (session / "briefs").mkdir(parents=True)
    (session / "pact-session-context.json").write_text(json.dumps({"team_name": TEAM}))
    task = json.loads((store / "7.json").read_text())
    task["metadata"]["teachback"] = {"accepted": {"by": "auto"}}
    (store / "7.json").write_text(json.dumps(task))
    calls = []
    monkeypatch.setattr("shared.ops.submit.handoff",
                        lambda ctx, task_id, given, relayed=False: calls.append((task_id, given, relayed)))
    return session, calls


def _stop(path: Path) -> dict:
    return {"hook_event_name": "SubagentStop", "session_id": "sid-1", "agent_transcript_path": str(path)}


def test_backstop_relays_a_valid_block(tmp_path, backstop):
    session, calls = backstop
    text = 'Done.\n```json\n{"produced": ["a"]}\n```'
    decision, _text = subagent_stop.verdict(_stop(_transcript(tmp_path, session / "briefs" / "7.md", text)))
    assert decision == "message" and calls == [("7", {"produced": ["a"]}, True)]


def test_backstop_blocks_once_on_no_block(tmp_path, backstop):
    session, calls = backstop
    payload = _stop(_transcript(tmp_path, session / "briefs" / "7.md", "All done."))
    decision, reason = subagent_stop.verdict(payload)
    assert decision == "block" and "repact submit 7" in reason and calls == []
    assert subagent_stop.verdict(dict(payload, stop_hook_active=True))[0] == "message"


def test_backstop_passes_a_teachback_submitted_line(tmp_path, store, backstop):
    session, calls = backstop
    task = json.loads((store / "7.json").read_text())
    task["metadata"]["teachback"] = {"submitted_at": "now", "accepted": None}
    (store / "7.json").write_text(json.dumps(task))
    payload = _stop(_transcript(tmp_path, session / "briefs" / "7.md", "TEACHBACK SUBMITTED #7"))
    assert subagent_stop.verdict(payload) == (None, "") and calls == []
