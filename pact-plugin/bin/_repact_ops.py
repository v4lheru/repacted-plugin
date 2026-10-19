#!/usr/bin/env python3
"""
Location: pact-plugin/bin/_repact_ops.py
Summary: The command line behind bin/repact:
the five workflow operations `repact dispatch|submit|accept|reject|amend` (plus `repact accept
--feature`, integration, §7), and the front door: `repact inspect` (the status screen; `<id>` the
change receipt, §6; `--key` a stored object; `--refusals`/`--rule`; `--acceptance`),
`repact decide` (the numbered list of what waits on the user, and its answers), `repact resume`
and `repact work`. It parses arguments, reads input files strictly (task_store._parse: no NaN,
Infinity or oversized integers), resolves the session (shared/ops/records.resolve) and the caller
(shared/ops/frame.from_cli), runs the op and prints at most 6 lines (a receipt or a front-door
screen prints whole). A refusal (OpError) prints its problems, then the way through and the rule
id: `→ <instead> [rule: <id>]`. `work` and `resume` start a session: they exec bin/repact again
with the claude arguments and REPACT_ROUTED=1, so the launcher's own checks and modes apply.
Used by: bin/repact, which routes these subcommands here; tests/test_ops_cli.py,
tests/test_front_door.py, tests/test_repact_launcher.py.

Exit codes: 0 done, 1 accept turned the facts into a rejection (or a write failed), 2 refused
(nothing written) or a usage error, 3 stored with warnings (submit's evidence preview); the
front-door readers keep their own codes (each module's docstring under bin/_front/).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from shared.ops import OpError, Result, brief, records  # noqa: E402
from shared.ops import accept, amend, dispatch, frame, integrate, receipt, reject, submit  # noqa: E402
from shared import amendments as AM  # noqa: E402
from shared.rules import explain  # noqa: E402
from shared.task_store import _parse  # noqa: E402

MAX_LINES = 6
PROBLEMS_SHOWN = 4
LINE_MAX = 240
FILE_MAX = 2 * 1024 * 1024   # read cap; the ops apply their own smaller limits after parsing

EXAMPLES = {
    "dispatch": ('repact dispatch --name coder-1 --agent pact-backend-coder --subject "Fix the parser" '
                 "--brief-file mission.md --variety 2,2,2,3 --kind fix --branch fix/parser"),
    "submit": "repact submit 12 --as coder-1 --teachback 12.teachback.json",
    "accept": "repact accept 12",
    "inspect": "repact inspect 12",
    "reject": 'repact reject 12 --reason "The fix does not cover the empty input"',
    "amend": 'repact amend 12 --text "Keep the old flag as an alias" --kind ruling',
    "decide": "repact decide 2 set --status active",
    "resume": "repact resume",
    "work": 'repact work "fix the parser on empty input"',
}
FRONT = ("decide", "resume", "work")
ROUTED_ENV = "REPACT_ROUTED"
UNTIL = __import__("re").compile(r"([01]?\d|2[0-3]):[0-5]\d")


class Parser(argparse.ArgumentParser):
    """argparse that appends the subcommand's own example to a usage error (exit 2)."""

    example = ""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        tail = f"\n\nexample:\n  {self.example}" if self.example else ""
        self.exit(2, f"{self.prog}: error: {message}{tail}\n")


def _clean(text: str, cap: int = 0) -> str:
    """One printable line: control and format characters escaped; cut at `cap` when given."""
    line = "".join(char if char.isprintable() else f"\\u{ord(char):04x}" for char in str(text))
    return line if not cap or len(line) <= cap else line[:cap - 3] + "..."


def _read_text(path: str, what: str) -> str:
    try:
        with open(os.path.expanduser(path), "rb") as handle:
            data = handle.read(FILE_MAX + 1)
    except OSError as error:
        raise OpError("ops.invalid_input", [f"{what} {path}: cannot read ({error.strerror or error})"])
    if len(data) > FILE_MAX:
        raise OpError("ops.invalid_input", [f"{what} {path} is over {FILE_MAX // (1024 * 1024)} MB"])
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise OpError("ops.invalid_input", [f"{what} {path} is not UTF-8 text"])


