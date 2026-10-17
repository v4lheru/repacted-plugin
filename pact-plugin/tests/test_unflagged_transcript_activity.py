"""D3 STATUS: the UNFLAGGED BACKGROUND WORK alarm names only teammates that look stopped.

the design notes §1.3 measured 13 alarm lines and 0 real stalls: the alarm named
teammates that were working. §D3's fix, driven end to end through run_surface on a lead
frame:
  - a teammate is working, and left out, when it is waiting on a call it started (from its
    last message; a long foreground call writes nothing until it returns), or when it wrote
    in the last 10 minutes AND its turn has not ended;
  - each teammate is named once, with all its task ids;
  - a teammate whose transcript cannot be found is still named (fail to the alarm, never
    to silence), and so is one with no lead transcript path at all;
  - the surface's first sentence is unchanged.
Fixture transcripts are synthetic: entry shapes only, no real content.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import missed_wake_scan as mw  # noqa: E402
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified  # noqa: E402

NOW = datetime.now(timezone.utc)
TEAM = "d3-team"
HEX = "0123456789abcdef"
FIRST_SENTENCE = ("UNFLAGGED BACKGROUND WORK — these teammates launched background work "
                  "and have no flagged wait: ")


def _iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def _assistant(minutes_ago, *, tool_id=None, stop="end_turn"):
    content = [{"type": "tool_use", "id": tool_id, "name": "Bash",
                "input": {"command": "true", "description": "wait"}}] if tool_id else []
    return {"type": "assistant", "timestamp": _iso(minutes_ago),
            "message": {"id": f"msg-{tool_id or minutes_ago}", "role": "assistant",
                        "stop_reason": "tool_use" if tool_id else stop, "content": content}}


def _result(minutes_ago, tool_id):
    return {"type": "user", "timestamp": _iso(minutes_ago),
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_id}]}}


@pytest.fixture
def lead(tmp_path, monkeypatch):
    """A team with outstanding unflagged launches, a lead transcript and teammate transcripts."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    registered = (NOW - timedelta(minutes=40)).isoformat()
    records = [
        {"agent_name": name, "session_id": "s", "task_ids": [tid], "registered_at": registered}
        for name, tid in [("busy-a", "5"), ("idle-b", "6"), ("idle-b", "7"),
                          ("missing-c", "8"), ("longcall-d", "9"), ("dead-e", "10"),
                          ("ended-f", "11"), ("orphan-g", "12"), ("refused-h", "13")]
    ]
    (tmp_path / "teams" / TEAM).mkdir(parents=True)
    (tmp_path / "teams" / TEAM / "background_work.json").write_text(json.dumps({"records": records}))
    tasks = [{"id": r["task_ids"][0], "status": "in_progress", "owner": r["agent_name"]}
             for r in records]
    monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
    monkeypatch.setattr("shared.pact_context.get_team_name", lambda: TEAM)

    session = tmp_path / "projects" / "-slug" / "sess"
    subagents = session / "subagents"
    subagents.mkdir(parents=True)
    lead_path = session.with_suffix(".jsonl")
    lead_path.write_text("")

    def write(name, entries):
        path = subagents / f"agent-a{name}-{HEX}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))

    write("busy-a", [_assistant(3, tool_id="t1"), _result(2, "t1")])  # mid-turn: a result just came back
    write("idle-b", [_assistant(40)])
    write("longcall-d", [_assistant(25, tool_id="t2")])            # a call still running
    write("dead-e", [_assistant(90, tool_id="t3")])                # started 90 min ago, never returned
    write("ended-f", [_assistant(4, tool_id="t4"), _result(3, "t4"), _assistant(2)])  # turn ENDED 2 min ago
    # An orphaned call in an earlier message must not keep a finished teammate busy.
    write("orphan-g", [_assistant(30, tool_id="t5"), _assistant(20)])
    write("refused-h", [_assistant(2, stop="refusal")])            # a refusal ends the turn too
    return lead_path


def _surface(lead_path):
    frame = dict(captured_lead_userpromptsubmit_qualified())
    if lead_path is not None:
        frame["transcript_path"] = str(lead_path)
    else:
        frame.pop("transcript_path", None)
    return mw.run_surface(frame, now=NOW) or ""


def _named(surface):
    body = surface.split(FIRST_SENTENCE, 1)[1].split(". The job may", 1)[0]
    return [part.split(" (", 1)[0] for part in body.split("; ")]


def test_a_teammate_writing_its_transcript_is_not_named(lead):
    assert "busy-a" not in _named(_surface(lead))


def test_a_teammate_waiting_on_its_own_tool_call_is_not_named(lead):
    assert "longcall-d" not in _named(_surface(lead))


def test_an_idle_teammate_with_a_job_outstanding_is_still_named(lead):
    assert "idle-b" in _named(_surface(lead))


def test_a_call_that_never_returned_within_the_cap_does_not_hide_a_stop(lead):
    assert "dead-e" in _named(_surface(lead))


def test_a_teammate_whose_turn_ended_recently_is_still_named(lead):
    """A recent write is not work once the turn has ended: its job is outstanding and
    nothing will wake it."""
    assert "ended-f" in _named(_surface(lead))


def test_a_turn_that_ended_in_a_refusal_is_still_named(lead):
    assert "refused-h" in _named(_surface(lead))


def test_an_orphaned_call_in_an_earlier_message_does_not_hide_a_stop(lead):
    assert "orphan-g" in _named(_surface(lead))


def test_a_teammate_without_a_transcript_is_still_named(lead):
    assert "missing-c" in _named(_surface(lead))


def test_each_teammate_is_named_once_with_all_its_tasks(lead):
    surface = _surface(lead)
    assert _named(surface).count("idle-b") == 1
    assert "idle-b (task(s) 6, 7)" in surface


def test_the_exact_set_named(lead):
    assert sorted(_named(_surface(lead))) == ["dead-e", "ended-f", "idle-b", "missing-c", "orphan-g", "refused-h"]


def test_no_lead_transcript_path_keeps_every_teammate(lead):
    assert sorted(_named(_surface(None))) == [
        "busy-a", "dead-e", "ended-f", "idle-b", "longcall-d", "missing-c", "orphan-g", "refused-h"]


def test_the_first_sentence_is_unchanged(lead):
    assert _surface(lead).startswith(FIRST_SENTENCE)
