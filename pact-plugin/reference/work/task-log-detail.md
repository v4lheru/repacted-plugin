# Work: task log detail

> Loaded on demand from [task-log.md](task-log.md) when the rule behind a step is in question: why the writes split as they do, how the acceptance bridge closes tasks, every exit-code branch in full, the field rules, corrupt lines, and where the log lives. The two tables (Step 3 verdicts, boundary writes) live only in task-log.md, because tests compare them with the code. Design: the design notes.

## What this command is for

The task log holds **what the user intends to do next**, in order, with the
dependencies between tasks. It is not a view of the tracker, and nothing here
syncs the two. An item enters it by a deliberate act: the user asks, or an
import the lead reviewed.

You are the **sole writer** of status and intent. The user never hand-edits the
log, so their only view of it is what you report and the generated views
(`docs/backlog.md`, `docs/progress.md`, `docs/release-gates.json`). Reconcile
before you steer.

## Step 1 — Update what you already know

**This is the main job, not a preamble.** You have been at the work. Statuses you
WITNESSED changing are facts, and you write them without asking:

```bash
python3 "{plugin_root}/bin/repact-task" set <ref> --status <status>
```

Run the command rather than editing the file: it takes the lock, replays the
log, validates the event against the current state and appends ONE line. A line
written any other way skips the cycle, evidence, credential and path rules, and
nothing reports that.

Two writes need no permission, and ONLY these two:

- **A `status` transition you WITNESSED** — you saw the PR merge, you started the
  work, you raised or cleared the blocker.
- **Anything you are TRANSCRIBING from the user.** Recording their words is not
  deciding for them. This covers `add`, and it covers `drop`: the user says they
  are not doing something, and you write it down.

`drop` is transcription and NEVER inference. You never WITNESS a decision not to
do something — only the user can supply it. "We're not doing the rate-limit work
after all" is a `drop` you apply unasked. A task nobody has touched for a
fortnight is NOT dropped: that is the staleness flag, it is your inference, and
it ASKS.

`done` is never a plain status write. It needs at least one piece of evidence
that verifies (`commit:<sha>`, `merge:<sha>`, `tag:<name>`, `doc:<path>@<sha>`,
or `run:<log name>` matched to the run ledger), so `set --status done` is
refused and `done <ref> --evidence ...` is the verb.

Everything else asks. Report every write you made — silent correctness is
indistinguishable from silent breakage.

## The acceptance bridge

