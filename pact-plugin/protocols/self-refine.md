# Protocol: self-refine (shared)

Every skill that carries a `protocols/self-refine.md` runs this at the end of a session that used
it; the skill's own file holds only what is specific to that skill (its decay modes and the steps
that only it needs). One home for the shared steps, so a fix to the procedure lands once.

## Mission
Improve the skill from this session's evidence and the user's feedback, and record the change.

## Steps
1. Review the session. Note where the skill helped and where it caused friction: a missing trap,
   a wrong claim, or a step the agent skipped. Then the skill-specific review points.
2. Ask the lead (when you run as a teammate) or the user for one concrete correction if any is
   available; it outranks your own reading of the session.
3. Pick the single smallest change that prevents the top friction from recurring: sharpen an
   existing canonical rule before adding one. A new rule is one imperative line in the matching
   reference, no longer than its neighbours.
4. Apply it to the right file: the router's numbered workflow for a discipline step, a reference
   for facts. Keep each reference under its size target; split before it grows past it.
5. Run the skill-specific steps in the skill's own `protocols/self-refine.md`.
6. Append an entry to the skill's `refinement-log.md`: date, observation, change, files.
7. Re-run a skill validator on the skill and `scrub-check` on the touched files; commit by
   pathspec with a one-sentence message.

## Guidelines
- Pattern: one evidence-backed change per session; log "no change" when nothing went wrong.
- Pattern: a correction replaces the wrong rule in place; never leave both sides in a reference.
- Anti-pattern: refining in the abstract, or growing the router; new detail goes in a reference.

## Next
This is the terminal protocol for a session. Report the change and the log entry to the lead
when you run as a teammate, else to the user.
