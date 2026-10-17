# Protocol: self-refine (skill-specific part)

Context: this skill decays in two ways. Lessons from use stop reaching it, and its dated platform
facts (Supabase default grants, MCP version stamping, pg-boss v10, supabase-js typing) go stale as
the platforms change. Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve postgres-supabase for next time using evidence from this session and the user's feedback,
and record the change.

## Skill-specific steps
- If the session contradicted a dated fact (anything stamped "verified <date>"), re-measure it on a local or branch database, never by writing to a production project, and rewrite the fact in place with the new date. A correction appended beside the old claim leaves the wrong claim loadable.
- Re-run validation: a skill validator and the plugin's `scrub-check`. Write no project ids, connection strings, names or emails.

## Skill-specific guidelines
- Pattern: when a rule is proven by a query, keep the query in the reference so the next agent can re-run it.