def read_json(path: str, what: str):
    """The parsed JSON of `path`, strictly: OpError for NaN, Infinity, an integer over the digit
    cap, deep nesting or any other invalid JSON."""
    text = _read_text(path, what)
    try:
        return _parse(text)
    except (ValueError, RecursionError) as error:
        raise OpError("ops.invalid_input", [f"{what} {path} is not strict JSON ({str(error)[:120]})"])


def _variety(text: str):
    """'2,2,3,3' -> [2, 2, 3, 3]; anything else is passed on for dispatch to refuse."""
    parts = [part.strip() for part in text.split(",")]
    return [int(part) if part.isdigit() and len(part) <= 2 else part for part in parts]


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--team", help="the session's team (default: from the session context)")
    parser.add_argument("--session-dir", help="the session dir (default: from the session id)")
    parser.add_argument("--session-id", help="the session id (default: CLAUDE_CODE_SESSION_ID)")
    parser.add_argument("--as", dest="as_name", metavar="NAME",
                        help="the dispatched coder's name (a coder's call; default: the lead)")


# `repact --help` and `repact help` print this parser's help (bin/repact routes them here); these are the launcher's own uses, which bin/repact handles before it routes.
LAUNCH_USES = (
    ("repact [claude arguments]", "start a rePACTed Claude Code session; the arguments pass to "
                                  "claude (claude --help lists them)"),
    ("repact report [...]", "the session report (repact-report --help)"),
    ("repact --repact-status", "print the launch mode, the plugin root and the memory-dir state"),
    ("repact --force [...]", "start past another live Claude session (alternate mode only)"),
)


