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

from shared import cost_cap, pact_config, session_spend, team_policy

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

@pytest.mark.parametrize("raw, value", [("25", 25.0), ("12.50", 12.5), (" 7 ", 7.0),
                                        ("1000000", 1_000_000.0)])
def test_user_cap_reads_plain_dollars(monkeypatch, raw, value):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    assert pact_config.get_usd("PACT_SESSION_COST_CAP_USD") == (value, "")


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999", "1e3", "-5",
                                 "0", "0.0", "1000000.01", "99999999999", "1_000", "0x10", "true",
                                 "25 USD", "$25", "١٢", "２５", "25,5", "5.",
                                 ".5", "1.1234567"])
def test_user_cap_refuses_everything_else_and_says_so(monkeypatch, raw):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    value, why = pact_config.get_usd("PACT_SESSION_COST_CAP_USD")
    assert value is None and why


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_user_cap_is_unset(monkeypatch, raw):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", raw)
    assert pact_config.get_usd("PACT_SESSION_COST_CAP_USD") == (None, "")


# --------------------------------------------------------------------------- the policy's value

@pytest.mark.parametrize("value", [5, 5.5, 1_000_000])
def test_policy_cap_reads_a_positive_number(repo, value):
    policy(repo, {"version": 1, "session": {"cost_cap_usd": value}})
    assert team_policy.load(str(repo)).values["cost_cap_usd"] == float(value)


@pytest.mark.parametrize("value", [True, False, "5", 0, -1, -0.5, 1_000_001, 10 ** 17, None,
                                   [5], {"usd": 5}])
def test_policy_cap_ignores_and_notes_a_bad_value(repo, value):
    policy(repo, {"version": 1, "session": {"cost_cap_usd": value}})
    loaded = team_policy.load(str(repo))
    assert "cost_cap_usd" not in loaded.values
    assert any(line.startswith("session.cost_cap_usd") for line in loaded.ignored)


@pytest.mark.parametrize("text", ['{"session": {"cost_cap_usd": NaN}}',
                                  '{"session": {"cost_cap_usd": Infinity}}',
                                  '{"session": {"cost_cap_usd": 1e999}}',
                                  '{"session": {"cost_cap_usd": 1234567890123456789012}}'])
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


@pytest.mark.parametrize("model, key", [("claude-sonnet-4-5-20250929", "sonnet-4-5"),
                                        ("us.anthropic.claude-haiku-4-5@20251001", "haiku-4-5"),
                                        ("claude-opus-4-20250514", "opus-4"),
                                        ("claude-fable-5-1", "fable-5-1"),
                                        ("claude-3-5-sonnet-20241022", None), ("fable", None),
                                        ("<synthetic>", None), (None, None), (5, None)])
def test_model_ids_resolve_to_a_price_row_or_none(model, key):
    assert session_spend.price_key(model) == key


def test_unknown_model_is_priced_at_the_highest_rate_and_flagged():
    cost, _, priced = session_spend.message_cost("claude-next-9", ONE_MTOK_OUT)
    assert not priced and cost == pytest.approx(session_spend.HIGHEST[4])


def test_haiku_long_prompt_fast_mode_us_geo_and_web_search():
    long = usage(fresh=100_001)
    assert session_spend.message_cost("claude-haiku-5-5", long)[0] == pytest.approx(0.0500005)
    fast = dict(ONE_MTOK_OUT, speed="fast")
    assert session_spend.message_cost(OPUS, fast)[0] == pytest.approx(40.0)
    geo = dict(ONE_MTOK_OUT, inference_geo="us")
    assert session_spend.message_cost(OPUS, geo)[0] == pytest.approx(22.0)
    search = dict(usage(), server_tool_use={"web_search_requests": 3})
    assert session_spend.message_cost(OPUS, search)[0] == pytest.approx(0.03)


@pytest.mark.parametrize("bad", [True, -5, 10 ** 13, 1.5e6, "1000000", None])
def test_a_token_field_that_is_not_a_count_adds_nothing(bad):
    assert session_spend.message_cost(OPUS, {"output_tokens": bad})[0] == 0


# --------------------------------------------------------------------------- the reader

def test_spend_counts_each_message_once_across_lead_and_teammates(tmp_path):
    repeat = message("m1", ONE_MTOK_OUT)
    lead = lead_file(tmp_path, [repeat, repeat, '{"type": "user"}\n', "not json\n"])
    teammate_file(lead, "coder", [message("t1", ONE_MTOK_OUT)])
    spend = session_spend.session_spend(lead)
    assert spend.usd == pytest.approx(40.0) and spend.files == 2 and not spend.partial
    assert list(spend.teammates) == ["agent-acoder-0123456789abcdef.jsonl"]


