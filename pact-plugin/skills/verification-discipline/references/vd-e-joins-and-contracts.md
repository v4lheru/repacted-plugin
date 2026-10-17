# VD-E: joins and contracts

Context: read this when two lanes, two surfaces or two declarations meet: a shared contract built
in parallel, a safety argument in one file that depends on another, a helper serving two callers,
a request rewritten on its way to a vendor. Each side passes its own tests; the defect lives in
the composition, where no single owner is looking. Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

### Composition
- **E.53** Defects cluster at joins, which have no artefact and so no owner. Never let a value cross a boundary without its caveat, and name the approximation in the field that carries it.
  - Every such defect is a caveat dropped at exactly one hop (a capped count printed as a total). A field named `possiblyTruncated`, documented as a heuristic, lets the receiver render "20+" correctly even in cases nobody considered; obeying a caveat you did not derive buys that correctness. Better still, remove the uncertainty (fetch `limit + 1`).
  - A safety argument in one file often assumes something about another surface; make it state that assumption ("returning everything is safe only if the UI does not assert success").
  - Verify the whole project for every concurrent lane, not the diff, and have a dispatch name the files whose headers a lane must re-read. A control that fires only because something unrelated pointed you at it is not a control.
  - Name the JOIN in both briefs, and give one lane the job of checking the pair. On acceptance ask "could your check have seen the other side?"; an agreeing observation from a case that could not disagree is UNEXERCISED.
  - Derived copy (rendered from config) cannot go stale; observed copy can, so it carries its own date. Never refresh a shared "checked on" stamp: that re-attests notes nobody re-checked.
  - A control retired because today's data makes it look redundant silently becomes wrong as data grows; add a tripwire recording the current value, and frame its firing as success.
- **E.54** Parallel lanes that each build a local mirror of one contract compile and test green while disagreeing. Require a contract-reconcile diff against the committed source before integration.
  - "Both sides green" is zero evidence of agreement. Name the single authoritative artefact in the dispatch, prefer an executable source over prose, and mark doc-quoted values as unverified.
  - Delete the mirror rather than testing it (derive the enum from a shared const). A mitigation designed against a constraint survives as ceremony once the constraint lifts; re-derive it. Derivation closes only what it derives, and derived is not deployed (independent deploys skew).
  - Where two attributes must agree, have each branch construct the whole object, so the compiler demands every field in every arm.
