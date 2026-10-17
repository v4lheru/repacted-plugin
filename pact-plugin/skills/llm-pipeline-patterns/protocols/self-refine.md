# Protocol: self-refine (skill-specific part)

Context: this skill decays fast. Provider APIs, SDKs and model families change monthly, so its
dated facts go stale, and lessons from use stop reaching it. Run this at the end of a session that
used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Improve llm-pipeline-patterns for next time using evidence from this session and the user's
feedback, and record the change.

## Skill-specific steps
- If the session contradicted a dated provider fact (anything stamped "verified <date>"), re-measure it with one live call and rewrite the fact in place with the new date. If the fact is a model id, price or cache floor, delete it here and point to `claude-api` instead.
- Re-run validation: a skill validator, the plugin's `scrub-check`, and a grep for bare model ids (`claude-.*-[0-9]`, `gpt-[0-9]`): every hit must carry a "verified <date>" stamp.

## Skill-specific guidelines
- Pattern: a provider fact earns its place only if it saves a wrong turn and is stamped with the date and SDK or API it was measured on.
