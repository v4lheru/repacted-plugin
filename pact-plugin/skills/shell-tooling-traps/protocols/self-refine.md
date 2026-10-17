# Protocol: self-refine (skill-specific part)

Context: this skill decays when rtk, the harness or macOS changes behaviour and nobody re-measures.
Run this at the end of a session that used the skill.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Skill-specific steps
- Review the session: which shell reading was wrong or surprising, and which rule here caught it
   or failed to.
- Before editing a behavioural claim, re-measure it on your machine: `rtk --version`,
   `rtk-probe <cmd>` for a wrapper claim, `rtk rewrite "<cmd>"` for a rewrite claim. Stamp the
   result `(verified <date>, rtk <version>)`.
- A new runner format for `tcount` goes in the plugin's `_runcounts` parser module (bin), with a
   regression case in its tests.
- Re-run a skill validator on this skill and the scrub
   check on touched files; commit by pathspec with a one-sentence message.

## Skill-specific guidelines
- Pattern: when an rtk behaviour stops reproducing, record the version and keep the rule with a
  condition; a non-reproduction is not an all-clear.
- Anti-pattern: probing rtk through `eval`, a function or a script; the hook only sees the
  submitted command string, so such a probe measures unhooked behaviour.
