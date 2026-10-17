# Dispatch premises and deviations

Context: a dispatch, a spec or a lead's message carries factual premises (what exists, which options
there are, what is recoverable, who can do a step) and instructions that can conflict with the code
or with each other. Read this when a premise looks false, when two instructions disagree, or when
you are about to do something other than exactly what you were told.

## Canonical rules
1. Every factual premise in a dispatch is a claim: check what exists, which options exist, what is recoverable and who can execute a step against the code or artifact before acting.
2. A negative premise ("reasoning unrecoverable", "no account exists", "nothing applied yet") is the first thing to falsify; one grep of the artifact often recovers what the process layer lost.
3. Before building "add X", grep for X and date it with `git log -S`; if it exists, report the contradiction with the introducing commit and let the lead pick the real scope.
4. When asked to choose between options (moments, states, surfaces), open the component and confirm they exist; name the real option set and say you are contradicting the framing.
5. Before designing safeguards for a risky feature, grep the code you will change for a recorded ruling against it; building past one is a reversal, which needs its owner.
6. "The user does X by hand" is a stale capability claim: re-check it once you find the id, credential or CLI that would let you do X, and name the exact blocker if you still cannot.
7. Report a falsified premise to the lead rather than quietly delivering something better than commissioned; the lead's belief probably drives other decisions too.
8. When a dispatch and a design doc conflict, do not pick by precedence: test each against its own stated reason, then report the conflict and your analysis to the lead.
9. When two statements of a bar diverge, the text cannot settle it, and one reading favours you, do not pick: name both, say which you can meet on what evidence, and hand back the decision.
10. Grep the design doc for `supersede`, `ruling`, `overrid`, `first draft`, `replaces` at warm-up; dispatches written from an earlier handoff carry superseded text.
11. When you deviate from an instruction, even to fix a defect you already corrected, name the deviation, the reason and the exact substitute, and offer the literal wording.
12. A message that praises a correction and then restates the uncorrected thing was composed from a stale copy; flag it as propagation, not as a reversal.
13. If you worked around a broken instruction, report it anyway: private adaptation is what keeps the defect alive for the next agent. When right by accident, write the rationale down.
14. When a go-ahead carries an "unless X" carve-out, look for the evidence that would falsify X (a pinned test, a sibling field, a caller count) and report that evidence, not just "checked".
15. When two instructions pull against each other, look for a framing where both hold (a standing rule instead of a present-tense claim) before gating one on the other; say you diverged.
16. A count, key list or carrier list in a dispatch is a floor, never the scope: enumerate from the record itself and report the delta.
17. State the predicate, the population and the source beside any number you pass on; a correction fixes the instance, not the error rate.
18. A self-report of an action ("dispatching now", "I pushed") has no detector: look for the artifact the action would leave, and write your own reports in the checkable form.
19. When your finding supports the recipient's existing claim, raise the evidence bar rather than lowering it, and say which arm is unobserved.
20. A dispatch that offers "run the destructive setup script or report a BLOCKER" is a false binary: use a private scratch resource and report the substitution with the destructive line quoted.

## Detail

**Where premises go wrong.** Premise errors are confident, not uncertain, so nobody flags them at
dispatch. The discriminating question for each load-bearing fact: was it MEASURED on the success
path, or INFERRED from an adjacent observation (an error response, a failed lookup)? Both kinds
of false premise in one measured arc were inferences from adjacent observations. Even a spec
written from the lead's own live investigation carried two false facts; one survived because the
advice it produced was harmless and so never failed.

**Reporting the premise error.** Put it where the lead reads your doubts at teachback (see
repacted:teammate for the fields), with the file and line that falsify it, the option set
that does exist, and your pick. Mark the seam item by item when part of a record is recovered and
part reconstructed.

**Reading an instruction's justification.** A conditional instruction whose premise is false
usually inverts: "amend it, it is unpushed" becomes "stack a new commit, it is pushed". Follow the
author's stated logic with the corrected premise, and say so. Existence and relevance are different
predicates: a "verified" target file can exist and still not be the one that matters, so check what
it contains.

**Self-consistency check for a doc-vs-dispatch conflict.** Extract each document's stated reason
and test whether its own prescription satisfies it. A document whose prescription violates its own
reason has named its own loser; that finding goes to the lead with the conflict. Where stored data
or a user decision is affected, it is the lead's call.

**Deploy steps you can do yourself (clasp example).** Clone live into an isolated temp dir; compare
with `cmp` or `md5`, not a wrapped `diff`; keep the clone from session start as the drift control;
confirm live lacks your symbols; copy editor-only files locally first because `clasp push` deletes
remote files absent locally; generate `.claspignore` from the file list; re-clone after pushing and
compare file by file; check the deployment list before and after (a pinned version is untouched by a
push, `@HEAD` and triggers are not). `clasp run` executes with the calling token's scopes; compare
them with the script's declared scopes before creating an API-executable deployment.

## Examples
- A reconstruction dispatch said the reasoning behind a set of edits was unrecoverable; the
  implementer's dated comments inside the workflow JSON stated the root cause verbatim.
- A brief asked whether a warning belonged at tick time or save time; the UI had neither, only a
  one-click button whose click was the write.
- A route file already said "no bulk endpoint, by design; the absence of the route is the only
  guarantee a UI change cannot bypass"; every mitigation in the dispatch was a UI change.
- One figure went 12, then 9, then 52, then 37 across three days; each correction fixed a different
  axis (count, scope, source) and none protected the next.
