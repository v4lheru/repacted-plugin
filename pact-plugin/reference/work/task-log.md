# Work: the project task log

> Loaded from [work.md](../../commands/work.md) when the task log is reported or written: what is next, what is blocked, and every write to `.repacted/tasks.jsonl`.

The project's task log (`.repacted/tasks.jsonl`, in the repo) holds **what the user intends to do next**, in order, with its dependencies. You are the **sole writer** of status and intent. No arguments: report and recommend; with arguments: apply, then report. Why each rule holds: [task-log-detail.md](task-log-detail.md) (load when a rule is in question). `<ref>` is a task's `T-` ref; any unique prefix of 4 or more hex characters works.

## Step 1 — Update what you already know

Write WITNESSED status transitions and anything you TRANSCRIBE from the user (`add`; `drop` when they say so) without asking, always via the command (it validates and appends under the lock):

```bash
python3 "{plugin_root}/bin/repact-task" set <ref> --status <status>
```

`done` needs evidence, so it is its own verb: `done <ref> --evidence commit:<sha>`. `drop` is transcription, NEVER inference: an untouched task is a staleness flag that ASKS. Everything else asks. Report every write you made.

## Step 2 — Reconcile and report

```bash
python3 "{plugin_root}/bin/repact-task" next
python3 "{plugin_root}/bin/repact-task" blocked
```

Each read first records any lead acceptance the hook missed. Report: what is `active`, the next two or three ready tasks, what is blocked and on what, and every flag (`show <ref>`, `fsck`) with what it means. To the user, use titles, never refs.

Exit code 1 comes only from `sync --check`: a lead acceptance is missing from the log. Run `sync` and report what it wrote.

Exit code 64 is a USAGE error: the tool never ran. Fix the invocation.

Exit code 65 is a REFUSAL: nothing was written. Fix what it names (a cycle, missing evidence, credential text, a path outside its root).

Exit code 66: this repo has no task log. Never create one unasked; bootstrap offers `init` once.

Exit code 74: the log could not be read. Report the access problem and stop.

ANY OTHER exit code — 0, 1, 64, 65, 66 and 74 are the only ones the tool chooses, so the tool never started (wrong path or directory). Check the invocation; trust no diagnosis of the log from that run.

## Step 3 — Apply the facts, ask about the intent

This step governs INFERENCES ONLY. Each row carries its own verdict:

| Flag | Verdict | What you do |
|---|---|---|
| a relation names an unknown task | ASK | the ref WAS the record; ask what was meant |
| stuck: depends on a dropped task | ASK | propose removing the dependency, or dropping this task too |
| two exclusive tasks are both `active` | ASK | propose pausing one |
| active and untouched past 14 days | ASK | confirm it is still live |
| planned and unranked, untouched past 14 days | ASK | rank it, or ask if still intended |
| accepted without evidence | ASK | find the commit, tag or run, then close it with `done --evidence` |
| open, platform task completed | ASK | `done --evidence` (`--remaining` for a part left) or `drop` |
| concurrent edit on a field | ASK | show both values; the user picks |
| one event id with different contents | NEITHER | report it (a bad merge); the lead fixes the log by hand |
| a second create for one ref | NEITHER | report it; the first create wins |
| corrupt line N | NEITHER | report it; the lead fixes the line by hand, as a normal commit |

Put each proposal to the user with `AskUserQuestion`. `repact decide` lists them with stop signals and held KB items.

## Step 4 — Write what the user decides

```bash
python3 "{plugin_root}/bin/repact-task" add "<title>" --priority P2 --rank <n> --note "<one line, your voice>"
python3 "{plugin_root}/bin/repact-task" dep <ref> --on <ref>
```

`set` as in Step 1; `link`, `start`, `reopen` and `drop` as `--help` shows. Refused, nothing written: a title over 80 characters or a note over 500, credential text, a dependency or parent cycle, a path outside its root, `--status done` through `set`, closing a parent with an open child. `note` in YOUR voice (mark `user ruled:` or `inferred:`).

### Where the report happens, and which files carry it

**THREE FILES carry a task report**: `bootstrap.md`, `wrap-up.md` and `work.md`.

### When the writes happen, and which they are

**FOUR FILES carry boundary writes**: `orchestrate.md`, `comPACT.md`, `decide.md` and `wrap-up.md`. Boundary writes cover witnessed transitions; reconciliation covers unwitnessed ones. Accepting a work task that carries `metadata.project_task` with `repact accept` also closes its task on verified evidence (the acceptance bridge), so a boundary `done` it already made is refused harmlessly.

| # | Write | Where | Asked first? |
|---|---|---|---|
| 1 | `start` → `active` | `orchestrate.md`, end of pre-flight | no — witnessed |
| 2 | `done` | `orchestrate.md`, between the completion steps | no — witnessed |
| 3 | `start` → `active` | `comPACT.md`, pre-invocation | no — witnessed |
| 4 | `done` | `comPACT.md`, after task-completion verification | no — witnessed |
| 5 | `status` → `blocked` | `decide.md`, blocker raised | no — witnessed |
| 6 | `status` → `active` | `decide.md`, blocker cleared | no — witnessed |
| 7 | `done` | `wrap-up.md`, worktree cleanup, BEFORE removal | no — witnessed |
| 8 | `add` | wherever the user says | no — transcription only |
| 9 | `drop` → `dropped` | wherever the user says | no — transcription only |
| 10 | `priority`, `rank` | — | ALWAYS ask |
| 11 | `owner`, `tag`, `parent` | — | ALWAYS ask |
| 12 | `dep`, `link` | — | ALWAYS ask |
| 13 | `title`, `note` | — | ALWAYS ask |
| 14 | `reopen` | — | ALWAYS asks |
| 15 | removing a task | — | NEVER; use `drop` |

## Step 5 — When a line is corrupt

`fsck` names each corrupt line by number. The tool never deletes or rewrites a line: report it; the lead fixes it by hand in a reviewed commit.
