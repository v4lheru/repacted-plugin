# VD-E: relay and reporting

Context: read this before you relay, quote, confirm, correct or attribute a claim you did not
measure yourself in this session, and before you write the report, message or comment another
agent will act on. A claim loses its epistemic label (measured, inherited, inferred) at each hop,
and agreement between hops is easily mistaken for corroboration. This file covers RELAY AND
PROVENANCE. The rest of E is split by question:

- Who did it, which record says so, and what an absence means:
  [vd-e-attribution-and-records.md](vd-e-attribution-and-records.md)
- Putting the evidence in the report (full output, the discriminating check, proxies, raw rows):
  [vd-e-evidence-in-reports.md](vd-e-evidence-in-reports.md)
- "Done", "fixed", "I have X" and handoff claims:
  [vd-e-completion-claims.md](vd-e-completion-claims.md)
- Figures, repairs, severity and accepted limits:
  [vd-e-figures-and-severity.md](vd-e-figures-and-severity.md)
- Copy, comments and alerts that a reader will conclude from:
  [vd-e-copy-and-comments.md](vd-e-copy-and-comments.md)
- Defects at joins between lanes, mirrors and duplicated contracts:
  [vd-e-joins-and-contracts.md](vd-e-joins-and-contracts.md)

## Canonical rules

### Keep the label across the hop
- **E.1** A relayed claim keeps its epistemic label (measured, inherited, inferred) clause by clause, or the relayer verifies it first. A restatement is not evidence for the claim.
  - Partial labelling reads as full labelling to you and as none to the reader: split the sentence. Never add severity in transit; a relay more alarming than its source invented something.
  - One cheap check beats any relay ("I verified X at `file:line`"). State your lane boundary ("CONFIRMED the call sites differ; NOT asserting a defect"). Retract in the same channel, naming your own addition.
  - A mostly-measured message launders the inferences inside it, because trust is calibrated per message. When a ruling hardens your inference, go and measure it unprompted and report the upgrade.
  - Each hop removes a hedge and adds authority; nobody adds a claim. Carry the METHOD with the result ("zero in a top-6 listing", not "zero"), and never write "verified" into a durable artefact for a check you did not run.
  - A dispatch or harvest brief is the most dangerous hop, because it is written in instruction voice: label each item live-verified or document-derived as you write it.
  - Quoting a number adopts it. The originator's error and the relayer's error have separate owners, and a trusted relay is exactly where the check stops happening.
  - Tag every expectation you hand a verifier MEASURED, INHERITED or INFERRED. A figure copied from a ticket, comment or handoff is inherited until re-measured; be most suspicious of one generalised from a single neighbouring surface.
  - A teammate's claim about their own artifact ("it is labelled X", "always called") is a lead to grep, not a fact. Test the shipped line, never a paraphrase of it; the misses cluster on the load-bearing items.
- **E.2** Agreement among sources that share an origin, method or input is one observation. Independent confirmation needs an independent method or artefact, not another person.
  - Shared wording, counts or enumeration order mark a common ancestor. Distrust a count and its list separately; verify the list you will act on. When a direct check disagrees with the consensus, the difference is the finding.
  - Ask what command each confirmer ran and which tree it read (working tree, index, HEAD, built bundle, deployed image). A second opinion built by passing the same candidates through a narrower window (a truncated prompt) agrees almost by construction.
  - Two NEGATIVE results agreeing are weak even when the instruments are genuinely independent: there are many ways to miss a thing and few ways to see it. Ask what each would have shown if the thing were present.
  - When two credible teammates conflict, first ask whether they describe the same object (shipped code vs the contract doc); only reading the artefact resolves it.
  - Ask whether the two routes could fail together. When scattered findings suddenly resolve into one clean story, slow down: does it predict the observed DIRECTION, and is a retraction in flight?
- **E.3** A correct verdict carrying an unchecked rationale is never re-tested. Separate the measured finding from the inferred cause, and check a peer's REASONS even when you agree with the conclusion.
  - The decision gets reviewed; the rationale gets inherited, and a false one invites the revert. Write "origin unknown" rather than an unmeasured cause; shared browser history is not evidence about your session.
  - The reason is invisible to its holder and load-bearing for the receiver, so self-review is blind here; mark which reasons you checked, and flag rather than silently fix.
  - In an async team neither silence nor an observed state change is an event; only a message or a verified artefact is. "You have not told me X" is not "X is false".
  - On self-correction say which part died: mechanism refuted, conclusion stands, here is the replacement mechanism. Ask whether a premise degrades as the product grows.
