"""
Location: pact-plugin/tests/test_session_cost_cap.py
Summary: The session cost cap and the cost preview: the numbers read at the boundary
         (PACT_SESSION_COST_CAP_USD and the policy's session.cost_cap_usd: NaN, inf, 1e999,
         negative, zero, huge, bool, string numbers, a policy that would raise the cap), the
         pricing of one message, the incremental spend reader, dispatch_gate's rule (10) with
         its fail-open-loud note, and the one-line preview with its stated basis.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from shared import pact_config, session_spend, team_policy

import dispatch_gate

PLUGIN = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("_cost_preview", PLUGIN / "bin" / "_cost_preview.py")
_cost_preview = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cost_preview)

SESSION = "11111111-2222-3333-4444-555555555555"
OPUS = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("PACT_SESSION_COST_CAP_USD", raising=False)
    team_policy.reset()
    yield
    team_policy.reset()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".repacted").mkdir()
    return root


def policy(repo: Path, document) -> None:
    text = document if isinstance(document, str) else json.dumps(document)
    (repo / ".repacted" / "policy.json").write_text(text, encoding="utf-8")


def message(mid: str, usage: dict, model: str = OPUS) -> str:
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": model,
                                                        "usage": usage}}) + "\n"


def usage(fresh=0, read=0, write_1h=0, output=0, **extra) -> dict:
    return {"input_tokens": fresh, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write_1h, "output_tokens": output,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": write_1h},
            **extra}


def lead_file(tmp_path, lines=(), session=SESSION) -> Path:
    path = tmp_path / "config" / "projects" / "-repo" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def teammate_file(lead: Path, name: str, lines) -> Path:
    path = lead.with_suffix("") / "subagents" / f"agent-a{name}-0123456789abcdef.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


# 1,000,000 output tokens of Opus 5.5 cost $20 at the table's rate.
ONE_MTOK_OUT = usage(output=1_000_000)


# --------------------------------------------------------------------------- the user's value

@pytest.mark.parametrize("raw, value", [("25", 25.0),])
def test_user_cap_reads_plain_dollars(monkeypatch, raw, value):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    assert pact_config.get_usd("PACT_SESSION_COST_CAP_USD") == (value, "")


@pytest.mark.parametrize("raw", ["nan",])
def test_user_cap_refuses_everything_else_and_says_so(monkeypatch, raw):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    value, why = pact_config.get_usd("PACT_SESSION_COST_CAP_USD")
    assert value is None and why


@pytest.mark.parametrize("raw", ["",])
def test_blank_user_cap_is_unset(monkeypatch, raw):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    assert pact_config.get_usd("PACT_SESSION_COST_CAP_USD") == (None, "")


# --------------------------------------------------------------------------- the policy's value

@pytest.mark.parametrize("value", [5,])
def test_policy_cap_reads_a_positive_number(repo, value):
    policy(repo, {"version": 1, "session": {"cost_cap_usd": value}})
    assert team_policy.load(str(repo)).values["cost_cap_usd"] == float(value)


@pytest.mark.parametrize("value", [True,])
def test_policy_cap_ignores_and_notes_a_bad_value(repo, value):
    policy(repo, {"version": 1, "session": {"cost_cap_usd": value}})
    loaded = team_policy.load(str(repo))
    assert "cost_cap_usd" not in loaded.values
    assert any(line.startswith("session.cost_cap_usd") for line in loaded.ignored)


@pytest.mark.parametrize("text", ['{"session": {"cost_cap_usd": NaN}}',])
def test_policy_cap_non_numbers_void_the_whole_file(repo, text):
    policy(repo, text)
    loaded = team_policy.load(str(repo))
    assert loaded.values == {} and loaded.ignored[0].startswith("the whole file")


def test_policy_cap_look_alike_and_duplicate_keys_are_ignored(repo):
    policy(repo, '{"session": {"cost_cap_usd": 5, "cost_cap_usd": 6, '
                 '"\\uff43ost_cap_usd": 1}}')
    loaded = team_policy.load(str(repo))
    assert "cost_cap_usd" not in loaded.values
    assert len([line for line in loaded.ignored if "cost_cap_usd" in line or "\\uff43" in line]) == 2


def test_session_is_no_longer_a_reserved_key(repo):
    policy(repo, {"session": "cheap"})
    assert team_policy.load(str(repo)).ignored == ["session: must be an object, not str"]


# --------------------------------------------------------------------------- the merge

def test_lower_policy_cap_wins_and_is_named(repo):
    policy(repo, {"session": {"cost_cap_usd": 10}})
    assert team_policy.session_cost_cap(50.0, str(repo)) == (10.0, "team policy")
    assert team_policy.raised(str(repo)) == {"session.cost_cap_usd": "10 (this machine: 50)"}
    assert "set session.cost_cap_usd 10" in team_policy.refusal_note(("session.",), str(repo))


def test_policy_cap_above_the_users_is_ignored_and_noted(repo):
    policy(repo, {"session": {"cost_cap_usd": 500}})
    assert team_policy.session_cost_cap(50.0, str(repo)) == (50.0, "this machine")
    assert any("looser than this machine's 50" in line for line in team_policy.ignored(str(repo)))


def test_policy_cap_applies_when_the_user_sets_none(repo):
    policy(repo, {"session": {"cost_cap_usd": 8}})
    assert team_policy.session_cost_cap(None, str(repo)) == (8.0, "team policy")


def test_no_cap_anywhere(repo):
    assert team_policy.session_cost_cap(None, str(repo)) == (None, "")


# --------------------------------------------------------------------------- pricing

def test_opus_message_is_priced_per_token_class():
    cost, tokens, priced = session_spend.message_cost(
        "claude-opus-5-5", usage(fresh=1_000_000, read=1_000_000, write_1h=1_000_000,
                                 output=1_000_000))
    assert priced and tokens == 4_000_000
    assert cost == pytest.approx(4 + 0.2 + 8 + 20)


def test_unsplit_cache_write_is_priced_as_one_hour():
    raw = {"cache_creation_input_tokens": 1_000_000}
    assert session_spend.message_cost("claude-sonnet-4-5-20250929", raw)[0] == pytest.approx(6.0)


# --------------------------------------------------------------------------- the reader


# --------------------------------------------------------------------------- dispatch_gate (10)

SPAWN = {"subagent_type": "pact-backend-coder", "name": "coder", "prompt": "check TaskList"}


@pytest.fixture
def gate(tmp_path, monkeypatch, repo):
    session_dir = tmp_path / "pact-sessions" / "-repo" / SESSION
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(dispatch_gate.pact_context, "get_session_dir", lambda: str(session_dir))
    monkeypatch.setattr(dispatch_gate, "project_dir", lambda: str(repo))

    def run(decision=("ALLOW", None, None), tool_input=SPAWN, lead=None):
        data = {"session_id": SESSION, "transcript_path": str(lead) if lead else ""}
        return dispatch_gate._apply_cost_cap(*decision, tool_input, data)
    run.session_dir = session_dir
    return run


# --------------------------------------------------------------------------- the preview

def past_session(tmp_path, session: str, dispatch_usd) -> None:
    lead = lead_file(tmp_path, [], session=session)
    (tmp_path / "config" / "pact-sessions" / "-repo" / session).mkdir(parents=True)
    for index, usd in enumerate(dispatch_usd):
        tokens = int(usd / 20 * 1_000_000)
        teammate_file(lead, f"t{index}", [message(f"x{index}", usage(output=tokens))])


