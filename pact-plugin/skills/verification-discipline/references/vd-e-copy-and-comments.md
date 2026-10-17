# VD-E: copy and comments

Context: read this before you write a code comment, a docblock, UI copy, an alert, a disclosure
or a report line that a reader will draw a conclusion from. The acceptance test is what the reader
concludes, not whether each sentence is true. Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

### Comments and docblocks
- **E.43** Write the MECHANISM in a comment, never an inferred outcome, and keep a claim and its caveat inseparable. A rename must rewrite the justification, not just the nouns.
  - A comment claiming an outcome you inferred ("no ring painted") is a hostage to the next measurement; one stating the mechanism and what is unverified stays true. The false version is more persuasive, which is why it survives review.
  - A caveat kept elsewhere (the handoff, a message) is the half that never gets read; if the caveat makes the sentence awkward, the sentence is overstated. Give a second audience a pointer, not a copy, and record the lookup that defeated you where it happened.
  - A proxy establishes a mechanism, not a verdict about your element, and it can over-predict failure. After correcting an outcome, re-run rather than re-read the "mechanism" you kept; an arm of an experiment can measure something other than its label (an ordering effect). A measurement is not evidence for the label it was filed under.
  - Report evidence as an enumeration; a summary hides its own holes. Keep a right change and correct its wrong reason, in place, with its history ("an earlier version wrongly said X").
  - A commit message is not a correction: when you disprove a claim mid-task, grep your own comments and docs for it before staging.
  - A rename driven by a changed decision leaves a comment arguing the losing side, which invites the revert. Rewrite the argument; add a do-not-re-add note at a deletion site.
- **E.44** Accept copy by what the reader concludes, not by whether it is true. First ask whether the reader needs the fact at all, then whether it is true.
  - A true implementation detail (write cadence, retries, scheduling) beside an unchanging number can lead the reader to the very misreading you were fixing. Cut it, and record the cut as a deliberate deviation with its reason.
- **E.45** A thorough caveat about one open question implies no other is open. Caveat each open question where its own symptom lands, and say inside the paragraph what it does not cover.
  - Ask what other question could bring a reader to this exact spot, usually a different failure moment or symptom of the same constant. Record a failed probe as non-discriminating together with its mechanism, never as untested or passed.
- **E.46** Copy describing a control must be present tense with no promise. A forward-looking claim cannot be falsified at review and outlives its truth; pin the rule with an absence assertion.
  - A request for copy "true whichever way a deferred decision goes" about the control it describes is unsatisfiable; contest it and substitute present tense. Test for the absence of will, soon, future, coming, planned, upcoming.
  - Re-derive whether the proposed sentence is true today against the code that consumes the flag, not against the design doc. Copy that understates what a control touches is the same defect as copy that denies it works.

### Numbers in alerts and displays
- **E.47** In alerts, print the absolute observed value, the baseline and a time anchor, not a percentage. A leading-indicator alert states exposure and mechanism, never predicted loss.
  - A ratio hides its base and lies at both extremes (an unmatched window inflates it; a tiny base reads as "-100%"). Demand both absolutes from the backend; if a ratio is unavoidable, use coarse buckets, round toward the less dramatic end, and suppress below a volume floor, in the testable module.
  - Exposure form: a certain, current fact (the URL returns 403; 90 of 90 ads disapproved), the normal daily flow through the affected thing, and the mechanism. Let the loss arrive by editing the message as it is observed.
  - Never let a parent aggregate read healthy while a segment detector is open; summarise as a count of problems ("9 of 47 campaigns have stopped").
- **E.48** Counter over-trust in a displayed score by rendering its weighted composition beside it, not by visual hedging. A renormalised score breaks sorting, so rank with a shrink-to-baseline key.
  - Weights beat counts: "3 not demonstrated" can mean 8% or 40% of the matrix. Show the quantity the system itself acts on (coverage), bind it to the number with a word, and assert it on computed style.
  - Carry the qualifier to every surface; the widest-audience surface (a Slack post) is often the one without it. Name what a sort key does without rendering it as a second numeral, and derive its baseline at runtime from the domain's own definition.
  - Distinguish "scored zero" from "never demonstrated" with no numeral for the latter. An uncalibrated model gets a data-driven "uncalibrated" state that retires itself.
- **E.49** Never clip verbatim evidential text (quotes, testimony, citations, log lines) to fit a budget. Include whole units, declare omissions ("+N more"), and emit a notice if every item exceeds the ceiling.
  - A clipped quote misrepresents the speaker invisibly. A per-item cap is a sanity ceiling: skip an over-long item whole and report how often that fires. Generated summaries may be clipped; evidence may not.
- **E.50** When a figure gains prominence its disclosure must move with it. For any headline aggregate, check the exclusion's size, whether every counted row contributes to the sum, and unit homogeneity.
  - A stakeholder request for a figure a previous decision refused to fake can be met by printing it and making it falsifiable in place. Check what sits next to the number, not only the number.
  - Before changing a peer's taxonomy to satisfy a request, query the stored vocabulary; the defect may be downstream rendering.

### Screens and the rules you write
- **E.51** A correct fail-closed refusal and the defect it guards against render the same screen. Tell observers which one they will see, with the verbatim on-screen text for each outcome, before they look.
  - Afterwards you are arguing them out of a belief formed from real evidence. Ask what silent prerequisite's omission produces a correct-looking broken screen, and verify the fixture against the real functions first so a null result means something.
  - The mirrors arrive in pairs: an always-true column reads as confirmation; a legitimately absent value reads as failure. Know before the run which half of a claim is provable from state and which needs an event.
- **E.52** A rule learned in one role does not fire in the mirrored role. Write each rule in both its reader and author forms, and attach a question-framing finding to the step that asks the question.
  - After writing anything a checker will consume, ask who will grep it, for what, and whether your text satisfies their pattern by accident. When your own check fails surprisingly, suspect the check; make it structure-aware rather than degrading the writing.
  - A finding about how to frame a question cannot fire while a prescribed step is asking it; amend the step itself.

## Examples
- E.43: a comment asserting "no ring painted" was falsified by measurement, while a sibling comment saying "not proven, no browser here" needed no edit.
- E.44: "refreshed daily" was true and was cut, because beside a monthly figure it read as a broken pipeline.
- E.45: a careful docblock on a token modifier sent the person debugging an immediate registration refusal away from the right file.
- E.46: "used in a later cycle; no effect yet" described a write-target control that had been live for months.
- E.47: spend was up 186% at 09:59 on the day ads were disapproved, so a loss-led alert would have been false when it mattered.
- E.48: two perfect answers renormalised to exactly 100.0 from 8% coverage and would have sorted to the top.
- E.49: quotes are packed whole within the budget, with "+N more quote(s) not shown" after them.
- E.50: excluded duplicate-record closes outweighed the real losses beside them.
- E.51: three correct refusals all rendered as "identity capture is broken".
- E.52: a closure that quoted retired text tripped the author's own absence check for that text.
