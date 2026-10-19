---
description: Work through the project's task list on its own, one task after another, until the list, the clock or the budget runs out
argument-hint: "[--until HH:MM] [--max-usd N] [--max-tasks N] [--pr]"
disable-model-invocation: true
---

# Autopilot: drain the task log into reviewed branches

Take the top ready task of the task log ([the task-log page](../reference/work/task-log.md)), do it, stop it at a committed branch, record it, take the next, until `next` says stop. Run every command below as written, one program call per line with its full path: never through a shell function or variable (the guards cannot read through them and refuse the call). Exit codes of `repact-autopilot`: 0 a task or done; 1 a stop (JSON `stop`); 3 refused. Spend before `start` (bootstrap) is outside `--max-usd`.

**Only `next` ends the run.** A halted or failed task, a guard refusal, a cost preview line or a hunch that the next task will fail the same way never does: record the outcome and run `next` again. The limits you were given (`--until`, `--max-tasks`, `--max-usd`, the session cost cap) are enforced by `next` on measured spend; a cost preview is an estimate and is never a reason to stop.

## 1. Start, or resume

- `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' status`. An `Autopilot:` line means a resume: skip `start`, go to step 2; `next` returns the task in flight with `"resume": true`.
- Otherwise `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' start $ARGUMENTS`. **On exit 3 (refused), stop and tell the user what it names.** It refuses when unattended mode is OFF (only the owner turns it on, [unattended-runs.md](../reference/unattended-runs.md)) and when a dollar limit (`--max-usd`, or the session cost cap) cannot be measured. Never turn the mode on yourself and never drop a limit to get past it.

## 2. The loop

1. `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' next`. Exit 1: go to step 3. Exit 0: the JSON names `ref`, `title`, `resume` and `pr`.
2. `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' begin <ref>` (the checkpoint; skip it when `resume` is true). Read `python3 "{plugin_root}/bin/repact-task" show <ref>`.
3. Score variety ([workflows.md §Variety](../protocols/workflows.md#variety)) and run each task by band through the team: Low (4-6) `/repacted:comPACT`, Medium (7-10) `/repacted:orchestrate`; High or Extreme: `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' record <ref> halted --reason "<why>"` and go on. The direct-edit lane does not apply here. Each run gets its own worktree and branch.
4. Every task's `"{plugin_root}/bin/repact" dispatch` carries `--ref <ref>`, plus `--acceptance <file>` when it has acceptance criteria: the op links the task to the row and pins the criteria (what `metadata.project_task` and `metadata.acceptance` did by hand), so the evidence check judges it; its brief names `.repacted/profile.md` when the repo has one. A `-p` teammate (no task tools) ends its final output with its HANDOFF in a fenced json block: store it verbatim as `metadata.handoff` (`"{plugin_root}/bin/repact" inspect --relayed-handoff <file>` prints it); with none, `"{plugin_root}/bin/repact" accept <id> --waive "<why>"` parks the task for the owner.
5. Stop the task at a **committed branch in its worktree**. With `pr` false nothing is pushed. With `pr` true: push that feature branch (never the default branch, never `--force`) and open a DRAFT pull request (`gh pr create --draft`). Never merge, close a PR or delete a branch. A guard refusing a push or a PR halts that task; never route around it.
6. `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' record <ref> done --branch <branch> [--pr <url>]` (closes the row only on an accepted HANDOFF, else says why; `--waive "<why>"` parks the row for a human, since unattended a waiver never closes it), or `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' record <ref> failed`. A recorded task is never picked again in the run. Then back to 1, whatever the outcome.

**A question.** The unattended gate refuses every `AskUserQuestion`. DEFAULT: record it and go on. ALWAYS-ASK: `python3 "{plugin_root}/bin/repact-unattended" halt --session-dir '{session_dir}' --task <ref> --question "<question>"`, one `telegram_notify` when Telegram is set up, stop work on that task only, `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' record <ref> halted`, then back to 1. A halted record parks the row for the owner with its reason or question, so `"{plugin_root}/bin/repact" decide` lists it and no later session takes it; a halted record with neither is refused. `next` skips a halted task, one with an open inbox question and all that depends on them.

**Context.** Before each `begin`, stop the previous task's teammates and commit its worktree. After a compaction the resume screen's `Autopilot:` line lets step 1 pick the run up.

## 3. The end

`next` stopped (exit 1); its JSON names the reason. Start nothing new; record the task in flight. Then:

1. `python3 "{plugin_root}/bin/repact-autopilot" --session-dir '{session_dir}' report` and `"{plugin_root}/bin/repact" decide`; show both to the user unedited. A report that says `still running` means `next` has not stopped the run: go back to step 2.
2. When Telegram is set up: one `telegram_notify` with the report's first two lines and the counts of done and halted.
