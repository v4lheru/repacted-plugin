# Task store, metadata and harvest

Context: what the task store, task metadata and the session journal actually keep, and the
workarounds a lead or harvester needs when a task, a handoff or a metadata key seems to be gone.
Read it before deleting a task, before a harvest, and before reporting that work was lost.

Stamp legend: `(verified|observed DATE, VERSION; until MILESTONE)`. "re-probe" marks a Claude Code
platform behaviour with no fork fix; re-check it on a new Claude Code release.
`{config_dir}` is `$CLAUDE_CONFIG_DIR` when set, else `$HOME/.claude`.
Marks ("re-probe", "practice", "until Mn"): defined once, in [SKILL.md](../SKILL.md).

## Canonical rules
1. Enumerate the keys a task CARRIES (`jq '.metadata | keys'` on the task file) before harvesting; never harvest from a key list, yours included. (observed 2026-09-02, PACT 4.6.54; until M7)
2. A correction to a handoff lives in a SIBLING key (a nested rewrite would erase the object), so read every key that looks like a resolution or amendment. (observed 2026-08-21, PACT 4.6.x; until M7)
3. Harvest promptly after acceptance: the task store drains completed tasks, and has drained whole stores, minutes after writes. No hook backs task files up (the 1.3.6 `task-files-backup/` copy was removed; an old session dir may still hold one). A drained task is read from the journal: `repact inspect <id> --key handoff` falls back to it and names the source in its header (the HANDOFF whole; the teachback, rulings, grants and `lead_*` keys from the latest snapshot). A lead key written after acceptance is journalled only as `amend_<n>`, `<resource>_grant` or `lead_<name>`; any other late key dies with the file. (drains observed 2026-09-02, PACT 4.6.54, and 2026-10-07; fallback verified 2026-10-08, repacted 1.6.1, by test; re-probe)
4. "Task not found" or an empty TaskList describes retention or routing, not lost work; check every team dir and the session journal first. (observed 2026-09-01, PACT 4.6.x; re-probe)
5. The journal is durable but incomplete, the task file complete but reapable: read both, and all `task_metadata_snapshot` events, not only the latest. (observed 2026-09-10, PACT 4.7.x; until M7)
6. Latest snapshot is lossy: a key can keep its name and change its value. Hash values per key across snapshots; a key-set diff sees nothing. (observed 2026-09-10, PACT 4.7.x; until M7)
7. On reject-revise-accept, or any metadata write after the completion emit, journal and task file diverge in either direction; merge both. (observed 2026-08-25, PACT 4.6.x; until M7)
8. `completion_type: "signal"` tasks and a secretary's self-completed save emit no `agent_handoff`; verify a harvest by recalling the saved memory ids. (observed 2026-08-18, PACT 4.6.x; until M7)
9. Never re-dispatch a harvest that looks unfulfilled (a duplicate row is worse than a gap); ask for an additive update to the existing rows. (observed 2026-08-18, PACT 4.6.x; until M7)
10. Never delete a session journal to tidy up, even when a cleanup step says so; after a drain it is the only copy of side-channel keys. (observed 2026-08-25, PACT 4.6.x; until M7)
11. Task ids restart after a drain: join journal events on task id AND a time window AND the owner, never first-match on id. (observed 2026-09-05, PACT 4.7.x; until M12)
12. Deleting a task destroys its `metadata.handoff` with no warning; read the handoff first and prefer completing over deleting a touched task. (observed 2026-08-18, PACT 4.6.x; re-probe)
13. After TaskStop, confirm the name left `members[]` in `teams/<team>/config.json`; a bare-name stop can hit the wrong record and report success. (observed 2026-08-18, PACT 4.6.x; re-probe)
14. Task tools arrive deferred in a spawn; load them with `ToolSearch select:TaskList,TaskGet,TaskUpdate,SendMessage` before the first claim. (observed 2026-10-02 in a teammate spawn, Claude Code 2.1.288, repacted 1.3.7; re-probe)
15. The handoff key is lowercase `handoff`; any other casing is read by nothing and emits no journal event, and nothing warns at write time. (observed 2026-09-10, PACT 4.7.x; until M12)
16. TaskGet is metadata-blind; a ruling added to task metadata after dispatch reaches nobody unless you also send it (see pact-lead-playbook). (observed 2026-09, PACT 4.7.x; until M6)
17. A metadata write can fail with "expected record, provided string/unknown"; resend once unchanged, then probe with a tiny throwaway key, then read the file back. (observed 2026-09-19, PACT 4.7.x; re-probe)
18. Mirror anything that cost a measurement into a versioned file in the repo; task metadata is a coordination channel, not a record. (observed 2026-09-07, PACT 4.7.x; practice)
19. The session scratchpad is shared by every agent: namespace a subdirectory, never `rm -rf` a path you did not create this turn. (observed 2026-10-02 in a teammate spawn, Claude Code 2.1.288, repacted 1.3.7; re-probe)
20. Sweep the scratchpad before session end when an agent pulled real user or customer data; open each residual hit before deleting by path. (observed 2026-09-07, Claude Code, version n/a; practice)
21. Never write scratch or build files into `{config_dir}/tasks/`; it is the live task store. (observed 2026-07, Claude Code, version n/a; practice)
22. A shared file written by peers (ledger, register) must be append-only with a self-identifying line per record; section edits clobber. (observed 2026-08-18, PACT 4.6.x; until M7)
23. `compact-summary.txt` is written to the pact-sessions ROOT and can be archived into the next project's resume; check its team name before trusting it. (observed 2026-09-02, PACT 4.6.54; until M12)
24. `dispatch_variety` is write-once and never emitted for a variety stamped via TaskUpdate; read `metadata.variety` from the latest snapshot. (observed 2026-09-10, PACT 4.7.x; until M8)
25. `task_claim_gate` can auto-claim a peer's task on an unrelated tool call; treat status as a hint, check the owner, claim explicitly. (observed 2026-09-08, PACT 4.7.x; until M12)
26. A journal event is evidence of a moment, not of current state; a harvester cannot report live schema, deploys or row counts, so check state before acting on it. (observed 2026-08-04, PACT 4.6.x; practice)

