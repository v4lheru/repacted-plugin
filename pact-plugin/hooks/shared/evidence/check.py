"""The checks themselves: item_evidence per item, evaluate_handoff (the CLI verdict)
and handoff_gate (the write-time notes).

Location: pact-plugin/hooks/shared/evidence/check.py, split out of the package's
__init__, which re-exports every name here; callers import shared.evidence.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from shared.clock import utc_now_iso
from shared.plugin_paths import ledger_path
from shared.scrub import scrub_text
from shared.runcounts import _RUNNER_PATTERNS
from shared.evidence.claims import (SEVERITY, _has_counts, _nonzero, _runner_label, claim_marks,
                        claim_readings, every_count, failed_exits, flag_item, gated_fields, handoff_items,
                        reports_test_run, run_check, uncertainty_items, unparsed_only,
                        unreadable_count)
from shared.evidence import files as _files
from shared.evidence.ledger import (_command_key, _inside, _run_end, _write_gate_state, cited_runs, cited_total,
                        claim_time, claimant_session, newest_runs, project_root, read_gate_state,
                        read_runs, recorded_elsewhere, recorded_run_ids, runs_in_scope, superseded,
                        use_mode, written_at)
from shared.evidence.files import NO_AGE_LIMIT, RED_FILE, ResultFiles, edited_after_run, result_files
from shared.evidence.notes import UNPARSED_SHOWN, _exited, _flag, _mismatch, _undecided, _unread, unparsed_line

EXCERPT_CHARS = 140
# Why a count is a note for the lead and no flag (item_evidence, the field report of 2026-10-06).
OTHER_TREE = "its only counted runs ran in another tree (a scratch copy), which is not the tree of this claim"
RED_RUN = "a count reported as red by design (a revert or mutation check) that no recorded run has"
EARLIER_COUNT = "an earlier count beside a final count the latest run has; no recorded run has the earlier one"
PARTIAL_COUNT = ("a package-script run (runner {runner}) can hold several reports and tcount counted fewer tests "
                 "than claimed: read its log")
# Runners that start other runners: one run can print several summaries, of which tcount's line
# may hold one (finding 6: the root line of a monorepo read one package).
WRAPPER_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun", "npx", "turbo", "nx", "lerna", "make", "just"})


def _other_tree(run: dict, project: Optional[str], pads: list[str], seen: dict) -> bool:
    """True when a run ran outside the tree a claim of this task is about: inside the session's
    scratchpad, or outside the project and in no checkout or linked worktree of it. Never when
    the project is itself in that scratchpad, or when the project or the run's directory is
    unknown. seen: the answer per directory (project_root reads the file system)."""
    cwd = run.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not project:
        return False
    if cwd not in seen:
        in_pad = any(_inside(cwd, pad) and not _inside(project, pad) for pad in pads)
        seen[cwd] = in_pad or (not _inside(cwd, project) and project_root(cwd) != os.path.realpath(project))
    return seen[cwd]


def _clean(run: dict) -> bool:
    return run.get("exit_code") == 0 and not run.get("failed") and not run.get("errors")


TEST_RUNNERS = frozenset(name for name, _ in _RUNNER_PATTERNS)     # a count-less run of one is no exit-only tool


def _exit_only(claims: list[dict], cited: list[dict]) -> bool:
    """Every claim states zero failures or errors and nothing else, and every run the item cites
    is this task's recorded run of a tool that prints no test count (tsc -b, eslint), exited 0:
    the recorded exit code is what such a claim can be checked against. A pass
    count, a test runner's uncounted run, a non-zero exit or an uncited run is not this."""
    def countless(run: dict) -> bool:
        tool = run.get("runner")
        return not run.get("count_parsed") and run.get("exit_code") == 0 and isinstance(tool, str) \
            and bool(tool.strip()) and tool not in TEST_RUNNERS
    return bool(claims) and bool(cited) and all(countless(run) for run in cited) \
        and all(claim and set(claim) <= {"failed", "errors"} and not any(claim.values()) for claim in claims)


