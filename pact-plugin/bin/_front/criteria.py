"""
Location: pact-plugin/bin/_front/criteria.py
Summary: `repact inspect --acceptance <acceptance.md> [--task ID]...` and `repact inspect
--acceptance <file> --pin` (formerly bin/repact-accept): each acceptance
criterion with its latest recorded run. Reads docs/<feature>/acceptance.md (`AC-<n>: <criterion>
`<command>``) and the run ledger, and prints one line per AC: green, red, missing, none or
unverified (no readable ledger), the run's log stem and counts, whether a red run came before the
green one, and what bound it. With --task, the run a HANDOFF cites for the AC (handoff.refs) binds
it; otherwise the runs of the AC command's test selection (acceptance.same_selection) are shown,
for display only; a task's metadata.acceptance_pin that disagrees with the file reads red.
--pin prints the {AC id: command} map a work task's metadata.acceptance_pin holds. It changes
nothing.
Used by: bin/_repact_ops.py (`repact inspect`).
Exit codes: 0 every AC green (pin: printed); 1 any AC not green, or an id listed twice; 2 usage error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shared import acceptance as AC, ref_dispositions as RD, task_files  # noqa: E402
from shared.ledger import _record_time  # noqa: E402


def counts(run: dict) -> str:
    return f"{run.get('passed', '?')} passed, {run.get('failed', '?')} failed"


def cited(args) -> dict[str, tuple]:
    """{AC id: (task label, cited run ids, the task's dispatch time or None, the task's pin or None,
    why the pin cannot be read)} from each --task's stored HANDOFF; the first task wins."""
    found: dict[str, tuple] = {}
    for given in args.task or []:
        team, _, task_id = given.rpartition("/")
        team = team or args.team
        task = task_files.load_task(task_files.task_path(team, task_id)) if team else None
        metadata = task.get("metadata") if isinstance(task, dict) else None
        handoff = metadata.get("handoff") if isinstance(metadata, dict) else None
        if task is None:
            print(f"note: task {given} not found (give --team or TEAM/ID)", file=sys.stderr)
        dispatch = metadata.get("dispatch") if isinstance(metadata, dict) else None
        since = _record_time({"ts": dispatch.get("dispatched_at")}) if isinstance(dispatch, dict) else None
        pin, pin_bad = AC.read_pin(metadata)
        for ac, value in RD.ac_dispositions(handoff).items():
            found.setdefault(ac, (f"HANDOFF #{task_id}", RD.ac_run_ids(value), since, pin, pin_bad))
    return found


def verdict(item: dict, runs: list[dict] | None, bound: tuple | None, root) -> str:
    """One line for one AC. A HANDOFF-cited AC gets the gate's own verdict (acceptance.judge):
    green only when the newest cited run passes, runs this AC's command, inside the tree that
    holds the acceptance file, after the task's dispatch."""
    head = f"{item['id']:<8}"
    if bound and bound[1]:
        label, ids, since, pin, pin_bad = bound
        off_pin = pin_bad or (AC.pin_problem(item["id"], item, pin) if pin is not None else "")
        if off_pin:
            return f"{head} red      -  {off_pin}  [{label}]  {item['criterion'][:60]}"
        if runs is None:
            return f"{head} unverified  -  the run ledger is absent or unreadable  [{label}]"
        run, why = AC.judge(ids, runs, item["command"], root, since)
        if run is None:
            return f"{head} missing  {why}  [{label}]"
        hits = sorted((hit for run_id in ids for hit in AC.runs_for(run_id, runs)),
                      key=lambda hit: _record_time(hit) or 0.0)
        older = [hit for hit in hits if hit is not run]
        red_first = "yes" if not why and any(not AC.run_passed(hit) for hit in older) else "no"
        state = "red" if why else "green"
        note = f"  {why}" if why else ("" if since is not None else "  dispatch time unknown, run time not checked")
        return (f"{head} {state:<8} {AC.stem(run)}  {counts(run)}  red-first: {red_first}  [{label}]{note}  "
                f"{item['criterion'][:60]}")
    if runs is None:
        return f"{head} unverified  -  the run ledger is absent or unreadable  {item['criterion'][:60]}"
    named = AC.runs_of(item["command"], runs, root)
    if not named:
        return (f"{head} none     -  no recorded run of `{item['command'][:80]}` (the same test selection)  "
                f"{item['criterion'][:60]}")
    last = named[-1]
    state = "green" if AC.run_passed(last) else "red"
    red_first = "yes" if state == "green" and any(not AC.run_passed(run) for run in named[:-1]) else "no"
    return (f"{head} {state:<8} {AC.stem(last)}  {counts(last)}  red-first: {red_first}  "
            f"[by command, display only]  {item['criterion'][:60]}")


def check(args) -> int:
    path = Path(args.file).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(f"repact inspect: cannot read {path}: {error.strerror}", file=sys.stderr)
        return 2
    items, ignored = AC.parse_criteria(text)
    for ident in ignored:
        print(f"ignored: {ident!r} is not an AC id (AC-1 .. AC-9999)")
    if not items:
        print(f"repact inspect: no `AC-<n>: <criterion> `<command>`` line in {path}", file=sys.stderr)
        return 2
    runs = AC.ledger_runs(args.ledger)
    bound, root, seen, ok = cited(args), AC.project_root(path.resolve().parent), {}, True
    for item in items:
        if item["id"] in seen:
            print(f"{item['id']:<8} duplicate  listed again on line {item['line']} (first on line {seen[item['id']]})")
            ok = False
            continue
        seen[item["id"]] = item["line"]
        line = verdict(item, runs, bound.get(item["id"]), root)
        ok = ok and line.split()[1] == "green"
        print(line)
    return 0 if ok else 1


def pin(args) -> int:
    """Print the {AC id: command} map of an acceptance file, for metadata.acceptance_pin."""
    items = AC.read_criteria(Path(args.file).expanduser())
    if not items:
        print(f"repact inspect: no readable `AC-<n>: <criterion> `<command>`` line in {args.file}",
              file=sys.stderr)
        return 2
    print(json.dumps(AC.make_pin(items), indent=2))
    return 0
