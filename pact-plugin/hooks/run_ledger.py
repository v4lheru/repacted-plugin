#!/usr/bin/env python3
"""Run-ledger hook: record every test run's parsed counts (D15 item 3, repacted:shell-tooling-traps).

Location: pact-plugin/hooks/run_ledger.py.
Events: PostToolUse AND PostToolUseFailure, matcher Bash. Both are needed: a Bash call
that exits non-zero fires PostToolUseFailure only (measured 2026-09-26), so a
PostToolUse-only hook would record passing runs and miss every failing one.

For a Bash command that runs tests (see shared/runcounts.py detect_runner), it parses the output
for a runner summary and appends ONE record to ${CLAUDE_PLUGIN_DATA}/runs/ledger.jsonl
through shared.ledger.ledger_append (the writer tcount also uses); for a command that runs
tcount, one attributed record per tcount run its output names (`log: <path>`), which
read_ledger pairs with tcount's own unattributed row, so each run reads as one attributed
run (e1baab9f: `tcount a && tcount b` left b's row unattributed). A report is a run only
when tcount's own row for it is in the ledger's tail: by its log path when the output shows
the `log:` line, else (run-tests' summary read back through grep, a run whose log line a
pipe cut) by its counts, in this session and written during this call. With no such row a
report is text (a grep of an old log, a quoted or pasted report, a fixture) and records
nothing. A tcount run whose whole report a pipe cut (`tcount ... | grep Passed`) shows neither
line: its row is adopted when this session wrote it during this call, in a directory the call
names, for a command whose words the call spells (unseen_rows). The HANDOFF evidence
gate (evidence_gate.py, unit 11) matches a HANDOFF's claimed counts against these records.

When no count parses and the command is not tcount, it adds one line of context for the
model: rerun with `tcount <cmd>`. This closes the rtk exit-0 trap at the source (D7).

Payload fields read (measured 2026-09-26; identity fields re-measured on Claude Code 2.1.283):
  PostToolUse:        tool_input.command, tool_response.{stdout, stderr, interrupted}
  PostToolUseFailure: tool_input.command, error = "Exit code N\\n<combined output>"
  both:               session_id, cwd, hook_event_name, agent_type, and agent_id.
Identity on 2.1.283 (up-builder's live capture for #180; tests/fixtures/role_frames.py):
  - an in-process teammate: agent_type = its MEMBER NAME, agent_id present with NO '@', and
    session_id = the LEAD's session_id;
  - the lead: a lead agent_type and no agent_id;
  - a tmux teammate: agent_type pact-<specialist>, no agent_id, its own session_id;
  - an Agent-tool subagent: agent_id with no '@' and its own agent_type (e.g. general-purpose);
  - older Claude Code gave an in-process agent_id of the form "<name>@<team>".
The command is the POST-rewrite text, e.g. `rtk pytest -q` when rtk rewrote it.

Attribution only: agent_id and agent_type LABEL the ledger line. They never decide a role or a
permission, and agent_type is never compared with a lead spelling here.

Never blocks: always exits 0, never returns a permission decision, silent on any error.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import sys
import time

from shell.words import token_spans  # the reader every guard shares

ADVISORY = ("No test count in this output. Before reporting a result, rerun with "
            "`tcount <cmd>` (it prints collected/passed/failed and the real exit code).")
_EXIT_PREFIX = re.compile(r"^Exit code (\d+)\s*\n?")
HANDLED_EVENTS = ("PostToolUse", "PostToolUseFailure")
TAIL_BYTES = 256 * 1024          # tcount appends its row moments before this hook runs
CALL_DIRS_MAX = 6                # directories kept on one row (the ledger line is capped)
# One directory as the ledger line spells it (UTF-8, JSON escapes): six of them stay well under
# shared.ledger.LEDGER_LINE_MAX, which a row may not exceed and call_dirs is never shrunk to fit.
CALL_DIR_MAX_BYTES = 400
BASH_MAX_SECONDS = 600           # the Bash tool's longest foreground call
CLOCK_SLACK_SECONDS = 2          # ledger stamps are whole seconds
_SHELL_WORD = r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s;&|<>()"'])+"""
_TCOUNT_OPERATORS = ";&|<>()\n"      # what ends a tcount run (token_spans operators)
# In command order: `NAME=value`, `cd <dir>` at the start of a step, `git -C <dir>`.
_CALL_STEP = re.compile(
    r"(?:^|[\s;&|(])(?P<name>[A-Za-z_]\w*)=(?P<value>" + _SHELL_WORD + r")?"
    r"|(?:^|[;&|(]\s*|\b(?:then|do|else)\s+)(?:cd|pushd)\s+(?:--\s+)?(?P<cd>" + _SHELL_WORD + r")"
    r"|\bgit\s+-C\s+(?P<git>" + _SHELL_WORD + ")", re.MULTILINE)