def build_parser() -> Parser:
    uses = "\n".join(f"  {use:<26} {text}" for use, text in LAUNCH_USES)
    parser = Parser(prog="repact", description="The five workflow operations of a rePACTed session, "
                    "its front door, and the launch of a session.",
                    epilog=f"other uses:\n{uses}\n\nexamples:\n"
                    + "\n".join(f"  {line}" for line in EXAMPLES.values()),
                    formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="op", required=True, parser_class=Parser)

    def sub(name: str, text: str) -> Parser:
        made = subs.add_parser(name, help=text, description=text,
                               epilog=f"example:\n  {EXAMPLES[name]}",
                               formatter_class=argparse.RawDescriptionHelpFormatter)
        made.example = EXAMPLES[name]
        _common(made)
        return made

    one = sub("dispatch", "Make a work task, its worktree and its brief; print the Agent call.")
    one.add_argument("--name", required=True)
    one.add_argument("--agent", required=True)
    one.add_argument("--subject", required=True)
    one.add_argument("--brief-file", required=True, help="the lead's mission, as a text file")
    one.add_argument("--variety", required=True, type=_variety, help="N,S,U,R, each 1-4")
    one.add_argument("--kind", required=True, choices=dispatch.KINDS)
    place = one.add_mutually_exclusive_group(required=True)
    place.add_argument("--worktree")
    place.add_argument("--branch")
    one.add_argument("--feature")
    one.add_argument("--acceptance", help="the acceptance file whose AC commands are pinned")
    one.add_argument("--ref", action="append", default=[], help="a project task id (repeatable)")
    one.add_argument("--repo", default=None, help="the repository (default: the current directory)")

    one = sub("submit", "Hand in a teachback or a HANDOFF (the coder's call).")
    one.add_argument("task_id")
    given = one.add_mutually_exclusive_group()
    given.add_argument("--teachback", metavar="FILE")
    given.add_argument("--handoff", metavar="FILE", help="default: the HANDOFF skeleton from the brief")

    one = sub("accept", "Accept a waiting teachback, or judge and close a HANDOFF; with --feature, "
              "accept a feature on its merged tree.")
    one.add_argument("task_id", nargs="?")
    one.add_argument("--feature", help="integration: every task of this feature passes together on the merged tree")
    one.add_argument("--tree", help="with --feature: the merged tree (default: .worktrees/<feature>)")
    one.add_argument("--waive", metavar="WHY", help="close over failing checks with this reason when attended; "
                     "unattended, it parks the task for a human instead (exit 0)")

    one = sub("reject", "Reject the stored HANDOFF with a reason.")
    one.add_argument("task_id")
    reason = one.add_mutually_exclusive_group(required=True)
    reason.add_argument("--reason")
    reason.add_argument("--reason-file", metavar="FILE")

    one = sub("inspect", "With no task id, the status screen; with one, its change receipt (or --key, "
              "one stored object); --feature, a feature's receipt; --refusals, --rule and --acceptance, "
              "the refusals, a rule and the acceptance criteria.")
    one.add_argument("task_id", nargs="?")
    one.add_argument("--feature")
    one.add_argument("--json", action="store_true", help="the whole receipt, screen or rows as JSON")
    one.add_argument("--rebuild", action="store_true", help="status: rebuild workflow-state.json first")
    one.add_argument("--key", help="with a task id: teachback, handoff, rejection, amend (every undisposed "
                     "one) or amend_<n>")
    one.add_argument("--digest", help="with --key: the 12-hex digest a notify named")
    one.add_argument("--relayed-handoff", metavar="FILE", help="the HANDOFF in a -p teammate's saved output")
    one.add_argument("--refusals", nargs="?", const="", metavar="last|CODE",
                     help="the last refusals (default the last --n), the latest one, or a code prefix")
    one.add_argument("--n", type=int, default=5, help="with --refusals: rows to show (default 5)")
    one.add_argument("--frame", choices=("lead", "teammate"), help="with --refusals")
    one.add_argument("--agent", help="with --refusals: only rows the named actor earned")
    one.add_argument("--hook", help="with --refusals: only rows of this check")
    one.add_argument("--rule", metavar="ID", help="one rule's sentence and way through")
    one.add_argument("--acceptance", metavar="FILE", help="each AC of an acceptance.md with its latest run")
    one.add_argument("--pin", action="store_true", help="with --acceptance: the map for metadata.acceptance_pin")
    one.add_argument("--task", action="append", help="with --acceptance: a task whose HANDOFF refs bind ACs "
                     "(ID or TEAM/ID)")
    one.add_argument("--ledger", help="with --acceptance: a run ledger other than this machine's")

    one = sub("amend", "Add a ruling, correction or scope change to an open task.")
    one.add_argument("task_id")
    one.add_argument("--text")
    one.add_argument("--kind", default=amend.DEFAULT_KIND, choices=amend.KINDS)
    one.add_argument("--next", action="store_true", help="print the next amend_<n> key and write nothing")

    one = sub("decide", "List every question that waits on you, numbered; `decide <n|T-ref> <verb> ...` "
              "answers a task-log row through repact-task.")
    one.add_argument("target", nargs="?", help="the item number of the last listing, or a T- ref")
    one.add_argument("answer", nargs=argparse.REMAINDER, help="set|done|drop|dep|reopen and its arguments")
    one.add_argument("--count", action="store_true", help="only the head line")
    one.add_argument("--json", action="store_true", help="the same items as JSON")

    one = sub("resume", "Print the status screen, then resume that session in Claude Code.")
    one.add_argument("--no-launch", action="store_true", help="print the screen only")
    one.add_argument("--force", action="store_true", help=argparse.SUPPRESS)

    one = sub("work", "Start a session: with an ask, on it; with none, on the next task-log row; with "
              "--unattended, autopilot drains the task log.")
    one.add_argument("ask", nargs="*")
    one.add_argument("--unattended", action="store_true")
    one.add_argument("--until", help="--unattended: stop starting tasks after HH:MM")
    one.add_argument("--max-usd", type=float, help="--unattended: dollars this run may spend")
    one.add_argument("--max-tasks", type=int, help="--unattended: tasks this run may take")
    one.add_argument("--pr", action="store_true", help="--unattended: push each branch and open a draft PR")
    one.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    return parser


