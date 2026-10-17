# Pause: writing the paused state

> Loaded by `/repacted:pause` step 5 (see [pause.md](../../commands/pause.md)). Run the template below as written.

### 5. Write Paused State to Session Journal

Persist session state as a `session_paused` event in the session journal. The event contains PR number (when there is a PR), branch, worktree path, and consolidation status — detected by `session_init.py` on resume. See [recovery.md](../../protocols/recovery.md) for the full recovery protocol.

When consolidation ran successfully (step 3 completed), ALSO emit a `session_consolidated` event BEFORE the `session_paused` write. This is the signal consumed by `check_unpaused_pr` in `session_end.py` so the SessionEnd hook can recognize consolidation-ran sessions uniformly whether they went through wrap-up or pause. The bash template below is **shell-clamped** via a three-branch `case` statement — `true` emits, `false` is a no-op, and anything else (empty string, `True`, `TRUE`, a stray integer, an accidental placeholder) fails fast with a stderr message and non-zero exit. The orchestrator MUST pass the literal string `true` or `false` for `{true_or_false}` (matching `session_paused`'s `consolidation_completed` field type); any other value is treated as a template-substitution bug, not a caller convention.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] pause.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
# Emit session_consolidated only when consolidation actually ran in step 3.
# Shell-clamped via case/esac so the prose contract is enforced mechanically
# — the session_consolidated signal cannot be falsely emitted when
# consolidation did not run, and an invalid flag value fails fast rather
# than silently taking the false branch.
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
    echo "[pause.md] invalid {true_or_false} flag: '{true_or_false}' (expected literal 'true' or 'false')" >&2
    exit 1
    ;;
esac
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_paused --session-dir '{session_dir}' --stdin <<'JSON'
{"pr_number": {pr_number}, "pr_url": "{pr_url}", "branch": "{branch}", "worktree_path": "{worktree_path}", "consolidation_completed": {true_or_false}, "team_name": "{team_name}"}
JSON
```

> **No PR**: a pause without a PR is valid. Write `"pr_number": null, "pr_url": null` (unquoted `null`), or leave both keys out; an empty string or `0` is rejected or ignored. With or without a PR you may add `"resume_hint": "{free text}"`: the next session start shows it with the paused state.

> ⚠️ **Heredoc-stdin contract**: This write uses `--stdin <<'JSON' ... JSON` (quoted delimiter). The quoted delimiter disables bash variable expansion, so an apostrophe in `{branch}` (e.g., `feat/o'connor-fix`) or in `{pr_url}` cannot close the shell quote and silently drop the event. The orchestrator must still produce JSON-valid string content (escape `\"`, `\\`, and control chars when constructing the body).

**Event fields**:
| Field | Type | Description |
|-------|------|-------------|
| `pr_number` | integer or null | GitHub PR number; null or absent when there is no PR |
| `pr_url` | string or null | Full URL to the PR; null or absent when there is no PR |
| `resume_hint` | string, optional | Free text for the next session: where to pick up |
| `branch` | string | Git branch name |
| `worktree_path` | string | Absolute path to the worktree |
| `consolidation_completed` | boolean | Whether memory consolidation finished successfully |
| `team_name` | string | Session team name (format: `pact-{session_hash}`) |

The timestamp (`ts`) is set automatically by `make_event()`.