_WORD_PIECE = re.compile(r"\"([^\"\n]*)\"|'([^'\n]*)'|([^\"']+)")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_]\w*=")
_WRAPPER_WORDS = frozenset({"nice", "env", "time", "rtk", "proxy", "nohup", "command", "exec", "timeout"})
_INTERPRETER = re.compile(r"^(?:python[\d.]*|bash|sh|zsh|node)$")
_HOLDS_ABSOLUTE = re.compile(r"(?:^|[=:;,])/")      # `/tmp/x/red.trx`, `trx;LogFileName=/tmp/x/red.trx`


def output_and_exit(payload: dict) -> tuple[str, int | None]:
    """(combined tool output, exit code) for either event shape; exit None when unknown."""
    if payload.get("hook_event_name") == "PostToolUseFailure":
        error = payload.get("error")
        text = error if isinstance(error, str) else ""
        match = _EXIT_PREFIX.match(text)
        if match:
            return text[match.end():], int(match.group(1))
        return text, None
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return "", None
    stdout, stderr = response.get("stdout"), response.get("stderr")
    text = "\n".join(part for part in (stdout, stderr) if isinstance(part, str) and part)
    return text, (None if response.get("interrupted") is True else 0)


def _attribution_agent_id(payload):
    """The payload's raw agent_id, or None. The ONLY place this file reads that field.

    Attribution only: the value fills the ledger record's agent_id and label. It never
    decides a role, a lead/teammate branch or a permission (the fork's named-site
    exemption in test_lead_discriminator_invariant covers exactly this helper).
    """
    return payload.get("agent_id")


def _attribution_agent_type(payload):
    """The payload's raw agent_type, or None: on Claude Code 2.1.283 an in-process teammate's
    member name. Attribution only: it names the ledger line and is never compared with a lead
    spelling (is_lead is the one role reader, and this file does not call it)."""
    return payload.get("agent_type")


def actor_label(payload: dict, session_id: str | None) -> tuple[str | None, str | None, str | None]:
    """(agent_id, agent_name, team) for LABELLING the ledger line; never a role decision.

    TEAM comes from the session, never from an agent_id suffix: it is the one team whose
    config.json names this session_id as its lead (in-process teammates share the lead's session_id), or,
    for a tmux teammate with its own session, the session registry's team if that team exists.
    NAME, by the agent_id value (the only branch, and it is on a label):
      1. agent_id present (an in-process teammate or an Agent-tool subagent; the lead's frame
         carries none): the name part of an older "<name>@<team>" id, else the frame's
         agent_type, which is the member name on 2.1.283 (F14's lesson reversed: without this,
         every in-process run fell through to step 2 and was labelled the lead);
      2. no agent_id, and a team config names this session as its lead: the lead, labelled
         from that config's leadAgentId ("team-lead@<team>"), never the registry;
      3. no agent_id, not a lead session: a tmux teammate, labelled from the session registry;
    a registry team with no <config>/teams/<team>/ dir is dropped (a guess, not a team).
    When MORE than one team config claims the session (a stale one and a live one), the session
    has no team: an in-process run keeps its name with no team and no task, and a run without an
    agent_id stays unlabelled rather than fall through to the registry (F14's trap).
    """
    from shared.team_labels import label_from_agent_id, team_exists, teammate_label, teams_claiming_session

    raw = _attribution_agent_id(payload)
    agent_id = raw if isinstance(raw, str) and raw else None
    claims = teams_claiming_session(session_id)
    lead = claims[0] if len(claims) == 1 else None
    if agent_id is not None:
        name, _suffix_team = label_from_agent_id(agent_id)
        if name is None:
            agent_type = _attribution_agent_type(payload)
            name = agent_type if isinstance(agent_type, str) and agent_type else None
        return agent_id, name, lead[1] if lead else None
    if lead:
        return None, lead[0], lead[1]
    if claims:
        return None, None, None
    name, team = teammate_label(session_id)
    return None, name, team if team_exists(team) else None


