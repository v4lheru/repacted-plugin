"""
Location: pact-plugin/hooks/shared/ops/integrate.py
Summary: Integration acceptance: `repact accept --feature <name> [--tree
<path>]`. A feature is accepted on the combined result, not on each part passing alone. Every task
dispatched with `--feature <name>` must be accepted; the lead merges their branches into the
feature tree (default `<main checkout>/.worktrees/<name>`), runs the checks there with tcount, then
runs this. It checks:
  1. each task's head commit is an ancestor of the feature tree's HEAD;
  2. the feature tree's evidence, by the same rules as a task (shared/evidence_check.check on the
     tree): a passing run on the tree as it is now, every input file committed, and each
     acceptance criterion pinned on any of the tasks (their union) met by a run on that tree with
     the same test selection;
  3. a suite command, when the repo declares one (none does yet: reported, never a block);
  4. each contract test the feature's architecture doc declares (`Contract: <name> | coders: a, b
     | test: <path>` in docs/architecture/<name>.md of the tree) was run, and passed, on that tree.
     None declared is reported, never a block.
Pass: one record {feature, tree, path, head, tasks, statuses, runs, at} in
<main>/.repacted/features.jsonl (shared/task_log/features.py; a rerun on the same tree adds none),
then each task's bound task-log rows close with the merged head commit as evidence
(task_log/bridge.integrate). Fail: the failing items are printed and nothing is written; with
unattended mode on the feature's open rows are parked for a human instead (exit 0).
`task_status` is what `repact accept` records for one task of a feature: "pass" when it is the
feature's only task (its worktree is the integration tree), else "waiting".
Used by: bin/_repact_ops.py (`accept --feature`); shared/ops/accept.py (task_status);
shared/ops/receipt.py (feature_tasks).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from . import OpError, Result, frame, records

FEATURE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
DOC_MAX_BYTES = 1_000_000
CONTRACTS_MAX = 50
CONTRACT_LINE = re.compile(
    r"^\s*(?:[-*]\s+)?Contract:\s*(?P<name>[^|\n]+?)\s*\|\s*coders:\s*(?P<coders>[^|\n]+?)\s*"
    r"(?:\|\s*test:\s*(?P<test>[^|\n]*?)\s*)?$",
    re.IGNORECASE | re.MULTILINE,
)
CODER_SPLIT = re.compile(r"\s*(?:,|\+|&|/|\band\b)\s*", re.IGNORECASE)
TREE_STATUSES = ("run_recorded", "inputs_unchanged", "inputs_committed", "checks_passed")
SHOWN = 4
GIT_TIMEOUT = 30


def _git(cwd: str, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def feature_tasks(ctx: records.Context, feature: str) -> List[dict]:
    """The team's tasks dispatched with `--feature <feature>`, by task id."""
    found = [task for task in ctx.store.iter_tasks(ctx.team)
             if (records.metadata(task).get("dispatch") or {}).get("feature") == feature]
    return sorted(found, key=lambda task: (len(str(task.get("id"))), str(task.get("id"))))


def task_status(ctx: records.Context, dispatched: dict) -> dict:
    """{"integrated": status} for a task of a feature, {} for a task with none."""
    feature = dispatched.get("feature")
    if not isinstance(feature, str) or not feature:
        return {}
    count = len(feature_tasks(ctx, feature))
    if count <= 1:
        return {"integrated": {"state": "pass", "line": f"the feature {feature} has this one task, so its "
                               "worktree is the integration tree and its checks passed there"}}
    return {"integrated": {"state": "waiting", "line": f"the feature {feature} has {count} tasks: their "
                           f"task-log rows close when `repact accept --feature {feature}` passes on the "
                           "merged tree"}}


def parse_contracts(text: str) -> List[dict]:
    """[{"name", "coders": [..], "test"}] for each Contract line of an architecture doc."""
    found = []
    for match in CONTRACT_LINE.finditer(text or ""):
        coders = list(dict.fromkeys(part.strip() for part in CODER_SPLIT.split(match.group("coders"))
                                    if part.strip()))
        found.append({"name": match.group("name").strip(), "coders": coders,
                      "test": (match.group("test") or "").strip()})
        if len(found) >= CONTRACTS_MAX:
            break
    return found


def _contracts(tree: str, feature: str) -> List[dict]:
    doc = Path(tree) / "docs" / "architecture" / f"{feature}.md"
    try:
        if doc.stat().st_size > DOC_MAX_BYTES:
            return []
        return [item for item in parse_contracts(doc.read_text(encoding="utf-8", errors="replace"))
                if len(item["coders"]) >= 2]
    except OSError:
        return []


def _union_pin(tasks: List[dict]) -> dict:
    """Every task's pinned acceptance commands; an AC id two tasks pin differently is kept once per
    task (`AC-1 (#12)`), so neither is lost."""
    union: dict = {}
    for task in tasks:
        pin = records.metadata(task).get("dispatch", {}).get("acceptance_pin") or {}
        for ident, command in (pin.items() if isinstance(pin, dict) else []):
            if ident in union and union[ident] != command:
                union[f"{ident} (#{task.get('id')})"] = command
            else:
                union.setdefault(ident, command)
    return union


