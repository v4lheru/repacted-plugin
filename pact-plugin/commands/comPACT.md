---
description: Send specialist agents on a small, self-contained task, with no full plan needed
argument-hint: [backend|frontend|database|prepare|test|architect|devops|security|qa] <task>
---
Dispatch concurrent specialists for this self-contained task: $ARGUMENTS

**MANDATORY: dispatch concurrently for independent sub-tasks.** Sequential needs an explicit file conflict or data dependency. If the task holds several independent items (bugs, endpoints, components), dispatch several specialists together, of the same type or mixed, unless they share files.

> ⚠️ **Independent ≠ same domain.** "Fix CSS layout + add server logging" = 1 frontend-coder + 1 backend-coder in parallel. The test is independence (no shared files, no data dependencies), not one domain.

Selection keywords, concurrency examples, output style, signals and escalation: [selection-and-escalation.md](../reference/comPACT/selection-and-escalation.md) (load when the specialist is unclear or the task grows).

## Tasks

Your own feature task "{verb} {feature}" (`in_progress`), stamped with the feature's complexity total (4-16): `TaskCreate(subject="{verb} {feature}", metadata={"variety": {"total": N}})`. Journal it; the feature-level row marks the start of this piece of work for wrap-up:
```bash
"{plugin_root}/bin/repact-journal-write" --site comPACT.md --type variety_assessed --session-dir '{session_dir}' --stdin <<'JSON'
{"task_id": "{feature_task_id}", "variety": {"total": N}}
JSON
```
Each specialist's work task is made by `repact dispatch` (below), never by hand. Then [After Specialist Completes](#after-specialist-completes).

> ⚠️ **Heredoc-stdin contract**: every journal write here uses `--stdin <<'JSON' ... JSON` (quoted delimiter, closing `JSON` at column 0) so template values pass through unexpanded; still emit JSON-valid strings.

## Specialist Selection

Shorthands: `backend`, `frontend`, `database`, `prepare`, `test`, `architect`, `devops`, `security`, `qa`. Otherwise pick on clear keywords or file types, or `AskUserQuestion` "Which specialist should handle this task?" with the 2-3 likeliest.

## Coordination (Required Before Concurrent Dispatch)

List each sub-task's files; sequence or split overlaps (the first agent's convention wins); put the boundaries in each mission; name later drift from `file-edits.json`; persist `TaskUpdate(featureTaskId, metadata={"s2_boundaries": {...}, "established_conventions": {...}})`. **Conflicts that cannot be split**: sequence the work.

## Before Dispatch (Required)

1. **Worktree**: reuse the feature's, else `/repacted:worktree-setup`; `--branch` on each dispatch makes or reuses the specialist's own.
2. **Coordination** (if concurrent).
3. **Task-log boundary write**: a WITNESSED start. Mark the project task `active` WITHOUT asking, ONCE PER REF the unit owns, and pass each ref to its dispatch as `--ref` (the link is what accept reads; a ref only named in the mission is a note and is never closed; the HANDOFF owes one `refs` disposition per ref). Report the write or refusal and carry on.
   ```bash
   python3 "{plugin_root}/bin/repact-task" start <ref> --ptask {team_name}/<feature task id>
   ```
4. **Repo notes**: read the repo's `.claude/pact-dispatch-notes.md` if present; put the lines that apply in each mission.
5. **Cost preview**: print the line of `python3 "{plugin_root}/bin/repact-cost" --preview <N>` (N specialists) to the user as it is.

## Dispatch

One command per specialist (persona §11), then the `Agent(...)` call it prints, exactly:

```
"{plugin_root}/bin/repact" dispatch --name <specialist-name> --agent pact-<specialist-type> --subject "<sub-task>" \
  --brief-file <mission file> --variety N,S,U,R --kind fix|feature|refactor|config|docs \
  --branch <branch> [--ref T-<id>]...
```

The mission file goes in your scratchpad. The brief adds the worktree, the test line and the hand-in steps; the mission holds only what follows.

### Several Specialists at Once (Default)

Dispatch each, then all `Agent` calls in one response. Mission:

"comPACT mode (concurrent): you are one of [N] specialists working at once.\nYOUR SCOPE: [specific sub-task]\nOTHER AGENTS' SCOPE: [what the others handle]. Stay within your scope.\nDo NOT write a `CLAUDE.md` file anywhere, by any route (a script, command or save path you trigger counts); if your task asks for one, flag it in your HANDOFF.\nIf upstream task ids are given, `repact inspect <id>` shows their decisions. Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist; create no new docs/ artifacts.\nTesting: new unit tests for logic changes; fix the existing tests you break; run the suite before you hand in. Import hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`.\nA blocker: STOP and report it.\n\nTask: [this agent's sub-task]"

