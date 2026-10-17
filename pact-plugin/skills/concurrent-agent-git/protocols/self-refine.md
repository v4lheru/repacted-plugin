# Protocol: self-refine (skill-specific part)

Context: this skill decays when a new shared resource or git behaviour bites and nobody records it.
Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which rule you applied, which collision happened anyway, which command
   behaved differently than written here.
- Pick the single smallest change that would have prevented the top friction: sharpen an existing
   canonical rule before adding a new one, and put new detail in the matching reference.
- If a workflow command changed, re-run it in a throwaway repo on this machine (BSD userland on
   macOS) before editing it here.
- Re-run a skill validator on this skill and the scrub check on touched files, then commit by
   pathspec with a one-sentence message.

## Skill-specific guidelines
- Anti-pattern: a wrapper script for commits. A pathspec in the command is reviewable; a wrapper
  hides it.
- Anti-pattern: moving PACT worktree-guard bugs here; they belong in repacted:pact-field-notes.