def _not_accepted(tasks: List[dict]) -> List[str]:
    return [f"#{task.get('id')} is {task.get('status')}, not accepted: `repact accept {task.get('id')}` first"
            for task in tasks if task.get("status") != "completed"
            or not isinstance(records.metadata(task).get("acceptance"), dict)]


def _ancestry(tree: str, tasks: List[dict]) -> List[str]:
    """A line for each task whose head commit the feature tree's HEAD does not contain."""
    problems = []
    for task in tasks:
        dispatched = records.metadata(task).get("dispatch") or {}
        worktree, branch = dispatched.get("worktree"), dispatched.get("branch")
        head = (_git(worktree, "rev-parse", "HEAD") if isinstance(worktree, str) and os.path.isdir(worktree)
                else None) or (_git(tree, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
                               if isinstance(branch, str) and branch else None)
        if not head:
            problems.append(f"#{task.get('id')}: its head commit cannot be found (worktree and branch gone)")
        elif _git(tree, "merge-base", "--is-ancestor", head, "HEAD") is None:
            problems.append(f"#{task.get('id')}: its branch {branch} ({head[:10]}) is not merged into the "
                            "feature tree: merge it, rerun the checks with tcount, then this")
    return problems


def _tree_check(tree: str, tasks: List[dict], pin: dict, runs) -> dict:
    """The evidence check on the feature tree as if it were one task: the earliest dispatch time,
    the union of the pins, and no red-first rule (that belongs to each task)."""
    from shared import evidence_check as ec
    since = min(str(records.metadata(task)["dispatch"].get("dispatched_at") or "") for task in tasks)
    return ec.check({"metadata": {"dispatch": {"worktree": tree, "kind": "refactor", "dispatched_at": since,
                                               "base_tree": None, "acceptance_pin": pin or None}}}, runs)


def _on_tree(tree: str, verdict: dict, runs, since: str) -> list:
    """The passing runs on the feature tree as it is now (for the contract tests)."""
    from shared import acceptance as AC
    from shared import evidence_check as ec
    from shared.ledger import _record_time, read_runs
    from shared.plugin_paths import ledger_path
    if verdict.get("tree") is None:
        return []
    if runs is None:
        runs = read_runs(ledger_path(create_dir=False))[0]
    dispatch = {"worktree": tree, "base_tree": None}
    picked = ec.task_runs(runs, dispatch, verdict["tree"], _record_time({"ts": since}) or 0.0)
    return [run for run in picked if AC.run_passed(run) and ec.run_tree(run) == verdict["tree"]]


def _contract_status(tree: str, contracts: List[dict], greens: list) -> tuple:
    """(status, problems) for the declared contract tests."""
    from shared import evidence_check as ec
    if not contracts:
        return {"state": "not_required", "line": "no contract test is declared for this feature"}, []
    root = os.path.realpath(tree)
    problems = []
    for item in contracts:
        test = item["test"]
        rel = os.path.relpath(test, root) if os.path.isabs(test) else test
        if not test:
            problems.append(f"contract {item['name']}: the doc names no test file")
        elif not any(ec.covers(run, rel, root) for run in greens):
            problems.append(f"contract {item['name']}: no passing tcount run of {rel} on the merged tree")
    state = "fail" if problems else "pass"
    return {"state": state, "line": f"{len(contracts) - len(problems)} of {len(contracts)} contract tests "
                                    "passed on the merged tree"}, problems


def _tree(ctx: records.Context, feature: str, tasks: List[dict], given: Optional[str]) -> str:
    from shared.project_tasks import find_main_checkout
    if given:
        path = os.path.realpath(os.path.expanduser(given))
    else:
        worktree = (records.metadata(tasks[0]).get("dispatch") or {}).get("worktree")
        main = find_main_checkout(worktree) if isinstance(worktree, str) else None
        if main is None:
            raise OpError("ops.invalid_input", ["the feature tree cannot be found: pass --tree <path>"])
        path = os.path.realpath(os.path.join(str(main), ".worktrees", feature))
    if not os.path.isdir(path) or _git(path, "rev-parse", "HEAD") is None:
        raise OpError("ops.invalid_input", [f"the feature tree {path} is not a git worktree: merge the "
                                            "tasks' branches into it first, or pass --tree <path>"])
    return path


def judge(ctx: records.Context, feature: str, given_tree: Optional[str] = None, runs=None) -> dict:
    """{tree, path, head, tasks, statuses, problems, runs} for the feature; writes nothing."""
    if not isinstance(feature, str) or not FEATURE_RE.fullmatch(feature):
        raise OpError("ops.invalid_input", ["--feature must be a plain name"])
    tasks = feature_tasks(ctx, feature)
    if not tasks:
        raise OpError("ops.invalid_input", [f"no task of team {ctx.team} was dispatched with --feature {feature}"])
    tree = _tree(ctx, feature, tasks, given_tree)
    problems = _not_accepted(tasks) + _ancestry(tree, tasks)
    pin = _union_pin(tasks)
    verdict = _tree_check(tree, tasks, pin, runs)
    statuses = {name: verdict["statuses"][name] for name in TREE_STATUSES if name in verdict["statuses"]}
    problems += [item["line"] for item in verdict["problems"]]
    statuses["suite"] = {"state": "not_required", "line": "no suite command is declared for this repo"}
    since = min(str(records.metadata(task)["dispatch"].get("dispatched_at") or "") for task in tasks)
    statuses["contracts"], found = _contract_status(tree, _contracts(tree, feature),
                                                    _on_tree(tree, verdict, runs, since))
    problems += found
    statuses["integrated"] = ({"state": "fail", "line": f"{len(problems)} check(s) failed on the merged tree"}
                              if problems else {"state": "pass", "line": f"all {len(tasks)} tasks pass "
                                                "together on the merged tree"})
    return {"feature": feature, "tree": verdict.get("tree"), "path": tree, "head": _git(tree, "rev-parse", "HEAD"),
            "tasks": [str(task.get("id")) for task in tasks], "statuses": statuses, "problems": problems,
            "runs": list(verdict.get("runs") or [])[-20:], "notes": verdict.get("notes") or []}


def _store(path: str, ctx: records.Context):
    from shared.task_log import write as W
    try:
        return W.Store.locate(path, by=W.with_team("bridge", ctx.session_id or None))
    except W.NoLog:
        return None


def _close_rows(ctx: records.Context, judged: dict, runs_seen: list) -> List[str]:
    """Close each task's bound rows with the merged head commit (and the runs on it) as evidence."""
    from shared.task_log import bridge
    store = _store(judged["path"], ctx)
    if store is None:
        return []
    evidence = [f"commit:{judged['head']}"] + [f"run:{name}" for name in runs_seen]
    closed = []
    for task_id in judged["tasks"]:
        report = bridge.integrate(store, ctx.team, task_id, ctx.task(task_id), evidence, ctx.session_id or None)
        closed += report.get("closed") or []
    return closed


def _run_names(judged: dict, runs) -> List[str]:
    from shared import acceptance as AC
    from shared.task_log import write as W
    pool = runs if runs is not None else (AC.ledger_runs() or [])
    names = []
    for stem in judged["runs"]:
        for row in AC.runs_for(str(stem), pool):
            name = Path(row["log_path"]).name if isinstance(row.get("log_path"), str) else ""
            if W.run_name_ok(name) and name not in names:
                names.append(name)
    return names[:3]


def _park(ctx: records.Context, judged: dict) -> Result:
    from shared.task_log import bridge
    store = _store(judged["path"], ctx)
    why = (f"the feature {judged['feature']} failed its integration on the merged tree: "
           + "; ".join(problem[:160] for problem in judged["problems"][:3]))[:600]
    parked = [ref for task_id in judged["tasks"] for ref in
              (bridge.park(store, ctx.team, task_id, why) if store is not None else [])]
    return Result(lines=[f"Feature {judged['feature']} parked for a human: unattended, its rows stay open "
                         f"({len(parked)} parked).", *[f"  {line[:200]}" for line in judged["problems"][:SHOWN]],
                         "Next: dispatch the next task; the owner decides this one from repact decide."])


def run(ctx: records.Context, feature: str, tree: Optional[str] = None, runs=None) -> Result:
    """Judge the feature on its merged tree; on pass record it and close its rows (see module)."""
    frame.check("accept", ctx.caller)
    judged = judge(ctx, feature, tree, runs)
    if judged["problems"]:
        from shared.unattended import controls_on
        if controls_on(str(ctx.session_dir)).on:
            return _park(ctx, judged)
        shown = [f"  {line[:200]}" for line in judged["problems"][:SHOWN]]
        more = [f"  and {len(judged['problems']) - SHOWN} more"] if len(judged["problems"]) > SHOWN else []
        return Result(exit_code=1, lines=[f"Not integrated: feature {feature} fails {len(judged['problems'])} "
                                          f"check(s) on {judged['path']}; nothing written.", *shown, *more,
                                          "Next: fix it in the feature tree, rerun the checks with tcount, then "
                                          f"`repact accept --feature {feature}`."])
    from shared.project_tasks import find_main_checkout
    from shared.task_log import features
    main = find_main_checkout(judged["path"]) or Path(judged["path"])
    known = features.integrated(main, feature)
    if not known or known.get("tree") != judged["tree"]:
        record = {key: judged[key] for key in ("feature", "tree", "path", "head", "tasks", "statuses", "runs")}
        try:
            features.append(main, dict(record, at=records.now()))
        except OSError as error:
            raise OpError("ops.write_failed", [f"cannot write {features.path(main)}: {error}"], 1)
    closed = _close_rows(ctx, judged, _run_names(judged, runs))
    return Result(lines=[f"Integrated feature {feature}: {len(judged['tasks'])} task(s) pass together on "
                         f"{judged['path']} ({(judged['head'] or '')[:10]}).",
                         f"  task-log rows closed: {', '.join(closed) if closed else 'none bound'}",
                         f"Next: `repact inspect --feature {feature}` for the receipt, then merge the feature."])
