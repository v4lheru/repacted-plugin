"""
Location: pact-plugin/hooks/shared/ops/receipt.py
Summary: The change receipt: `repact inspect <id>` and `repact inspect
--feature <name>`. It is what the developer reads instead of the session: what was asked, the
diff, the test runs and whether each one tested the files as they are now, the checks at
acceptance and now, changes to the acceptance tests, waivers, known limits, decisions,
amendments, rejections and what is left. It writes nothing and is built from the stored records
every time (the task file, the run ledger, git, the task log and features.jsonl), so it cannot
drift. Waivers and parked state are printed first, in capitals. Every list is capped so a normal
task fits one screen; `--json` carries the whole dict.
Used by: bin/_repact_ops.py (`inspect`); tests/test_receipt_integration.py.

Example:
    lines = render(task_receipt(ctx, "12"))
    lines = render_feature(feature_receipt(ctx, "search"))
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional

from . import OpError, records

SHOWN = 5
CHECKS_SHOWN = 10
# The check names in plain words, for the printed receipt (--json keeps the names).
LABELS = {"inputs_changed": "files changed", "run_recorded": "test run", "inputs_unchanged": "tested as is",
          "inputs_committed": "committed", "checks_passed": "acceptance criteria", "red_first": "failing test first",
          "needs_human": "needs a person", "integrated": "integrated", "contracts": "contract tests",
          "suite": "whole suite"}
MISSION_LINES = 10
GIT_TIMEOUT = 30
FILES_SHOWN = 5


def _git(cwd, *args: str) -> Optional[str]:
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip("\n") if done.returncode == 0 else None


def _lines(text: Optional[str]) -> List[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def _capped(items: list, shown: int = SHOWN) -> list:
    return list(items[:shown]) + ([f"and {len(items) - shown} more"] if len(items) > shown else [])


def _strings(value) -> List[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


# -- the parts of a task receipt -------------------------------------------------------------

def _mission(ctx: records.Context, task_id: str) -> List[str]:
    from . import brief
    try:
        text = brief.paths(ctx.session_dir, task_id)["brief"].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    body = text.split("## Mission", 1)[1] if "## Mission" in text else ""
    return _lines(body.split("\n## ", 1)[0])[:MISSION_LINES]


def _teachback(meta: dict) -> dict:
    stored = meta.get("teachback") if isinstance(meta.get("teachback"), dict) else {}
    submit = stored.get("submit") if isinstance(stored.get("submit"), dict) else {}
    ack = submit.get("variety_acknowledgment") if isinstance(submit.get("variety_acknowledgment"), dict) else {}
    accepted = stored.get("accepted") if isinstance(stored.get("accepted"), dict) else None
    return {"understanding": _lines(str(submit.get("understanding") or ""))[:3],
            "concern": str(ack.get("concern") or "") if ack.get("rationale_articulates_this_dispatch") != "yes" else "",
            "accepted_by": accepted.get("by") if accepted else None}


def _diff(dispatch: dict) -> dict:
    worktree, base = dispatch.get("worktree"), dispatch.get("base_sha")
    if not base:
        return {"stat": [], "untracked": [], "commits": []}
    status = _lines(_git(worktree, "status", "--porcelain"))
    return {"stat": _lines(_git(worktree, "diff", "--stat", base)),
            "untracked": [line[3:] for line in status if line.startswith("??")],
            "commits": _lines(_git(worktree, "log", "--oneline", f"{base}..HEAD"))}


def _test_files(pin: dict) -> List[str]:
    from shared import acceptance as AC
    files = []
    for command in pin.values():
        picked = AC.selection(command)
        for target in (picked[2] if picked else []):
            file = target.split("::", 1)[0]
            if file and file not in files:
                files.append(file)
    return files


def _acceptance_tests(dispatch: dict, pin: dict) -> dict:
    files = _test_files(pin)
    if not files or not dispatch.get("base_sha"):
        return {"files": files, "changed": []}
    stat = _lines(_git(dispatch.get("worktree"), "diff", "--stat", dispatch["base_sha"], "--", *files))
    return {"files": files, "changed": stat[:-1] if len(stat) > 1 else stat}


def _runs(dispatch: dict, now: Optional[str], runs) -> List[dict]:
    """Every tcount run of the task since dispatch, oldest first, with what it tested."""
    from shared import content_tree as ct
    from shared import evidence_check as ec
    from shared.ledger import _record_time, read_runs
    from shared.plugin_paths import ledger_path
    since = _record_time({"ts": dispatch.get("dispatched_at")})
    if now is None or since is None:
        return []
    if runs is None:
        runs = read_runs(ledger_path(create_dir=False))[0]
    out = []
    for run in ec.task_runs(runs, dispatch, now, since):
        tree = ec.run_tree(run)
        changed = [] if tree == now else (ct.code_diff(str(dispatch.get("worktree")), tree, now) or [])
        out.append({"cmd": str(run.get("run_cmd") or run.get("cmd") or "")[:160], "when": str(run.get("ts") or ""),
                    "passed": run.get("passed"), "failed": run.get("failed"), "exit": run.get("exit_code"),
                    "current": tree == now, "changed_after": changed})
    return out


def _statuses(task: dict, runs) -> dict:
    from shared import evidence_check as ec
    meta = records.metadata(task)
    record = meta.get("acceptance") if isinstance(meta.get("acceptance"), dict) else {}
    then = record.get("statuses") if isinstance(record.get("statuses"), dict) else {}
    try:
        verdict = ec.check(task, runs)
    except Exception as error:  # noqa: BLE001 - the receipt reports what it cannot judge
        verdict = {"tree": None, "statuses": {"needs_human": {"state": "fail", "line": f"the check failed ({error})"}}}
    now = dict(verdict.get("statuses") or {})
    if "integrated" in then:
        now.setdefault("integrated", then["integrated"])
    changed = [name for name in then if isinstance(then.get(name), dict)
               and then[name].get("state") != (now.get(name) or {}).get("state")]
    return {"tree": verdict.get("tree"), "at_accept": then, "now": now, "changed": changed}


def _limits(ctx: records.Context, task_id: str, handoff: dict, main: Optional[str]) -> List[str]:
    found = [item for item in _strings(handoff.get("uncertainty")) if item.lower().startswith("known-limit:")]
    if main:
        from shared.task_log import ledger
        try:
            entries, _bad = ledger.read(Path(main))
            ptask = f"{ctx.team}/{task_id}"
            found += [f"known-limit (project ledger {ledger.short_id(entry)}): {entry.get('text', '')}"
                      for entry in ledger.active_limits(entries) if entry.get("ptask") == ptask
                      and str(entry.get("text", "")) not in " ".join(found)]
        except Exception:  # noqa: BLE001 - an unreadable ledger leaves the HANDOFF's own limits
            pass
    return found


def _left(handoff: dict) -> List[str]:
    from shared import ref_dispositions as RD
    left = [item for item in _strings(handoff.get("open_questions")) if item.lower().startswith("remaining:")]
    left += [f"{ref}: {text}" for ref, text in RD.dispositions(handoff).items()
             if RD.verb(text) in ("partial", "deferred")]
    return left


def _amendments(meta: dict, handoff: dict) -> List[str]:
    from shared import amendments as AM
    given = handoff.get("amendments") if isinstance(handoff.get("amendments"), dict) else {}
    return [f"{key}: {str(value.get('text'))[:160]} -> {given.get(key) or 'no disposition'}"
            for key, value in AM.amendments(meta).items()]


def _rejection(meta: dict) -> List[str]:
    rejected = meta.get("handoff_rejection")
    if not isinstance(rejected, dict):
        return []
    reason = rejected.get("reason") or "; ".join(_strings(rejected.get("problems")))
    return [f"rev {rejected.get('rev')} by {rejected.get('by')}: {str(reason)[:200]}"]


def _task_log(ctx: records.Context, task_id: str, worktree) -> dict:
    from shared.task_log import bridge, write as W
    if not isinstance(worktree, str):
        return {"line": None, "waivers": []}
    try:
        store = W.Store.locate(worktree)
    except (W.TaskError, OSError):
        return {"line": None, "waivers": []}
    state = store.state()
    refs = bridge.linked_refs(state, ctx.team, task_id)
    waivers = [f"{ref}: {state.tasks[ref]['waiver'].get('why')}" for ref in refs
               if isinstance(state.tasks.get(ref, {}).get("waiver"), dict)]
    return {"line": bridge.acceptance_line(bridge.log_report(store, ctx.team, task_id)), "waivers": waivers}


def task_receipt(ctx: records.Context, task_id: str, runs=None) -> dict:
    """The receipt of one dispatched task, as a dict (render prints it). `runs` defaults to this
    machine's run ledger. OpError for a task `repact dispatch` did not make."""
    task = ctx.task(task_id)
    dispatch = records.dispatched(task)
    meta = records.metadata(task)
    handoff = meta.get("handoff") if isinstance(meta.get("handoff"), dict) else {}
    pin = dispatch.get("acceptance_pin") if isinstance(dispatch.get("acceptance_pin"), dict) else {}
    from .sides import main_checkout
    main = main_checkout(dispatch.get("worktree"))
    statuses = _statuses(task, runs)
    log = _task_log(ctx, task_id, dispatch.get("worktree"))
    return {
        "task_id": str(task_id), "subject": str(task.get("subject") or ""), "status": task.get("status"),
        "kind": dispatch.get("kind"), "feature": dispatch.get("feature"), "worktree": dispatch.get("worktree"),
        "branch": dispatch.get("branch"),
        "waiver": meta.get("evidence_waiver") if isinstance(meta.get("evidence_waiver"), dict) else None,
        "parked": meta.get("parked") if isinstance(meta.get("parked"), dict) else None,
        "row_waivers": log["waivers"], "mission": _mission(ctx, task_id),
        "criteria": [f"{ident}: `{command}`" for ident, command in pin.items()],
        "teachback": _teachback(meta), "diff": _diff(dispatch), "runs": _runs(dispatch, statuses["tree"], runs),
        "statuses": statuses, "acceptance_tests": _acceptance_tests(dispatch, pin),
        "limits": _limits(ctx, task_id, handoff, main), "decisions": _strings(handoff.get("decisions")),
        "amendments": _amendments(meta, handoff), "rejections": _rejection(meta), "left": _left(handoff),
        "task_log": log["line"],
    }


