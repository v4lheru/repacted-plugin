# VD-B2 (split): citations and names

Context: read this when a comment's rationale, a `file:line` citation, a doc, a rationale for a
ruling, or an identifier's name is doing load-bearing work in a conclusion. Each can check out
while saying less than, or the opposite of, what you took it to say. Entry file:
[vd-b2-claims-comments-guards.md](vd-b2-claims-comments-guards.md).

## Canonical rules
- **B2.15** A comment that agrees with its code covers only the cases it names: enumerate the cases the branch admits, and name a pin test for your suspicion, readable by a cold reader.
  - A rationale naming fewer cases than its branch admits is the finding (it describes recovery; the branch also admits a plateau). An adjacent case's rationale in the same file often transposes word for word.
  - A passing test named for a behaviour is the strongest statement that the behaviour is intended: name a pin for the SUSPICION ("clears while the count is still down"), never the mechanism.
  - CI prints file, describe and test name on one line; say which system, what happens and when, avoid private vocabulary, and re-run a mutant after any rename.
- **B2.16** A citation that checks out lends its credibility to a characterisation nobody re-derived: verify the VERB (overstates, understates, strips, validates, enforces, prevents), not only the `file:line`.
  - An underselling comment hides an incomplete control better than an overselling one, because nobody audits what they do not know is there. The risk is highest when the surrounding measurements are good.
  - When correcting a misdescription, leave the correction visible.
- **B2.17** For each behavioural claim in a doc or skill, walk the code path that implements it; a doc stricter than the code reads as a safety rule and evades a keyword grep.
  - A `raise` is not a refusal until you know which call path reaches it (the same function with `create=True` writes the key). Applies to SKILL.md, CLAUDE.md, tool descriptions and agent prompts.
  - Watch the grammar: "everything is changeable EXCEPT X" can be true and still rebuild a prohibition you removed. Phrase by category, not carve-out.
- **B2.18** State a claim and its check as two sentences and ask what could make the check pass while the claim is false; a check ADJACENT to the claim passes honestly and hides the gap.
  - For a control tied to a named function, ask which rows and paths THAT function touches, not what the schema permits. Two controls on two paths do not substitute for each other.
  - A decision was load-bearing for its author; its REASON is load-bearing for you. Verify the reason, not the decision, and label unmeasured parts of a rationale you hand down.
  - A peer-to-peer correction needs a second hop to the shared artifact or it evaporates. Mark a superseded consultation with a staleness banner and an errata that lists what still stands. Mark what you CHECKED versus what you BELIEVE.
  - Self-review cannot catch this, because the check and the error come from the same model: publish the check (which assertions could have failed), not only the result. A false alarm and a missed loss are the same failure with opposite signs; one cheap check settles either.
  - Name enum values so that the literal negation of one is another (`demonstrated` / `not_demonstrated`); a neutral third value (`assessed`, `processed`) reads reassuring because it negates nothing.
- **B2.19** Treat a parameter, field or label name as a claim about form or provenance: trace the actual value to its origin (column, query, API method and params) rather than trusting the name.
  - A name asserting the contract (`projectUri` holding a bare id) makes every reader stop checking; rename it as part of the fix. Make the DEFAULT fixture the production form, and build one total converter that refuses malformed input.
  - Normalise at the write call site when the internal value feeds dedup keys or joins; do not uniformly normalise a vendor-echoed value. An empty origin table is unobservable, not unverified.
  - The same field name can carry opposite provenance one directory apart: trace UI → hook → route → helper → SQL per surface, before the teachback when it shapes the work. In a coexistence or dual-write window, scope every health signal to one side until you find its writer.
  - A metric's label is not its source; trace one hop past the fetch function to the API method and params. A "new metric" request may be a rename; enumerate the grain × naming grid, and check whether the value is already fetched and discarded.

## Examples
- B2.15: `applyDerivation` cleared a scope alert on any success, justified for recovery; the same branch cleared it on a plateau at the reduced count.
- B2.16: a research note said a comment "overstated" its regex; the comment undersold it, since the regex also stripped phone and date-of-birth fields.
- B2.17: a compliance doc said an unknown setting "does not exist"; `set_value` walked with `create=True` and wrote it.
- B2.18: "erasure deletes the record" rested on an `ON DELETE CASCADE` that fires on a different deletion path than erasure takes; an anonymise function scrubs the row instead.
- B2.19: one `total_*` column was warehouse revenue on one route and a vendor headcount on another. A column labelled as one metric was fed by a differently named API field.
