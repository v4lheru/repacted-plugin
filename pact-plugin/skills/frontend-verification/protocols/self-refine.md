# Protocol: self-refine (skill-specific part)

Context: this skill decays when a framework ships a new major version (Next.js, Astro, Tailwind,
React Query, Playwright) or a new rendering trap bites and nobody records it. Run this at the end
of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which rule applied, which no longer matched the installed version, and
   which rendering trap cost time without being here.
- A new rule carries the framework version when the behaviour depends on it.
- Only record a rendering fact you MEASURED in a real browser or from the built artifact; say
   which artifact and version. A claim from docs or reasoning is labelled as such.
- Keep project UI conventions out: they belong in the project's docs. No client names in examples.

## Skill-specific guidelines
- Anti-pattern: adding visual-design taste; this skill is about proving what rendered.
