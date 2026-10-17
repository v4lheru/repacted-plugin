# Protocol: self-refine (skill-specific part)

Context: the lead's own failure modes concentrate in the coordination layer (briefs, rulings,
relays), where nothing versions or reviews them; this protocol turns a session's slips into the one
rule that would have prevented the worst of them. Run it at `/repacted:wrap-up`, after the distill
pass.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve the playbook from this session's evidence, and record the change.

## Skill-specific steps
- Diff your own messages against the task texts of the session: list rulings, rationales or
   supersessions that never reached the durable record, and teammate corrections of your premises.
- Pick the single smallest change that would have prevented the top slip. Apply the every-branch
   test: keep it only if following it would have helped on every branch, not just the one that
   happened. Otherwise log the instance and mark the generalisation as a candidate.
- Put it in the reference whose trigger matches (brief, ruling, acceptance, long job). A teammate
   lesson goes to repacted:multi-agent-collaboration; a runtime bug to repacted:pact-field-notes.
- The commit is a patch bump.

## Skill-specific guidelines
- Pattern: one evidence-backed change per session; many new rules in one session is a warning sign.
- Anti-pattern: restating PACT protocol mechanics or the user's merge-approval rule here.
- Anti-pattern: teammate or colleague names in examples.
