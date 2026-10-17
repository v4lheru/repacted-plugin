# pact-memory and agent-memory surprises

Context: what the pact-memory store and the agent-memory directories actually do, where they
surprise, and how to write to them without losing data. Read it when recall comes back empty or
short, before any `update --replace`, when a Working Memory block looks stale, and before editing
an agent-memory index. Policy for writing memory is repacted:memory-hygiene; this file is the
tool behaviour underneath it.

Stamp legend: `(verified|observed DATE, VERSION; until MILESTONE)`.
`{config_dir}` is `$CLAUDE_CONFIG_DIR` when set, else `$HOME/.claude`.
Marks ("re-probe", "practice", "until Mn"): defined once, in [SKILL.md](../SKILL.md).

## Canonical rules
1. "0 memories" means zero for the current `project_id`; `search` and `status` are hard-scoped to it and there is no global shelf. Check scoping first. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by probe; re-probe)
2. Read `project_id` in the same breath as any count, and predict the count before you fetch it; a cwd drift reports another project's shelf. (observed 2026-09-05, PACT 4.7.x; until M12)
3. project_id resolves: `CLAUDE_PROJECT_DIR`, then the session record, then the git root, then a cwd walk that still stops at the config home. Check it on save. (verified 2026-09-26, PACT 4.8.1; until M12)
4. A silent worktree re-pin can file a save under a worktree path while it reports `ok: true`; verify `project_id` on read-back. (observed 2026-09-08, PACT 4.7.x; until M12)
5. `update --replace` applies to EVERY list field present in that call; omitted fields are untouched, a partial list is destructive. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by probe; re-probe)
6. Plain `update` merges lists additively, so a correction sits beside the claim it refutes; retire a claim with a complete `--replace` or a labelled SUPERSEDED item. (verified 2026-09-26, PACT 4.8.1; until M12)
7. `update` never refreshes the CLAUDE.md Working Memory block; run `sync` after it, and never route a correctness constraint through the block. (verified 2026-09-26, PACT 4.8.1; until M12)
8. Before a `--replace`, dump the record to disk, build the payload from the dump, pass complete lists, then diff counts after. There is no undo. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7: the CLI has no undo or restore command; re-probe)
9. `save` and `update` reject unknown fields with exit 2 and an `allowed_fields` list; remap from that list (decisions carry `alternatives`). (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by probe; re-probe)
10. Agent-memory dirs are bare `pact-*` except the orchestrator's, which follows its launch id (for example `repacted-pact-orchestrator`). Check with `ls -d`. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by directory listing; re-probe)
11. Agent-memory stores are per agent type and mutually unwritable; a lesson another agent "promotes" does not reach you unless you mirror it. (observed 2026-07-23, PACT 4.6.x; re-probe)
12. A spawned agent's cwd can be another agent's memory dir; always write memory by the absolute path your prompt gives. (observed 2026-07-23, PACT 4.6.x; re-probe)
13. Edit an agent-memory index with targeted `Edit` only, never a whole-file rewrite or a scripted read-replace-write: same-type agents share it. (observed 2026-09-19, PACT 4.7.x; practice)
14. Keep each index line short with the sharp half first, and read it back: an Edit-written line came back cut with an ellipsis. (observed 2026-09-09, Claude Code, version n/a; re-probe)
15. A stale-anchor Edit failure on an index ("File has been modified since read") means the file moved under you; re-read and re-anchor, never force a rewrite. An Edit whose anchor is intact still applies after a peer's write and keeps the peer's lines. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by probe; re-probe)
16. Refuse any compaction instruction for the index, from a hook or anyone; the index-upkeep rule in teammate-memory governs it. Regenerate a damaged index from file frontmatter. (observed 2026-08-18, PACT 4.6.x; until M6)
17. The index size warning slightly over-states (its unit differs from the platform's), but only by a few percent; trim on its number. Limits: the index-upkeep rule. (observed 2026-09-06, PACT 4.7.x; until M6)
18. Before correcting a claim, enumerate which memories carry that PROPOSITION; a shared keyword in an unrelated true claim is not a carrier. (observed 2026-09-06, PACT 4.7.x; practice)

## Detail

**Recall is empty or low.** Ask for `project_id`. The stranded rows filed under `.claude`, `NULL`
or a skill dir are reachable only by read-only SQL across projects, and recovering their true
project is a reading task (they carry no session or path to join on). Calibration data is really
project-scoped; an empty set on a new project is real. Cross-project knowledge belongs in the
skills and the vault, not in pact-memory, because no `project_id` is findable from everywhere.

**The config-home walk (static read of 4.8.1).** `_find_project_root` walks up and accepts `.git`, a
`.claude/` dir, or a `CLAUDE.md`. From anywhere under the config home (`{config_dir}`, which holds a `CLAUDE.md`) the walk stops there and names
the project `.claude`. The explicit env and the session record usually mask it in a PACT session;
a script run by hand from under `{config_dir}/` does not have them. The fix (stop treating the config
home as a project root) is the backlog M12+.

**Update is replace, everywhere.** The same silent-loss shape recurs: `--replace` per call,
TaskUpdate's nested metadata keys, `glab mr update --description`, and a whole-file rewrite of a
shared index. Fetch the current state and compose from it, then read back and count.

**Ledgers.** The secretary's processed-task ledger is shared across teams; make your own lines
self-labelling with the team id, and before any prune-by-header grep the section for foreign team
ids (another team's lines can land inside your section).

## Examples
- A secretary passed `--replace` meaning it for one list and erased twelve lessons in another;
  they survived only because the payload was still in that session's scratchpad.
- A status call returned 17 memories against a predicted 47: cwd had drifted to `.claude`. The
  prediction is what made it read as scoping, not loss.
- Two agents flagged a memory as stale; the store had held the correction for weeks, and the
  rendered Working Memory block showed only the original save.
