"""
Location: pact-plugin/tests/fixtures/ops_env.py
Summary: A throwaway world for the workflow operations (hooks/shared/ops/): a config root with
its own task store, a git repo with one commit, a session dir, and helpers that dispatch a task
and fill in a valid teachback and HANDOFF.
Used by: tests/test_ops_flow.py, test_ops_crash.py, test_ops_submit_inputs.py, test_ops_cli.py
(through the `ops_env` fixture, imported into each test module).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from shared.ops import frame, records
from shared.ops import dispatch as dispatch_op
from shared.ops import evidence as EV
from shared.task_store import TaskRef, TaskStore

TEAM = "team-ops"
SESSION_ID = "sess-ops-1"
CODER = "coder-1"
REAL_VERDICT = EV.verdict   # the fixture stubs it; a test that wants today's check restores this
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env={**_base_env(), **GIT_ENV})
    return done.stdout.strip()


def _base_env() -> dict:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def teachback(answer: str = "yes") -> dict:
    return {"understanding": "Build the parser fix in the worktree, one test, no new framework.",
            "most_likely_wrong": "The empty-input branch may already be covered elsewhere.",
            "least_confident_item": "That the old flag stays as an alias.",
            "first_action": "Write the failing test for the empty input.",
            "variety_acknowledgment": {"rationale_articulates_this_dispatch": answer,
                                       "concern": "" if answer == "yes" else "the risk is understated"}}


def handoff(worktree: str, **extra) -> dict:
    body = {"produced": [f"{worktree}/parser.py: handle the empty input"],
            "decisions": ["kept the old flag as an alias"], "reasoning_chain": "small fix",
            "uncertainty": ["not-applicable: nothing open"],
            "integration": "callers keep calling parse(text)", "open_questions": [], "amendments": {}}
    body.update(extra)
    return body


@dataclass
class World:
    root: Path
    repo: Path
    session_dir: Path
    store: TaskStore

    def ctx(self, caller: frame.Caller = frame.Caller(frame.LEAD)) -> records.Context:
        return records.resolve(TEAM, str(self.session_dir), SESSION_ID, caller, store=self.store)

    def coder(self) -> records.Context:
        return self.ctx(frame.Caller(frame.CODER, CODER))

    def task(self, task_id: str) -> dict:
        return self.store.get(TaskRef(TEAM, task_id))

    def ops_rows(self) -> list:
        path = self.session_dir / records.OPS_LOG
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def side_rows(self, op_id: str, side: str) -> int:
        return sum(1 for row in self.ops_rows() if row.get("op_id") == op_id and row.get("side") == side)

    def dispatch(self, variety=(1, 1, 1, 1), **overrides) -> str:
        args = dict(name=CODER, agent="pact-backend-coder", subject="Fix the parser",
                    mission="Fix the empty-input case in parser.py.", variety=list(variety),
                    kind="fix", repo=str(self.repo), branch="fix/parser")
        args.update(overrides)
        return dispatch_op.run(self.ctx(), **args).task_id

    def worktree(self, task_id: str) -> str:
        return self.task(task_id)["metadata"]["dispatch"]["worktree"]


@pytest.fixture
def ops_env(tmp_path, monkeypatch) -> World:
    """A dispatched-ready world. The evidence check is stubbed to pass (shared/evidence_check,
    tested on its own in tests/test_evidence_check.py); a test that wants a failing verdict patches it again."""
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("REPACT_AGENT_NAME", raising=False)
    monkeypatch.delenv("REPACT_EXECUTION", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "parser.py").write_text("def parse(text):\n    return text\n")
    git(repo, "add", "parser.py")
    git(repo, "commit", "-q", "-m", "init")
    # A real session dir of the team, found from the session id the way the CLI finds the
    # caller's own session (records._bind): <config>/pact-sessions/<project>/<id>.
    session_dir = config / "pact-sessions" / "proj" / SESSION_ID
    session_dir.mkdir(parents=True)
    (session_dir / records.CONTEXT_FILE).write_text(
        json.dumps({"team_name": TEAM, "session_id": SESSION_ID}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION_ID)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("REPACT_UNATTENDED", raising=False)
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": [], "notes": []})
    return World(tmp_path, repo, session_dir, TaskStore(config / "tasks"))
