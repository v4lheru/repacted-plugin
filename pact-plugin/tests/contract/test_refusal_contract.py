"""Contract: every refusal states the rule and the way through.

Every rule id in hooks/shared/rules.py carries a non-empty `instead` (one plain sentence: what
to do instead) and an anchor on docs/troubleshooting.md. The refusal line the host shows is
`<check>: <reason> → <instead> [rule: <id>]` for a registered id, the tail last and unchanged;
a fallback id (`<check>.<channel>`) and a registry that cannot load add no segment, and the
refusal stands either way. `repact inspect --refusals last` prints the way through and the anchor.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import dispatch
import dispatch_registry as registry
import dispatch_rules
from shared.rules import REFUSALS_DOC, RULES, anchor

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def test_every_rule_id_has_a_way_through_and_a_troubleshooting_anchor():
    assert REFUSALS_DOC == "docs/troubleshooting.md"
    for rule_id, rule in RULES.items():
        assert rule.instead.strip() and rule.instead.endswith("."), rule_id
        assert "\n" not in rule.instead and "→" not in rule.instead, rule_id
        assert rule.doc.startswith(f"{REFUSALS_DOC}#"), rule_id
    anchors = [rule.doc.split("#", 1)[1] for rule in RULES.values()]
    assert len(set(anchors)) == len(anchors)
    assert RULES["env.read.operand"].doc.endswith("#" + anchor("env.read.operand")) \
        and anchor("env_guard.timeout") == "env-guard-timeout"


def _deny(reason: str) -> bytes:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}).encode()


def _line(name: str, reason: str) -> tuple[int, str]:
    outcome = dispatch.Outcome(check=registry.Check(name=name, script=f"hooks/{name}.py"),
                               exit_code=0, stdout=_deny(reason))
    code, out, _err, _parsed = dispatch.combine("PreToolUse", [outcome])
    return code, json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_refusal_line_names_the_way_through_before_the_tail():
    instead = RULES["env.read.operand"].instead
    _code, line = _line("env_guard", "Blocked: cat .env [rule: env.read.operand]")
    assert line == f"env_guard: Blocked: cat .env → {instead} [rule: env.read.operand]"
    assert dispatch_rules.RULE_TAIL.search(line).group(1) == "env.read.operand"
    # Decorating the decorated line again adds nothing (no doubled segment).
    _code, again = _line("env_guard", line)
    assert again == line
    # A fallback id and a hostile tail get no segment; the refusal is unchanged.
    assert _line("bare", "plain no")[1] == "bare: plain no [rule: bare.deny]"
    assert _line("odd", "x → y [rule: ../etc]")[1] == "odd: x → y [rule: ../etc] [rule: odd.deny]"


def test_a_registry_that_cannot_load_costs_the_segment_never_the_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch_rules, "_RULES_FILE", tmp_path / "missing.py")
    monkeypatch.setattr(dispatch_rules, "_registry", [])
    _code, line = _line("env_guard", "Blocked: cat .env [rule: env.read.operand]")
    assert line == "env_guard: Blocked: cat .env [rule: env.read.operand]"


def test_repact_explain_last_prints_the_way_through_and_the_anchor(tmp_path):
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    rows = [{"type": "gate_refusal", "ts": f"2026-10-16T10:00:0{i}Z", "hook": "env_guard",
             "verdict": "deny", "rule": rule, "channel": "deny", "code": "abc123abc123"}
            for i, rule in enumerate(("mg.token.mismatch", "env.search.unknown_root"))]
    (session_dir / "session-journal.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    done = subprocess.run(["bash", str(PLUGIN_ROOT / "bin" / "repact"), "inspect", "--refusals", "last",
                           "--session-dir", str(session_dir)], capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CODE_SESSION_ID": ""}, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "mg.token.mismatch" not in done.stdout
    assert f"instead: {RULES['env.search.unknown_root'].instead}" in done.stdout
    assert re.search(r"doc docs/troubleshooting\.md#env-search-unknown-root$", done.stdout, re.M)
