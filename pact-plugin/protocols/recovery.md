# State recovery

How rePACTed rebuilds workflow state after compaction, a session resume or a crash.

## Recovery Steps

**Sources, most durable first:**

| Source | Location | Survives | Use for |
|---|---|---|---|
| Session journal | `{session_dir}/session-journal.jsonl` | compaction, task GC, team teardown, crashes | HANDOFFs, phases, variety, commits, pause state |
| Task system | `TaskList` / `TaskGet` (summaries); task files for metadata | compaction (summaries); files may be GC'd | status, blocking, assignment |
| pact-memory | `~/.claude/pact-memory/memory.db` (a default-root pin in code; do not migrate) | permanently | cross-session knowledge, not workflow state |

<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->
The `pact-memory` skill carries the full rule.

**Reading the journal:** `python3 "{plugin_root}/hooks/shared/session_journal.py" read --session-dir "{session_dir}" [--type <t>] [--since <ts>]` prints ONE JSON array: parse it with `json.loads` and iterate. Never parse line by line, and never pipe through `2>/dev/null`, `|| echo` or `head`: they turn a parse crash into apparent emptiness.

**Journal events** (each has `v`, `type`, `ts`): `session_start` (`team`, `session_id`, `project_dir`, `worktree`, `source` ∈ startup/resume/compact/clear/unknown), `session_end` (optional `warning`), `session_paused` (`pr_number` and `pr_url` or null, `branch`, `worktree_path`, `consolidation_completed`, `team_name`, optional `resume_hint`), `session_refreshed` (`consolidation_completed`, `halt_active`, optional feature/next-phase/worktree fields), `session_refresh_consumed` (`refresh_ts`), `session_consolidated` (optional `pass`, `task_count`, `memories_saved`), `variety_assessed` (`task_id`, `variety`, optional `scope`=`dispatch`), `phase_transition` (`phase`, `status`), `checkpoint`, `agent_dispatch` (`agent`, `task_id`, `phase`), `agent_handoff` (`agent`, `task_id`, `task_subject`, `handoff`; the GC-proof HANDOFF store), `commit` (`sha`, `message`, `phase`), `s2_state_seeded` (`worktree`, `agents`, `boundaries`), `review_dispatch`, `review_finding`, `remediation`, `pr_ready`.

Rebuild from ground truth: `git worktree list`, `{session_dir}/session-journal.jsonl`, `TaskList`, then in-progress task files (`TaskGet` omits metadata).

**Post-compaction (mid-session):**
1. Read the current session's journal: the full event history survives.
2. `TaskList` for status, blocking and ownership. Task tools and `SendMessage` can come back deferred: load them with `ToolSearch` (`select:TaskUpdate,TaskCreate,TaskStop,SendMessage`) before the next accept pair, or a schema error splits it.
3. Metadata from the task file on disk, `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.<key>`, while it exists (`TaskGet` does NOT surface it).
4. The journal is authoritative when task metadata is gone.
5. If a workflow you started is still in progress in `TaskList` and its re-attached copy is cut short or missing, read `{plugin_root}/commands/<name>.md` in full with `Read` (all pages) before continuing, where `repacted:<name>` is that workflow. Do not invoke the workflow again: that starts it over. If the file shows `$ARGUMENTS`, take the task from its re-attached copy or your compact summary.

**Cross-session (session start, `restore_last_session`):** read the previous journal via the `- Session dir:` line in CLAUDE.md (fallback: derive it from the Resume line and project root); group `agent_handoff` events by `(agent, task_subject)` keeping the latest; list `phase_transition` progress and `session_end` warnings; truncate decision summaries to 80 characters.

**Paused work (`check_paused_state`):** the latest `session_paused` event; older than 14 days → stale notice; `gh pr view` MERGED/CLOSED → informational; otherwise a resume prompt with PR (or none), branch, worktree and `resume_hint`.

**Unpaused exit (`check_unpaused_exit`):** fresh start, no paused or refreshed claim, a different previous session: its open task files, `agent_handoff` events after its last `session_consolidated`, or its `session_end` PR warning → a continue-or-start-fresh prompt.

**Refreshed work (`check_resume_state`):** the latest `session_refreshed` event, retired by a `session_refresh_consumed` whose `refresh_ts` equals its `ts`; older than 48 hours → a stale prefix (never suppression); the prompt cross-checks live HALT signal tasks, which the event can never suppress.

**Crash safety:** journal appends are POSIX `O_APPEND` JSONL; `read_events()` skips malformed lines; the journal lives in `{config_dir}/pact-sessions/`, not `teams/`, so teardown keeps it. `{config_dir}` is `$CLAUDE_CONFIG_DIR` when set and non-empty, else `$HOME/.claude`; read it off an absolute path already in your context (your plugin root is `{config_dir}/plugins/…`) and never assume `~/.claude`. Wrap-up harvests the journal to pact-memory; journals are kept 30 days (paused sessions are exempt).

**No session directory at all:** malformed `session_init` stdin (bad JSON, missing or sentinel `session_id`) skips the per-session anchor and logs to `{config_dir}/pact-sessions/_session_init_failures.log` (100-entry JSONL ring: timestamp, classification, error ≤ 200 chars, cwd, source). Inspect it with `tail -20` when a session start leaves no directory.
