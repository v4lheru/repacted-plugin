# Protocol: self-refine (skill-specific part)

Context: this skill decays when a library ships a new major version or a new tooling trap bites
and nobody records it. Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which rule applied, which did not match the installed version, and which
   trap cost time without being here.
- A new rule carries the library version where the lesson depends on one.
- When a rule is superseded by a new library version, rewrite it in place with the version
   boundary; do not leave both claims.
- Write examples with no project paths and no client names unless the fact is client-specific.

## Skill-specific guidelines
- Anti-pattern: adding generic TypeScript or Python style advice; this skill holds traps only.