def _literal(word: str | None, values: dict[str, str]) -> str | None:
    """The text a shell word stands for when the command's own text settles it, else None:
    quotes removed, `$NAME` filled from `values`. A substitution, a glob, `~`, an escape or a
    name this command never set leaves it unsettled."""
    parts = []
    for double, single, bare in _WORD_PIECE.findall(word or ""):
        if single:
            parts.append(single)
            continue
        text = _VARIABLE.sub(lambda match: values.get(match.group(1) or match.group(2), "$"), double or bare)
        unquoted = _VARIABLE.sub("", bare)
        if any(char in text for char in "$`\\") or any(char in unquoted for char in "*?[~{"):
            return None
        parts.append(text)
    return "".join(parts) or None


def _program_path(segment: str, values: dict[str, str]) -> str | None:
    """The program one command segment runs, when it is spelled as an absolute path: the first
    word after assignments and wrappers (`nice -n 19`, `env`, `rtk`), or the script handed to an
    interpreter (`python3 /tree/scripts/run-tests`). A path that is an argument is not one; nor
    are env-get's FILE and KEY (`env-get FILE KEY -- <program>`)."""
    script_next = env_get = False
    for found in re.finditer(_SHELL_WORD, segment):
        word = found.group(0)
        name = os.path.basename(word.strip("\"'"))
        if env_get:
            env_get = _unquoted(word) != "--"
            continue
        if not script_next and name == "env-get":
            env_get = True
            continue
        if word.startswith("-"):
            if script_next and word in ("-m", "-c"):
                return None             # a module or inline code, not a script path
            continue
        if not script_next:
            if word.isdigit() or name in _WRAPPER_WORDS or _ASSIGNMENT_WORD.match(word):
                continue
            if _INTERPRETER.match(name):
                script_next = True
                continue
        path = _literal(word, values)
        return os.path.normpath(path) if path and os.path.isabs(path) else None
    return None


def _fits_a_row(path: str) -> bool:
    """True when this path takes at most CALL_DIR_MAX_BYTES on a ledger line. A path of more
    characters than that cannot fit, and is not encoded to find out."""
    return len(path) <= CALL_DIR_MAX_BYTES and len(
        json.dumps(path, ensure_ascii=False).encode("utf-8", "surrogatepass")) <= CALL_DIR_MAX_BYTES


def call_dirs(command: str, cwd: str | None) -> list[str]:
    """The directories this Bash call itself was made in: (1) the payload's cwd, (2) the target
    of each `cd <dir>` and `git -C <dir>` the command spells, with `$NAME` filled from a plain
    `NAME=value` earlier in the same command (`W=/tree; cd "$W"`), and (3) the directory of a
    program that a test-run segment runs by its path (`"$W/repacted/scripts/run-tests"`). The
    evidence gate opens a linked worktree only for a run whose own call names it here
    (_evidence.own_worktrees): a row that took its cwd from a tcount row joined by a shown log
    line says where that run ran, not where this call was made, and a path that is only an
    argument of some program (`cat /tree/report.txt`) names no directory of the call. A target
    the text does not settle is left out, and so is one over CALL_DIR_MAX_BYTES: a row over the
    ledger's line cap is not written at all, which lost the run with its directories."""
    from shared.runcounts import detect_runner

    base = cwd if isinstance(cwd, str) and os.path.isabs(cwd) else None
    dirs = [os.path.normpath(base)] if base and _fits_a_row(os.path.normpath(base)) else []

    def add(path: str) -> None:
        if path not in dirs and _fits_a_row(path):
            dirs.append(path)

    values: dict[str, str] = {}
    for step in _CALL_STEP.finditer(command):
        if step.group("name"):
            value = _literal(step.group("value"), values)
            values.pop(step.group("name"), None)
            if value is not None:
                values[step.group("name")] = value
            continue
        target = _literal(step.group("cd") or step.group("git"), values)
        if target is None or not (base or os.path.isabs(target)):
            continue
        target = os.path.normpath(os.path.join(base or "", target))
        if step.group("cd"):
            base = target
        add(target)
    for segment in _SEGMENT.split(command):
        program = _program_path(segment, values) if detect_runner(segment) else None
        if program:
            add(os.path.dirname(program))
    return dirs[:CALL_DIRS_MAX]