def _names(text: str, run: dict) -> bool:
    """True when the item's text names the run: its runner as a word, or the command tcount ran."""
    said = " ".join(text.lower().split())
    runner, cmd = run.get("runner"), run.get("run_cmd")
    if isinstance(runner, str) and runner.strip() and re.search(rf"(?<![\w-]){re.escape(runner.lower())}(?![\w-])", said):
        return True
    return isinstance(cmd, str) and bool(cmd.strip()) and " ".join(cmd.lower().split()) in said


def _own_runs(text: str, readings: list, counted: list[dict], standing: list[dict]) -> list[dict]:
    """The standing runs a count of this item can be about: those of the command or runner of
    a run the item names, of a counted run that has one of its counts, or of a counted run of
    another tree. A red run of an unrelated command contradicts none of the item's counts
    (review of 2026-10-07, second pass, FIX 5). An item that points at no run at all is about
    every standing run: the safe side."""
    there = {id(run) for run in standing}
    anchors = [run for run in counted
               if id(run) not in there or _names(text, run)
               or any(_has_counts(run, reading) for both in readings for reading in both)]
    if not anchors:
        return standing
    commands = {_command_key(run) for run in anchors} - {None}
    runners = {run.get("runner") for run in anchors} - {None}
    return [run for run in standing if _command_key(run) in commands or run.get("runner") in runners]


