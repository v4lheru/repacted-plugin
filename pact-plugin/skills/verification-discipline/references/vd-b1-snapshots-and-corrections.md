# VD-B1: snapshots and corrections

Context: read this when you are about to restate something observed earlier (a grep, a build,
a listing, a count, a status, a memory, a handoff), or when a claim has been corrected and the
correction has to reach every copy. Every observation carries an as-of and a scope; restating
it drops both. This is the entry file for VD-B1 (B1.1–B1.3). Three splits hold the rest:
- [Journals, clearances and decay](vd-b1-journals-records-and-decay.md) (B1.4–B1.7): journals
  and handoffs, scan and enumeration clearances, specs with an mtime, the decay ranking of
  defects, TODOs, open items and declines.
- [Records and decay](vd-b1-journals-records-and-decay.md) (B1.8–B1.13): findings vs HEAD and trees,
  filenames vs capture time, ephemeral locations, staleness marks, config that ran, recorded
  workarounds and advice.
- [Corrections propagate](vd-b1-corrections-propagate.md) (B1.14–B1.21): every carrier,
  co-located corrections, update-means-replace, data strings, handoff resync, ticket numbers,
  scope of a correction, sweep blast radius.

## Canonical rules
- **B1.1** Treat every observation of mutable state, including your own, as a snapshot: carry its as-of and scope when you restate it, and re-read at the moment of acting or asserting.
  - Re-list before claiming something is ABSENT and before recording a dependent conclusion into memory or a deliverable. Stakeholder folders fill asynchronously, with no notification either way.
  - Before comparing two sources, establish which is NEWER. The freshest-looking artefact (a live query) can be the stalest (a row written months ago); the feeling tracks when YOU touched it.
  - A transient marker (a label present only while paused) sampled at the phase where it is absent looks permanent. Select the driving STATE beside the marker, or sample at two phases.
  - Absence is invisible in a list checked for correctness (a missing export, a commit without the edit, a guard asserting nothing); execute or diff it. An existence question needs a predicate query, not a top-N listing.
  - Several negatives from one search root are ONE negative. State the root you searched and include an item you know is there. An empty result conflates "nothing exists", "wrong place" and "not written YET"; a positive control catches only the second, so an absence needs a time bound too.
  - Keep every field you compare on: a time-only format over files from many dates fabricates a same-day span. If a figure supports what you are arguing, re-check it because it fits; if it contradicts you, suspect the instrument first.
  - When refuting someone's absence, check that your evidence existed when they looked (birth time, not modify time). A flag set before the write it claims is true-eventually and unsubstantiable at read time; record an id instead.
  - "My last action on X is the last action on X" fails on any concurrent team. The closing summary is the highest-risk claim of a session: re-read the tree, deployment and task list at the moment of writing it. The tell is present tense over a fact acquired earlier.
  - Do not bank a teammate's live editor state (what is staged or mid-edit) as a project fact; ask whether the sentence would still be true if the owning task finished thirty seconds from now. `git status` decays faster than a listing, and `origin/*` moves only on `git fetch`.
  - Re-read immediately before using an observation to tell someone their report is wrong; a stale PRESENCE claim contradicts a colleague with evidence. Quote with its revision (sha, line, timestamp) so the reader can measure the decay, and keep the verdict apart from the quote.
  - "I have written X into Y" is a claim about Y: grep Y right after saying it. An artifact can lag your intent or move past an instruction, so one re-read at the point of action covers both. Report a gap rather than closing it silently.
  - Timestamp every result you restate ("passes as of 19:13Z"), with no exemption for your own or the lead's. A passing precondition is not a reason to delete it. Make the check one command, so declining it feels lazy.
  - When a search spans a type boundary, the boundary is the finding; check what each hit RETURNS before counting hits as one kind. File a lesson by the PROPERTY it is about, not the surface where you met it, or it will not fire next time.
  - After a compaction, verify HEAD and the task list before redoing a dispatched item; the session-start git snapshot does not refresh. Re-check before going idle on "X has not landed", or a standing conditional instruction is silently dropped.
- **B1.2** In a concurrently edited tree, re-run a grep, build or test immediately before each signal (intermediate ones too) and timestamp any result that implicates a peer.
  - Put the fresh grep in the same tool block as the message. For a "missing X" signal on a shared file, grep twice at least 30 s apart and check the lock file.
  - Conflicting reports are usually both true in sequence; resolve them with a timestamp, not a grep. A defect fixed before the first commit is in no ref, and a moving error line number means an edit in progress.
  - Reporting a valid catch as a false positive teaches the team the gate is noisy when it worked.
- **B1.3** Before acting on a recalled conditional, re-measure its ANTECEDENT; and give any memory that rests on host or external state its invalidation trigger and a one-command re-check.
  - A conditional survives its own falsification, so no staleness habit fires on it. Sound arithmetic on the consequent manufactures confidence in proportion to its quality.
  - "Has never run yet" about a scheduled job has a shelf life of one schedule interval. Stamp the antecedent separately, and brief "IF X; not verified" rather than a finding.
  - A duration inside a memory ("1-month trial", "30-day grace") is a date in disguise: compute the end date at quote time. A dead credential and a capability gap look identical, so check reachability first.
  - Write "valid while X holds; re-check by running Y" and say in the first line whether the entry is state or structure. Rewrite a stale entry with its resolution rather than deleting it.
  - When closing an open question, ask whether a better instrument than the one briefed now exists; the named check is a floor.

## Examples
- B1.1: a folder listed at 12:50 gained the deciding brief at 13:31. A DB row from 1 June was read as newer than an 18 Aug capture. A `labels` column sampled after a bulk resume refuted a correct teammate. Three searches scoped to the git checkouts missed a plan kept at the project root.
- B1.2: an auditor's YELLOW about `vitest` missing from `package.json` lost a race with a peer's edit by under a minute.
- B1.3: a transitions writer recorded as "never executed" ran daily from 12 hours after the note; the predicted 6-of-7 was 7-of-7. A vendor key recorded as a 1-month trial was relied on five weeks past its end.
