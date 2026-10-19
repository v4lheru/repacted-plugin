"""
Location: pact-plugin/hooks/shared/ops/dispatch.py
Summary: `repact dispatch`: one call makes the work task the lead
used to build with a TaskCreate pair and five TaskUpdates. In order: the intent row, the
worktree (made or reused), ONE TaskStore create with metadata.dispatch, the brief and its
skeletons, the task-log links, a journal row and workflow state; then it
prints the exact Agent call.
Used by: bin/_repact_ops.py.

No separate teachback task: the teachback lives on the work task. Mode two_phase applies at variety total
>= 10, else auto. A rerun with the same arguments finds the
task by metadata.dispatch.op_id and creates nothing. The mode is the session's (shared/
execution.py): a teams session with no team file is downgraded here, and says so once.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from shared import acceptance as AC, execution, ref_dispositions as RD
from shared.content_tree import content_tree
from shared.evidence_check import KINDS
from shared import plugin_paths
from shared.variety_scorer import MAX_DIMENSION, MIN_DIMENSION

from . import OpError, Result, brief, frame, records, sides

TWO_PHASE_MIN = 10
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:.-]{0,79}")
FEATURE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
SUBJECT_MAX = 200
MISSION_MAX = 64 * 1024
GIT_TIMEOUT = 60


def _git(cwd: str, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _worktrees(repo: str) -> list:
    """The repo's worktrees, main first (git worktree list order), as real paths."""
    listing = _git(repo, "worktree", "list", "--porcelain") or ""
    return [os.path.realpath(line[len("worktree "):]) for line in listing.splitlines()
            if line.startswith("worktree ")]


def _place(repo: str, worktree: Optional[str], branch: Optional[str], name: str) -> tuple:
    """(worktree path, branch) after making or checking it."""
    trees = _worktrees(repo)
    if not trees:
        raise OpError("ops.invalid_input", [f"{repo} is not inside a git repository"])
    main = trees[0]
    if worktree:
        path = os.path.realpath(os.path.expanduser(worktree))
        if path not in trees[1:]:
            raise OpError("ops.invalid_input", [f"--worktree {worktree} is not a linked worktree of {main}"])
    else:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,127}", branch or "") or ".." in branch:
            raise OpError("ops.invalid_input", [f"--branch {branch!r} is not a plain branch name"])
        path = os.path.realpath(os.path.join(main, ".worktrees", name))
        if path not in trees:
            exists = _git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None
            args = ["worktree", "add", path, branch] if exists else ["worktree", "add", "-b", branch, path]
            if _git(main, *args) is None:
                raise OpError("ops.write_failed", [f"git worktree add for {path} failed"], 1)
    current = _git(path, "symbolic-ref", "--short", "-q", "HEAD") or ""
    return path, current or (branch or "")


def _variety(values) -> dict:
    names = ("novelty", "scope", "uncertainty", "risk")
    if (not isinstance(values, (list, tuple)) or len(values) != 4
            or not all(type(v) is int and MIN_DIMENSION <= v <= MAX_DIMENSION for v in values)):
        raise OpError("ops.invalid_input", [f"--variety takes four whole numbers "
                                            f"{MIN_DIMENSION}-{MAX_DIMENSION} (N,S,U,R)"])
    variety = dict(zip(names, values))
    variety["total"] = sum(values)
    return variety


def _check(name, agent, subject, mission, kind, feature, refs, worktree, branch) -> list:
    problems = []
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        problems.append("--name must be 1-64 letters, digits, - or _")
    if not isinstance(agent, str) or not AGENT_RE.fullmatch(agent):
        problems.append("--agent must be an agent type such as pact-backend-coder")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > SUBJECT_MAX or "\n" in subject:
        problems.append(f"--subject must be one line of 1-{SUBJECT_MAX} characters")
    if not isinstance(mission, str) or not mission.strip() or len(mission) > MISSION_MAX:
        problems.append(f"the brief file must hold a mission of 1-{MISSION_MAX} characters")
    if kind not in KINDS:
        problems.append(f"--kind must be one of {', '.join(KINDS)}")
    if feature is not None and not FEATURE_RE.fullmatch(feature):
        problems.append("--feature must be a plain name")
    problems += [f"--ref {ref!r} is not a project task id (T-<8 hex>)" for ref in refs if not RD.is_ref(ref)]
    if bool(worktree) == bool(branch):
        problems.append("give exactly one of --worktree and --branch")
    return problems


