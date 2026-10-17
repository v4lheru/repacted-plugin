# Acceptance and verification gates

Context: what the lead checks before accepting a teachback or a HANDOFF, before relaying anything to
the user, and when a teammate flags a risk. PACT's own acceptance mechanics (who completes, the
schemas, the wake signal) are in the persona and protocols; this is the judgment around them.

## Canonical rules
1. A teammate's SendMessage is not a finding. Reportable to the user: an accepted HANDOFF, a finished phase, a decision that is genuinely theirs, a hard blocker.
2. Before any status message to the user, all three must be yes: is it settled, does it change what they do or decide, and is it new or materially changed?
3. Settle before you speak: never publish a conclusion and its retraction in one session, and do not narrate agent-to-agent traffic ("the coder found X, I told the auditor Y").
4. Absolute words (never, always, all, none, zero) need an exhaustive check; otherwise give the scope inline ("0 in the 44 I scanned").
5. At teachback, read `least_confident_item` and `most_likely_wrong` as a map of where to demand evidence, never as reassurance the author has it covered.
6. For any flagged risk, ask for the discriminating case (what would show it real vs fine) and the blast radius; for a limit-shaped risk, test the largest instance you ship.
7. Loudness bounds damage per event, never the number of events: a loud failure on every item of a population is worse than a quiet one on a few.
8. When a green closes a risk someone raised, check the tested case could have gone red; record a case that could not have disagreed as UNEXERCISED.
9. After any catch, ask whether a WITNESS or a DETECTOR caught it, then list the variants with no witness; caught frequency measures detectability, not incidence.
10. At dispatch, verify that the measurement an acceptance criterion depends on can actually move, and that each named mitigation has been exercised; one grep each.
11. A deliverable that decides whether an irreversible action is permitted is an authorization control: commission an adversarial pass by a non-author AT DISPATCH.
12. When more than one agent has write access, or the tree is dirty at commit, accept only a result from an isolated build of the committed tree (repacted:concurrent-agent-git).
13. Identify every skip in an accepted test run; a skip count is the part of the report that says what was not tested.
14. When two or more lanes build against one contract, require a contract-reconcile checkpoint: each side diffs its mirror against the committed, named source. Both green is zero evidence.
15. Diff directives against artifacts, never the summary: grep the code for what you asked; an absent requirement is harder to spot than one done wrong.
16. When a lane reports its half verified, ask whether its check could have seen the other side of the join.
17. Demand the ROUTE, not the verdict, when someone argues a guard or branch is dead code; a tidy argument that a guard is unnecessary deserves more suspicion than a messy one.
18. A named risk in a teachback is diagnostic for you, never protective for its author; accept only with the test that settles it.
19. Persisting a conclusion is a higher bar than stating it: memory strips the hedges, and it is consulted when nobody is checking. Invite challenges to what you "verified" yourself.
20. Name your incentive before arguing a categorisation or a ruling it affects, and ask someone holding the data (not the incentive) to attack it.
21. For a rule written right after a defect, apply the every-branch test: would following it have helped on every branch, or only the one that happened? Bank the instance as fact, the generalisation as candidate.
22. Verify a repeated probe's method once before it loops, and clean up or announce operational litter in shared diagnostic surfaces (browser profiles, consoles, scratch tables).
23. Read what a run PRINTS, not a report file it may not have written; a reporter flag can silently disable the file you parse. `stat` any artifact against the run.
24. High risk is a class, not a judgement call: security code (auth, filters, redaction, sanitisers, guards), input validation and parsers, and shared contracts (core packages, public APIs, schemas) get adversarial boundary tests and ONE reviewer before the PR, whatever the lean defaults say.
25. Resolve every HANDOFF `uncertainty` item before you accept: fixed, or a known limit written into the PR. Never accept over one silently; an item that names a defect in the deliverable is a rejection.
26. Claude Code deletes every task file once all tasks are `completed`, HANDOFFs included. Before you complete the last open task, create the next task. A drained task is still readable: `repact-payload <id>` falls back to the session journal and says so in its header, so a verify brief may point at an accepted HANDOFF. A note you write on a completed task survives only as `amend_<n>`, `<resource>_grant` or `lead_<name>`.
27. Commit and push state in a HANDOFF is as of its write time ("not pushed" was true then). Read git (`git log origin/<branch>..<branch>`) before you repeat it in a PR summary or to the user.