When the lead accepts a work task that carries `metadata.project_task` with
`repact accept`, the acceptance bridge closes that task on the HANDOFF's
`evidence` object (else the run ledger's record for that task), and
records a rejection's revision without closing anything. Only the lead's acceptance does this; a teammate's own
completion never closes a task. An acceptance with nothing that verifies stays
open, flagged "accepted without evidence", until someone runs `done` with real
evidence. `sync` records any lead acceptance the hook missed, and every read
command runs it first.

## Step 2 — Reconcile and report

```bash
python3 "{plugin_root}/bin/repact-task" next
python3 "{plugin_root}/bin/repact-task" blocked
```

`next` lists the ready tasks in order (priority, then rank, then age) and the
active count; `--all` shows every ready task. `blocked` lists what cannot start:
waiting on an open dependency or an open child, stuck on a dropped one, or
blocked by hand. `show <ref>` gives one task's links, evidence, acceptances,
rejections and flags; `fsck` gives corrupt lines, conflicts, concurrent edits
and evidence that no longer verifies.

Read the output and report to the user in plain language:

1. What is `active` right now.
2. The next two or three ready tasks.
3. What is blocked, and on what.
4. Every flag, with what you think it means.

**Never cite a ref to the user.** Refs exist so relations survive a retitling;
they mean nothing to a person. Refer to tasks by title.

Exit code 1 comes only from `sync --check`, and it means a lead-attributed
acceptance is missing from the log: a defect, not a to-do. Run `sync` and report
what it wrote. Completions that are listed but not attributable exit 0: they are
visible, and a human closes them with `done --evidence`.

Exit code 64 is a USAGE error: the command line was malformed, so the tool never
ran. Nothing was read or written. Fix the invocation the message names and run
it again; do NOT report it as a refusal.

Exit code 65 is a REFUSAL: the log is readable and nothing was written. Report
what the message names and fix that: a cycle names its path, missing evidence
names the unverified items, a credential names only its kind.

Exit code 66 means this repo has no task log. Never create one unasked:
/repacted:bootstrap offers `init` once per project and records the answer.

Exit code 74 means the log could not be read (a permission bit, a directory at
the path). Report the access problem and stop.

ANY OTHER exit code — 0, 1, 64, 65, 66 and 74 are the only ones the tool
chooses, so anything else came from something that is not the tool. The usual
cause is that the tool never started: a wrong path or working directory makes
the interpreter exit 2 before any of our code runs. Check the invocation and the
directory, and distrust any diagnosis of the log drawn from such a run,
including your own.

## Step 4 — Write what the user decides

```bash
python3 "{plugin_root}/bin/repact-task" add "<title>" --priority P1 --rank 2 --owner lead --tag gate
python3 "{plugin_root}/bin/repact-task" dep <ref> --on <ref>
python3 "{plugin_root}/bin/repact-task" link <ref> --output doc:docs/plan.md
python3 "{plugin_root}/bin/repact-task" start <ref> --ptask <team>/<task id>
python3 "{plugin_root}/bin/repact-task" reopen <ref> --note "the fix regressed"
python3 "{plugin_root}/bin/repact-task" drop <ref>
```

Field rules the writer enforces; a violation is REFUSED with nothing written
rather than quietly adjusted:

- a title is one line of 80 characters or fewer; a `note` is 500 or fewer.
  Anything longer goes in a linked doc or in pact-memory.
- `note` is written in YOUR voice, never in the user's first person. A relay in
  the user's first person gains an authority it never had and no later reader
  can strip it off. Where the distinction carries weight, mark it: `user
  ruled:` or `inferred:`.
- a dependency or a parent that would form a cycle is refused, naming the path.
- a `doc:` path is repo-relative with no `..`; a `kb:` path must resolve inside
  the KB root.
- credential text in any field is refused, naming only its kind.
- a parent cannot close while a child is open.

### Where the report happens, and which files carry it

**THREE FILES carry a task report**: `bootstrap.md`, `wrap-up.md` and
`work.md`. Naming the files rather than the occasions is deliberate — a file is
checkable, an occasion is not.

A report belongs where the user is choosing WHAT TO DO NEXT: at session start
(where the `tasks:` line already says it), before the session decision, and in
this command. It does not belong where an already-chosen task is being
executed, or where one artifact is being judged — those are occasions to write
a witnessed transition, not to re-read the list.

### When the writes happen, and which they are

**FOUR FILES carry boundary writes**: `orchestrate.md`, `comPACT.md`,
`decide.md` and `wrap-up.md`. Naming the files rather than the occasions is
deliberate — a file is checkable, an occasion is not.

**BOUNDARY WRITES COVER WITNESSED TRANSITIONS; RECONCILIATION COVERS UNWITNESSED
ONES.** That is what lets you classify a site this table omits. An out-of-band
merge — a PR merged in the web UI with no PACT command run — is unwitnessed by
definition, so it correctly gets no site, and it is picked up and asked about at
the next reconciliation instead.

No site writes two status rows. The rows that ALWAYS ask are there because a
permissions table that lists only what is allowed reads as though the omissions
are allowed too.

Not on every small action: the log's value is its stability.

## Step 5 — When a line is corrupt

The log is append-only. A line that is not JSON or fails the schema is skipped
at replay, counted with its line number, and never deleted or rewritten by the
tool; `fsck` lists them and the session-start line shows `corrupt N`. The usual
cause is a crash mid-write, and the next write puts a newline first so the
partial line stays isolated. Report it; the lead fixes the line by hand, as a
normal commit with its own review.

## Where the log lives

`.repacted/tasks.jsonl` in the repo's MAIN checkout, git-tracked with
`merge=union`, so every worktree writes the same file and a branch merge never
conflicts on it. The lock and the summary cache under `.repacted/` are ignored.
Events accumulate uncommitted in the main checkout: /repacted:wrap-up runs
`repact-task commit`, which commits only the log and its generated views. Never
`git stash` or `git checkout --` the log; the doctor goes amber when events stay
uncommitted for more than 24 hours.

## The ledger

On the same acceptance, bound to a project task or not, the bridge appends the
HANDOFF's `known-limit:` items, its `decisions` and its `remaining:` items to
`.repacted/ledger.jsonl` (beside the log, same lock, same opt-in: a repo without
a task log gets nothing). Decisions are numbered DL1, DL2, ... in the ledger's
own sequence; they are a specialist's choices, never the user's D<n> rulings in
`docs/decisions.md`. Each remaining item also becomes a task owned by `user` and
tagged `proposed`, so it shows under Waiting on the user and is never planned
for an agent until the user says so. A re-accept writes nothing twice. The text
is model-written: it is flattened to one line, capped, stripped of control
characters, and an item carrying a credential value is withheld whole. Nothing
from the ledger leaves the repository on its own. `repact-task ledger` counts
it; `repact-task ledger --backfill [--team <team>]` adds what earlier accepted
HANDOFFs hold and the ledger lacks (idempotent). A decisions item that only
reports a run (a `N passed`/`N failed` count, a tcount run stamp, or a bare
`.log` path) is not recorded.

A limit is retired by an appended event, never an edit:
`repact-task ledger retire <id> --reason "<why>"` (the id is the one
`docs/known-limits.md` prints, or any unique prefix of 6 or more), or on its
own when a later accepted HANDOFF of the same project task (a ref bound to
both platform tasks) or the same platform task carries a `fixed:` item with
the same text, compared case-folded with punctuation ignored, or when any later
accepted `fixed:` item names its printed id (`fixed: id 1a2b3c4d, ...`).

## The generated views

Once `repact-task render --configure` has run in a repo, `docs/progress.md`,
`docs/backlog.md` and `docs/release-gates.json` are rendered from the log after
every write. `progress.md` alone is enough to resume from: in flight (owner,
branch, platform task), blocked with the reason, the release gates, what waits
on the user, ready next, recently done, recent notes and decisions. Once the
ledger exists, `docs/known-limits.md` renders beside them: the newest 50
limits not retired (`"known_limits_max"` in the config changes the number),
under their project task (or the platform task that reported them when none is
bound), newest first, with a count of the older and the retired ones;
`repact-task ledger --limits` lists every open limit.
`docs/decision-log.md` (DL<n> in acceptance order) is opt-in: add
`"decision_log": "docs/decision-log.md"` to the config's `views`. Every
Markdown and HTML character in their text is escaped. Never edit
a view: the next render overwrites the edit, the doctor goes amber on it, and
the drift test fails. Each old hand-edit has one command:

| Old habit | Command |
|---|---|
| Add a backlog item | `repact-task add "<title>" --priority P2 --tag <topic> [--owner user] [--note "<one line>"]` |
| Mark something done | `repact-task done <ref> --evidence commit:<sha>` (or `tag:`, `merge:`, `doc:<path>@<sha>`) |
| Write the resume block | `repact-task set <ref> --note "<where it stands>"` on each active task, then `repact-task commit` |
| Record a decision | `repact-task link <ref> --reference decision:D<n>` (the text stays in `docs/decisions.md`) |
| Start or hand off work | `repact-task start <ref> --ptask <team>/<id>` and `repact-task set <ref> --owner <name>` |
| Park something on the user | `repact-task add "<title>" --priority <P0-P3> --owner user` (it shows under Waiting on the user) |