def item_evidence(text: str, records: list[dict], scope: list[dict], ledger_exists: bool,
                  team: Optional[str], task_id: str, owner: Optional[str],
                  since: Optional[float], session: Optional[str] = None,
                  project: Optional[str] = None, files: Optional[ResultFiles] = None) -> dict:
    """The facts for one item: {"run_check", "ran", "missing", "counts", "unparsed",
    "unparsed_runners", "exited", "ambiguous", "backing", "cited"}. ambiguous: the short stamps it
    cites that more than one recorded log has (cited_runs). backing: for each count a recorded run
    backs, the newest run that has it (handoff_gate compares its end with the worktree's edits);
    cited: the recorded runs it cites, this task's (a later one of them covers those edits).

    run_check: the worst outcome over its claimed counts, each matched against this task's
    cited runs and runs in scope (MISMATCH only when a count matches none of them); an EXACT
    citation the ledger lacks makes it NO-RUN, a count it cannot read exactly UNREADABLE. ran: a
    recorded run backs the item (it cites one that exists, or the task has runs in scope) and it
    cites nothing missing; a count-free item that summarises such runs is not NO-RUN (lead
    ruling). counts: the claimed counts; only a counted run of this task backs them.
    unparsed: when the item claims counts and this task's runs (the cited ones, else those in
    scope) were all recorded with no parsed count, where each can be read: its log path, or
    "no log kept"; unparsed_runners names their runners (with a non-zero exit). Such an item is
    still NO-RUN and still flags, but its sender cannot fix a count the wrapper did not parse:
    it goes to the lead in one grouped line and is never returned (unparsed_only).
    Not so when the item claims only zero failures or errors and cites runs of tools that print
    no count, each exited 0 (_exit_only): the exit code backs it, MATCH.
    The path is the LEDGER's: a cited log is never opened. exited: the exit codes behind an
    EXIT outcome (failed_exits).

    Which run backs a count: a run the item CITES, by its own
    counts (an intended red run cited as red is a match), else a run in scope that no later run of
    the same command in the same tree superseded. A count only a superseded run has is a MISMATCH
    with "stale" set. A count no run backs is then put to the result files the item cites (files,
    ResultFiles): MATCH when a readable one has it; NO-FILE when it cites an absolute result-file
    path that does not exist; UNDECIDED (a note to the lead, never a return) when it cites
    something the check cannot decide ("file_notes" says what); MISMATCH when every cited result
    file was read and none has it.

    After the review of 96be605d (lead rulings on its findings 1 to 7): a claim with two readings
    (claim_readings) matches when either has a run or a file. A stale claim stays a MISMATCH
    whatever else the item cites: UNDECIDED never overrides it. It is not stale when ANOTHER claim
    of the item, or any pass or fail count in it (every_count), is the latest run's of that command
    and not the older run's: a before/after report states both, however it is written, and needs
    no log. A count both runs have clears nothing. A clean claim that only result files
    holding failures or errors have is EXIT, as over a run that exited non-zero.

    A total: a count no single run has matches when it is the sum of the runs the item cites
    (cited_total: two or more, each this task's own counted run, one per command and tree), and
    a clean total over a cited run that exited non-zero is EXIT.

    The field report of 2026-10-06 (findings 6, 10, 13, 27; amend_1 on its task). None of these
    lets a count that contradicts a run of its own tree pass:
      - A run in ANOTHER TREE (_other_tree: a scratch or mutated copy) can back a count the item
        states, and never contradicts one: with no other counted run the claim is NO-RUN
        (OTHER_TREE), not MISMATCH. A run the item cites always counts.
      - A count reported as RED BY DESIGN (claim_marks) matches any run in scope that has it, a
        superseded one too (the red run before the last green one); with no such run it is
        UNDECIDED (RED_RUN), a note for the lead. A CLEAN count that is red only by its
        neighbour ("74 passed" in front of "2 failed") is read so only while every standing run
        of the item's own command or runner (_own_runs) is clean: beside a red one it is a
        green claim, and a MISMATCH.
      - An EARLIER count in front of the item's last claim, which a standing run of this tree
        has, when its sentence says it came earlier or the last one's that it came later
        (claim_marks), is UNDECIDED (EARLIER_COUNT) when no run has it and every standing run
        of this tree is clean: "72 passed first, then the mutation check: 70 passed, 2 failed"
        over a red latest run is a MISMATCH (review of 2026-10-07, FIX 3). A count only an
        older run has stays stale.
      - A clean count LARGER than the count of a clean run a package script made
        (WRAPPER_RUNNERS), while every standing run of this tree is clean, is UNDECIDED
        (PARTIAL_COUNT): tcount's line may hold one of several reports."""
    readings = claim_readings(text)
    counts = [reading[0] for reading in readings]
    extra = {"stale": False, "file_notes": [], "absent": [], "ambiguous": [], "backing": [], "cited": []}
    if not ledger_exists:
        return {"run_check": run_check(text, [], False), "ran": False, "missing": [], "counts": counts,
                "unparsed": [], "unparsed_runners": [], "exited": [], **extra}
    own, other, missing, extra["ambiguous"] = cited_runs(text, records, team, task_id, owner, since, session,
                                                         project, scope)
    extra["cited"] = own
    cited = [run for run in own if run.get("count_parsed")]
    in_scope = [run for run in scope if run.get("count_parsed")]
    old = superseded(in_scope)
    counted = [*cited, *[run for run in in_scope if id(run) not in old]]
    pads = _files.session_scratchpads(session) if session else []
    trees: dict = {}
    standing = [*cited, *[run for run in in_scope if id(run) not in old
                          and not _other_tree(run, project, pads, trees)]]     # the runs that can contradict
    exit_backed = not counted and _exit_only(counts, own)
    uncounted = [] if counted or not counts or exit_backed else (own or scope)
    unparsed = list(dict.fromkeys(str(run.get("log_path") or "no log kept") for run in uncounted))[:UNPARSED_SHOWN]

    def backed(idx: int, sources: list[dict]) -> bool:
        """True when a source has the counts of claim idx, by either reading; the claim is then
        the reading that matched."""
        for reading in readings[idx]:
            if any(_has_counts(source, reading) for source in sources):
                counts[idx] = reading
                return True
        return False

    own_runs = _own_runs(text, readings, counted, standing)     # the standing runs that can contradict THIS item
    outcomes = [("MATCH" if backed(idx, counted) else "MISMATCH" if own_runs else "NO-RUN") if counted else "NO-RUN"
                for idx in range(len(counts))]
    if exit_backed:
        outcomes = ["MATCH"] * len(counts)      # "0 errors" over cited tsc and eslint runs that exited 0
    elsewhere = bool(counted) and not own_runs and "NO-RUN" in outcomes
    known = {id(run) for run in (*scope, *own)}         # ledger order: newest_runs reads it
    newest, _old = newest_runs([run for run in records if id(run) in known and run.get("count_parsed")])
    total, added = cited_total(own, other, missing, extra["ambiguous"], newest)
    summed = {idx for idx in range(len(counts)) if total and outcomes[idx] != "MATCH" and backed(idx, [total])}
    for idx in summed:
        outcomes[idx] = "MATCH"
    says_failure = any(claim.get("failed") or claim.get("errors") for claim in counts)   # anywhere in the item
    exited = []
    for idx, claim in enumerate(counts):
        codes = failed_exits(claim, counted) if outcomes[idx] == "MATCH" and not says_failure else []
        if idx in summed and not says_failure:       # a clean total over a run that exited non-zero
            codes = sorted({run.get("exit_code") for run in added if _nonzero(run.get("exit_code"))})
        outcomes[idx] = "EXIT" if codes else outcomes[idx]
        exited += codes
    open_claims = [idx for idx, outcome in enumerate(outcomes) if outcome in ("MISMATCH", "NO-RUN")]
    stale = [idx for idx in open_claims if backed(idx, in_scope)]
    latest = {_command_key(run): run for run in in_scope if id(run) not in old}
    every = every_count(text) if stale else []
    for idx in list(stale):
        said = [reading for other, both in enumerate(readings) if other != idx for reading in both] + every
        for older in [run for run in in_scope if id(run) in old and _has_counts(run, counts[idx])]:
            newer = latest.get(_command_key(older))
            if newer and any(_has_counts(newer, reading) and not _has_counts(older, reading) for reading in said):
                outcomes[idx] = "MATCH"         # the item also reports the run that replaced it
                stale.remove(idx)
                open_claims.remove(idx)
                break
    if files is not None and open_claims:
        seen = files.cited(text)
        extra["absent"] = seen["absent"]
        red_file = False
        results = [dict(result, exit_code=1 if result.get("failed") or result.get("errors") else 0)
                   for result in seen["counters"]]
        for idx in open_claims:
            if backed(idx, results):
                red = [] if says_failure else failed_exits(counts[idx], results)
                outcomes[idx] = "EXIT" if red else "MATCH"
                red_file = red_file or bool(red)
            elif seen["absent"]:
                outcomes[idx] = "NO-FILE"
            elif seen["undecided"] and idx not in stale:
                outcomes[idx] = "UNDECIDED"
            elif seen["counters"]:
                outcomes[idx] = "MISMATCH"
        left = {outcomes[idx] for idx in open_claims}
        extra["file_notes"] = seen["undecided"] if "UNDECIDED" in left else \
            seen["unread"] if left - {"MATCH", "EXIT"} else [RED_FILE] if red_file else []
    notes = [OTHER_TREE] if elsewhere and "NO-RUN" in outcomes else []
    marks = claim_marks(text)
    last = len(counts) - 1
    final = last > 0 and outcomes[last] == "MATCH" and any(_has_counts(run, counts[last]) for run in standing)
    all_clean = all(_clean(run) for run in own_runs)    # no run of the item's command contradicts a clean count
    for idx in [idx for idx, outcome in enumerate(outcomes) if outcome in ("MISMATCH", "NO-RUN")]:
        claim, why = counts[idx], None
        partial = [run for run in standing if run.get("runner") in WRAPPER_RUNNERS and isinstance(run.get("passed"), int)
                   and isinstance(claim.get("passed"), int) and run["passed"] < claim["passed"]]
        if marks[idx]["red"] and (claim.get("failed") or claim.get("errors") or all_clean):
            why = None if backed(idx, in_scope) else RED_RUN
            outcomes[idx] = "UNDECIDED" if why else "MATCH"
        elif idx in stale:
            continue
        elif final and all_clean and idx < last and (marks[idx]["earlier"] or marks[last]["later"]):
            why = EARLIER_COUNT
        elif partial and not claim.get("failed") and not claim.get("errors") and all_clean \
                and all(_clean(run) for run in partial):
            why = PARTIAL_COUNT.format(runner=partial[0]["runner"])
        if why:
            outcomes[idx] = "UNDECIDED"
            notes.append(why)
    extra["file_notes"] = list(dict.fromkeys([*notes, *extra["file_notes"]]))
    extra["stale"] = any(outcomes[idx] == "MISMATCH" for idx in stale)
    for idx, claim in enumerate(counts):
        having = [run for run in counted if _has_counts(run, claim)] if outcomes[idx] == "MATCH" else []
        if having:
            extra["backing"].append(max(having, key=lambda run: _run_end(run) or 0.0))
        elif idx in summed and outcomes[idx] == "MATCH":
            extra["backing"].append(min(added, key=lambda run: _run_end(run) or 0.0))   # the oldest run of the sum
    if missing:
        outcomes.append("NO-RUN")
    if unreadable_count(text):
        outcomes.append("UNREADABLE")
    if outcomes:
        outcome = max(outcomes, key=SEVERITY.get)
    else:
        outcome = "NO-COUNT" if reports_test_run(text) else "n/a"
    return {"run_check": outcome, "ran": not missing and bool(own or other or scope), "missing": missing,
            "counts": counts, "unparsed": unparsed, "unparsed_runners": sorted({_runner_label(r) for r in uncounted}),
            "exited": sorted(set(exited)), **extra}


