# Protocol: self-refine (skill-specific part)

Context: this skill decays fastest of all: vendors change APIs, defaults, pricing and limits
without notice, and lessons from use stop reaching it. Run this at the end of a session that used
the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve vendor-gotchas for next time using evidence from this session and the user's feedback, and
record the change.

## Skill-specific steps
- If the session contradicted a dated vendor fact (anything stamped "verified <date>"), re-probe it with a read-only call and rewrite the fact in place with the new date and version. A correction appended beside the old claim leaves the wrong claim loadable.
- Pick the single smallest change that prevents the top friction from recurring. Favour sharpening an existing rule in the vendor's reference over adding a file; a new vendor with several traps may earn its own section.
- Never add a rate limit, price or quota as a rule unless it was measured this session; stamp it with the date.
- Re-run validation: a skill validator and the plugin's `scrub-check`. Write no names, emails, account, workspace or channel ids, env-var values or token locations.

## Skill-specific guidelines
- Pattern: Railway facts go in `references/railway.md`, never into the vendor-installed `use-railway` skill.
