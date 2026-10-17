# Protocol: self-refine (skill-specific part)

Context: this skill decays when a review finds a defect class nobody recorded, or when a
recorded rule turns out wrong (a runtime behaves differently, a CVE is fixed). Run this at the end
of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which checklist rule found something, which found nothing because it no
   longer applies, and which finding had no rule at all.
- Pick the single smallest change: sharpen an existing checklist rule before adding one, and put
   it in the matching reference at the position where a reviewer running top to bottom meets it.
- Write the lesson generically: no secret values, token prefixes or shapes of a real credential,
   no project, client or people's names, no private IPs, and no recipe for evading a scanner or a
   guard. An unrotated credential goes to the lead, never into this skill.
- Leave the SACROSANCT block in the router exactly as absorbed; a change to it is a user decision.
- Stamp runtime and library facts with the version measured (`httpx 0.28.1`, `next 15.2.3`).

## Skill-specific guidelines
- Anti-pattern: adding textbook security advice; keep only what would have saved a wrong turn here.