- **E.55** Grep each load-bearing type or constant name across the whole tree before commit. Two declarations of one contract compile because they never meet in one file, and one copy starts lying.
  - Same name with two values, same name with two shapes, and same concept with two names all happen in concurrent builds. Correct neighbours camouflage the one wrong line; the test that found a defect may not reach where it lives.
  - Two provenance fields holding identical values cannot be told apart; drive a sentinel and assert each lands in its own field. Never give a local fallback constant the canonical name.
  - When two authoritative sources disagree about a contract (a task's text and the message announcing it), report the discrepancy as a finding; never resolve it by silently picking one. Say an instruction once, in the durable record.
- **E.56** Some defects exist only in the product of two constraints that are each correct alone. Hold both at once: what makes a terminal state un-terminal, and what is the scope of the key it terminalises?
  - Terminal plus global scope is unrecoverable state. Before trusting a matcher that feeds a terminal write, find whether anyone measured its precision.
  - Ask what B does while A has landed and B has not; a change can be right at both ends and dangerous in between. When classifying a failure mode's severity, list everything else that produces the same symptom.
  - "May I follow this?" and "what do I carry there?" are different controls (a permitted redirect can still receive a forwarded credential). A guard load-bearing for two paths names both at the guard.
  - The implementer is the only reviewer who holds two rules at once; brief them to report contradictions as findings. Read a concession's follow-up closely; it often carries a better reason.
- **E.57** A shared helper that branches on its input's shape silently takes a weaker path for callers with the other shape, and both call sites then report true things. Test against every input shape.
  - Ask whether both reports would still be true if the helper were broken. A fuzzy rule pointed at a new input shape does not inherit its old audit.
  - When a server guard's correctness depends on a client choice (a custom widget sends an empty string where a native element omits the field), accept the whole family and say why at the guard.
  - The same code shape can have opposite safety: skipping an undetermined entry is safe in an incremental refresher and a permanent hole in a builder from empty. Re-derive a reused rule in each caller, assert completeness against an expected count, and check whether your own tool causes the condition.
- **E.58** Before blaming the system that answered, diff what the user supplied against what your code actually sent. A silent rewrite makes every result a true answer to a question nobody asked.
  - A dropped constraint looks like a vendor fault; an added one (a mandatory dropdown) makes a thin market look real. Surface every drop and addition as a warning, and offer a pass-through option.
  - A count from a rewritten request answers a different question. The tell: the explanation lives outside your code while the transformation lives inside it.
- **E.59** Propagating a block backward along a prerequisite edge deadlocks when the dependent's reason means "waiting for you". Propagate only terminal blocks, and test the prerequisite-pending case.
  - Split the dependent's block reasons into terminal and pending-on-this. The motivating scenario passes either way, so it cannot be the regression test. A new block reason in a separately deployed consumer is a cross-service contract change.

### Risks, counterparties and refusals
- **E.60** A risk handed over without its discriminating case gets closed on whatever is nearest. Ask what exact case would tell "real" from "fine", and price the risk by blast radius, not loudness.
  - For a limit-shaped risk (size, count, length, rate), test the largest instance you actually ship. Loudness bounds the damage per event, never the number of events.
  - Self-prediction is diagnostic for the reviewer, never protective for the author. After any catch ask whether a witness or a detector caught it, and list the variants that have no witness; a catalogue of catches is biased by detectability.
- **E.61** When an internal ledger and an outbound artefact disagree, only the copy the counterparty received can tell "our books are wrong" from "we shortchanged someone". Name both branches before looking.
  - The internal record and the outbound document can agree and both be wrong; reconcile against the ledger. Without the counterparty's copy the finding is undetermined; do not default to the benign reading. Stop hunting the mechanism once an output-vs-ledger guard covers it regardless of cause.
- **E.62** A zero from a check that looked and a zero from a check that could not look print identically. Report three outcomes: measured zero, not measurable, measured delta.
  - Check blindness per key, not per prefix. Prose more refusing than the code is a defect no grep finds: read the implementation of every behaviour the prose asserts, and cite the call path for any claim that a refusal still exists.
  - An imperative that asserts authority, not a consequence, gets reconstructed or ignored by the next reader; state a fact about the world. Removing a veto makes misreporting more tempting, so strengthen the honest-measurement language and attach the scar. Never write a typeable command you have not seen in source.

## Examples
- E.53: "1652 tests green and nobody has seen a pixel"; a query ignoring an unbuilt exclude mode was safe for the query and unsafe for a UI that confidently said "Excluding 3 origins".
- E.54: a frontend mirror of a committed contract diverged on four points while its whole suite passed.
- E.55: a prompt-version constant written unconditionally put a version that never ran into an audit record.
- E.56: marking a skipped write-claim `applied` on a global dedup key would have made a record permanently uncreatable for every tenant.
- E.57: a name resolver gated on a comma gave budget owners (First Last) exact-match only while requestors (Last, First) got the full rule.
- E.58: a lead-enrichment parser dropped the named companies from a search, and the wrong results were about to be blamed on the scraping vendor.
- E.59: blocking a create because its paired add was blocked deadlocked, since the add was blocked waiting for that create.
- E.60: a schema limit was "verified" on the 13-criterion matrix; the 15-criterion matrix failed on every input of that kind.
- E.61: a summary tab said 100, the ledger 210, and the PDF actually sent said 99 (illustrative figures): the partner had been underpaid.
- E.62: every replay check was blind to one settings namespace, so the operator's most common change measured a confident zero.