def build_records(payload: dict, command: str, runner: str) -> tuple[list[dict], bool]:
    """(ledger records, whether a count was parsed) for one test-run tool call: one record, or
    for a command that runs tcount, one per tcount run in its output (`tcount a && tcount b`),
    joined to tcount's own row by its log path (never by time). A run that already has a hook
    row, or whose tcount row names another session, gets none: each run is attributed once.
    A report whose tcount row is not in the tail records nothing: tcount writes its row before
    it prints, so a report without one is pasted text, and it used to become the caller's
    counted run (second review, finding 2).
    A counts line with no log of its own is joined to tcount's row by its counts (quiet_row),
    and records nothing when there is no such row. A tcount run the output shows nothing of is
    adopted from the tail (unseen_rows), as many as the command has tcount segments left; with
    none to adopt and no report shown, the call gets one count-less row, as before."""
    from shared.ledger import LEDGER_COUNT_FIELDS
    from shared.runcounts import parse_counts, tcount_reports, tcount_runs
    from shared.team_labels import current_task_id, git_context

    text, exit_code = output_and_exit(payload)
    counts = parse_counts(text, runner_hint=runner)
    session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    agent_id, agent_name, team = actor_label(payload, session_id)
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    label = {"session_id": session_id, "agent_id": agent_id, "agent_name": agent_name, "team": team,
             "task_id": current_task_id(team, agent_name), "cmd": command, "source": "hook",
             "call_dirs": call_dirs(command, cwd)}
    parsed = {**(counts.as_ledger_fields() if counts else {"runner": runner}),
              "exit_code": exit_code, "count_parsed": counts is not None}
    reports = tcount_reports(text) if runner == "tcount" else []
    logged: dict[str, tuple] = {}       # log path -> (counts, exit code) of the report that names it
    for shown_counts, shown_exit, log_path in reports:
        if log_path and (log_path not in logged or logged[log_path][0] is None):
            logged[log_path] = (shown_counts, shown_exit)
    shown = [(shown_counts, shown_exit) for shown_counts, shown_exit, log_path in reports
             if shown_counts and not log_path]
    own_fields = ("runner", *LEDGER_COUNT_FIELDS, "exit_code", "count_parsed", "cwd", "git_head", "git_branch")
    tail = ledger_tail() if runner == "tcount" else []
    if not logged and not shown:
        unseen = unseen_rows(command, label["call_dirs"], session_id, tail, set(), call_started(payload),
                             tcount_runs(command)) if runner == "tcount" else []
        if unseen:
            return [{**{key: own.get(key) for key in own_fields}, **label, "log_path": own["log_path"]}
                    for own in unseen], counts is not None
        git_head, git_branch = git_context(cwd)
        tree = {"cwd": cwd, "git_head": git_head, "git_branch": git_branch}
        return [{**parsed, **label, **tree, "log_path": None}], counts is not None
    records = []
    for log_path in logged:
        own, hooked = tcount_row(log_path, tail), hook_row(log_path, tail)
        if hooked or not own or (own.get("session_id") and session_id and own["session_id"] != session_id):
            continue
        # `cd other-tree && tcount ...`: tcount's counts and tree, not the session's cwd
        records.append({**{key: own.get(key) for key in own_fields}, **label, "log_path": log_path})
    with_log = [shown_counts.as_ledger_fields() for shown_counts, _ in logged.values() if shown_counts]
    taken = set(logged)
    not_before = call_started(payload)
    for fields, shown_exit in shown_runs(shown, with_log):
        own = quiet_row(fields, shown_exit, session_id, tail, taken, not_before)
        if own:
            taken.add(own["log_path"])
            records.append({**{key: own.get(key) for key in own_fields}, **label, "log_path": own["log_path"]})
    for own in unseen_rows(command, label["call_dirs"], session_id, tail, taken, not_before,
                           tcount_runs(command) - len(taken)):
        records.append({**{key: own.get(key) for key in own_fields}, **label, "log_path": own["log_path"]})
    return records, counts is not None