def evaluate_handoff(task: dict, team: Optional[str], *, cwd: Optional[str] = None,
                     branch: Optional[str] = None, ledger: Optional[Path] = None,
                     session_id: Optional[str] = None) -> dict:
    """Verdict for the task's metadata.handoff. Never raises on content problems."""
    task_id = str(task.get("id"))
    base = {"kind": "handoff", "team": team, "task_id": task_id, "ts": utc_now_iso(),
            "mode": use_mode("handoff_evidence"), "items": [], "flagged": 0}
    handoff = (task.get("metadata") or {}).get("handoff")
    if not isinstance(handoff, dict):
        return dict(base, stop="no metadata.handoff")
    items = handoff_items(handoff, gated_fields(handoff))
    if not items:
        return dict(base, stop="no gated field content")
    ledger_file = Path(ledger) if ledger else ledger_path(create_dir=False)
    ledger_exists = ledger_file.is_file()
    records, skipped = read_runs(ledger_file) if ledger_exists else ([], 0)
    since, owner = claim_time(team, task) if team else None, task.get("owner")
    session, project = claimant_session(session_id, team), project_root(cwd)
    scope = runs_in_scope(records, team, task_id, cwd, branch, since, owner, counted=False, session=session)
    runs = [run for run in scope if run.get("count_parsed")]
    files = result_files(records, team, task_id, owner, since, cwd, session, ledger_file.parent,
                         written_at(team, task_id) or NO_AGE_LIMIT)       # the write-time check applied the limit
    files.prime([item["text"] for item in items])
    for item in items:
        item.update(item_evidence(item["text"], records, scope, ledger_exists, team, task_id, owner, since,
                                  session, project, files))
    for item in items:
        item["flag"] = flag_item(item)
        item["text"] = scrub_text(item["text"])[:EXCERPT_CHARS]
    return dict(base, items=items, flagged=sum(i["flag"] for i in items), runs_in_scope=len(runs),
                ledger="present" if ledger_exists else "no ledger", ledger_skipped=skipped)


