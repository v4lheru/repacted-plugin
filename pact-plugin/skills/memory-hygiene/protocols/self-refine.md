# Protocol: self-refine (skill-specific part)

Context: memory failures are quiet: a truncated index line, a correction that missed a carrier, a
lesson filed where it never fires. This protocol turns one session's slip into the smallest change
to this skill that would have prevented it. Run it at `/repacted:wrap-up`, after the distill pass.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve the memory policy from this session's evidence, and record the change.

## Skill-specific steps
- List what went wrong with memory this session: a head `check-index` rejected, an inbox line
   that had no evidence path, a correction that missed a carrier, a distill row you could not place.
- Pick the single smallest change that would have prevented the worst slip. Apply the
   every-branch test; if it only helps on the branch that happened, log the instance as a
   candidate instead.
- Put it in the reference whose trigger matches: head and inbox edits, the distill pass, or
   corrections. Never write the head budget's figures into this skill.
- Re-run a skill validator, `scrub-check` on the touched files and `check-index --all`, then
   commit by pathspec with a one-sentence message as a patch bump.

## Skill-specific guidelines
- Pattern: one evidence-backed change per session.
- Anti-pattern: restating the index rule's numbers, or PACT's harvest mechanics.
- Anti-pattern: names of people, clients or projects in examples.