def test_spend_reads_only_what_was_appended_since_the_cache(tmp_path):
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    cache = tmp_path / "session" / session_spend.CACHE_FILE
    assert session_spend.session_spend(lead, cache).usd == pytest.approx(20.0)
    with open(lead, "a", encoding="utf-8") as handle:
        handle.write(message("m2", ONE_MTOK_OUT))
    again = session_spend.session_spend(lead, cache)
    assert again.usd == pytest.approx(40.0) and again.read == len(message("m2", ONE_MTOK_OUT))


def test_a_line_still_being_written_is_read_next_time(tmp_path):
    whole = message("m1", ONE_MTOK_OUT)
    lead = lead_file(tmp_path, [whole[:-10]])
    cache = tmp_path / "s" / session_spend.CACHE_FILE
    assert session_spend.session_spend(lead, cache).usd == 0
    lead.write_text(whole, encoding="utf-8")
    assert session_spend.session_spend(lead, cache).usd == pytest.approx(20.0)


@pytest.mark.parametrize("cache_text", ["not json", '{"v": 1, "files": {"lead": {"offset": -1}}}',
                                        '{"v": 1, "files": {"lead": {"offset": 0, "ids": [], '
                                        '"usd": NaN, "tokens": 0, "unknown": []}}}',
                                        '{"v": 99, "files": {}}'])
def test_a_cache_that_cannot_be_trusted_is_read_again(tmp_path, cache_text):
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    cache = tmp_path / session_spend.CACHE_FILE
    cache.write_text(cache_text, encoding="utf-8")
    assert session_spend.session_spend(lead, cache).usd == pytest.approx(20.0)


def test_a_replaced_transcript_is_read_from_the_start(tmp_path):
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT), message("m2", ONE_MTOK_OUT)])
    cache = tmp_path / session_spend.CACHE_FILE
    session_spend.session_spend(lead, cache)
    lead.write_text(message("m3", ONE_MTOK_OUT), encoding="utf-8")
    assert session_spend.session_spend(lead, cache).usd == pytest.approx(20.0)


def test_a_spent_byte_budget_gives_a_lower_bound(tmp_path):
    lead = lead_file(tmp_path, [message(f"m{i}", ONE_MTOK_OUT) for i in range(4)])
    spend = session_spend.session_spend(lead, None, byte_budget=1)
    assert spend.partial and spend.usd == pytest.approx(20.0)


def test_no_lead_transcript_is_unreadable(tmp_path):
    with pytest.raises(session_spend.SpendUnreadable):
        session_spend.session_spend(tmp_path / "missing.jsonl")


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


def test_gate_refuses_a_spawn_past_the_cap_with_the_rule_and_who_set_it(gate, tmp_path, repo):
    policy(repo, {"session": {"cost_cap_usd": 30}})
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT), message("m2", ONE_MTOK_OUT)])
    decision, reason, rule = gate(lead=lead)
    assert (decision, rule) == ("DENY", "session_cost_cap")
    assert "about $40.00" in reason and "$30.00 set by team policy" in reason
    assert "policy.json set session.cost_cap_usd 30" in reason
    assert reason.endswith(f"[rule: {cost_cap.RULE_ID}]")


def test_gate_lets_a_spawn_under_the_cap_through_unchanged(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "50")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    assert gate(lead=lead) == ("ALLOW", None, None)


def test_gate_finds_the_lead_by_session_id_when_the_path_is_another_file(gate, tmp_path,
                                                                         monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "10")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    decoy = teammate_file(lead, "x", [])
    assert gate(lead=decoy)[0] == "DENY"


def test_gate_fails_open_loudly_once_when_the_spend_cannot_be_read(gate, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "10")
    decision, reason, rule = gate()
    assert (decision, rule) == ("WARN", cost_cap.NOTE_ID)
    assert "not enforced" in reason and "Shown once per session" in reason
    assert gate() == ("ALLOW", None, None)
    journal = (gate.session_dir / "session-journal.jsonl").read_text(encoding="utf-8")
    assert '"cost_cap_note"' in journal


def test_a_note_with_no_session_dir_does_not_claim_it_shows_once():
    verdict = cost_cap.note_once("spend_unreadable", "Cap not enforced.", "")
    assert verdict.message == "Cap not enforced."
    assert cost_cap.note_once("spend_unreadable", "Cap not enforced.", "").action == "note"


def test_gate_notes_an_unreadable_setting_and_applies_no_cap(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "1e999")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    decision, reason, _ = gate(lead=lead)
    assert decision == "WARN" and "no cap applies" in reason