def _pin(acceptance: Optional[str], worktree: str) -> dict:
    if not acceptance:
        return {}
    path = AC.resolve_file(acceptance, worktree)
    items = AC.read_criteria(path) if path else None
    if not items:
        raise OpError("ops.invalid_input", [f"--acceptance {acceptance}: no AC-<n> lines could be read"])
    return AC.make_pin(items)


def _existing(ctx: records.Context, op_id: str) -> Optional[str]:
    for task in ctx.store.iter_tasks(ctx.team):
        if (records.metadata(task).get("dispatch") or {}).get("op_id") == op_id:
            return str(task.get("id"))
    return None


def run(ctx: records.Context, *, name: str, agent: str, subject: str, mission: str, variety,
        kind: str, repo: str, worktree: Optional[str] = None, branch: Optional[str] = None,
        feature: Optional[str] = None, acceptance: Optional[str] = None, refs=()) -> Result:
    frame.check("dispatch", ctx.caller)
    refs = list(dict.fromkeys(refs or ()))
    problems = _check(name, agent, subject, mission, kind, feature, refs, worktree, branch)
    if problems:
        raise OpError("ops.invalid_input", problems)
    scores = _variety(variety)
    args = {"name": name, "agent": agent, "subject": subject, "mission": mission, "variety": scores,
            "kind": kind, "worktree": worktree, "branch": branch, "feature": feature,
            "acceptance": acceptance, "refs": refs}
    op_id = records.begin(ctx, "dispatch", "", records.digest(args))
    path, branch_name = _place(repo, worktree, branch, name)
    task_id = _existing(ctx, op_id)
    mode, downgraded = execution.settle_for_team(ctx.session_dir, ctx.team, cwd=repo)
    if task_id is None:
        meta = {"op_id": op_id, "name": name, "agent_type": agent, "kind": kind, "variety": scores,
                "teachback": "two_phase" if scores["total"] >= TWO_PHASE_MIN else "auto",
                "worktree": path, "branch": branch_name, "base_sha": _git(path, "rev-parse", "HEAD"),
                "base_tree": content_tree(path), "feature": feature, "acceptance_file": acceptance,
                "acceptance_pin": _pin(acceptance, path), "refs": refs, "mode": mode["mode"],
                "dispatched_at": records.now()}
        task_id = ctx.store.create_next(ctx.team, lambda new_id: _task(new_id, subject, name, meta, op_id))
        if task_id is None:
            raise OpError("ops.write_failed", [f"could not create a task file in team {ctx.team}"], 1)
    task = ctx.task(task_id)
    dispatched = records.dispatched(task)
    brief_path = brief.write(task_id, dispatched, mission, ctx.team, ctx.session_dir,
                             sides.main_checkout(path) or path)
    records.finish(ctx, op_id, [
        ("task_log", lambda: sides.task_log(ctx, task_id, task, {"refs": refs}, ["link"], path)
         if refs else None),
        ("journal", lambda: sides.journal(ctx, "agent_dispatch", agent=name, task_id=task_id, phase="CODE",
                                          agent_type=agent, variety=scores, op_id=op_id)),
        ("workflow", lambda: sides.workflow(ctx, task_id, path)),
    ])
    team_part = f", team_name=\"{ctx.team}\"" if dispatched.get("mode") == "teams" else ""
    return Result(task_id=task_id, lines=([downgraded] if downgraded else []) + [
        f"Dispatched #{task_id} to {name} ({agent}); teachback {dispatched['teachback']}; worktree {path}.",
        f"Brief: {brief_path}",
        f"Next: Agent(name=\"{name}\", subagent_type=\"{agent_type(agent)}\", run_in_background=true{team_part}, "
        f"prompt=\"Read {brief_path} and do what it says.\")",
    ])


def agent_type(agent: str) -> str:
    """The Agent tool's subagent_type for `agent`: a shipped agent (agents/<agent>.md in this
    plugin) carries the plugin prefix, the one name that exists under a plugin install with no
    user-layer copy; an already prefixed name or a project's own agent is kept."""
    if ":" in agent or not (plugin_paths.plugin_root() / "agents" / f"{agent}.md").is_file():
        return agent
    return f"{plugin_paths.plugin_name()}:{agent}"


def _task(task_id: str, subject: str, name: str, meta: dict, op_id: str) -> dict:
    return {"id": task_id, "subject": subject, "description": "Dispatched by repact dispatch; the "
            "brief is in the session dir (briefs/).", "activeForm": subject, "status": "in_progress",
            "owner": name, "blocks": [], "blockedBy": [], "metadata": {"dispatch": meta, "last_op": op_id}}