- **E.4** A relay is a lossy projection chosen by the relayer's purpose, and what arrives reads complete. Go back to the primary source before banking it.
  - Ask by what ROUTE content arrived (relay, commit message, git) and probe for a term only the original could contain. A relayer forwards the half they needed; the sharper half (a bypass, not an over-strict check) is the one dropped.
  - A tidy relayed enumeration may have been open ("like", "right" and "uh" are the first hedges a relay strips). Doubt closedness before membership, ask for the verbatim wording, and never mark as binding a claim you did not read at the source.
  - Name your suspected weakest field at save time so a later correction is findable. Decide cheap, reversible open questions rather than carrying them; an open question that outlives the session becomes a silent omission at build.

### Credentials, authority and contested claims
- **E.5** A credential (a verified source, polish, a good track record) attests who said it or how carefully it was phrased, never whether it is true. Test the claim itself.
  - Tracing a claim to an authority answers "who said it"; say so in those words and name truth as still open. A faithfully placed false claim belongs to the source's author, so fix the source or it returns.
  - Fluency is read as verification, and the most reliable teammate's one unchecked claim travels fastest. Trust should change how much you expect to find, never whether you look.
  - Knowing this does not inoculate you. Put the limit inside the claim sentence with no separable headline; a caveat in the next paragraph is already lost.
- **E.6** When your measurement disagrees with a lead's stated baseline, report their number as wrong with your derivation. Never rationalise your own correct result as drift.
  - A brief's number is someone's earlier run and inherits its failures (a rate limit mid-pagination). A 40% gap gets investigated; a +1 gets rationalised. Two agreeing independent peers outweigh one stated figure.
- **E.7** A contested claim is settled only when you have run the check yourself, preferably by a different method from the claimant's. Concession is not evidence, and neither is a confident correction.
  - The riskiest moment is correcting someone else: the corrective frame supplies the confidence. Keep contested claims in a list with their state; "unverified" is a real third state. Verify the verification ("mutation-tested": mutate it yourself).
- **E.8** A cited deliverable fails as correct citation, wrong characterisation. Check the sentence and its VERB for implying a different amount of work, in either direction, via a second reader.
  - "Discarded" or "dropped" implies recovery work; "summed" or "flattened" means the data is present and only its structure changed. A deflating error is the comfortable one you miss. Quote the whole sentence and the next one.
  - A spec's as-is section expires when the spec ships; check whether the same spec proposed changing it. Provenance decoration (ledger ids, VERIFIED tags) attests past checking, not current truth: trust the marker for provenance and the date for currency.
  - Memory is the last hop of a confidence chain: for a runtime claim ask whether anyone RAN it. A commit message describes a change, not the resulting state. Name a string probe's own question beside its answer (a missing signature is not a missing function).
  - A consultant supplies facts and the lead supplies scope; "this changes your scope" reads as authorisation. Neutral form: "here is the full census; the lead owns scope".

## Examples
- E.1: a relayed diff-read arrived as two agents agreeing, with a defect label the originator had disclaimed, and it drove a dispatch decision.
- E.2: a brief, a census and a findings doc all said "six occurrences across five files"; the list was five across four and missed a sixth site a grep found.
- E.3: ~180 failed refresh calls were correctly ruled not a product defect, then explained by an invented "older bundle"; the traffic was a keepalive another agent had set up.
- E.4: a banked lesson held only the over-strict direction of a test-database guard; the handoff held the bypass that made it safety-critical.
- E.5: a site's product claims traced faithfully to a brand document, and three of four were contradicted by the engine code.
- E.6: two agents measured 2,297 and wrote their correct answer up as "+1 drift" against a stated 2,296 taken from a rate-limited pull.
- E.7: a "no API" conclusion came from a path sweep one URL segment too shallow.
- E.8: a verified-claims ledger row said "no test job in CI" after the same document's own plan had added two blocking jobs.