def _unquoted(word: str) -> str:
    return "".join(double or single or bare for double, single, bare in _WORD_PIECE.findall(word))


def _spelled(command: str, argv_text) -> bool:
    """True when the Bash command spells the words of a tcount row's cmd (its argv): every word
    with no variable or quote character in it, and there is at least one. A word is spelled when
    it is a whole shell word of the command, quotes aside: never a part of one (`tests` is not
    spelled by `tests/a.py`, nor `tests/a.py` by `tests/a.py::test_x`). A path word may also match
    a glob the command spells (`Tests/Tools/*.csproj`), or hold an absolute path whose last part
    is the last part of a word the command leaves to the shell (`$S/red.trx` in the command is
    `/tmp/x/red.trx` in the argv). A run of another test file is then not this call's (review of
    96be605d, finding 8); a word the ledger writer scrubbed occurs nowhere, and the row is left
    alone."""
    words = [word for word in str(argv_text or "").split() if not any(char in word for char in "$\"'`~*")]
    raw = re.findall(_SHELL_WORD, command)
    whole = {_unquoted(found) for found in raw}
    lasts = {found.rsplit("/", 1)[1] for found in whole if "/" in found and any(char in found for char in "$`~")}
    globs = [found for found in raw                                      # a path glob: `dir/*.csproj`, never `*`
             if "/" in found and found[0] not in "*?[\"'" and any(char in found for char in "*?[")]
    for word in words:
        if word in whole:
            continue
        if "/" not in word:
            return False
        if word.rsplit("/", 1)[1] in lasts - {""} and _HOLDS_ABSOLUTE.search(word):
            continue
        if not any(fnmatch.fnmatchcase(word, pattern) for pattern in globs):
            return False
    return bool(words)


def _tcount_argvs(command: str) -> set[str]:
    """The argv of each `tcount <words>` the command spells, as tcount records it (quotes removed,
    `$NAME` filled from a plain assignment in the command), where the text settles every word."""
    values: dict[str, str] = {}
    for step in _CALL_STEP.finditer(command):
        if step.group("name"):
            value = _literal(step.group("value"), values)
            values.pop(step.group("name"), None)
            if value is not None:
                values[step.group("name")] = value
    argvs = set()
    for words in _tcount_runs(command):
        settled = [_literal(word, values) for word in words]
        if settled and None not in settled:
            argvs.add(" ".join(settled))
    return argvs


def _tcount_runs(command: str) -> list[list[str]]:
    """The words after each `tcount` that starts a simple command, as typed (quotes kept for
    _literal), read with the shared tokeniser: a `tcount` inside quotes is text, a quoted
    operator stays inside its word. Unreadable quoting yields no run."""
    spans = token_spans(command, _TCOUNT_OPERATORS)
    if spans is None:
        return []
    runs: list[list[str]] = []
    words: list[str] | None = None
    for start, end, token in spans:
        if command[start] in _TCOUNT_OPERATORS:
            if words and token[0] in "<>" and words[-1].isdigit():
                words.pop()             # the descriptor of a redirection: `2>&1`
            if words:
                runs.append(words)
            words = None
            continue
        if words is not None:
            words.append(command[start:end])
        elif token == "tcount" and (start == 0 or command[start - 1] in " \t"
                                    or command[start - 1] in _TCOUNT_OPERATORS):
            words = []
    if words:
        runs.append(words)
    return runs


