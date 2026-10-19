"""
reference/work/task-log.md and the command files' boundary writes against bin/repact-task.

Ported from test_backlog.py when the commands moved from hooks/shared/backlog.py to repact-task: the
RULES survive (where writes happen, what the boundary table may name, every drift flag has a
verdict row), and each population is taken from the code, never from a list in this file:
  - the subcommands and set's flags come from repact-task's own parser;
  - the drift-flag kinds come from project_tasks.drift_flags run on a state that trips every branch.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.machinery
import importlib.util
import re

from project_tasks_support import PLUGIN_ROOT, reader

COMMANDS_DIR = PLUGIN_ROOT / "commands"
CLI_PATH = PLUGIN_ROOT / "bin" / "repact-task"
INVOCATION = 'bin/repact-task"'
# The classification of every subcommand. A subcommand the CLI grows without being classified
# here reddens test_every_subcommand_is_classified, so the write-site detector cannot go blind.
ITEM_WRITES = frozenset({"add", "set", "dep", "link", "start", "done", "reopen", "drop"})
READS = frozenset({"next", "blocked", "tree", "show", "fsck", "summary"})
OTHER = frozenset({"init", "sync", "ledger", "render", "commit", "import-md", "import-gates", "import-pact-backlog",
                   "project", "atlas"})
COUNT_WORDS = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX"}
NAMED_FILE = re.compile(r"`([A-Za-z0-9_-]+\.md)`")


def cli():
    loader = importlib.machinery.SourceFileLoader("repact_task_cli_for_next", str(CLI_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def subparsers():
    parser = cli()._parser()
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0].choices


def write_sites(name):
    """Lines in one command file that invoke repact-task with an item-writing verb."""
    text = (COMMANDS_DIR / name).read_text(encoding="utf-8")
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1)
            if INVOCATION in line and any(f'" {verb} ' in line for verb in ITEM_WRITES)]


def task_log_md():
    return (PLUGIN_ROOT / "reference" / "work" / "task-log.md").read_text(encoding="utf-8")


def rule_sentence(lead: str) -> str:
    """The one bold rule sentence that starts with `lead` (e.g. 'FOUR FILES carry boundary writes')."""
    for line in task_log_md().splitlines():
        if line.startswith("**") and lead in line:
            return line
    raise AssertionError(f"no rule sentence containing {lead!r} in task-log.md")


def test_every_subcommand_is_classified():
    declared = set(subparsers())
    assert declared, "the parser declares no subcommands — this arm is vacuous"
    classified = ITEM_WRITES | READS | OTHER
    assert classified == declared, (
        f"only in the CLI: {sorted(declared - classified)} (a write site using one is invisible); "
        f"only here: {sorted(classified - declared)} (dead vocabulary)")
    assert not (ITEM_WRITES & READS), "a verb classified both as a write and a read"


def test_the_write_sites_are_where_the_rule_says_and_nowhere_else():
    """The positive half runs first: a detector that matches nothing makes every zero vacuous."""
    carriers = ("wrap-up.md", "orchestrate.md", "comPACT.md", "decide.md")
    empty = [name for name in carriers if not write_sites(name)]
    assert not empty, f"no repact-task write site found in {empty}; the detector is orphaned"
    for name in ("rePACT.md", "pause.md", "peer-review.md"):
        assert write_sites(name) == [], f"{name} gained a boundary write the ruling put at zero"


def test_task_log_md_names_exactly_the_files_that_carry_write_sites():
    sentence = rule_sentence("FILES carry boundary writes")
    named = set(NAMED_FILE.findall(sentence))
    carrying = {path.name for path in COMMANDS_DIR.glob("*.md") if write_sites(path.name)}
    assert named == carrying, f"the rule names {sorted(named)}; the sites are in {sorted(carrying)}"
    assert sentence.startswith(f"**{COUNT_WORDS[len(named)]} FILES"), sentence


def test_the_report_files_exist():
    sentence = rule_sentence("FILES carry a task report")
    named = set(NAMED_FILE.findall(sentence))
    assert named and all((COMMANDS_DIR / name).exists() for name in named), named
    assert sentence.startswith(f"**{COUNT_WORDS[len(named)]} FILES"), sentence


def boundary_table_identifiers():
    text = task_log_md().split("### When the writes happen", 1)[-1]
    names = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 5 and cells[1].isdigit():
            names.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_-]*)`", cells[2]))
    return names


def test_the_boundary_table_and_the_cli_name_the_same_writes():
    """Omission reads as permission: every field `set` can write needs a row, and a row may name
    only what the CLI can do (a set flag, a subcommand, or a status value)."""
    choices = subparsers()
    writable = {option.lstrip("-") for action in choices["set"]._actions
                for option in action.option_strings if option.startswith("--")} - {"help"}
    statuses = set(choices["set"]._option_string_actions["--status"].choices)
    named = boundary_table_identifiers()
    assert writable and statuses and named, "a population came back empty — the arm is vacuous"
    missing = writable - named
    assert not missing, f"writable but named in NO row: {sorted(missing)}"
    invented = named - writable - set(choices) - statuses
    assert not invented, f"the table names {sorted(invented)}, which the CLI cannot write"


# ---------------------------------------------------------------------------------------------
# every drift flag the code can raise has a verdict row in Step 3
# ---------------------------------------------------------------------------------------------

# flag text fragment (what drift_flags prints) -> the Step 3 row that gives its verdict
ROW_FOR_FLAG = {
    "relation names unknown": "a relation names an unknown task",
    "stuck (depends on a dropped task)": "stuck: depends on a dropped task",
    "active together with exclusive": "two exclusive tasks are both `active`",
    "active and untouched for over": "active and untouched past 14 days",
    "planned, unranked and untouched": "planned and unranked, untouched past 14 days",
    "accepted without evidence": "accepted without evidence",
    "concurrent edit": "concurrent edit on a field",
    "different contents": "one event id with different contents",
    "second create": "a second create for one ref",
    "corrupt (": "corrupt line N",
}


def _ts(days_ago: float, n: int) -> str:
    moment = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n % 1000:03d}Z"


def _every_flag_state():
    R = reader()
    events, n = [], 0

    def ev(op, t, days=1.0, **fields):
        nonlocal n
        n += 1
        event = {"v": 1, "eid": f"20260901T000000{n:03d}Z-{n:08x}", "ts": _ts(days, n), "by": "t",
                 "op": op, "t": t, **fields}
        events.append(event)
        return event

    def create(ref, status="planned", days=1.0, **fields):
        return ev("create", ref, days, title=f"task {ref}", status=status, priority="P2", **fields)

    create("T-00000001", rank=1)
    ev("dep_add", "T-00000001", on="T-0000ffff")                                  # unknown relation
    create("T-00000002", status="dropped", rank=1)
    create("T-00000003", rank=1)
    ev("dep_add", "T-00000003", on="T-00000002")                                  # stuck
    create("T-00000004", status="active", rank=1)
    create("T-00000005", status="active", rank=1)
    ev("set", "T-00000004", field="exclusive_with", value=["T-00000005"])        # exclusive pair
    create("T-00000006", status="active", days=40, rank=1)                       # stale active
    create("T-00000007", days=40)                                                # stale unranked
    create("T-00000008", status="active", rank=1)
    ev("accept", "T-00000008", ptask="s/1", rev=1, verified=False, evidence=[])  # no evidence
    base = create("T-00000009", rank=1)["eid"]
    ev("set", "T-00000009", field="note", value="a", base=base)
    ev("set", "T-00000009", field="note", value="b", base=base)                  # concurrent edit
    twin = dict(ev("set", "T-00000009", field="rank", value=2))
    events.append(dict(twin, by="someone-else"))                                 # same eid, two contents
    create("T-00000001", rank=1)                                                 # second create
    return R.replay(events, corrupt=[(99, "not JSON")])


def test_every_drift_flag_kind_has_a_step_three_row():
    R = reader()
    flags = R.drift_flags(_every_flag_state(), now=datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc))
    unmapped = [flag for flag in flags if not any(key in flag for key in ROW_FOR_FLAG)]
    assert not unmapped, f"drift_flags raised a kind with no verdict row: {unmapped}"
    raised = {key for key in ROW_FOR_FLAG if any(key in flag for flag in flags)}
    assert raised == set(ROW_FOR_FLAG), (
        f"the fixture no longer trips {sorted(set(ROW_FOR_FLAG) - raised)}; fix the fixture, "
        f"or retire the row if the code dropped that flag")
    table = task_log_md().split("## Step 3", 1)[1].split("## Step 4", 1)[0]
    rows = {line.split("|")[1].strip() for line in table.splitlines() if line.startswith("| ")}
    missing = sorted(set(ROW_FOR_FLAG.values()) - rows)
    assert not missing, f"task-log.md's Step 3 table has no row for {missing}"
