# Protocol: self-refine (skill-specific part)

Context: this skill decays when a new secret path or a new destructive API bites and nobody
records it. Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which secret or delete rule applied, and where a leak or a wrong delete
   nearly happened anyway.
- A change to how keys are parsed belongs in `env-get` itself, with a test.
- Write examples with key NAMES and paths only: never a value, a prefix or a real file's contents.

## Skill-specific guidelines
- Anti-pattern: a convenience wrapper that hides a destructive verb or a host from the command
  text; string-matching guards then go silent.