def _unsettled_cd(command: str) -> bool:
    """True when the command changes directory to a target its own text does not settle (a
    substitution, a glob, a name it never set: _literal), to `-`, or by pushd (a popd undoes it).
    Where its tcount ran is then unknown, and call_dirs may hold only directories it did NOT run
    in."""
    values: dict[str, str] = {}
    for step in _CALL_STEP.finditer(command):
        if step.group("name"):
            value = _literal(step.group("value"), values)
            values.pop(step.group("name"), None)
            if value is not None:
                values[step.group("name")] = value
        elif step.group("cd") and (_literal(step.group("cd"), values) in (None, "-")
                                   or "pushd" in command[step.start():step.start("cd")]):
            return True
    return False


def unseen_rows(command: str, dirs: list[str], session_id: str | None, tail: list[dict], taken: set[str],
                not_before: float, limit: int) -> list[dict]:
    """tcount's own rows for runs this call started and its output shows nothing of (a pipe after
    tcount kept neither the counts line nor the `log:` line; 1.3.4 field report, C: six such runs
    stayed unattributed and their counts were returned as unrecorded). A row is this call's when
    no hook row has it, no report of this call joined it, THIS session wrote it, it is not older
    than the call (call_started), it ran in a directory the call names (call_dirs: the payload's
    cwd, a cd target) and the command spells its argv (_spelled). At most `limit` rows, oldest
    first: the command's tcount segments less the runs already joined. A row whose argv IS one of
    those segments (_tcount_argvs) is taken before a newer one that is only spelled. In-process
    teammates share one session id, so the directory and the argv are what tell their calls apart:
    a command that changes directory to a target its text does not settle (_unsettled_cd) adopts
    nothing, because a peer's run in the payload's cwd would pass for its own."""
    if limit <= 0 or not isinstance(session_id, str) or not session_id or _unsettled_cd(command):
        return []
    real_dirs = {os.path.realpath(path) for path in dirs if isinstance(path, str) and os.path.isabs(path)}
    hooked = {row.get("log_path") for row in tail if row.get("source") == "hook"}
    exact = _tcount_argvs(command)
    found: list[tuple[int, dict]] = []
    for place, row in enumerate(reversed(tail)):
        log_path, cwd = row.get("log_path"), row.get("cwd")
        if row.get("source") != "tcount" or not isinstance(log_path, str) or not log_path \
                or log_path in taken or log_path in hooked or row.get("session_id") != session_id:
            continue
        written = _row_epoch(row)
        if written is None or written < not_before:
            continue
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or os.path.realpath(cwd) not in real_dirs \
                or not _spelled(command, row.get("cmd")):
            continue
        if log_path not in {own["log_path"] for _, own in found}:
            found.append((place, row))
    found.sort(key=lambda own: not (isinstance(own[1].get("cmd"), str) and own[1]["cmd"] in exact))   # stable
    chosen = sorted(found[:limit], key=lambda own: -own[0])
    taken.update(row["log_path"] for _, row in chosen)
    return [row for _, row in chosen]


def shown_runs(shown: list, with_log: list[dict]) -> list[tuple[dict, int | None]]:
    """(count fields, stated exit code) of each distinct counts line the output shows with no log
    line of its own. A line that repeats a report with a log (run-tests' closing summary) is that
    run again, and is left out: its run is joined by its log, or was left alone on purpose
    (already attributed, or another session's)."""
    seen = {tuple(fields.values()) for fields in with_log}
    fresh: dict[tuple, tuple[dict, int | None]] = {}
    for shown_counts, shown_exit in shown:
        fields = shown_counts.as_ledger_fields()
        if tuple(fields.values()) not in seen:
            fresh.setdefault((*fields.values(), shown_exit), (fields, shown_exit))
    return list(fresh.values())


def call_started(payload: dict, now: float | None = None) -> float:
    """The earliest epoch second this Bash call can have started: now minus the payload's
    duration_ms, or minus the Bash tool's longest foreground call when the frame carries no
    usable duration, less the slack for whole-second ledger stamps."""
    now = time.time() if now is None else now
    duration = payload.get("duration_ms")
    usable = isinstance(duration, (int, float)) and not isinstance(duration, bool) \
        and 0 <= duration <= BASH_MAX_SECONDS * 1000
    return now - (duration / 1000 if usable else BASH_MAX_SECONDS) - CLOCK_SLACK_SECONDS


