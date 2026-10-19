# Wrap-up steps 1, 5, 7 and 8 in full

> Loaded from [wrap-up.md](../../commands/wrap-up.md). Step 6 is [worktree-cleanup.md](worktree-cleanup.md), step 4 [retrospective.md](retrospective.md), step 7b [kb-queue.md](kb-queue.md).

## 1. The consolidation dispatch

Ensure the secretary first: the on-demand secretary step in [bootstrap.md](../../commands/bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).

Compose the payload BEFORE you create the task. Answer the step-4 retrospective now (on a trivial session, the literal `no retrospective this session`) and fix the difficulty rating: actual difficulty against the scored variety (`higher`, `lower` or `same`), the actual score (4-16), and any dimension that surprised you. Both go INSIDE the task description. Why: a `SendMessage` sent after dispatch lands only at the secretary's next idle boundary, so it cannot reach a consolidation that is already finalizing; the description is read before any work starts.

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. The orchestration retrospective and the difficulty rating are below: fold the retrospective into the SAME consolidation memory write as ONE coherent entry (do NOT save it separately), and build the calibration record from the rating without asking for it. Report summary when done.

ORCHESTRATION RETROSPECTIVE:
{retrospective payload from step 4, or: no retrospective this session}

DIFFICULTY RATING: {higher | lower | same}, actual {4-16}; surprised by: {dimensions, or none}")
TaskUpdate(taskId, owner="secretary")
```

The dispatch is QUIET: it carries no propagate sentence, because only step 6 knows whether the arc is over. Send no retrospective or skip `SendMessage` after it, and create no second save task.

**Concurrent, not serialized.** Do not wait for the harvest: steps 2-3 run while the secretary harvests; only step 6 (worktree cleanup) and step 7 (task audit) wait for step 5's drain-confirmation. No destructive step may run before the harvest has read what it would destroy.

## 5. When the drain cannot be confirmed

Warn the user that undistilled entries stay in the journal under `{config_dir}/pact-sessions/` (`{config_dir}` = `$CLAUDE_CONFIG_DIR` when set and non-empty, else `$HOME/.claude`; read it off your plugin root path, never assume `~/.claude`). The `case` in wrap-up.md's block clamps `{consolidation_ran}` to the literal `true` or `false`, mirroring pause.md step 5: any other value fails fast rather than silently taking the false branch.

## 7. Task audit

`TaskList`; complete or document abandoned `in_progress` tasks; complete the feature task ONLY when the arc closed (step-6 branch A or B). On branch C, and whenever step 8 may still pick "Pause work for now", leave it `in_progress`: the platform deletes the whole task list once every task in it is completed, so completing the last open task leaves pause nothing to keep. Report "Session has N tasks (X completed, Y pending)" and the `write checks` lines of `repact report`.

Then show, each as printed:
- `python3 "{plugin_root}/bin/repact-session-numbers" --session-dir '{session_dir}'`: six numbers of this session, computed now unless its ledger row is current (the plugin's measure, not billing). An `n/a` line carries its reason; never fill one in.
- `python3 "{plugin_root}/bin/repact-unattended" list --session-dir '{session_dir}'`, when it counts a default, a halt or a refused question ([unattended-runs.md](../unattended-runs.md)).
- `repact decide --count`'s head line.

In multi-session mode (`CLAUDE_CODE_TASK_LIST_ID` set) only, offer, on the user's OK, to delete completed feature hierarchies; never an `in_progress` one.

## 8. The session decision

The task report is a CALL SITE, not a boundary write: every status was recorded at its own transition. Report only the flags and what remains open; the report is read-only, so report a flag, never correct one (a wrong-looking status is corrected through `/repacted:work`). No flags and nothing open: say nothing. `commit` commits ONLY the log and its views, with one sentence. With no task log (exit 66), skip them; with the bootstrap step-5 task-log question declined or left open, skip both and say once that the task log was not kept. If the report fails, say so and go on to the decision.

The `AskUserQuestion` option descriptions:
- **"Yes, continue"**: "Continue with the next task". Report "Ready for next task." Teammates with an open task or findings awaiting verification stay; the others were stopped when their tasks were accepted (persona §11).
- **"Pause work for now"**: "Save session knowledge and pause — resume later". Invoke `/repacted:pause`, which owns the teammate shutdown: do not stop teammates here.
- **"No, end session"**: "Stop teammates now, then close out — PACT's 30-day TTL cleans directories (recommended)". Run the shutdown below, then report.

### Teammate shutdown (end-session branch only)

Stop each active teammate by name, one per turn: `TaskStop("{teammate_name}")`. Treat a not-found or already-exited error as success and continue: an abort strands later teammates.

The secretary is in the loop. On the normal path step 5 confirmed its consolidation, so stopping it loses nothing. If step 5 took its cannot-confirm branch, which warns rather than halts, the secretary may still be mid-harvest and `TaskStop` is unconditional: stop it LAST and say so in the report, so an undrained journal is visible.

Each stop removes that member's roster entry; the config file and the team identity survive. A lead-only roster is the correct post-state, not corruption. Do not delete the team. Then report: "Session complete. All teammates stopped. Team and task directories (`{config_dir}/teams/`, `{config_dir}/tasks/`) are reaped after 30 days by PACT's own `session_end` hook."