def run(args: argparse.Namespace):
    """The op's Result, or for a front-door reader the exit code it printed under."""
    if args.op in FRONT:
        return _front_door(args)
    if args.op == "inspect" and _reader(args):
        return _read(args)
    ctx = records.resolve(args.team, args.session_dir, args.session_id, frame.from_cli(args.as_name),
                          bind=True)   # the caller's own session only
    if args.op == "dispatch":
        return dispatch.run(ctx, name=args.name, agent=args.agent, subject=args.subject,
                            mission=_read_text(args.brief_file, "--brief-file"), variety=args.variety,
                            kind=args.kind, repo=os.path.realpath(args.repo or os.getcwd()),
                            worktree=args.worktree, branch=args.branch, feature=args.feature,
                            acceptance=args.acceptance, refs=args.ref)
    if args.op == "submit":
        if args.teachback:
            return submit.teachback(ctx, args.task_id, read_json(args.teachback, "--teachback"))
        path = args.handoff or str(brief.paths(ctx.session_dir, args.task_id)["handoff"])
        return submit.handoff(ctx, args.task_id, read_json(path, "--handoff"))
    if args.op == "accept":
        if _one_of(args, "accept") == "feature":
            if args.waive is not None:
                raise OpError("ops.invalid_input", ["--waive takes a task id; a feature has no waiver"])
            return integrate.run(ctx, args.feature, tree=args.tree)
        return accept.run(ctx, args.task_id, waive=args.waive)
    if args.op == "inspect":
        return _inspect(ctx, args)
    if args.op == "reject":
        text = args.reason if args.reason is not None else _read_text(args.reason_file, "--reason-file")
        return reject.run(ctx, args.task_id, text)
    if args.next:
        return _next_amend(ctx, args.task_id)
    if args.text is None:
        raise OpError("ops.invalid_input", ["`repact amend` needs --text (or --next to see the next key)"])
    return amend.run(ctx, args.task_id, args.text, args.kind)


def _next_amend(ctx: records.Context, task_id: str) -> Result:
    """The next free amend_<n> and the amendments already on the task; nothing is written."""
    meta = records.metadata(ctx.task(task_id))
    existing = ", ".join(f"{key} ({value.get('kind') or 'note'})" for key, value in AM.amendments(meta).items())
    return Result(task_id=task_id, lines=[f"next: {AM.AMEND_PREFIX}{AM.next_number(meta)} on #{task_id}",
                                          f"existing: {existing or 'none'}"])


# -- the front door -------------------------------------------------------------

def _reader(args: argparse.Namespace) -> bool:
    """True for an inspect that reads with no op context: the status screen and the flag readers."""
    return (args.key is not None or args.relayed_handoff is not None or args.refusals is not None
            or args.rule is not None or args.acceptance is not None
            or (args.task_id is None and args.feature is None))


def _read(args: argparse.Namespace) -> int:
    from _front import criteria, payload, refusals, status
    modes = [name for name, given in (("--key", args.key is not None), ("--relayed-handoff", args.relayed_handoff),
                                      ("--refusals", args.refusals is not None), ("--rule", args.rule),
                                      ("--acceptance", args.acceptance), ("--feature", args.feature)) if given]
    if len(modes) > 1:
        raise OpError("ops.invalid_input", [f"`repact inspect` takes one of {', '.join(modes)}"])
    if args.key is not None:
        if args.task_id is None:
            raise OpError("ops.invalid_input", ["--key reads a task: repact inspect <id> --key handoff"])
        return payload.read(args.task_id, args.key, args.team, args.digest)
    if args.task_id is not None and (args.relayed_handoff or args.refusals is not None or args.rule
                                     or args.acceptance):
        raise OpError("ops.invalid_input", [f"{modes[0]} takes no task id"])
    if args.relayed_handoff:
        return payload.relayed_handoff(args.relayed_handoff)
    if args.rule:
        return refusals.rule(args.rule)
    if args.refusals is not None:
        return refusals.show(args.refusals or None, args.n, args.frame, args.agent, args.hook,
                             args.session_id, args.session_dir, args.json)
    if args.acceptance:
        from types import SimpleNamespace
        given = SimpleNamespace(file=args.acceptance, team=args.team, task=args.task, ledger=args.ledger)
        return criteria.pin(given) if args.pin else criteria.check(given)
    return status.show(args.session_id, args.session_dir, args.rebuild, args.json)[0]


def _front_door(args: argparse.Namespace) -> int:
    from _front import inbox, status
    if args.op == "decide":
        if args.target is None:
            return inbox.show(args.session_dir, args.json, args.count)
        return inbox.decide(args.target, args.answer, args.session_dir)
    if args.op == "resume":
        code, folder = status.show(args.session_id, args.session_dir)
        if code != 0 or args.no_launch:
            return code
        if os.environ.get("CLAUDECODE") == "1":
            print("(inside a Claude Code session: not resuming; run `repact resume` from a terminal)")
            return 0
        return _launch(["--resume", folder.name], args.force)
    return _work(args)