def _row_epoch(row: dict) -> float | None:
    """The row's ts as epoch seconds (a stamp with no offset is UTC), or None when unreadable."""
    try:
        stamp = datetime.datetime.fromisoformat(row.get("ts"))
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def quiet_row(fields: dict, shown_exit: int | None, session_id: str | None, tail: list[dict],
              taken: set[str], not_before: float = 0.0) -> dict | None:
    """tcount's own row for a counts line shown without its log, or None: the newest row in the
    ledger's tail with the same runner, counts and exit code that no hook row has attributed, no
    report of this call has joined, that THIS session wrote, and that is not older than this
    call (not_before, from call_started). A counts line is text until a run stands behind it: a
    grep of an old log, a quoted report or a fixture has none, and used to become the caller's
    counted run (review R7; main recorded no run for it beside a real one). A row or a payload
    with no session id, and a row from before the call, are no run of this call either: such a
    row of another project, two days old, used to be joined (second review, finding 4)."""
    if not isinstance(session_id, str) or not session_id:
        return None
    hooked = {row.get("log_path") for row in tail if row.get("source") == "hook"}
    for row in reversed(tail):
        log_path = row.get("log_path")
        if row.get("source") != "tcount" or not isinstance(log_path, str) or not log_path \
                or log_path in taken or log_path in hooked:
            continue
        if row.get("count_parsed") is not True or row.get("exit_code") != shown_exit \
                or any(row.get(key) != value for key, value in fields.items()):
            continue
        if row.get("session_id") != session_id:
            continue
        written = _row_epoch(row)
        if written is None or written < not_before:
            continue
        return row
    return None


def ledger_tail() -> list[dict]:
    """The ledger's last TAIL_BYTES as rows, oldest first (read_tail without the count)."""
    return read_tail()[0]


def read_tail() -> tuple[list[dict], int]:
    """(rows, skipped): the ledger's last TAIL_BYTES as rows, oldest first, and how many whole
    lines in them could not be read. The line the window starts inside is cut by the seek, not
    damaged, so it is dropped and never counted."""
    from shared.ledger import parse_ledger_lines
    from shared.plugin_paths import ledger_path

    try:
        with open(ledger_path(create_dir=False), "rb") as handle:
            handle.seek(0, 2)
            start = max(0, handle.tell() - TAIL_BYTES)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return [], 0
    if start:
        data = data.partition(b"\n")[2]
    return parse_ledger_lines(data)


def tcount_row(log_path: str | None, tail: list[dict] | None = None) -> dict | None:
    """The row tcount wrote for this run (same log path), from the ledger's tail, or None.
    tcount records every run itself (a background run never reaches this hook); this hook's row
    adds the agent, and read_ledger keeps only this one of the pair."""
    return _tail_row(log_path, "tcount", ledger_tail() if tail is None else tail)


def hook_row(log_path: str | None, tail: list[dict]) -> dict | None:
    """This hook's earlier row for the same tcount run (a run is attributed once), or None."""
    return _tail_row(log_path, "hook", tail)


def _tail_row(log_path: str | None, source: str, tail: list[dict]) -> dict | None:
    if not log_path:
        return None
    for row in reversed(tail):
        if row.get("source") == source and row.get("log_path") == log_path:
            return row
    return None


def handle(payload: dict) -> str | None:
    """Record the run; return the JSON to print (the advisory) or None."""
    from shared.ledger import ledger_append
    from shared.runcounts import detect_runner, runs_tcount

    event = payload.get("hook_event_name")
    if event not in HANDLED_EVENTS or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    runner = detect_runner(command) if isinstance(command, str) else None
    if runner is not None and runs_tcount(command):
        runner = "tcount"       # a later segment's tcount report is read as one, whatever ran first
    if runner is None:
        return None
    records, count_parsed = build_records(payload, command, runner)
    for record in records:
        ledger_append(record)
    if count_parsed or runner == "tcount":
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": ADVISORY}})


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        output = handle(payload) if isinstance(payload, dict) else None
        if output:
            print(output)
    except Exception:  # noqa: BLE001  a ledger hook must never break a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
