---
name: research-discipline
description: "Use when research or a source document drives the work: technology, library or API evaluation and comparison matrices, requirements gathering, meeting transcripts and ASR, counting or quoting from a corpus, xlsx/pptx/docx/pdf forensics, verifying a claimed capability, and writing an inventory, audit or report."
---

# Research discipline

Context: when research or a provided document drives the work, the output must rest on what the
source actually says: nothing sampled or paraphrased, counts that survive referent variants, every
claim marked checked or believed, and reports that carry signal rather than a log of the process.
Code-level claims (tests, deploys, guards) belong to repacted:verification-discipline.

## Workflow
1. **Frame the research.** Write the primary question, the success criteria and the constraints
   first. For a technology, library or API evaluation, rank sources (official docs and repos
   first), record versions and dates, and use `templates/technology-comparison.md` or
   `templates/api-exploration.md`. → `references/claim-and-capability-verification.md`
2. **Read the whole document** when it drives a decision or you will quote it, including the parts
   that are not body text: every sheet and column of an xlsx, the comments in a docx. Searches and
   diagnostic noise may be filtered. → `references/transcripts-and-asr.md`
3. **Hand delegated work the file, not a paraphrase.** If the document drives delegated work,
   extract a structured reference first (headers, row counts, verbatim sample rows, comments) and
   hand the sub-agent that file. → `references/transcripts-and-asr.md`
4. **Read transcripts as ASR, not English.** Plausible-English manglings, elided subjects and
   interleaved turns are the traps. Check which question a quote answers. Map ASR variants of the user's
   own name to the user. NEVER lift an ASR quote into outbound text.
   → `references/transcripts-and-asr.md`
5. **Count with the referent's forms before you trust a total.** Nicknames, ASR variants and
   anaphora undercount; bare substrings overcount. Classify the hits, then count, and state the
   term list. Trace a topic through every consolidation layer of a staged corpus.
   → `references/corpus-and-document-forensics.md`
6. **Treat office files by their structure.** Read an xlsx twice (formulas, then cached values);
   a Sheets-export `max_row` is the grid. In a Google-exported docx, tabs are Title paragraphs and
   mailto runs are @-mention chips. Cross-check a PDF text layer against a render. Inventory a pptx
   zip before modelling it. → `references/corpus-and-document-forensics.md`
7. **Prove a claimed capability with four signals:** code, entry point, tests and wiring. A status
   flag, a registry 200 or a doc page proves presence, not use. Positive-control any robots or
   feasibility verdict (Python `robotparser` ignores wildcard rules).
   → `references/claim-and-capability-verification.md`
8. **Write the report for its reader.** Filter to signal; mark every line checked or believed;
   put an as-of date and a staleness banner on snapshots; record errata with what still stands;
   no personification. → `references/report-writing.md`

## Map
- `references/transcripts-and-asr.md` read-whole, extract-reference, ASR traps, the user's aliases.
- `references/corpus-and-document-forensics.md` counting and locating in a corpus; docx, xlsx,
  pptx, pdf and site forensics.
- `references/claim-and-capability-verification.md` shipped vs planned, liveness, authorship, OSS
  claims, classifiers, robots.txt, market and SEO claims, the requirements checklist.
- `references/report-writing.md` filtering to signal, checked vs believed, errata, objectivity.
- `templates/technology-comparison.md` and `templates/api-exploration.md` copy and fill.

## Related skills
- repacted:verification-discipline: relayed claims, reporting absences and counts about systems.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