# -- printing --------------------------------------------------------------------------------

def _top(receipt: dict) -> List[str]:
    lines = []
    if receipt.get("waiver"):
        waiver = receipt["waiver"]
        lines.append(f"WAIVED: {waiver.get('why')} (by {waiver.get('by')}, {waiver.get('at')}): accepted "
                     "without the checks passing")
    if receipt.get("parked"):
        lines.append(f"PARKED FOR A HUMAN: {receipt['parked'].get('why')}: decide it from repact decide")
    lines += [f"TASK-LOG ROW CLOSED WITH A WAIVER: {item}" for item in receipt.get("row_waivers") or []]
    if receipt["statuses"]["changed"]:
        lines.append("CHANGED SINCE ACCEPTANCE: " + ", ".join(receipt["statuses"]["changed"]))
    return lines


def _run_line(run: dict) -> str:
    counts = f"{run['passed']} passed, {run['failed']} failed, exit {run['exit']}"
    if run["current"]:
        tested = "tested the files as they are now"
    else:
        files = run["changed_after"]
        shown = ", ".join(files[:FILES_SHOWN]) + (f" and {len(files) - FILES_SHOWN} more" if len(files) > FILES_SHOWN
                                                  else "")
        tested = f"files changed after this run: {shown or 'none listed (taken in another checkout)'}"
    return f"  {run['when']}  `{run['cmd']}`: {counts}; {tested}"