def _work(args: argparse.Namespace) -> int:
    """`repact work`: the claude prompt for an ask, the next task-log row, or autopilot."""
    if os.environ.get("CLAUDECODE") == "1":
        print("repact work: refused, you are inside a Claude Code session; `repact work` starts a new "
              "one from a terminal (in this session, ask the lead, or run /repacted:work)", file=sys.stderr)
        return 2
    flags = [("--until", args.until), ("--max-usd", args.max_usd), ("--max-tasks", args.max_tasks)]
    given = [name for name, value in flags if value is not None] + (["--pr"] if args.pr else [])
    if not args.unattended:
        if given:
            raise OpError("ops.invalid_input", [f"{', '.join(given)} go with --unattended"])
        ask = " ".join(args.ask).strip()
        return _launch([ask or "/repacted:work"], args.force)
    if args.ask:
        raise OpError("ops.invalid_input", ["--unattended takes no ask: autopilot works the task log"])
    if args.until is not None and not UNTIL.fullmatch(args.until):
        raise OpError("ops.invalid_input", [f"--until is HH:MM, not {_clean(args.until, 20)!r}"])
    for name, value in flags[1:]:
        if value is not None and not value > 0:
            raise OpError("ops.invalid_input", [f"{name} must be above 0"])
    words = ["/repacted:autopilot"] + [f"{name} {value:g}" if isinstance(value, float) else f"{name} {value}"
                                       for name, value in flags if value is not None]
    words += ["--pr"] if args.pr else []
    return _launch([" ".join(words)], args.force, {"REPACT_EXECUTION": "subagents"})


def _launch(claude_args: list, force: bool = False, extra_env: dict = None) -> int:
    """Exec bin/repact with these claude arguments, past its routing (REPACT_ROUTED=1)."""
    env = dict(os.environ, **(extra_env or {}), **{ROUTED_ENV: "1"})
    launcher = str(Path(__file__).resolve().parent / "repact")
    argv = ["repact", *(["--force"] if force else []), *claude_args]
    sys.stdout.flush()
    os.execve("/bin/bash", ["/bin/bash", launcher, *argv[1:]], env)
    return 2   # not reached


def _one_of(args: argparse.Namespace, op: str) -> str:
    """"task" or "feature": exactly one of a task id and --feature, else OpError."""
    if (args.task_id is None) == (args.feature is None):
        raise OpError("ops.invalid_input", [f"`repact {op}` takes a task id or --feature <name>, one of them"])
    return "task" if args.task_id is not None else "feature"


def _inspect(ctx: records.Context, args: argparse.Namespace) -> Result:
    if _one_of(args, "inspect") == "feature":
        made = receipt.feature_receipt(ctx, args.feature)
        lines = receipt.render_feature(made)
    else:
        made = receipt.task_receipt(ctx, args.task_id)
        lines = receipt.render(made)
    if args.json:   # ASCII only, so no control or direction character reaches the terminal
        lines = json.dumps(made, ensure_ascii=True, indent=2, default=str).splitlines()
    return Result(task_id=args.task_id or "", lines=lines, whole=True)


def refusal_lines(error: OpError) -> list:
    """Problems (at most PROBLEMS_SHOWN, then a count), then `→ <instead> [rule: <id>]`."""
    shown = [f"refused: {_clean(problem, LINE_MAX)}" for problem in error.problems[:PROBLEMS_SHOWN]]
    if len(error.problems) > PROBLEMS_SHOWN:
        shown.append(f"  and {len(error.problems) - PROBLEMS_SHOWN} more")
    rule = explain(error.rule)
    instead = rule.instead if rule else "Run repact inspect --refusals last for the full refusal."
    return (shown or ["refused"]) + [f"→ {instead} [rule: {error.rule}]"]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except OpError as error:
        for line in refusal_lines(error)[:MAX_LINES]:
            print(line, file=sys.stderr)
        return error.exit_code
    if isinstance(result, int):
        return result
    for line in (result.lines if result.whole else result.lines[:MAX_LINES]):
        print(_clean(line))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
