# VD-E: evidence in reports

Context: read this before you send a finding, a verification result or a risk to someone who will
act on it. The recipient can only weigh what you put in the message: the full output, the check
that could have failed, the method they can re-run, and the rows behind the aggregate. Test-runner
count mechanics are in repacted:shell-tooling-traps. Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

- **E.17** Never `tail` or `head` a run whose failure list is your evidence. Save the full output and reconcile the failure lines you read against the summary count before attributing any failure.
  - Truncation keeps the "something broke" line and drops the "what" body, so a FAILED line with no body is a rumour that reads as a finding. Status can stay correct while the evidence is cut.
  - Before writing "none of these are mine", count: if you read fewer failure lines than the summary reports, you have not read the set. Use a compact summary mode, or write to a file and read it all.
  - `tail` is fine only for a run whose result is a single number you already expect. Report a count and how you got it; if you truncated, say so rather than paraphrasing the failure.
  - Correct a superseded figure in place, at its original site; a correction in a different field leaves the stale one live. When a lead offers a charitable cause, check whether the real one is worse.
- **E.18** Decompose a conclusion into the links or surfaces it depends on and verify which were actually observed. One sound measurement cannot carry the rest.
  - The tell is a sentence that changes subject between clauses (a tool surface, then the transport, then an active code path) while its confidence stays flat. When corrected by such a claim, do not concede to its tone: call the thing yourself and show the raw response.
  - Write the conclusion first, list what it depends on, then pick the control. Write mechanisms down; the surface list is the reusable part.
  - Re-read a commit before raising a concern built on what you assume it contains (cheap). Re-run the experiment for a load-bearing claim nobody will revisit (expensive).
- **E.19** Deliver a finding or an unverified risk with the exact method or commands that would check it, so the recipient can act without you.
  - Label an untested risk "unverified, conditional". A vague warning ("hash tests can be brittle") costs an investigation or gets ignored; two commands let someone retire it with evidence in one round trip.
  - Pre-register predictions before the run; only a prediction sent first can be falsified. Ask which dimension your evidence actually varied (tool representation is not server version).
  - For a pattern in someone else's failures, state the sample, the detection method, what should change on their side, and a structural rather than dispositional cause. A finding without a method is a verdict; with one it is an instrument.
- **E.20** Authors cannot see their own verification defects, and written rules fire on recall, not in flow. Make checks structural, publish the check, and invite a cross-read.
  - Verification errors fail toward looking fine (a grep that cannot match, a control that passes on a typo, a wrong pipe stage's exit status, a `-k` subset). Instruments deserve mutation testing more than product code does.
  - Never cite a green whose scope you have not read: an advisory (`allow_failure`) job is an echo, not a second source. One source plus an echo is worth one.
  - State per assertion whether it could actually fail and how you know (ideally the mutation you ran), and name the assertion you are least sure of. Report the gap you did not close.
  - Structural habits: never pipe a command whose exit status is the evidence; select tests by explicit node id and read the deselected count; put the weight on a PRESENCE assertion; require a red's failure text to match the predicted reason.
  - Record a save that came from a defect (duplicated work that happened to corroborate) as luck, not as a control.
- **E.21** Quote the check that would have come out differently if you were wrong. A check that passes under every hypothesis makes even a correct conclusion unauditable.
  - Ask what the command would print if your conclusion were false. Prefer value-returning plumbing (`git rev-parse HEAD`) over a boolean predicate about it (`--is-ancestor` is also true of HEAD itself).
  - Two tools disagreeing about one fact is the finding, not a puzzle to settle in favour of whichever ran second. Accepting a right answer for a non-discriminating reason lets the next wrong answer through.
- **E.22** A discriminator separates two known hypotheses; it is not a health audit. State its blind spots when handing it to a verifier, and ask for the audit separately.
  - A verifier given only a tripwire optimises for the tripwire. A "money has cents" test cannot see a NULL rendered as `$0`; check nullability wherever a rendered number could be unknown.
  - When a verifier hands back an anomaly without ruling on it, rule on it with the reasoning shown. "Immaterial" without a mechanism is a shrug; the owner decides materiality, the verifier reports.
- **E.23** When the exact state is unreachable, measure the shared mechanism with an equivalent input, but label it PROXY, name the substitute, and state the band it covers.
  - Use a proxy only when the component and code path are genuinely shared. Keep what stays unobservable as its own finding; a proxy reported as the real observation is a false green.
- **E.24** Store raw per-entity or per-dimension data, not only aggregates; the question the data must answer later is unknowable when you bank it.
  - Keep spike rows until the consuming task closes, especially when a re-pull is rate-limited, paid or auth-gated. An aggregate cannot answer a question about its parts (a total cannot say which dimension moved).
  - Record ex-ante and as-carried values separately where a gate moved them, and bank a standing rule as its own record, not inside one sample.

## Examples
- E.17: A dashboard project: two failures reported from `pytest -q | tail -12` could not be backed with assertion text, and a hold on a production push followed; another run showed 14 of 17 failure lines and hid the reporter's own.
- E.18: a focus-ring "transferred" because three component diffs were byte-identical, while the ring is painted by an unchanged-but-unchecked stylesheet.
- E.19: a flagged view-hash fragility across Postgres majors was retired in one round trip because the two comparison commands came with the flag.
- E.20: five verification defects in one session were all made by agents who had the rule written down, including in the repo's own CI file.
- E.21: a HEAD claim was "proved" with `--is-ancestor`, which is true whether the commit is HEAD or an ancestor; the discriminating `rev-parse` went unquoted.
- E.22: a cents-based discriminator missed that 1,290 of 1,902 rows were NULL rendered as `$0`.
- E.23: a wrap-versus-clip question was settled on an existing 72-character label in the same row and span, reported as a proxy.
- E.24: per-journalist rows were deleted after reporting aggregates, and a refresh-token guard then correctly refused the re-pull.