def test_gate_a_reader_error_is_a_note_not_a_refusal(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "10")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(session_spend, "session_spend", broken)
    decision, reason, _ = gate(lead=lead)
    assert decision == "WARN" and "RuntimeError" in reason


def test_gate_keeps_an_earlier_warning_and_an_earlier_refusal(gate, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "10")
    decision, reason, rule = gate(decision=("WARN", "long prompt", "long_inline_mission"))
    assert (decision, rule) == ("WARN", "long_inline_mission")
    assert reason.startswith("long prompt\n\nPACT cost cap")
    assert gate(decision=("DENY", "no task", "no_task_assigned")) == ("DENY", "no task",
                                                                      "no_task_assigned")


@pytest.mark.parametrize("subagent_type", ["Explore", "Plan", "pact-secretary",
                                           "repacted:pact-secretary"])
def test_gate_does_not_cap_research_or_the_secretary(gate, tmp_path, monkeypatch, subagent_type):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "1")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    spawn = dict(SPAWN, subagent_type=subagent_type)
    assert gate(tool_input=spawn, lead=lead) == ("ALLOW", None, None)


@pytest.mark.parametrize("subagent_type", ["general-purpose", "claude", "my-agent",
                                           "other-plugin:pact-x", "explore", None, ""])
def test_gate_caps_every_other_spawn_type(gate, tmp_path, monkeypatch, subagent_type):
    """Regression: only pact-* types were capped, so general-purpose, built-in, other
    plugins' and non-pact project agents were the way around the cap."""
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "1")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    spawn = dict(SPAWN, subagent_type=subagent_type)
    assert gate(tool_input=spawn, lead=lead)[0] == "DENY"


def test_with_no_session_dir_the_spend_read_continues_from_the_state_dir(tmp_path,
                                                                         monkeypatch):
    """Regression: with no session dir nothing was cached, so each check re-read the same
    first bytes and a large spend stayed a frozen lower bound under the cap."""
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "50")
    lines = [message(f"m{i}", ONE_MTOK_OUT) for i in range(3)]
    lead = lead_file(tmp_path, lines)
    data = {"session_id": SESSION, "transcript_path": str(lead)}
    budget = len(lines[0]) + 2          # two of the three lines per check
    verdicts = [cost_cap.check(data, "", None, byte_budget=budget) for _ in range(2)]
    assert verdicts[0].action != "deny" and verdicts[1].action == "deny"
    assert (tmp_path / "state" / "repacted" / "spend" / f"{SESSION}.json").is_file()


def test_gate_caps_the_namespaced_spelling_too(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "1")
    lead = lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    spawn = dict(SPAWN, subagent_type="repacted:pact-backend-coder")
    assert gate(tool_input=spawn, lead=lead)[0] == "DENY"


# --------------------------------------------------------------------------- the preview

def past_session(tmp_path, session: str, dispatch_usd) -> None:
    lead = lead_file(tmp_path, [], session=session)
    (tmp_path / "config" / "pact-sessions" / "-repo" / session).mkdir(parents=True)
    for index, usd in enumerate(dispatch_usd):
        tokens = int(usd / 20 * 1_000_000)
        teammate_file(lead, f"t{index}", [message(f"x{index}", usage(output=tokens))])


def test_preview_states_the_median_of_past_dispatches_and_the_cap(tmp_path, monkeypatch):
    past_session(tmp_path, "past-1", [1, 2, 3, 4, 100])
    lead_file(tmp_path, [message("m1", ONE_MTOK_OUT)])
    monkeypatch.setenv("PACT_SESSION_COST_CAP_USD", "25")
    line = _cost_preview.preview_line(3, SESSION, None, cwd=str(tmp_path))
    assert line.startswith("Cost preview: 3 dispatches, about $9.00 ($3.00 each: median of 5 "
                           "past dispatches on this machine)")
    assert "this session so far $20.00" in line
    assert "cap $25.00 set by this machine, $5.00 left; these dispatches would pass it" in line
    assert line.endswith("estimate at list API prices, not billing.")


def test_preview_with_too_few_dispatches_uses_the_labelled_default(tmp_path):
    past_session(tmp_path, "past-1", [1, 2])
    line = _cost_preview.preview_line(1, SESSION, None, cwd=str(tmp_path))
    assert f"(${_cost_preview.DEFAULT_DISPATCH_USD:.2f} each: a default: only 2 recorded" in line
    assert "this session so far: unknown (no transcript of this session)" in line
    assert "no session cap (PACT_SESSION_COST_CAP_USD)" in line
