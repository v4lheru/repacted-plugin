# Protocol: self-refine (skill-specific part)

Context: verification lessons decay unless new incidents land in the right reference. Run this
at the end of a session that used verification-discipline, or when the distill pass
(repacted:memory-hygiene) routes an inbox lesson here.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve this skill from evidence in this session, with one small durable change, and record it.

## Skill-specific steps
- Review the session. Which claim did a question in the router catch, and which claim slipped
   past every step? A slipped claim is the evidence that matters most.
- Find the reference that owns the lesson by its question (the router's Map). If a rule there
   already covers it, add at most one Examples line. Do not add a duplicate rule.
- If the lesson is new, add ONE imperative rule line (at most 200 characters) under Canonical
   rules, with the next free id. Add its incident to Examples.
- Never write a secret, token prefix, private IP, personal name or email into a reference.
   Run `scrub-check` on the skill directory and require exit 0.
- If the reference passes 12 KB, split it by family, update the router link, and record the
   split in `../refinement-log.md`.
- Append an entry to `../refinement-log.md` (date, observation, change, files).
- Re-run a skill validator, and report
   its output.

## Skill-specific guidelines
- Pattern: one evidence-backed rule per session beats a rewrite. The log should read as a
  history of real claims caught or missed.
- Pattern: keep project-specific facts in Examples. They are what save the
  next wrong turn.
- Anti-pattern: growing the router. New detail goes in a reference, and the router gains a line
  only when a new question is needed.
- Anti-pattern: carrying a self-retracted draft. Distil only the final correction.
