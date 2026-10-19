# Pause: the checkpoint mode

> Loaded by `/repacted:pause checkpoint` ([pause.md](../../commands/pause.md)). Run the steps and the template as written.

Reset context mid-workstream without losing state; the workstream CONTINUES (nothing closed, merged or cleaned). The harvest and its drain confirmation are MANDATORY.

## Constraints (apply to every step)

- **LEAD-FRAME-ONLY**: spawn, stop and message teammates from the lead frame.
- **Never clean worktrees, branches, or commits.** Verification is read-only.
- **Never complete or delete tasks**: feature, phase and STOP/ALERT signal tasks must survive.
- **Never write under `{config_dir}/pact-refresh/`** (legacy; `{config_dir}` = `$CLAUDE_CONFIG_DIR` or `$HOME/.claude`); state lives ONLY in the journal.

## Steps

> **CRITICAL**: step 2 drain-confirmed BEFORE step 5. Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](../../commands/bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).

### 1. Preflight — degenerate case

No in_progress feature task? Say the checkpoint runs harvest-only (`/repacted:wrap-up` may fit better); on confirm, write the minimal step-4 event.

### 2. HARVEST — memory consolidation (Pass 2)

```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

**Drain-confirmation gate (MUST)**: READ the task (`TaskGet` or its JSON) and confirm `completed` before any shutdown; else wait or nudge.

### 3. PERSIST/VERIFY — read-only state check + payload computation

Per worktree, `git status --porcelain`; report uncommitted changes, never clean or commit. Compute the checkpoint fields step 4 lists (STOP/ALERT signal tasks, feature task, `team_name`, `next_phase`, worktrees, open PR).
Task log: note each active task's state (`repact-task set <ref> --note`), then `repact-task commit` (with the task-log question of bootstrap step 5 declined or left open, skip both).

### 4. Write the checkpoint to the session journal

Emit `session_consolidated` only when step 2's consolidation actually completed, then write the `session_refreshed` checkpoint. The flag is **shell-clamped** via a three-branch `case`: `true` emits, `false` is a no-op, anything else fails fast as a template-substitution bug. Pass the literal string `true` or `false` for `{true_or_false}`.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] pause checkpoint (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
case '{true_or_false}' in
  true)
    python3 "{plugin_root}/hooks/shared/session_journal.py" write \
      --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}
JSON
    ;;
  false)
    ;;  # intentional no-op
  *)
    echo "[pause checkpoint] invalid {true_or_false} flag: '{true_or_false}' (expected literal 'true' or 'false')" >&2
    exit 1
    ;;
esac
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refreshed --session-dir '{session_dir}' --stdin <<'JSON'
{"consolidation_completed": {true_or_false}, "halt_active": {halt_active}, "halt_task_ids": {halt_task_ids}, "feature_task_id": "{feature_task_id}", "feature_subject": "{feature_subject}", "team_name": "{team_name}", "next_phase": "{next_phase}", "worktrees": {worktrees}, "pr_number": {pr_number}}
JSON
```

**Degenerate case** (no active workstream): omit every optional field — write the two required fields only:

```bash
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refreshed --session-dir '{session_dir}' --stdin <<'JSON'
{"consolidation_completed": {true_or_false}, "halt_active": false}
JSON
```

> ⚠️ **Heredoc-stdin contract**: the writes use `--stdin <<'JSON' ... JSON` (quoted delimiter), so an apostrophe in a substituted value cannot close the shell quote and silently drop the event. The orchestrator must still produce JSON-valid string content (escape `\"`, `\\`, and control chars).

**Event fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `consolidation_completed` | boolean | yes | Whether the step 2 harvest completed |
| `halt_active` | boolean | yes | Whether any HALT/algedonic signal task is live at checkpoint time |
| `halt_task_ids` | list of strings | no | Ids of the live signal tasks (diagnostic — the live task store is the SSOT at resume) |
| `feature_task_id` | string | no | The in_progress feature task id |
| `feature_subject` | string | no | The in_progress feature task subject |
| `team_name` | string | no | The active session team's name — resume-time identity cross-check |
| `next_phase` | string | no | Bounded vocabulary above |
| `worktrees` | list of strings | no | Absolute paths of active worktrees |
| `pr_number` | integer | no | Open PR number — surface-only, never gates surfacing |

The timestamp (`ts`) is set automatically by the journal writer and becomes the checkpoint's claim id — bootstrap's consumption write binds to it.

### 5. SHUTDOWN teammates

Stop each active teammate **by name**, 1 per turn; no `shutdown_request` first:
```
For each active teammate:
  TaskStop("{teammate_name}")
```
A not-found error is already-stopped success: CONTINUE. EXPECTED post-state: a lead-only roster. Do NOT delete the team.

### 6. STANDBY

Report: "Context checkpoint complete. Run /compact now, then /repacted:bootstrap to resume mid-flight." Until bootstrap runs:

- **FORBID new dispatches**.
- **RESPAWN-BEFORE-SEND**: messaging a pre-checkpoint name (the secretary too) resurrects its stale transcript.
- **Expect and IGNORE stragglers**: a late pre-stop message is not a live teammate; never reply.

Telegram bridge active: `telegram_notify` the same line.
