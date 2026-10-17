# VD-E: attribution and records

Context: read this before you say who did something, credit or blame a teammate, cite a record
or ID, record a stakeholder's input, or report that something is absent from someone else's work.
The container a fact sits in, the identity that performed an action and the label on a record
all look like provenance and usually are not. Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

### Who did it
- **E.9** The container or shared identity an action or artefact sits in does not establish who authored it. Label attribution as inferred or self-reported unless the author recorded it at the time.
  - When agents share one forge identity, `merged_by`, commit author and a CI job's user all name the same human for every actor. A job showing a person's name does not mean that person acted; reconciling two transcripts is inference, not measurement.
  - When it matters who acted, the actor must say so in a durable record at the time; the platform cannot reconstruct it afterwards.
  - A repo, instance, org or team project is the unit you measured, not the person's work. Aggregates over a container (spans, totals, first-commit dates) inherit its whole history.
  - A file's directory shows who filed it, not who wrote it; check the artefact names its own subject in its text. A missing artefact may sit in another container: search other directories by content before declaring it never existed.
  - The symmetric guard: a failed ownership claim does not falsify the work claim. Lead with the true shape ("real work inside someone else's repo"), not a bare denial.
- **E.10** Relay an attribution back to the agent it names: only the party named can reliably catch a misattribution. Re-read the carrier field before writing "your".
  - A wrong figure invites recomputation; a wrong name invites citation. A named party must check a credit as hard as a blame; a flattering misattribution survives.
  - Disown a wrong attribution and stop. Naming a replacement candidate is a new, weak attribution that needs its own evidence.
  - A claim written into a third party's document has no cross-check if its subject never reads that carrier; a carrier selects its readers. A request and its completion are different events ("I moved it" vs "I asked it to move").
  - A range is not a value. When an answer underdetermines the value, record who closed the gap, and keep "stakeholder decision", "choice from their range" and "team call not yet put to them" distinct.

### What the record says
- **E.11** A stakeholder accepting text that contains your number confirms its format, at most its semantics. Record who originated each fact, and walk provenance to the source, not the last hop.
  - Keep `format-confirmed`, `semantics-confirmed` and `value-confirmed` distinct. A fact's strength is part of the fact, including facts from a lead or specialist: mark "recorded from X's report, not re-verified".
  - Marking a fact unverified is cheap at any depth. Correcting its provenance, or declaring a record stale, is an assertion about content you must have opened in the same session. Attributing a fact to a source you did not open is how a true fact acquires a false provenance.
  - A derived figure drifts into a sentence about what was observed; two durable records disagreeing on an observable count are flagged, not resolved in favour of the senior one.
  - Prefer the durable form of a fact (the mechanism) over the observed instance. A caveat decays by restatement: say which half is proven, what evidence would discharge the assumption, and not to paraphrase it upward.
- **E.12** Record a stakeholder's observation as a fact; never silently turn it into a prescribed action. Attribute facts and actions to separate originators and check the action against design principles.
  - The tell is that the stakeholder's words contained no imperative. If you cannot name who decided an action, you probably invented it. An exclusion smuggled in under a fact is the highest-risk case.
- **E.13** Before citing an ID or record in support of a claim, open it and find the sentence. Values riding in asides (an id, a date, a filename, a count) get no scrutiny, so verify those first.
  - A topical index label answers "what area is this?", never "does this record support P?". A freshness check on a store does not transfer to your summary of it. A second id for the same claim is where an unverified one hides.
  - Never type an id you cannot paste from a verification result; a hedge or a trailing "..." around it means you are composing. A dangling id is worse than none. Correct a bad one immediately and name it as non-existent.
  - The checks we skip are the ones attached to the incidental clause, not to the claim under scrutiny; reviewers audit the headline and accept the furniture.

### What an absence or a surprise means
- **E.14** Being surprised twice by a source is not evidence it was wrong twice. Label each instance RIGHT or WRONG, and write the operational consequence rather than a reliability verdict.
  - Ask what the sentence licenses a reader to do: calling the only source a constant rests on "unreliable" licenses ignoring it. Write "treat every statement here as a claim with no second source".
- **E.15** A measurement confirming a risk you predicted invites vindication. Controls validate the measurement, not the inference: ask the peer whether an absence was a decision before escalating a miss.
  - Absence-as-defect and absence-as-decision look identical; rulings arrive out of order, and a peer may be acting on one you have not received. The number is a fact; "missed again" is a story about intent.
  - The tell is quoting your own earlier prediction while reporting. Committing someone else's hunks is authorship; declaring a fact about their code is a claim, and only the claim can be false.
- **E.16** During a migration that keeps the old path alive, every health and status field belongs to one of the two systems until you find its writer. Do not cross-reference two signals meanwhile.
  - Grep for the writer, not the reader, with a positive control on a file known to write it. When you confirm the split, write the prohibition and its reason into the file; cross-referencing looks like an obvious improvement.

## Examples
- E.9: two merge requests merged minutes apart by two agents both read `merged_by` as the same person; a compacted session summary filed under one project held another project's session.
- E.10: a closing message credited an agent with finding an issue that its origin record, read minutes earlier, gave to a different lane that predated the recipient.
- E.11: two agents independently declared a memory stale from a commit message; the memory, when opened, already said the flag had been removed.
- E.12: "this domain is never a customer" became a hard-exclusion rule that contradicted the project's annotate-not-filter principle.
- E.13: a memory id cited in a plain aside did not exist; the real row had a different id.
- E.14: a vendor's OAuth limitation turned out true by an undescribed mechanism (a scopeless token returned an empty tool list), not false.
- E.15: a file absent from a peer's commit had been unstaged deliberately under a ruling that assigned it to the reporter's own commit.
- E.16: a reachability field was wired only into the old OAuth1 client, so pairing it with a bootstrap status described two credentials as one connection.
