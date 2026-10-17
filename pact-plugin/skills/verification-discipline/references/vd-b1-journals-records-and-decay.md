# VD-B1: journals, records and decay

Context: read this when you are about to bank or brief something from a journal, handoff,
snapshot, clean scan, earlier clearance, spec read at warm-up, or a recorded defect, TODO,
decline or open item. Each is true of the moment it was written.

Also read this when a finding, a filename, a cited location, a "stale" mark, a config
reading or a recorded workaround is about to drive what you do next. Each looks like a fact
about now and is a fact about some earlier moment or some other tree. Entry file:
[vd-b1-snapshots-and-corrections.md](vd-b1-snapshots-and-corrections.md).

## Canonical rules

### Journals and clearances
- **B1.4** Read a journal, handoff or snapshot as claims at a timestamp, often written before the outcome existed; check the live artifact before banking a failure, absence, defect or open item.
  - A negative in a derived artifact is the case where the source has most likely moved on. A session that ended shortly after a failure is where the fix is likeliest to be invisible. An earlier gate short-circuiting a later one leaves the later one OPEN, not refuted.
  - Compare a handoff's timestamp with the commits and mtimes it describes; a handoff full of open questions is the highest-risk kind. Where a handoff reports a defect in a file still on disk, read the file and bank the defect with its closure.
  - A journal's last entry dates the last thing that told it something, not the last thing that happened. Ask whether the RECORD is still being written, using a witness it does not own (`git log -1 -- <file>`, an image digest), and state that gap before the conclusion.
  - An inference inherits the time bound of its oldest premise. Falsifying someone's unverified claim gives you no standing to make one; a harvester's output carries a tone of adjudication and outlives everyone who could contradict it.
  - A log-only agent cannot report live state; say so in its dispatch, and check the state before dispatching action on an urgent claim. A false provenance label ("lead-supplied" for harvested content) is worse than none.
  - When an experiment settles a question mid-session, close it in the artifact, the memory AND the handoff that raised it; do not score an implementer on hedging later work resolved.
  - Re-send merge, deploy and verification outcomes yourself after a handoff, into a sibling metadata key, or hand them to the memory keeper if the slot is gone. Check what others attribute to you; when disowning an attribution, stop, without guessing whose it is.
- **B1.5** A clean scan or a clearance proved by enumeration holds only for its input domain at run time; any change that widens the domain voids it, so re-run instead of citing it.
  - Widening events need not touch the scanned files: an ignore or exclude list loosened, a glob or allowlist changed, a client wired, a flag flipped, a new call site into a shared helper, a test tier reaching production code. Narrowing is safe for correctness but can hide regressions.
  - Classify a clearance as STRUCTURAL (a type, a required parameter, an allowlist constructor) or ENUMERATIVE (a grep count, "every caller"), and say which. Extend sink greps to the test tree; an allowlist constructor holds only while its fields stay scalars.
  - A premise falsified in the safe direction can make a LIVE control look disposable. Re-derive against the code, classify controls by the axis the premise touched, and ask whether the new mechanism is finer or coarser than the old one.
  - A gate needs a third answer for "the check could not run": confirmed, violated, could-not-ask.
- **B1.6** Re-read the spec section a finding rests on at the moment you write the finding; specs and design memories change mid-task, often announced only to the lead.
  - Stat the reference docs at warm-up and before each signal, and put the line number and mtime in the signal. Frame a departure you cannot adjudicate as "confirm or reverse".
  - Re-verify same-session memories before a build consumes them. A builder's memory-vs-spec conflict is a staleness probe; sort it by type (the spec moved, or the memory encoded an unauthorised action).
- **B1.7** Rank re-verification by decay rate: defect records, TODOs, open items and declines decay fastest, because recording them causes the fix; check the code before briefing one as live.
  - Rank: defects, TODOs, open items and deferrals > "not merged / not applied" status > live counts > design rationale and rulings. When still live, say what you checked and when.
  - Bank a defect's shape and fix-direction, not only the broken state, and re-frame a fixed defect as the precedent for a pattern. Whoever implements a recorded decision owes that record an update.
  - A superseded TODO has no staleness tell, since nothing measures it. "Decided but not built" is a claim about code: grep before re-dispatching.
  - Correct an open item at its carrier and mark it completed WITH its evidence; a deleted item is indistinguishable from one never done. Write "owed by X as of T".
  - A deferral gated on "X does not exist yet" is partly discharged by whoever builds X; grep for the assertion itself before briefing the gap, and split the obligation from its level.
  - Record a decline with its decider, date and the fact that would reopen it; a refusal justified by another system's absence inherits that system's roadmap. Do not let a reversal discredit the mitigation.
  - Give every imperative ("should be raised", "must be checked") an owner and date, or write it conditionally. Show superseded and new readings with their times. Delete a prose hedge once a measurement discharges it; ledger items are marked closed with evidence instead.

