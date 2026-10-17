# VD-B2 (split): comments that decay

Context: read this when a comment, docstring, header, test name or warning bears on what you
are about to do, and when you have just changed code that prose elsewhere describes. Comments
go stale in a predictable direction, and the most confident ones are the likeliest to be
false. Entry file: [vd-b2-claims-comments-guards.md](vd-b2-claims-comments-guards.md).

## Canonical rules
- **B2.9** Read a component's own comments first, then treat each claim as a prediction to verify; a stale comment costs you if trusted and a good one costs you if ignored.
  - Grep the component's source for prose before theorising about its behaviour. A near-match is not a match (22 predicted, 23 measured): a stray failure hides best inside a documented class. State what each outcome would mean before a re-run.
  - When a comment states a RULE, restate it in general terms and check every case it covers; reading it at instance level ("this one red job is expected") leaves you believing you consulted it. Treat a heavily annotated config as a checklist to enumerate.
  - Before citing a green pipeline, check what it gates on: every `allow_failure`, inherited `extends:`, and each blocking job's `rules:`. Comparing the job lists of an MR pipeline and a default-branch pipeline is a cheap independent check.
  - Two sources agreeing is worth more than one; one source and its echo is worth exactly one.
- **B2.10** When a comment argues FOR the design you are about to change, test its premise; do not delete it (careless) or obey it (leaves the bug). A stale reason outlives its fact and talks you out of the fix.
  - Replace it with the measurement, so nobody restores the old behaviour on the old argument. Prove the fix with the relevant credential deliberately absent, so the result cannot be faked by a passing secret.
  - Ask which caller each guard is for: two guards on one path are often two callers' requirements collapsed (a shared secret for an unattended job, a session for a human).
  - A justification can expire through a change in a different file, with nothing in either to signal it. When you descope a mode or enum arm, grep comments whose reasoning assumes the old cardinality ("X or Y"), and sweep safe defaults justified by the option set.
  - When a ruling carries two reasons and one expires, do not infer lapse: check whether the remaining reasons stand, and look for "even after" or "regardless". Put the durable reason first when writing one, and grep for rulings that cite a fact when you change it.
  - After adopting someone else's implementation of a step you owned, re-read every comment you wrote about that step. User-facing copy decays like a comment and is read far more often; grep rendered strings after a change to what the system can do.
- **B2.11** Rank a stale WARNING above a stale fact: it sends readers hunting a hazard that is gone while the real change goes unnoticed. Sweep the premise's vocabulary and temporal markers, untruncated.
  - Search for the premise's words across the tree, then for `today`, `currently`, `for now`, `not yet`, `still no` in every file you open. Never `head` a sweep whose width you will report.
  - Replace counts with conditions and write the reason, never the arithmetic: a formula copied into three files goes stale in all three at once. Fix the source, not only the copies.
  - When deleting a claim, replace its reasoning, or the value reads as arbitrary. If a surviving constraint is checkable, move it into a test and prove the test can fail.
- **B2.12** Expect a comment describing a KNOWN LIMITATION to have outlived it, and rewrite a present-tense defect statement to past tense with a timestamp in the same change as the fix.
  - Nothing rewrites a comment when the gap it describes closes, and `KNOWN RESIDUAL` / `NOT FIXED HERE` headings are trusted most. Understating protection wastes effort; overstating it is how a leak survives review.
  - When a control changes polarity, grep both directions: comments describing the old hole and comments promising the old guard. A count in an index is the same kind of claim; replace it with a check that counts.
  - A correction can itself be superseded: read ALL corrections. Sibling keys carry no recency signal unless the content has a `supersedes` field; otherwise say the set is unordered.
  - A TRUE comment that reads as stale (forbidding something the product now visibly does) is as dangerous, because the reader's "fix" is the damage: give it a reason to be believed.
  - A comment asserting a fact about the world inside a test that sets that fact as a fixture can never be caught by the suite. Delete the dated measurement if the rule never needed it.
  - "Deliberately distinct" / "must not be shared" marks a convenient path someone will take: make it unrepresentable, and grep whether it has been taken.
  - Cite mechanisms, not bare numbers: minting a new id silently re-points every bare citation of it. Ship a revisit trigger in the diff that creates the assumption; a follow-up loses to release night.
  - Where a deployment could falsify a sentence in a shared, environment-agnostic file, write the REQUIREMENT ("this credential MUST be narrow") and let each environment record its own compliance.
- **B2.13** A "verified live on <date>" comment is a fossil: re-run the query before repeating it as current fact, and trust it less the older the date.
  - Its rigour markers (a count, "verified", a date) are what suppress the check. When authoring, say what the code does about a condition, not what the database contained on a Tuesday. A unique constraint that rejects a wrong belief is the loud case; everywhere else the comment spreads silently.
- **B2.14** After adding a thing, grep the SAME file for statements of its absence ("does not exist", "deliberately", "N entries", counts); the author is the worst-placed person to notice.
  - Dispatch a recurrent defect class as a PREDICATE to apply ("any statement whose truth depends on lane count or identity"), never a list to action: a list invites stopping when it is exhausted.
  - Never fix a stale duplicate with a fresh duplicate; replace it with a pointer to the canonical copy. Prefer the self-maintaining form: stop a header promising a complete enumeration, and name where the live index is.
  - Writing a rule down does not install it; you must meet it again at the moment of action.

## Examples
- B2.9: a lane's source already documented 22 failures with the alternatives eliminated while a User-Agent theory was being tested; the measured 23rd was a different failure. A CI comment explaining one red job meant no test suite gated merges at all.
- B2.10: a header argued that a direct library call would be "an unguarded path"; a cookie-less POST already returned 307, so it never was. `parseOriginMode`'s "include shows MORE people" stopped being true when a third outcome appeared.
- B2.11: a warning to watch a router shim sat in the code after the shim was deleted and attribution was enforced by a required field.
- B2.12: a `KNOWN RESIDUAL, NOT FIXED HERE` comment had been false for five days. A test comment stated the production model id while the test set that id itself. A route comment said "deliberately NO bulk endpoint" beside a new bulk UI that posts N times, which is why it is safe.
- B2.13: "0 rows, verified live 2026-08-02" was repeated as the main blocker four times; the table had been seeded two days after the comment.
- B2.14: a commit added a scheduled lane under a comment saying it was "DELIBERATELY ABSENT" and "FIVE ENTRIES" above six; a predicate-driven reader then found six contradictions where the list named two.
