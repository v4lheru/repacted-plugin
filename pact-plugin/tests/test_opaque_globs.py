"""
Location: pact-plugin/tests/test_opaque_globs.py
Summary: Glob and defaulted program words, through hooks/dispatch.py the way the host sends
         them, in a scratch HOME, config dir and git repo. A glob, or a defaulted expansion,
         as the program must not hide a gated git act, a dotenv read or a lead op. Everyday
         forms (a defaulted program, tool setup output given to eval) stay allowed. Fake
         values only.
Used by: pytest. Marked real_approval_record, so conftest's stand-in for the answer is off.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_approval_record

PLUG = str(Path(__file__).resolve().parent.parent)
DISPATCH = PLUG + "/hooks/dispatch.py"
CODER = {"agent_id": "a1234567890abcdef", "agent_type": "repacted:pact-backend-coder"}


@pytest.fixture
def s(tmp_path):
    home = tmp_path / "home"
    config = home / ".claude"
    config.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "PACT_", "REPACT_", "GH_", "GL_", "GITLAB_"))}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(config), GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"],
                 ["remote", "add", "origin", "https://github.com/example/fake.git"],
                 ["update-ref", "refs/remotes/origin/main", "HEAD"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    digest = hashlib.sha256(b"fake-digest").digest() * 2
    fake = "gh" + "p_" + "".join(alpha[b % 62] for b in digest[:36])
    (repo / "cfg.py").write_text(f'TOKEN = "{fake}"\n')
    (repo / ("." + "env")).write_text("EXAMPLE=1\n")
    proj = config / "projects" / "-scratch-repo"
    proj.mkdir(parents=True)
    transcript = proj / "s1.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    return {"env": env, "repo": repo, "T": transcript}


def _judge(s, command, frame=None):
    payload = {"hook_event_name": "PreToolUse", "session_id": "", "cwd": str(s["repo"]),
               "transcript_path": str(s["T"]), "tool_name": "Bash",
               "tool_input": {"command": command}, **(frame or {})}
    return subprocess.run([sys.executable, DISPATCH, "PreToolUse", "Bash"],
                          input=json.dumps(payload), capture_output=True, text=True,
                          cwd=str(s["repo"]), env=s["env"], timeout=60).returncode


@pytest.mark.parametrize("command", [
    "/usr/bin/g[i]t push --force origin main",])
def test_glob_program_cannot_run_a_gated_git_act(s, command):
    assert _judge(s, command) == 2


def test_glob_program_cannot_commit_a_staged_credential(s):
    subprocess.run(["git", "-C", str(s["repo"]), "add", "cfg.py"], check=True, env=s["env"],
                   capture_output=True)
    assert _judge(s, "/usr/bin/g[i]t commit -m x") == 2