### Records
- **B1.8** Re-verify a review finding against committed HEAD before it drives a fix, and attach the tree (branch or worktree) to every line number or fact you quote.
  - A self-review of code amended mid-task can describe the pre-amendment behaviour. Use `git show HEAD:<file>` and drop stale findings loudly.
  - "Already fixed" is not "never true": correct-looking cited lines may mean the reporter fixed it. A citation now pointing at unrelated content is evidence a fix inserted lines above; search for the content, not the line number.
  - In a multi-worktree session, two contradictory line numbers can both be right. Compute the delta before calling either wrong; if it equals a known diff, that is the explanation. Never key a tool on line numbers while branches are in flight.
- **B1.9** A timestamped filename records when a file was filed, not when its content was captured; check its mtime and its terminal state against an independent source before executing its next step.
  - A summary's "next step" is the most dangerous line in it: written to be executed, from its oldest state. Cross-check against `git log` times and task-store mtimes, report the discrepancy, and do not delete the archive as cleanup.
- **B1.10** Classify a cited artifact location as durable or ephemeral before reporting the artifact as existing or deleted; a re-created, empty scratch directory with a fresh mtime is the signature.
  - Count files (`find <path> -type f | wc -l`). Report the fourth option, "written outside the project tree, now gone", rather than forcing "never written / stripped / deleted".
- **B1.11** Marking a figure stale needs evidence just as marking it current does: read the configuration the run itself reports before striking a measurement.
  - A config change stales only measurements taken BEFORE it; chronology in a conversation is not evidence. State a staleness mark with its basis ("measured at 45, deployed is 30"), and default to "provenance unknown, check the run".
  - A false stale mark is not the safe error: it carries no signal, destroys standing without measuring, and nothing ever collides with it. Prudent-looking claims escape scrutiny.
  - Record a hazard on the component and a margin as a dated value, so a later parameter move re-opens the question by itself.
- **B1.12** To learn what a system DID, read the configuration embedded in the run or the changelog, never the current live file or object.
  - Landed-then-reverted is indistinguishable from never-landed through current state (use a changelog). A Kubernetes ConfigMap's `creationTimestamp` does not change when `helm upgrade` rewrites its data; use `helm history` and `helm get values --revision N`. An n8n execution embeds its own workflow version.
  - A substring grep hit is not a reference. When you feel confirmed and a teammate or the user holds contradicting evidence, chase the contradiction.
- **B1.13** A recorded workaround is more dangerous than a recorded failure: test the BROKEN thing first, and date every workaround with the condition it works around.
  - A failure invites a check; a workaround in instruction voice gets adopted and never touches the premise. Environments heal silently. Record the diagnosis over the detour.
  - Advice ages fastest when it was correct, because nobody re-reads what worked: when you land a control, audit the advice you gave before it, per named consumer. Assert the COST of your own ruling in tests too.
  - Compare SETS, not counts, when two methods agree: two different mistakes can land on the same number.
  - Relay mechanics (carry the method, never "verified" for a check you did not run, label brief items, two agreeing negatives, same object first): E.1 and E.2 in [vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Examples
- B1.4: a harvest reported that a C3 engine "cannot run" from a `42P17` failure fixed the same hour; four SELECTs showed every object present. A journal ended nine days before production stopped changing, because deploys bypassed git.
- B1.5: an allowlist justified by an unscoped credential looked redundant after scoped OAuth2, but the new scope was coarser than the allowlist.
- B1.6: an audit flagged a composite key as a departure from a spec section corrected an hour earlier.
- B1.7: a "the column is NOT coming" note stood while the column shipped eleven days later, unannounced. "Decided, NOT built" was briefed six days after it was built.
- B1.8: a MINOR finding described code an amendment had already guarded, and nearly caused a duplicate guard. Call sites at 83/73/80 on main and 70/61/80 on a branch differed by exactly one diff.
- B1.9: a compact summary named 09:15 on the 9th had mtime 20:56 on the 8th, and its next step would have re-run a completed production region move.
- B1.10: a "65/65 Tier A" harness had run in a session scratchpad that was later emptied.
- B1.11: three of five figures struck as stale after `EXPIRY_DAYS` 45 → 30 had been measured at 30; the execution reported it.
- B1.12: a ConfigMap dated June carried `BATCH_SCHEDULER_ENABLED=false`, but revisions 31–34 had it true, which explained a July API bill (a dashboard project). A live JQL filter was added after the incident it seemed to explain.
- B1.13: a journal said "venv 3.13 symlink broken, use python3.12 + PYTHONPATH"; eight weeks later the venv was healthy.