## Detail

**The write-time checks.** Hooks run automatically on the teammate's `metadata.handoff`
write and on `git commit`. None asks a model: each decides from facts
(the run ledger, the HANDOFF's labels, the commit message). Modes (off, shadow, advisory, note,
gate) are set per check in the plugin's checks file (bin, then checks.json), not in code; in shadow mode a check
logs only and you see nothing. The `write_checks.py` hook RETURNS one thing to the teammate: a
commit message that breaks the commit rules (returned every time it is issued, 1.4.1). An
undisposed amend key (amendment_gate) and an `uncertainty` line with no `fixed:` /
`known-limit:` / `not-applicable:` disposition (uncertainty_disposition; one item per line) are
NOT returned since 1.4.1 (measured: 0 of 8 uncertainty returns and at most 3 of 7 amendment
returns led to a real change): the HANDOFF is stored, the teammate gets one `Note on #<id>` line,
and you get a `Note #<id>:` line per item. A HANDOFF item with a parsed count that is NO-RUN,
MISMATCH or EXIT is never returned either (1.4.1: returns pushed teammates to
re-run live probes only for a ledger entry): the teammate gets one `Note on #<id>` line and you
get an `Evidence #<id>:` line per item at acceptance. Claims whose run was recorded with no
parsed count reach you as one grouped line (`run recorded, count not parsed`) naming the logs to
check; prose with no parsed count is never flagged. At acceptance you see one line per return,
repeat and note, and you can always accept over a check; an acceptance with nothing found
injects nothing. A NO-RUN or MISMATCH against the run ledger is a fact. A flagged claim needs a
recorded run (`tcount`) or a rejection with the reason.

**`handoff-evidence` (manual look or replay).** Built over the same module the hooks use,
so a replay decides exactly what the hooks decided. Checked against its `--help` on 2026-10-03.
```
handoff-evidence handoff --team <T> --task <N> [--show]
handoff-evidence handoff <path to the task JSON> [--show]
```
It reads the task JSON (TaskGet does not return metadata) and checks each item of EVERY string or
list field of the HANDOFF except `open_questions` and `uncertainty` (speculative by construction):
the canonical fields first, then any other field, `reasoning_chain` last.
Per item it prints `<field>[i] <run check> <text>` under a header naming the mode and how
many ledger runs are in scope. The run check against the ledger is MATCH (a recorded run has the
claimed counts), MISMATCH (runs are recorded, none has them), NO-RUN (the ledger holds no run in
scope), NO-LEDGER (no ledger at all; never a flag by itself), NO-COUNT (it reports a test run but
states no count, e.g. only an exit code) or n/a (no count and no test run; a bare `638/638` counts as
638 passed). An item is flagged on MISMATCH, EXIT, UNREADABLE, NO-FILE or UNDECIDED, on a cited
file that is missing, or on a parsed count that is NO-RUN. `--cwd` and `--branch` narrow which
ledger runs are in scope. Exit codes: 0 no flag, 1 flagged, 2 usage, 4 task not found. Nothing
here is written back into the HANDOFF.

**Before accepting any HANDOFF,** also read the task file's keys (a correction can sit in a sibling
key, repacted:pact-field-notes `task-store-and-harvest.md`) and diff it against every directive you
sent after dispatch (rulings-and-relays.md).

**Completion authority** is PACT's rule and stays with the lead; it is not restated here.

**A monitor that has never fired is silence.** A monitor, alert or differ whose degraded state looks
like its healthy state ("no change" with no baseline) reports nothing either way; ask what it reports
when its input is missing, and check its own run history. Deeper method: repacted:verification-discipline.

## Examples
- "Verify strict mode works" was closed on the 13-criterion matrix; the product's other matrix had
  15 and every one of its inputs failed in production with a grammar-size error.
- "`total_cost_usd` must be non-zero" was set as proof of a fix; the writer bound a literal 0.0,
  so 180 runs could never have moved it.
- An auto-claim bug was caught twice, both times by the agent it misattributed; the variant under
  the correct owner had no witness at all.
- A committed tree verified with `tsc` and 301 tests in a shared worktree failed with three errors
  when extracted with `git archive`; a peer's uncommitted edits had propped it up.
