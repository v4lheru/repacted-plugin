---
name: pact-field-notes
description: 'Use when PACT itself misbehaves: resume or compact team-name mismatch, dispatch_gate "no Task assigned", task not found or metadata missing, HANDOFF harvest gaps, worktree write-guard pins, hook ask/deny semantics, merge-guard approval format, pact-memory recall or --replace surprises.'
---

# PACT field notes

Context: version-stamped workarounds for the moments the repacted runtime (forked from PACT 4.8.1)
or the Claude Code harness misbehaves, so recovery takes minutes instead of a re-derivation. Every
fact line carries its stamp: `(verified|observed DATE, VERSION; until MILESTONE)`. A line marked
"until Mn" works around a fork bug that repacted milestone Mn fixes (the backlog); a line
marked "re-probe" is a Claude Code platform behaviour; "practice" is a working habit, never
re-probed. `{config_dir}` means the Claude config root:
`$CLAUDE_CONFIG_DIR` when set, else `$HOME/.claude`.

## Workflow
1. Name the symptom class and open its reference:
   - team name wrong after resume or compact, "no Task assigned", "already a live member":
     `references/resume-and-team-identity.md`
   - task not found, metadata missing, handoff or harvest gaps, deleting a task, TaskStop:
     `references/task-store-and-harvest.md`
   - Write/Edit or git refused naming a worktree, results from the wrong tree, worktree cleanup:
     `references/worktrees.md`
   - a hook that does not block or does not fire, the merge-guard prompt, missing tools,
     background jobs dying, plugin data and channels, transcripts:
     `references/hooks-and-harness.md`
   - building, validating or packaging a skill, or running one outside Claude Code:
     `references/skill-authoring-runtime.md`
   - empty or short recall, `--replace`, a stale Working Memory block, editing a memory index:
     `references/pact-memory.md`
2. Run the discriminating check the note names BEFORE applying its fix. For team identity that
   is `pact-which-team` (read-only; lists `teams/` and `tasks/` by freshness, marks the team
   `pact-session-context.json` names, says whether its dir exists, and prints the branch).
3. Apply the fix for THAT branch only. Notes with two fixes say which symptom selects which;
   applying the other one wastes the attempt or reverts.
4. Prove the fix by its effect (a write probe, a spawn that lands, a count read back), never by a
   tool's success or error string.
5. If the note's milestone has landed in the installed repacted, or a re-probe shows the platform
   changed, do not apply it: delete or re-stamp the line (patch bump) and log it in
   `refinement-log.md`.

## Map
- `references/` one file per symptom class, each opening with "Canonical rules".
- `protocols/self-refine.md` how this skill updates itself after use.
- `protocols/live-reprobe-checklist.md` the re-probes only a live session can run.
- `refinement-log.md` history of changes, newest first.

## Related skills
- repacted:pact-lead-playbook: the lead's briefs, rulings and acceptance (not bugs).
- repacted:concurrent-agent-git: git discipline in a shared tree when nothing is broken.
- repacted:memory-hygiene: policy for writing and migrating memory.
- repacted:teammate and repacted:pact-memory: the protocol and tool these notes patch.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
