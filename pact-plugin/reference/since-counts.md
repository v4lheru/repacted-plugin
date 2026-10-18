# repact-report --since

> **Purpose**: what the plugin did across many sessions: refusals by rule, teammates dispatched, tasks accepted and rejected, and the summed session numbers, in one time window.
>
> **Usage**: `repact-report --since 7d` (or `12h`, `30m`, `2w`, or an ISO date or time such as `2026-10-01`). Add `--json` for the export below, `--until ISO` to close the window, `--project NAME` for another project or `--all-projects` for every one. The default project is the folder name of `$CLAUDE_PROJECT_DIR`, else of the working directory.

---

## What is read

- Every session journal under `<config dir>/pact-sessions/<project>/<session>/session-journal.jsonl`. An event counts when its `ts` falls in the window. A journal last written before the window starts is not opened.
- The session ledger, `sessions.jsonl` in the plugin data dir. It holds one row per session end, and a resumed session appends a newer row. The report takes the latest row of each session whose end date falls in the window.

Counts only. Rule ids and project folder names are the only strings. A `rule` value that does not have the shape of a rule id counts under `(unrecognised)`. The report never reads a prompt, command, subject, task id, teammate name, file content or path into its output. Input it cannot use is skipped and counted under `skipped`, never guessed.

## JSON export, schema `repact-report.since/1`

The schema id changes when a key is removed or changes meaning. A new key does not change it. Readers should ignore keys they do not know.

| Key | Type | Meaning |
|---|---|---|
| `schema` | string | `repact-report.since/1` |
| `window` | object | `since` and `until`, ISO 8601 UTC. `until` is the time of the run when `--until` is not given |
| `scope` | object | `projects`: the list of project folder names, or `"all"` |
| `sessions` | int | sessions with at least one journal event in the window |
| `sessions_by_project` | object | project folder name to the number of sessions |
| `refusals` | object | `total`, and `by_rule`, which maps a rule id to a count (`gate_refusal` events) |
| `dispatches` | object | `allowed` and `denied`: decisions of the dispatch gate (`dispatch_decision` events) |
| `teammates_dispatched` | int | the same as `dispatches.allowed` |
| `teachbacks` | int | teachback writes (`metadata_write` events whose key is `teachback_submit`) |
| `handoffs` | int | HANDOFFs recorded (`agent_handoff` events) |
| `tasks` | object | `accepted`: tasks completed (one `task_metadata_snapshot` per task and occupant). `handoff_rejected` and `teachback_rejected`: the completed tasks that had that rejection |
| `compactions` | int | context compactions (`compaction_attributed` events) |
| `ledger` | object | the session-ledger sums, below |
| `skipped` | object | input that could not be used, below |

`ledger` adds up the numbers that `repact-session-numbers` prints for each session:

| Key | Meaning |
|---|---|
| `sessions` | ledger rows summed: one per session that ended in the window |
| `lead_calls`, `teammate_calls` | model calls by the lead and by teammates |
| `lead_input_side` | the lead's input-side tokens, cache reads weighted (the plugin's own measure, not billing) |
| `per_call_lead_input` | `lead_input_side / lead_calls`, rounded. `null` when there are no lead calls or a row lacks either field |
| `idle_only_cache_read` | cache-read tokens of lead wakes that only reported idle |
| `rejected_tasks` | tasks that carried a handoff rejection |
| `check_returns` | items the write-time checks returned |
| `missing` | the number of rows that lacked each field. A missing field adds nothing to its sum; it is never counted as 0 |

`skipped`:

| Key | Meaning |
|---|---|
| `unreadable_journals` | journals that could not be read |
| `malformed_journals` | journals with lines but no JSON object among them |
| `malformed_lines` | journal lines that are not a JSON object |
| `undated_events` | journal events with no usable `ts` |
| `unreadable_ledger` | 1 when the session ledger could not be read (for example, when it does not exist yet) |
| `malformed_ledger_lines` | ledger lines that are not a row with a session id |

The single-session report (`repact-report` without `--since`) has no schema id. Its keys follow `hooks/shared/transcripts.py` `build_report`.
