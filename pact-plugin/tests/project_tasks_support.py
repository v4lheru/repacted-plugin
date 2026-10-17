"""
Location: pact-plugin/tests/project_tasks_support.py
Summary: Shared helpers for the D10 P1 project task log tests (see
         the design notes). Every module is loaded BY PATH (bin/ and hooks/shared
         are not put on sys.path by the tests), every repo is a temp git repo, and the KB root is
         a temp folder selected through XDG_CONFIG_HOME's repacted profile. Nothing touches the
         real repo, the real vault or ~/.claude.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
READER_PATH = PLUGIN_ROOT / "hooks" / "shared" / "project_tasks.py"
WRITER_PATH = PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "write.py"
EVIDENCE_PATH = PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "evidence.py"
CLI = PLUGIN_ROOT / "bin" / "repact-task"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
INERT_MARKER = "REPACT_TEST_SECRET_" + "ABCDEFGH12"


def load(name: str, path: Path):
    """Import one file by path under `name` (registered in sys.modules for dataclasses)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def reader():
    return load("project_tasks_under_test", READER_PATH)


def writer():
    return load("project_tasks_write_under_test", WRITER_PATH)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check, env={**os.environ, **GIT_ENV})


def new_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    git(path, "commit", "-q", "--allow-empty", "-m", "root")
    return path


def kb_profile(tmp: Path, monkeypatch) -> Path:
    """A temp KB root selected through the repacted profile; returns the KB root."""
    kb = tmp / "vault" / "05-Education" / "Coding-Notes"
    kb.mkdir(parents=True, exist_ok=True)
    (tmp / "vault" / "01-Personal").mkdir(parents=True, exist_ok=True)
    xdg = tmp / "xdg"
    (xdg / "repacted").mkdir(parents=True, exist_ok=True)
    (xdg / "repacted" / "profile.json").write_text(json.dumps({"paths": {"vault": str(kb)}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return kb


def run_cli(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`init` registers the checkout in ${CLAUDE_PLUGIN_DATA}/projects.json (D10 §15.3), so every
    run gets a plugin data dir beside the temp repo, never the real one; no session id, so `by`
    never looks up the caller's own session (a test passes one in `env`)."""
    data = {"CLAUDE_PLUGIN_DATA": str(Path(cwd).parent / ".plugin-data"), "CLAUDE_CODE_SESSION_ID": ""}
    return subprocess.run([sys.executable, str(CLI), *args], cwd=str(cwd), capture_output=True,
                          text=True, env={**os.environ, **GIT_ENV, **data, **(env or {})})
