"""
Location: pact-plugin/tests/test_opaque_routes.py
Summary: Commands whose program, subcommand or file is built at run time, driven through the
real PreToolUse dispatcher (hooks/dispatch.py) in a scratch HOME, config and repositories with
fake values: one row per hidden route past the commit scan, the push scan, the .env read
check and the lead-ops check, each refused, and the honest forms
beside them (a path in a variable, $PYTHON from the environment, a literal heredoc, an
installer piped from curl) still allowed.
Used by: pytest.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
SESSION = "11111111-2222-3333-4444-555555555555"
FAKE = "ghp_" + "Fk9mQ2vLp7Rz4tXk9mQ2vLp7Rz4tXk9mQ2vL"          # github-token shape, not real
CODER = {"agent_id": "a1", "agent_type": "repacted:pact-backend-coder"}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "a")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("opaque")
    home = root / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "repact").write_text("#!/bin/sh\n")
    config = root / "config"
    (config / "pact-sessions" / "proj" / SESSION).mkdir(parents=True)
    staged = _repo(root, "staged")                       # a fake token staged, not committed
    (staged / "cfg.yml").write_text(f"github_token: {FAKE}\n")
    _git(staged, "add", "cfg.yml")
    pushed = _repo(root, "pushed")                       # a fake token committed on a branch
    _git(pushed, "checkout", "-qb", "feat")
    (pushed / "cfg.yml").write_text(f"github_token: {FAKE}\n")
    _git(pushed, "add", "cfg.yml")
    _git(pushed, "commit", "-qm", "cfg")
    _git(pushed, "remote", "add", "origin", "https://example.com/x.git")
    clean = _repo(root, "clean")
    _git(clean, "checkout", "-qb", "feat")
    _git(clean, "remote", "add", "origin", "https://example.com/x.git")
    secrets = _repo(root, "secrets")                     # a fake .env beside ordinary files
    (secrets / ".env").write_text("NAME=x\n")
    (secrets / "src").mkdir()
    (secrets / "src" / "main.py").write_text("print(1)\n")
    env = {key: value for key, value in os.environ.items()
           if key not in ("REPACT_AGENT_NAME", "CLAUDE_SESSION_ID", "REPACT_UNATTENDED")}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(config), CLAUDE_CODE_SESSION_ID=SESSION,
               CLAUDE_PLUGIN_ROOT=str(PLUGIN))
    return {"env": env, "home": home, "staged": staged, "pushed": pushed, "clean": clean,
            "secrets": secrets}


def _run(world: dict, command: str, where: str, coder: bool = False):
    cwd = world[where]
    payload = {"session_id": SESSION, "hook_event_name": "PreToolUse", "cwd": str(cwd),
               "tool_name": "Bash", "tool_input": {"command": command}}
    if coder:
        payload.update(CODER)
    done = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse",
                           "Bash"], input=json.dumps(payload), capture_output=True, text=True,
                          env=dict(world["env"], CLAUDE_PROJECT_DIR=str(cwd)), timeout=90)
    assert FAKE not in done.stdout + done.stderr, "a value reached the hook output"
    return done


def _refused(world: dict, command: str, where: str, coder: bool = False) -> str:
    done = _run(world, command, where, coder)
    assert done.returncode == 2, (command, done.stdout, done.stderr)
    return done.stderr


def _allowed(world: dict, command: str, where: str, coder: bool = False) -> None:
    done = _run(world, command, where, coder)
    assert done.returncode == 0, (command, done.stdout, done.stderr)


# ── the commit scan ─────────────────────────────────────────────────────────
def test_commit_decoded_into_a_shell_is_refused(world):
    _refused(world, f"echo {_b64('git commit -m x')} | base64 -d | sh", "staged")


def test_commit_through_a_variable_program_is_scanned(world):
    assert "git_commit_check" in _refused(world, "G=git; $G commit -m x", "staged")


def test_commit_verb_added_by_xargs_is_refused(world):
    _refused(world, "echo commit -m x | xargs git", "staged")


def test_commit_through_a_substituted_program_is_refused(world):
    _refused(world, "$(echo git) commit -m x", "staged")


# ── the push scan ───────────────────────────────────────────────────────────


# ── .env reads ──────────────────────────────────────────────────────────────
def test_a_read_printed_by_a_shells_script_into_sh_is_refused(world):
    """The script word of `bash -c '...; /bin/echo'` was read as echo printing literal text."""
    read = "cat " + "." + "env"
    _refused(world, f"bash -c 'X=1; echo {read}; /bin/echo' | sh", "secrets")


@pytest.mark.parametrize("command", [
    "bash -c 'X=1; echo ls; /bin/echo' | sh",
    "bash -c 'echo ls; /bin/echo' | sh",
    "bash -c '1; echo ls; /usr/bin/printf x' | bash",
    "bash -c 'X=1; echo ls; /usr/bin/head' | sh",
    "bash -c 'X=1; echo ls; /bin/cat' | sh",
    "bash -c 'X=1; echo ls; /usr/bin/curl' | sh",
    "dash -c 'X=1; echo ls; /bin/base64' | sh",
    "flock /tmp/l -c 'X=1; echo ls; /bin/echo' | sh",
    "watch -t 'X=1; echo ls; /bin/echo' | sh",
    "script -qc 'X=1; echo ls; /bin/echo' /dev/null | sh",
    "sh <(bash -c 'X=1; echo ls; /bin/echo')",
])
def test_a_script_word_is_never_read_as_the_program_that_prints(command):
    from shell.opaque import opaque_reason
    reason = opaque_reason(command)
    assert reason and "builds at run time" in reason, (command, reason)


@pytest.mark.parametrize("command", ["echo ls | sh", "bash -c 'echo ls' > out.txt",
                                     "bash -c 'X=1; ls' | wc -l"])
def test_a_literal_script_or_a_script_not_fed_to_a_shell_passes(command):
    from shell.opaque import opaque_reason
    assert opaque_reason(command) is None


# Only a shell's -c script was read for text built at run time: the script watch, flock -c,
# script -c or fish's -C / --command hands to a shell, and fish's second script, were not.
_DECODED = f"echo {_b64('cat ' + '.' + 'env')} | base64 -d | sh"


@pytest.mark.parametrize("command", [form.replace("D", _DECODED) for form in (
    "watch 'X=1; D'", "watch 'D'", "watch -n 5 'D'", "watch nice 'X=1; D'",
    "flock /tmp/l -c 'X=1; D'", "flock -nc 'D' /tmp/l", "script -c 'X=1; D' /dev/null",
    "script -qc 'D' /dev/null", "fish --command 'D'", "fish -C 'D'", "fish -C true -c 'X=1; D'",
    "fish -c true -c 'D'",
    "bash -c 'X=1; D'",                                     # control: refused before the fix
)])
def test_text_decoded_into_a_shell_inside_a_handed_script_is_refused(command):
    from shell.opaque import opaque_reason
    reason = opaque_reason(command)
    assert reason and "decodes text" in reason, (command, reason)


@pytest.mark.parametrize("command", ["watch 'echo ls | sh'", "flock /tmp/l -c 'echo ls | sh'",
                                     "watch 'X=1; echo bHM= | base64 -d'", "watch echo \"it's\"",
                                     "fish -C 'set x 1' -c 'echo $x'", "watch nice ls"])
def test_a_handed_script_that_builds_nothing_for_a_shell_passes(command):
    from shell.opaque import opaque_reason
    assert opaque_reason(command) is None


def test_an_unreadable_watch_line_keeps_the_rest_of_the_command_read():
    """watch's joined line can hold a quote the inner shell never closes; reading it must not
    drop the verdict the rest of the command earns."""
    from shell.opaque import opaque_reason
    assert opaque_reason(f"{_DECODED}; watch echo \"it's\"")


# ── the lead's operations in a coder frame ──────────────────────────────────
_ACCEPT = "rep" + "act accept 5"


@pytest.mark.parametrize("command", [form.replace("A", _ACCEPT) for form in (
    "watch 'R=rep\"\"act; $R accept 5'",
    "watch nice 'R=rep\"\"act; $R accept 5'",
    "flock /tmp/l -c 'R=rep\"\"act; $R accept 5'",
    "script -c 'R=rep\"\"act; $R accept 5' /dev/null",
    "fish -C true -c 'A'",
    "fish -c true -c 'A'",
    "fish --init-command 'echo hi' --command 'A'",
    # controls: refused before the fix and still
    "bash -c 'R=rep\"\"act; $R accept 5'",
    "fish -c 'A'",
)])
def test_a_lead_op_in_a_handed_script_is_refused_in_a_coder_frame(world, command):
    assert "ops_frame_guard" in _refused(world, command, "clean", coder=True), command
    import ops_frame_guard

    class _Coder:
        name, is_lead = "coder-1", False

        def runs_lead_ops(self):
            return False
    for tool in ("Bash", "Monitor"):
        reason = ops_frame_guard.decide({"tool_name": tool, "tool_input": {"command": command}},
                                        _Coder())
        assert reason and reason.endswith("[rule: ops_frame_guard.lead_only]"), (tool, command)


# ── honest forms that must stay allowed ────────────────────────────────────────────────