def _section(title: str, items: list, empty: Optional[str] = None, shown: int = SHOWN) -> List[str]:
    if not items:
        return [f"{title}: {empty}"] if empty else []
    return [f"{title}:"] + [f"  {item}" for item in _capped(items, shown)]


def check_lines(statuses: dict) -> List[str]:
    """One plain line per check: its label, its state and what it found."""
    return [f"{LABELS.get(name, name.replace('_', ' '))} ({value.get('state')}): {value.get('line')}"
            for name, value in statuses.items() if isinstance(value, dict)]


def render(receipt: dict, short: bool = False) -> List[str]:
    """The receipt as plain lines. `short` (the feature receipt's per-task part) keeps the top
    lines, the header, the checks now and what is left."""
    lines = _top(receipt)
    lines.append(f"#{receipt['task_id']} {receipt['subject']} ({receipt['kind']}, {receipt['status']}"
                 + (f", feature {receipt['feature']}" if receipt.get("feature") else "") + ")")
    statuses = receipt["statuses"]
    checks = check_lines(statuses["now"])
    if short:
        return lines + _section("Checks now", checks, shown=CHECKS_SHOWN) + _section("Left to do", receipt["left"])
    lines += _section("Asked", receipt["mission"]) + _section("Acceptance criteria", receipt["criteria"])
    teachback = receipt["teachback"]
    if teachback["understanding"]:
        lines += _section(f"Teachback (accepted by {teachback['accepted_by'] or 'nobody yet'})",
                          teachback["understanding"] + ([f"concern: {teachback['concern']}"]
                                                        if teachback["concern"] else []))
    diff = receipt["diff"]
    files = diff["stat"][:-1]
    lines += _section("Diff since dispatch", files[:FILES_SHOWN] + diff["stat"][-1:]
                      + [f"new file, not committed: {name}" for name in diff["untracked"]], "no change",
                      shown=FILES_SHOWN + 1)
    lines += _section("Commits", diff["commits"])
    lines += _section("Test runs", [_run_line(run).strip() for run in receipt["runs"][-SHOWN:]], "none recorded")
    if statuses["at_accept"]:
        lines += _section("Checks at acceptance", check_lines(statuses["at_accept"]), shown=CHECKS_SHOWN)
    lines += _section("Checks now", checks, shown=CHECKS_SHOWN)
    tests = receipt["acceptance_tests"]
    if tests["files"]:
        lines += _section("Acceptance tests changed since dispatch", tests["changed"], "none")
    lines += _section("Known limits", receipt["limits"]) + _section("Decisions", receipt["decisions"])
    lines += _section("Amendments", receipt["amendments"]) + _section("Rejections", receipt["rejections"])
    lines += _section("Left to do", receipt["left"])
    if receipt.get("task_log"):
        lines.append(receipt["task_log"])
    return lines


