# Refresh: writing the checkpoint

> Loaded by `/repacted:refresh` step 4 (see [refresh.md](../../commands/refresh.md)). Run the template below as written.

### 4. Write the checkpoint to the session journal

Emit `session_consolidated` only when step 2's consolidation actually completed, then write the `session_refreshed` checkpoint. The flag is **shell-clamped** via a three-branch `case`: `true` emits, `false` is a no-op, anything else fails fast as a template-substitution bug. Pass the literal string `true` or `false` for `{true_or_false}`.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] refresh.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
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
    echo "[refresh.md] invalid {true_or_false} flag: '{true_or_false}' (expected literal 'true' or 'false')" >&2
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
| `halt_active` | boolean | yes | Whether any HALT/algedonic signal task is live at refresh time |
| `halt_task_ids` | list of strings | no | Ids of the live signal tasks (diagnostic — the live task store is the SSOT at resume) |
| `feature_task_id` | string | no | The in_progress feature task id |
| `feature_subject` | string | no | The in_progress feature task subject |
| `team_name` | string | no | The active session team's name — resume-time identity cross-check |
| `next_phase` | string | no | Bounded vocabulary above |
| `worktrees` | list of strings | no | Absolute paths of active worktrees |
| `pr_number` | integer | no | Open PR number — surface-only, never gates surfacing |

The timestamp (`ts`) is set automatically by the journal writer and becomes the checkpoint's claim id — bootstrap's consumption write binds to it.

