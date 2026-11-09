"""CC-17: the claude.ai sign-in expiry warning given when an unattended run starts.

Location: pact-plugin/tests/test_auth_expiry.py. Covers bin/_auth_expiry.py (the parse and
the fail-soft read) and its three callers: `repact-unattended on`, the launcher's preflight
with REPACT_UNATTENDED on, and `repact work --unattended`.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
BIN = PLUGIN / "bin"
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expiry_module():
    return _load("_auth_expiry_cc17", BIN / "_auth_expiry.py")


def fake_run(stdout="", code=0, raises=None):
    def run(argv, **kwargs):
        assert argv[1:] == ["auth", "status", "--json"] and kwargs["timeout"] == 5
        if raises:
            raise raises
        return SimpleNamespace(returncode=code, stdout=stdout)
    return run


def warn(stdout="", code=0, raises=None, which=lambda name, path=None: "/bin/claude"):
    return _expiry_module().warning(now=NOW, run=fake_run(stdout, code, raises), which=which)


def status(value) -> str:
    return json.dumps({"loggedIn": True, "loginExpiresAt": value})


def test_a_sign_in_ending_within_48_hours_warns_and_one_further_off_does_not():
    soon = warn(status("2026-11-10T00:24:00.294Z"))
    assert soon.startswith("warning: the claude.ai sign-in ends at 2026-11-10 00:24 UTC (in about 12 h)")
    assert "claude auth login" in soon
    assert warn(status("2026-11-11T12:00:01Z")) is None                 # 48 h and a second
    assert warn(status("2026-11-11T13:00:00+02:00")) is not None        # 47 h, an offset
    assert "ended" in warn(status("2026-11-01T00:00:00Z"))              # already past


@pytest.mark.parametrize("stdout, code, raises, which", [
    (json.dumps({"loggedIn": True}), 0, None, None),                     # field absent
    (status(None), 0, None, None),                                       # null (an API key)
    (status(""), 0, None, None),                                         # empty
    (status("tomorrow"), 0, None, None),                                 # malformed
    (status("2026-11-10"), 0, None, None),                               # a date, no time
    (status("2026-11-10T00:00:00"), 0, None, None),                      # no zone: not guessed
    (status("2026-02-30T00:00:00Z"), 0, None, None),                     # no such day
    (status("２０２６-11-10T00:00:00Z"), 0, None, None),                  # full-width digits
    (status("2026-11-10T00:00:00Z" + " " * 40), 0, None, None),          # oversized
    (status(1762732800), 0, None, None),                                 # a number
    (status(True), 0, None, None),                                       # a bool
    (status(["2026-11-10T00:00:00Z"]), 0, None, None),                   # a list
    ("Logged in as someone", 0, None, None),                             # not JSON (--text)
    ('["loginExpiresAt"]', 0, None, None),                               # JSON, not an object
    ("[" * 5000 + "]" * 5000, 0, None, None),                            # deep JSON
    (status("2026-11-10T00:00:00Z"), 1, None, None),                     # non-zero exit
    ("", 0, subprocess.TimeoutExpired("claude", 5), None),               # slow
    ("", 0, FileNotFoundError("claude"), None),                          # vanished
    (status("2026-11-10T00:00:00Z"), 0, None, lambda name, path=None: None),  # not on PATH
], ids=["absent", "null", "empty", "malformed", "date-only", "naive", "no-such-day",
        "full-width", "oversized", "number", "bool", "list", "text", "json-list", "deep",
        "exit-1", "timeout", "oserror", "no-binary"])
def test_every_failure_is_silent(stdout, code, raises, which):
    assert warn(stdout, code, raises, which or (lambda name, path=None: "/bin/claude")) is None


def _stub_claude(tmp_path: Path, expires: str) -> Path:
    stub = tmp_path / "stubbin"
    stub.mkdir()
    script = stub / "claude"
    script.write_text(f"#!/bin/sh\necho '{status(expires)}'\n")
    script.chmod(0o755)
    return stub


def test_unattended_on_warns_on_stderr_and_still_turns_the_mode_on(tmp_path):
    soon = (datetime.datetime.now(UTC) + datetime.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stub = _stub_claude(tmp_path, soon)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "REPACT_UNATTENDED" and not k.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               PATH=f"{stub}{os.pathsep}/usr/bin{os.pathsep}/bin")
    on = subprocess.run([sys.executable, str(BIN / "repact-unattended"), "on", "--session-dir",
                         str(session_dir)], text=True, capture_output=True, env=env, timeout=60)
    assert on.returncode == 0 and on.stdout.startswith("unattended: ON (flag)")
    assert "repact-unattended: warning: the claude.ai sign-in ends at" in on.stderr


def test_the_launcher_warns_only_on_an_unattended_launch(tmp_path):
    launch = _load("_repact_launch_cc17", BIN / "_repact_launch.py")
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"enabledPlugins": {"repacted@repacted-local": True}}))
    calls = []

    def expiry(env):
        calls.append(env)
        return "warning: the claude.ai sign-in ends soon"
    env = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(config)}
    code, out, err = launch.preflight(PLUGIN, env=env, expiry=expiry)
    assert (code, out, err, calls) == (0, ["installed"], [], [])
    code, out, err = launch.preflight(PLUGIN, env={**env, "REPACT_UNATTENDED": "1"}, expiry=expiry)
    assert (code, out) == (0, ["installed"]) and err == ["repact: warning: the claude.ai sign-in ends soon"]


def test_repact_work_unattended_warns_unless_the_launcher_will(monkeypatch, capsys):
    ops = _load("_repact_ops_cc17", BIN / "_repact_ops.py")
    expiry = lambda env: "warning: the claude.ai sign-in ends soon"  # noqa: E731
    ops._expiry_note(env={}, expiry=expiry)
    assert capsys.readouterr().err == "repact work: warning: the claude.ai sign-in ends soon\n"
    ops._expiry_note(env={"REPACT_UNATTENDED": "on"}, expiry=expiry)
    assert capsys.readouterr().err == ""