# ---------------------------------------------------------------------------
# The write-time checks (D30, 2026-09-29): facts decide
# ---------------------------------------------------------------------------
# A check RETURNS work to its sender (a PreToolUse deny with the reason) only when it cannot be
# wrong or gamed: the commit rule (shared/commit_rules.py, every time) and amendment_gate's enforce
# mode (each key once). uncertainty_disposition is NOT a return since 1.4.1 U11 (0 of 8 measured
# returns led to a real change; checks.json _notes): an item with no leading disposition is a
# note (uncertainty_note). handoff_evidence is NOT a return since 1.4.1 (a design rule):
# a count with no recorded run in scope (NO-RUN), with counts no recorded run has
# (MISMATCH) or a clean count over a failed run (EXIT) is a note: the HANDOFF is stored, its
# writer gets one line (evidence_note) and the lead the item lines at acceptance. The lead can
# always accept over a check; everything is logged.

NOT_RUN = re.compile(r"\b(not run|not (?:yet )?(?:tested|executed|verified)|untested|unverified|"
                     r"did ?n[o']t run|could ?n[o']t run|can ?not run|no (?:test )?run|by (?:inspection|reading))\b",
                     re.I)


def handoff_gate(task: dict, team: Optional[str], handoff: dict, *, cwd: Optional[str] = None,
                 ledger: Optional[Path] = None, session_id: Optional[str] = None,
                 now: Optional[float] = None) -> dict:
    """The handoff_evidence gate for a HANDOFF about to be written.

    Returns {"flagged": [{key, where, text, line}], "advisories": [...]}: an item is flagged
    when it CLAIMS something ran (a parsed count), does not say it was not run, and no recorded
    run of this task backs it: none in scope or cited (NO-RUN), a cited run that exists nowhere
    (NO-RUN), or counts none of its cited or in-scope runs has (MISMATCH). A count it cannot read
    exactly is UNREADABLE and flags; a clean count that only runs that exited non-zero have is
    EXIT and flags. 'not run' wording exempts only an item that states no count (J5). Prose
    with no parsed count is never flagged.
    A cited run is missing for the SENDER only when it exists nowhere: no ledger row and no log
    file in the runs directory has its id (recorded_run_ids). One that exists outside this
    task's scope (another team, session or project, or older than the claim) is a note for the
    lead, and the item is checked as if that citation were not written: the old gate returned
    nine real logs of one reviewer for it.
    An item whose only problem is a run recorded with no parsed count is NOT flagged: its sender
    cannot fix it. advisories carries the lines for the lead: that one grouped line
    (unparsed_line), then the out-of-scope citations, the short stamps more than one log has
    (cited_runs), the counts the check cannot decide (UNDECIDED, item_evidence) and, once per
    HANDOFF, a file changed after the run that backs a count (edited_after_run).
    FLAGGED, in short: a count that contradicts the latest run, a
    count with no run in scope and no readable cited result file, a cited run or result file that
    exists nowhere, a count it cannot read. Whatever it cannot decide is an advisory. Since 1.4.1
    a flagged item is a note too, never a return (hooks/write_checks.py): the flag only
    decides which list the lead's line goes in and whether the writer gets its one line.
    `uncertainty` is not read for counts or runs at all, whatever the question set excludes: the
    lead disposes of every uncertainty line by hand, and a count quoted there is often a stale
    value named as one, not a run claim (1.3.5 noted such a line as a
    MISMATCH). The paths it names still place a bare result-file name (ResultFiles.prime), and
    the rule that each line opens with its disposition is uncertainty_gate's.
    An item that states only the exit code of a command tcount cannot count (`tsc -b exit 0`,
    exit_only_clause) claims no run: it is never flagged.
    now: when this write happens, for the age of a cited result file; the clock, unless a replay
    of an old write passes that write's time. A live write records its time in the gate state
    (written_at), where acceptance reads it; a replay records nothing.
    """
    task_id = str(task.get("id"))
    if now is None:
        state = read_gate_state(team, task_id)
        state["handoff_written"] = time.time()
        try:
            _write_gate_state(team, task_id, state)
        except OSError:
            pass
    fields = tuple(field for field in gated_fields(handoff) if field != "uncertainty")
    items = handoff_items(handoff, fields)
    ledger_file = Path(ledger) if ledger else ledger_path(create_dir=False)
    records, skipped = read_runs(ledger_file) if ledger_file.is_file() else ([], 0)
    since, owner = claim_time(team, task) if team else None, task.get("owner")
    session, project = claimant_session(session_id, team), project_root(cwd)
    scope = runs_in_scope(records, team, task_id, cwd, None, since, owner, counted=False, session=session)
    files = result_files(records, team, task_id, owner, since, cwd, session, ledger_file.parent, now)
    files.prime([item["text"] for item in items] + uncertainty_items(handoff))
    run_ids: list[set] = []             # every run that exists, listed once and only if a citation needs it

    def facts(text: str) -> dict:
        found = item_evidence(text, records, scope, True, team, task_id, owner, since, session, project, files)
        if found["missing"] and not run_ids:
            run_ids.append(recorded_run_ids(records, ledger_file.parent))
        outside = recorded_elsewhere(found["missing"], run_ids[0]) if found["missing"] else []
        if outside:
            rest = text
            for citation in outside:
                rest = rest.replace(citation, " ")
            found = item_evidence(rest, records, scope, True, team, task_id, owner, since, session, project, files)
        return dict(found, outside=outside)

    for item in items:
        item.update(facts(item["text"]))
    flagged, held = [], []
    for item in items:
        where, what = f"{item['field']}[{item['idx']}]", None
        if item["outside"]:
            held.append(_flag(where, item["text"], "cites a run recorded outside this task's scope, not returned "
                                                   f"({', '.join(item['outside'][:3])})")["line"])
        if item["ambiguous"]:
            held.append(_flag(where, item["text"], "cites a run by a short stamp that more than one recorded log "
                                                   f"has, not returned ({', '.join(item['ambiguous'][:3])}: the "
                                                   "full log name tells them apart)")["line"])
        if NOT_RUN.search(item["text"]) and not item["counts"] and item["run_check"] != "UNREADABLE":
            continue
        if item["run_check"] == "MISMATCH":
            what = _mismatch(item)
        elif item["run_check"] == "UNREADABLE":
            what = "states a count that cannot be checked exactly (UNREADABLE: write it in digits as tcount printed it)"
        elif item["run_check"] == "EXIT":
            what = f"claims a clean count over a failed run (EXIT: {_exited(item)})"
        elif item["run_check"] == "NO-FILE":
            what = f"cites a result file that does not exist (NO-FILE: {', '.join(item['absent'][:3])})"
        elif item["missing"]:
            what = ("cites a run the ledger does not hold and no log file has "
                    f"(NO-RUN: {', '.join(item['missing'][:3])})")
        elif item["run_check"] == "UNDECIDED":
            held.append(_flag(where, item["text"], _undecided(item))["line"])
            continue
        elif unparsed_only(item):
            continue            # the sender cannot fix an unparsed count: the lead's line (advisories)
        elif item["counts"] and item["run_check"] != "MATCH":
            what = f"claims a run-time result with no recorded run in scope (NO-RUN{_unread(item)})"
        if what:
            flagged.append(_flag(where, item["text"], what))
    # A later run that uncertainty cites ("fixed: ..., rerun 214500Z-94460") covers edits too; it
    # is never read for counts or returns.
    named_in_uncertainty = cited_runs(" ".join(uncertainty_items(handoff)), records, team, task_id, owner, since,
                                      session, project, scope)[0] if records else []
    edited = edited_after_run(items, time.time() if now is None else now, tuple(named_in_uncertainty))
    if edited:
        held.append(edited)
    grouped = unparsed_line(items)
    return {"flagged": flagged, "advisories": ([grouped] if grouped else []) + held,
            "ledger_skipped": skipped}
