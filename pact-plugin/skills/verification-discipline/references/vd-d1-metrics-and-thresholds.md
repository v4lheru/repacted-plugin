# VD-D1: metrics and thresholds

Context: read this before you adopt a metric as acceptance or detection evidence, set or review a
threshold or gate, score a classifier, count samples toward a conclusion, or read a column of
verdicts. The question is whether the instrument CAN do the job, which is separate from whether
today's number is right. Entry point and the other D1 splits:
[vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### Can it move, can it fire
- **D1.48** Before trusting a metric as acceptance or detection evidence, prove it can move: read its write path, and measure it under the real failure against quiet control days.
  - Grep the WRITE, not the column. A column bound to a literal (`0.0`) looks like live instrumentation and never varies; a dead measurement validates whatever you already believe.
  - Treat a requirement's named metric as a hypothesis. Ask what would hold it flat while the system is broken (budget, quota, retry, cache); compensating mechanisms are the usual answer.
  - Report the incident value AND the worst quiet-control value; separation decides viability. Sweep threshold x evaluation time x metric and count false positives per cell. "No threshold separates this" is a finding; say it early.
- **D1.49** Invert a threshold to the input it demands, in the producer's units, and check the producer can reach it. A gate nothing reaches never fires and every suite stays green.
  - Check the gate's predicate too: pin it true, maximise everything else, and see whether the gated value can get there. Check test inputs are emittable (quantised scores cannot hit 64.9).
  - To test a new gate, search for the best score that still PASSES it. A gate that bounds an exploit has not removed it; report the residual as a product decision.
  - Solve each guard's break-even and compare it with the other guards' thresholds; overlapping guards can be redundant. A defence adopted under a wrong framing retires the concern. Check which surface the guard actually reaches.
  - A purity or unanimity threshold is fragile to a second actor on the same stream. Test the consumer's OUTCOME with a permanent verify script; artefact counts can all look healthy while it learns nothing.
  - Distinguish "not enough data yet" from "unsatisfiable in principle". Grep whether the column documented as the separator is actually read. Regroup events by actor and bulk signature before reporting a temporal pattern.
  - No suite catches a silent gate. Name the out-of-band instrument (comparison with real downstream human decisions) and build it early; outcome data cannot be collected retroactively.
- **D1.50** Agreement against a lopsided base rate measures the base rate. Evaluate a rare-class rule by precision on the items it flags, and report the majority baseline beside every score.
  - A bare agreement figure without its baseline is uninterpretable; ask for it like a denominator. Keep `validated: false` through a small-n success.
  - A column identical on almost every row is a badge: say the common case once and mark the exceptions.
- **D1.51** Before a cap or threshold explains a number, show the number reaches it. A number consistent with two hypotheses supports neither; find the measurement that separates them.
  - Check that your mechanism predicts the observed DIRECTION; a wrong sign refutes it. Agreement between people about which hypothesis a number supports is worth nothing.
  - When credited for a claim you have retracted, re-assert the retraction. When stating a practice ("I always X"), name the population you verified it over. For "A unchanged, so B unchanged", ask where B actually executes.
- **D1.52** When you add a step to a fan-out, grep the step-list constant and read every consumer; `.length` denominators and `totalItems` are invisible to the type system.
  - Two independent lists of the same thing with nothing reconciling them will drift. Adding to the full list must not add to a safety allow-set. The constant's own test staying green is not confirmation; a verified claim about one mechanism does not cover another.

### Reading verdicts and samples
- **D1.53** When a check emits a verdict and a measurement, read the measurement's trend across the sample, not the verdicts. Sample the extremes, not the typical case.
  - A threshold expressed as a share of a per-input quantity (`max(3, 0.15 * N)`) is a different threshold per input and collapses at the small end. Grep thresholds for shares of varying quantities.
  - Ask what your fixtures VARY; fixtures that hold length constant cannot see a length-driven defect. Any fix must keep the negative controls failing and be re-verified by re-running.
  - A false rejection that routes to silence is invisible; widening acceptance until it stops converts a visible false negative into an invisible false positive.
  - Read what a red test SAYS, not how many went red. A red you did not read is a green with extra steps.
- **D1.54** A threshold set just below the lowest control observation scores zero false positives by construction. Report the curve around it and the evaluability denominator.
  - At a few nearby thresholds, how many false positives? A shoulder (0, 0, 1) is robust; (0, 6, 20) balances on one observation. A detector can reach zero false positives by declining to judge; state what was excluded. Label an in-sample zero as such.
- **D1.55** Run the important checks unconditionally, never when suspicion triggers them. A plausible wrong value closes a question that an obviously broken one would have opened.
  - Prefer a STRUCTURAL tell (does the mechanism or ledger exist at all) over a numeric one. Pair every green with a negative control. Two coinciding quantities do not corroborate each other; record the collision with its number.
  - "It is gone" is terminal: run the recovery search you would run under pressure before recording a loss as permanent.
- **D1.56** Write the qualifying criterion down before any candidate exists; a category chosen after seeing the score is a preference. Count INDEPENDENT samples only.
  - One actor repeating one procedure on one day is one sample measured several times. Name your incentive before giving the argument, and ask someone holding the data, not the incentive, to attack the ruling.
  - Record the instance as FACT and the generalisation as CANDIDATE until independently reproduced. Every-branch test: would the rule have improved the outcome on every branch, or only on the one that happened? A flood of new rules in one session is a warning sign.
- **D1.57** Before adding sample N to a numbered series, read sample N-1 for retirements and pre-registered falsifiers. A series keeps re-confirming a finding it already retired.
  - An off-condition sample neither confirms nor revives; say "off-condition". Re-register an untested falsifier or it lapses. Never pool two instruments into one count.
- **D1.58** Assert on every field you capture, or do not capture it. A captured field you skim past is a written record that looks like coverage.
  - Reduce last: print raw values beside the verdict. Never swallow an error inside a probe; it turns "could not look" into "looked and found nothing". Prefer computed values over proxies that also pass in the broken world. Ask which captured field would differ if the thing were absent.

### Gates over controlled processes
- **D1.59** Count a headline metric from the deterministic stage. A figure computed after a stochastic stage (an LLM step) is not a signal, and neither is any anchor taken from it.
  - Classify artefacts by a signal that cannot be absent on a clean input: a marker that only appears on defective rows reads a clean run as unfixed. Two signals answering different questions beat one.
  - Gate on the DELTA against a stored baseline with a normalised comparison key, not on absolute agreement; rebaselining is itself a controlled operation. Freeze an external pool for determinism, not availability.
  - Historical fixtures differ in population rule: report coverage beside the delta, never divide by a fixture's raw row count, and quarantine structurally corrupt rows as unscoreable.
  - An accuracy gate only sees rows its ground truth knows. Add a population gate that needs no truth (rows entering or leaving scope per period). Gates measure and report; they do not overrule. "I cannot show you its effect" is not "no impact".
  - `unzip -l` an office document you did not author and diff its parts across a library round-trip before designing on that library.
  - Design lessons from the same control: keep operator config, fixtures and changelog in a data root that an upgrade never replaces; choose a config format by its WRITE path (the stdlib reads TOML but cannot write it); a pending change must never dead-end (log it, name who it waits on, re-surface it each run); put the rendered procedure diff, not just the config diff, in the audit changelog, and hash what you last wrote to detect hand edits.

## Examples
- D1.48: `total_cost_usd` was bound to `0.0` on every one of 176 runs and had been chosen as the success criterion.
- D1.49: a total of 20x the mean rating needs a mean of exactly 4.00 to cross 80; an all-"meets expectations" record scores 60 and never notifies.
- D1.50: a markup rule scored 3/3 where the majority baseline scored 2/3 and the base rate was 96.5%.
- D1.51: a 200-row cap was blamed for a result of 96.
- D1.52: a cron job added an 8th fan-out step against a 7-item constant; a log line could print `succeeded=8/7`.
- D1.53: five documents passed a layout gate, and the gap column rose as word count fell; 22- and 45-word documents were rejected.
- D1.54: a 0.30 threshold was set under a lowest control of 0.3288 and then scored against those controls.
- D1.55: a harvest hole hid behind a count of 5; a 0 would have been investigated.
- D1.56: three releases by one agent, on one runbook, in one day were counted as three samples.
- D1.57: calibration sample 7 recorded a "third confirmation" of a conclusion sample 6 had retired.
- D1.58: a probe captured `{sheetHidden: true, sheetDisplay: "flex"}`, read only the first field, and a nav sheet shipped open.
- D1.59: rows needing review fell to about 0 after an LLM step (960 to 0); the deterministic count for a "clean" month was 1,090.