# -- the feature receipt ---------------------------------------------------------------------

def feature_receipt(ctx: records.Context, feature: str, runs=None) -> dict:
    """The task receipts of a feature joined, plus its integration record (features.jsonl) or,
    with none yet, the integration checks as they stand now."""
    from . import integrate
    from shared.task_log import features
    tasks = integrate.feature_tasks(ctx, feature)
    if not tasks:
        raise OpError("ops.invalid_input", [f"no task of team {ctx.team} was dispatched with --feature {feature}"])
    receipts = [task_receipt(ctx, str(task.get("id")), runs) for task in tasks]
    from .sides import main_checkout
    main = main_checkout(receipts[0].get("worktree"))
    record = features.integrated(main, feature) if main else None
    current = None
    if record is None and len(tasks) > 1:
        try:
            current = integrate.judge(ctx, feature, None, runs)
        except OpError as error:
            current = {"statuses": {}, "problems": error.problems}
    return {"feature": feature, "tasks": receipts, "record": record, "current": current}


def render_feature(receipt: dict) -> List[str]:
    record, current, tasks = receipt["record"], receipt["current"], receipt["tasks"]
    if record:
        lines = [f"Feature {receipt['feature']}: integrated on {record.get('path')} at {record.get('at')} "
                 f"({str(record.get('head') or '')[:10]})"]
        lines += [f"  {line}" for line in check_lines(record.get("statuses") or {})]
    elif len(tasks) == 1 and (tasks[0]["statuses"]["at_accept"].get("integrated") or {}).get("state") == "pass":
        lines = [f"Feature {receipt['feature']}: integrated at the acceptance of #{tasks[0]['task_id']}, its only task"]
    else:
        lines = [f"FEATURE {receipt['feature'].upper()} IS NOT INTEGRATED YET: `repact accept --feature "
                 f"{receipt['feature']}` on the merged tree"]
        lines += [f"  {problem[:200]}" for problem in _capped((current or {}).get("problems") or [], 4)]
    for task in tasks:
        lines += [""] + render(task, short=True)
    return lines