## Detail

**What survives where.**

| Carrier | Keeps | Loses |
|---|---|---|
| task file `tasks/<team>/<id>.json` | every metadata key, current values | reaped on drain; overwritten values |
| journal `agent_handoff` | the handoff at emit time | writes after the emit; signal tasks; odd key casing |
| journal `task_metadata_snapshot` | every metadata write, per write | nothing, if you read ALL of them |
| pact-memory rows + processed-task ledger | what a harvest saved | anything never harvested |
| SendMessage thread | relayed text | not greppable by the next agent |

**Harvest checklist (lead).** Census keys per task; read journal events and every snapshot;
merge divergent revisions; include teachback tasks (they can carry work keys); re-run discovery
at the END of the harvest, since tasks complete mid-harvest; record the ledger line at harvest
time, not at wrap-up. When no carrier fired, your relay message is the only copy: label it
relayed, not measured.

**Plausible state evades review.** A non-zero `memory_count` or a populated Working Memory block
closes the question that a zero would open. Prefer structural tells (does the processed-task
ledger exist at all?) and run the check every time, not when the number looks low.

**Declared loss is terminal.** "It is gone" stops everyone looking. Run the recovery search
(every team dir, every snapshot, the journal, the repo) before writing a loss into memory.

**Runtime files (reference).** `sessions/<pid>.json` persists after exit, so check `kill -0 <pid>`
for liveness. Inbox `text` fields can hold JSON-stringified objects; parse, then fall back to text.
`completed_handoffs.jsonl` holds only `{task_id, teammate_name, timestamp}`. Open the pact-memory
database read-only (`PRAGMA query_only = ON`). (observed 2026-04, Claude Code, version n/a)

**Git in a shared worktree** is its own skill: repacted:concurrent-agent-git.

## Examples
- A cancelled smoke-test task was deleted after its teammate had written the handoff; the
  teammate idled believing it had succeeded.
- A harvest named five handoff keys; the task carried nine, and an omitted sibling held the arc's
  most important security finding.
- A handoff opened "CONFIRMED DATA LOSS"; a sibling key written later reversed it. Reading only
  `metadata.handoff` would have filed a false incident.
- A harvester read a migration-failure journal event and reported "the engine cannot run"; four
  queries showed the fix had been applied the same day.