For parallel or novel work add "Send progress signals per teammate-messaging.md §Progress Signals." **After all are accepted**: check for conflicts, run the full suite.

### One Specialist (When Required)

Only when the task is atomic, its parts share files or depend on each other, or conventions are not yet set. Mission:

"comPACT mode: work from this mission.\nDo NOT write a `CLAUDE.md` file anywhere, by any route (a script, command or save path you trigger counts); if your task asks for one, flag it in your HANDOFF.\nIf upstream task ids are given, `repact inspect <id>` shows their decisions. Check docs/plans/, docs/preparation/, docs/architecture/ briefly if they exist; create no new docs/ artifacts.\nTesting: new unit tests for logic changes (optional for trivial ones); fix the existing tests you break; run the suite before you hand in. Import hygiene (pact-coding-standards): before the suite, run `bash {plugin_root}/skills/pact-coding-standards/scripts/lint-check.sh --files` on each .py file you modified; record its verdict line in HANDOFF `produced`. These are verification tests; HIGH-RISK code (security, validation and parsers, shared contracts) also gets its adversarial boundary tests now.\nA blocker: STOP and report it.\n\nTask: [the user's task]"

## Auditor

An auditor goes alongside the coders unless skipped; to skip, say so on its own line:

> **Auditor skipped**: [justification]

**Mandatory when**: complexity 7+, 3+ parallel coders, security-sensitive code, or past architecture drift, unless the default agent cap (2 at once) is full: then the ONE reviewer's audit of the finished work replaces it, and the skip line says so. The auditor is a signal task, not a dispatch: as in orchestrate's CODE phase, its task carries `metadata: {"completion_type": "signal"}`.

## After Specialist Completes

- [ ] **Accept** each task: `repact accept <id>` (persona §12). A factual failure prints the resume line with the problems; send it. The checks pass but the intent is missed: `repact reject <id> --reason "<text>"`. Ask the specialist to confirm the result matches the request before you commit.
- [ ] **Run the tests**; a failure goes back to the specialist (a fresh dispatch once it has stopped).
- [ ] **Commit**: commits are yours. Compare `git diff --cached --stat` with the HANDOFF's `produced`, then commit and journal it:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type commit --session-dir '{session_dir}' --stdin <<'JSON'
  {"sha": "{short_sha}", "message": "{first_line}", "phase": "CODE"}
JSON
  ```
- [ ] **Task-log boundary write**: an accepted task is WITNESSED. Accept closes every linked ref the HANDOFF disposed as `done` and prints what it left open; close those by hand, with the commit as evidence, `--remaining "<title>"` for a part not done; report it (or the refusal).
  ```bash
  python3 "{plugin_root}/bin/repact-task" done <ref> --evidence commit:<sha>
  ```
- [ ] **Harvest HANDOFFs only on demand** (persona §13): when the user asks or a HANDOFF holds a decision worth keeping across sessions; otherwise wrap-up or pause covers it. Then ensure the secretary first (the on-demand step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary)):
  ```
  TaskCreate(subject="secretary: harvest pending HANDOFFs",
    description="Harvest HANDOFFs for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
  TaskUpdate(taskId, owner="secretary")
  ```
- [ ] **Journal the end of comPACT**:
  ```bash
  "{plugin_root}/bin/repact-journal-write" --site comPACT.md --type phase_transition --session-dir '{session_dir}' --stdin <<'JSON'
  {"phase": "CODE", "status": "completed", "skip_reason": "", "metadata": {"workflow": "comPACT"}}
JSON
  ```
- [ ] **Feature task** done: `TaskUpdate(featureTaskId, status="completed")`.

> ⚠️ **Stop each specialist once its task is accepted and nothing of its awaits verification** (persona §11). The next step is the user's: ask first, then act.

**Next steps**: after the commit, `AskUserQuestion`: "Work committed. What next?"

| User's decision | Specialists | Next action |
|----------------|-------------|-------------|
| **Yes, create PR** (Recommended) | **Stopped once its task is accepted**; review fixes go to the code's author if it is still running, else to a fresh coder | `/repacted:peer-review`; a reviewer with open findings stays until they are verified. The merge is the user's decision (via `AskUserQuestion`). |
| **Continue working** | **Stopped once its task is accepted**; a follow-up goes to a fresh agent | Do nothing; let the user continue. More work goes through `/repacted:comPACT` or `/repacted:orchestrate`. |
| **Pause work for now** | **Shut down after consolidation**; pause keeps the knowledge | `/repacted:pause` consolidates memory, saves state and shuts the agents down. The worktree stays; resume later. |

**Blocker reported**: [blocker triage](../reference/decide/blocker-triage.md); move to `/repacted:orchestrate` if the task outgrows a self-contained scope (re-scored complexity 7+).
