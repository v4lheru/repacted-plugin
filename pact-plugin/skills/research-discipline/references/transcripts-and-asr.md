# Provided documents, transcripts and ASR

Context: how to read documents the user provides and machine-generated meeting transcripts
(call recorders, Google Meet, Gemini notes), and how to hand either to delegated work. Read this before
you quote, count or summarise from a transcript, and before you brief a sub-agent on a document.

## Canonical rules
1. Read the whole document when it drives a decision or you will quote it, including non-body parts: every xlsx sheet and column, docx comments, pptx notes. Searches and command noise may be filtered.
2. Do not truncate your own read of a payload you reason from (`[:N]`, `head`, "first N rows"). If it is large, write it to a file and read it whole, or page through it.
3. Never paraphrase a document to a sub-agent when the file is on disk: give the path.
4. If the document drives delegated work, extract a structured reference first (exact headers, row counts, per-column stats, verbatim sample rows, comment threads) and hand over that file.
5. Treat every ASR transcript as lossy. The dangerous manglings read as valid English ("a cloud skill" for "Claude skill"); obvious nonsense ("Germany" for Gemini) gets caught.
6. ASR renders the user's own name as variants that read like other people (keep the known list in private memory): treat those as the user, not a third party, and flag rather than assert when context is ambiguous.
7. When the user is the proposed implementer of the work they ask you to review: frame findings as build blockers and dependencies the user will own, and deliverables owed to them.
8. When the user builds and a programme owner owns the business case, before asking anyone anything for the user, test: does the answer change what the user builds? Cut hours-saved and ROI questions.
9. Subject elision leaves nothing looking wrong: when a transcript sentence carries a preference, opinion, commitment or decision, check who the subject is and record a supplied subject as inferred.
10. Before citing a quote, read the question it answers: ASR interleaves turns, so adjacent lines are not reliably adjacent speech.
11. A token repeated identically is a real token; a one-off garble is likelier noise. Either way mark it unrecovered and ask; never pick the likeliest candidate.
12. Disambiguate every product and person name against adjacent lines before relying on it; product names are both the highest-risk and the load-bearing tokens.
13. Prefer a typed artifact by the same stakeholder (brief, doc, email) over the transcript on any point both cover.
14. Never lift an ASR quote verbatim into an outbound message: a mangled quote attributed to a colleague is worse than none.
15. When quoting a community or corpus voice in a deliverable, quote the exact text with its recorded date; never paraphrase a dated quote.
16. Do not invent a track record for the user: when calls name them as a route or capability, record that, not a build history the calls never attribute.
17. Unless the user has stated their pronouns, a transcript or teammate saying he/him describes that source, not a fact: write "the user" or they/them.

## Examples
- 3: a stakeholder-edited report was described to a sub-agent twice from memory; both instructions were wrong and had to be retracted.
- 5: one file rendered Gemini as "Germany", Claude as "cloud" and "claw", and Apify as "appi".
- 10: "let me think about it as well" was cited as a tool-choice deferral; it answered a question about output format seven lines earlier.
- 16: on one engagement the calls named the user "the general automation route" and attributed no prior build history.
