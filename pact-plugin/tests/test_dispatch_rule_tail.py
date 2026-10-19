"""tests/test_dispatch_rule_tail.py: every refusal line names the check and a rule id.

combine() prefixes `<check>: ` and suffixes `[rule: <id>]` on the reason the host shows
(stderr on an exit 2, the JSON's own reason field otherwise), once; the gate_refusal row
carries the id as `rule` and the old channel as `channel`. A script names its id with a
`[rule: <id>]` tail; a native check in Result.rule; a check that names none gets
`<check>.<channel>`. Hostile: a tail that is not a dotted id is text, not an id. The
contract rows pin every id the plugin emits to shared/rules.py, and `repact inspect --refusals`
over a written journal prints the sentence.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import dispatch
import dispatch_registry as registry
from shared.rules import RULES
from shell.env import programs
from shell.merge import arms

# The shell classifiers choose their ids from these tables (the tail is built, never literal).
SHELL_TABLES = (programs.RULES, arms.RULE_IDS, arms.MASK_RULE_IDS)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CLI = PLUGIN_ROOT / "bin" / "repact"


def _deny(reason: str) -> bytes:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}).encode()


def _outcome(name, exit_code=0, stdout=b"", stderr=b""):
    return dispatch.Outcome(check=registry.Check(name=name, script=f"hooks/{name}.py"),
                            exit_code=exit_code, stdout=stdout, stderr=stderr)


def _combined(outcomes, event="PreToolUse"):
    code, out, err, parsed = dispatch.combine(event, outcomes)
    rows = dispatch.journal_rows(event, {"tool_name": "Bash"}, parsed)
    return code, out.decode(), err.decode(), [f for t, f in rows if t == "gate_refusal"]


def _reason(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("outcome, line, rule, channel", [
    (_outcome("tailed", 2, _deny("no [rule: tailed.no_way]"), b"no [rule: tailed.no_way]\n"),
     "tailed: no [rule: tailed.no_way]", "tailed.no_way", "exit_2"),
    (_outcome("bare", 0, _deny("plain no")), "bare: plain no [rule: bare.deny]", "bare.deny", "deny"),
    (_outcome("already", 0, _deny("already: done [rule: already.done]")),
     "already: done [rule: already.done]", "already.done", "deny"),
    (_outcome("hostile", 0, _deny("x [rule: ../etc]")),
     "hostile: x [rule: ../etc] [rule: hostile.deny]", "hostile.deny", "deny"),
    (_outcome("nodot", 0, _deny("x [rule: nodot]")),
     "nodot: x [rule: nodot] [rule: nodot.deny]", "nodot.deny", "deny"),
])
def test_one_refusal_is_named_once_and_journaled_under_its_id(outcome, line, rule, channel):
    code, out, err, rows = _combined([outcome])
    assert _reason(out) == line
    assert err.strip() == (line if channel == "exit_2" else "")
    assert [(r["hook"], r["rule"], r["channel"]) for r in rows] == [(outcome.check.name, rule, channel)]


def test_an_exit_2_with_only_stderr_keeps_its_tail():
    code, out, err, rows = _combined([_outcome("exit_only", 2, b"", b"stop [rule: exit_only.stop]\n")])
    assert code == 2 and out == ""
    assert err == "exit_only: stop [rule: exit_only.stop]\n"
    assert rows[0]["rule"] == "exit_only.stop" and rows[0]["channel"] == "exit_2"


def test_two_refusals_are_each_named():
    code, out, err, rows = _combined([
        _outcome("tailed", 2, _deny("no [rule: tailed.no_way]"), b"no [rule: tailed.no_way]\n"),
        _outcome("bare", 0, _deny("plain no"))])
    assert code == 2
    assert _reason(out) == "tailed: no [rule: tailed.no_way] | bare: plain no [rule: bare.deny]"
    assert err == "tailed: no [rule: tailed.no_way] | bare: plain no [rule: bare.deny]"
    assert [r["rule"] for r in rows] == ["tailed.no_way", "bare.deny"]


def test_a_block_decision_is_named_in_its_own_reason_field():
    block = json.dumps({"decision": "block", "reason": "held"}).encode()
    code, out, err, rows = _combined([_outcome("block", 0, block)], event="PostToolUse")
    assert code == 0 and err == ""
    assert json.loads(out) == {"decision": "block", "reason": "block: held [rule: block.decision_block]"}
    assert [(r["rule"], r["channel"]) for r in rows] == [("block.decision_block", "decision_block")]


def test_a_native_result_rule_wins_over_the_fallback():
    outcome = dispatch.Outcome(check=registry.Check(name="nat", native="x:y"))
    dispatch._apply_native(outcome, registry.Result(decision="deny", reason="no", rule="nat.rule_a"),
                           "PreToolUse")
    code, out, err, parsed = dispatch.combine("PreToolUse", [outcome])
    assert _reason(out.decode()) == "nat: no [rule: nat.rule_a]"
    rows = dispatch.journal_rows("PreToolUse", {}, parsed)
    assert rows[0][1]["rule"] == "nat.rule_a" and rows[0][1]["channel"] == "deny"


# --- contract: every id the plugin emits has a sentence -----------------------------------------

_LITERAL_TAIL = re.compile(r"\[rule: ([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)\]")
_RESULT_RULE = re.compile(r"""rule=["']([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)["']""")
_RULE_CONST = re.compile(r"""^[A-Z_]*RULE[A-Z_]* = ["']([A-Za-z][A-Za-z0-9_.-]+)["']""", re.M)


def _emitted_ids() -> set[str]:
    ids = set()
    for path in (PLUGIN_ROOT / "hooks").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        ids.update(_LITERAL_TAIL.findall(text))
        ids.update(_RESULT_RULE.findall(text))
        ids.update(_RULE_CONST.findall(text))
    for checks in registry.REGISTRY.values():
        for check in checks:
            if check.on_timeout == "deny":
                ids.update({dispatch.timeout_rule(check), f"{check.name}.error"})
    for table in SHELL_TABLES:
        ids.update(table)
    return ids


def test_every_emitted_rule_id_has_a_sentence():
    ids = _emitted_ids()
    assert "dispatch.task_store_absent" in ids and "env_guard.timeout" in ids  # the grep sees
    assert "env.read.operand" in ids and "mg.merge.explicit" in ids            # the tables
    assert ids - set(RULES) == set()


def test_the_shell_tables_and_the_registry_carry_the_same_sentence():
    """One registry: a shell table may hold its ids, but the sentence is the registry's."""
    for table in SHELL_TABLES:
        for rule_id, sentence in table.items():
            assert RULES[rule_id].explain == sentence, rule_id


def test_every_rule_has_one_sentence_and_a_doc_anchor():
    for rule_id, rule in RULES.items():
        assert rule.explain.endswith(".") and "\n" not in rule.explain, rule_id
        assert re.fullmatch(r"docs/[A-Za-z0-9_/.-]+\.md#[a-z0-9-]+", rule.doc), rule_id


# --- repact inspect --refusals over a written journal -------------------------------------------------------

def _journal(tmp_path, rows):
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "session-journal.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return session_dir


def _explain(*args):
    return subprocess.run(["bash", str(CLI), "inspect", "--refusals", *args], capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CODE_SESSION_ID": ""}, timeout=30)


def test_repact_explain_prints_the_sentence_for_the_last_refusal(tmp_path):
    session_dir = _journal(tmp_path, [
        {"type": "gate_refusal", "ts": "2026-10-04T10:00:00Z", "hook": "env_guard",
         "verdict": "deny", "rule": "env_guard.timeout", "channel": "exit_2",
         "code": "abc123abc123", "target": "0f0f0f0f0f0f", "frame": "teammate", "event": "PreToolUse"},
        {"type": "check_failure", "ts": "2026-10-04T10:00:01Z", "check": "env_guard",
         "event": "PreToolUse", "exc_class": "CheckTimeout"},
        {"type": "gate_refusal", "ts": "2026-10-04T10:00:02Z", "hook": "legacy",
         "verdict": "deny", "rule": "legacy.exit_2", "channel": "exit_2", "code": "0000deadbeef",
         "target": "", "frame": "lead", "event": "PreToolUse"},
    ])
    done = _explain("last", "--session-dir", str(session_dir))
    assert done.returncode == 0, done.stderr
    assert "legacy  deny  legacy.exit_2" in done.stdout and "no sentence on file" in done.stdout
    done = _explain("abc123", "--session-dir", str(session_dir))
    assert done.returncode == 0
    assert RULES["env_guard.timeout"].explain in done.stdout
    assert "doc docs/troubleshooting.md#env-guard-timeout" in done.stdout
    assert f"instead: {RULES['env_guard.timeout'].instead}" in done.stdout
    done = _explain("--n", "9", "--session-dir", str(session_dir))
    assert done.stdout.count("\n") >= 6 and "failed  CheckTimeout" in done.stdout


def test_repact_explain_exit_codes(tmp_path):
    assert _explain("last", "--session-dir", str(tmp_path / "none")).returncode == 3
    session_dir = _journal(tmp_path, [])
    assert _explain("last", "--session-dir", str(session_dir)).returncode == 1
    assert _explain("zz", "--session-dir", str(session_dir)).returncode == 2
