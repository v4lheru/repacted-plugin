# Protocol: self-refine (skill-specific part)

Context: this skill decays in two ways. Lessons from use stop reaching it, and its measured platform facts drift (the clasp version, the Workspace scope policy, Drive API behaviour). Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve google-workspace for next time using evidence from this session and the user's feedback, and record the change.

## Skill-specific steps
- Review the session. Note where the skill helped and where it caused friction: a missing trap, a wrong claim, or a proof step the agent skipped.
- If the session contradicted a dated fact (for example "verified 2026-09-26", "re-verified 2026-08-12"), re-test it with one read-only call and rewrite the fact in place with the new date. Do not append a correction next to the old claim.
- Apply it to the right file: the router's numbered workflow for a proof step, a reference for facts. Project IDs go to the project's docs/agent-notes/, never here.
- Re-run validation: a skill validator, plus the plugin's scrub check.
