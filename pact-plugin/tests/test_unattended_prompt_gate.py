"""CC-19: a tool call that would wait on a permission prompt is denied while unattended.

Location: pact-plugin/tests/test_unattended_prompt_gate.py. Covers
hooks/unattended_prompt_gate.py through its real entry (hooks/dispatch.py PermissionRequest),
its registration, and the doctor's `prompts:` note (bin/_doctor_host.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import unattended as u

PLUGIN = Path(__file__).resolve().parent.parent
DISPATCH = PLUGIN / "hooks" / "dispatch.py"
SESSION = "sess-prompt-gate"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key != u.ENV_NAME and not key.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PROJECT_DIR=str(project))
    session_dir = tmp_path / "config" / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    return env, session_dir


def ask(env, raw=None, **extra_env):
    payload = raw if raw is not None else json.dumps({
        "hook_event_name": "PermissionRequest", "session_id": SESSION, "tool_name": "Bash",
        "tool_input": {"command": "curl -H 'Authorization: Bearer sk-secret' x"}})
    return subprocess.run([sys.executable, str(DISPATCH), "PermissionRequest"], input=payload,
                          text=True, capture_output=True, env={**env, **extra_env}, timeout=60)


def decision(result):
    out = result.stdout.strip()
    if not out:
        return None
    return json.loads(out).get("hookSpecificOutput", {}).get("decision")


def gate_rows(session_dir: Path) -> list:
    path = session_dir / "session-journal.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if row.get("hook") == "unattended_prompt_gate"]


def test_on_denies_the_prompt_with_a_reason_and_off_lets_it_ask(root):
    env, session_dir = root
    off = ask(env)
    assert off.returncode == 0 and decision(off) is None and gate_rows(session_dir) == []

    u.write_flag(str(session_dir), True, "2026-11-09T10:00:00Z")
    on = ask(env)
    assert on.returncode == 0, on.stderr
    denied = decision(on)
    assert denied["behavior"] == "deny" and denied["interrupt"] is False
    assert "Do not retry the same call" in denied["message"]
    assert "[rule: unattended.prompt_denied]" in denied["message"]
    rows = gate_rows(session_dir)
    assert [(r["tool_name"], r["verdict"], r["rule"]) for r in rows] == [
        ("Bash", "deny", "unattended.prompt_denied")]
    assert "sk-secret" not in (session_dir / "session-journal.jsonl").read_text()

    # the flag wins over the environment both ways: OFF flag, env on, the prompt asks
    u.write_flag(str(session_dir), False, "2026-11-09T11:00:00Z")
    assert decision(ask(env, REPACT_UNATTENDED="1")) is None


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null", '{"tool_name": 7}',
                                 '{"session_id": ["x"], "tool_name": "Bash"}'],
                         ids=["empty", "text", "list", "null", "number-tool", "list-session"])
def test_hostile_stdin_never_raises_and_reads_the_environment(root, raw):
    env, _ = root
    assert decision(ask(env, raw=raw)) is None              # mode OFF: no decision
    on = ask(env, raw=raw, REPACT_UNATTENDED="1")          # no session dir: the env decides
    assert on.returncode == 0
    if raw.strip().startswith("{"):
        assert decision(on)["behavior"] == "deny"


def test_a_tool_name_with_control_characters_is_cleaned_in_the_message(root):
    env, session_dir = root
    u.write_flag(str(session_dir), True, "2026-11-09T10:00:00Z")
    raw = json.dumps({"session_id": SESSION, "tool_name": "mcp__x\x1b[31m\n" + "y" * 500})
    message = decision(ask(env, raw=raw))["message"]
    assert "\x1b" not in message and "\n" not in message and "y" * 81 not in message


def test_it_is_registered_once_on_permission_request_through_the_dispatcher():
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import dispatch_registry
    assert [c.name for c in dispatch_registry.checks_for("PermissionRequest")] == ["unattended_prompt_gate"]
    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    commands = [hook["command"] for entry in config["hooks"]["PermissionRequest"]
                for hook in entry["hooks"]]
    assert commands == ['python3 "${CLAUDE_PLUGIN_ROOT}/hooks/dispatch.py" PermissionRequest']


@pytest.mark.parametrize("settings, expected", [
    ({"permissions": {"denyInsteadOfPrompting": True}}, "prompts: denied instead of waiting"),
    ({"permissions": {"denyInsteadOfPrompting": "true"}}, "prompts: wait for you"),
    ({"permissions": {}}, "prompts: wait for you"),
    (None, "prompts: wait for you"),
], ids=["on", "string-not-bool", "absent", "no-file"])
def test_the_doctor_prompts_note_is_info_only(tmp_path, settings, expected):
    host = _load("_doctor_host_cc19", PLUGIN / "bin" / "_doctor_host.py")
    if settings is not None:
        (tmp_path / "settings.json").write_text(json.dumps(settings))
    line = host.prompts_line(SimpleNamespace(config_root=tmp_path))
    assert line.startswith(expected)
    if expected.startswith("prompts: wait"):
        assert "AskUserQuestion" in line and "info:" in line


def test_the_doctor_prompts_note_survives_settings_nested_too_deep_to_parse(tmp_path):
    host = _load("_doctor_host_cc19_deep", PLUGIN / "bin" / "_doctor_host.py")
    (tmp_path / "settings.json").write_text('{"a":' * 100_000 + "1" + "}" * 100_000)
    assert host.prompts_line(SimpleNamespace(config_root=tmp_path)).startswith("prompts: wait for you")
