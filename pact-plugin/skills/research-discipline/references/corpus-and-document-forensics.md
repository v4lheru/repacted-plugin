# Corpus and document forensics

Context: counting, locating and extracting from a corpus (transcripts, interviews, questionnaires,
Slack exports, articles, scraped communities) and from office files (docx, xlsx, pptx, pdf) and
sites. Read this before you count mentions, claim something is absent from a corpus, extract data
from a client file, or report what a document displays.

## Canonical rules

### Counting and locating
1. A canonical-name search undercounts: nicknames, surnames, role titles, functional referents ("the data person") and ASR variants all miss. Search the union and state the term list beside the count.
2. A bare-substring search overcounts (`lora` in "exploration", `rag` in "storage", `eval` in "medieval"). Use word boundaries or a tokeniser, a known-present control and a nonsense control.
3. Classify hits before counting: your own prep notes and interviewer questions are hypotheses, template boilerplate sent to everyone is zero information, only unprompted testimony counts.
4. A keyword hit in a returned form is usually the form's own question text. Check whether the hit sits above or below the answer line.
5. Anaphora ("the tool", "it", a generic category noun) is unreachable by string matching. Counting or locating those references is a reading task for a model, not a pattern.
6. Confirming a dependency is not confirming the person: a stakeholder who names "someone in SEO" corroborates the dependency, not the individual.
7. Re-derive a population handed to you as verified: one query can show a "31 articles" figure was 6 real hits plus 14 links to a different page.
8. Ordinary words are jargon in a specialist org ("ticket" as a support case, "release" as a contract term). Follow each hit into context; publish the collision table.
9. In a corpus consolidated in stages (leaf notes, department analyses, final report), trace a topic from the leaves through each layer. Where the chain breaks is the finding.
10. Qualifiers die in summary chains ("self-reported"). Trace every client-facing "only person", "no X exists" or "departed" claim to its origin; the report's own appendix often disproves it.
11. When a corpus uses a distinct word for the unambiguous case ("departure" for others, "transitions" for one person), the choice of word is evidence.
12. A fact relayed from memory or a teammate may be a superseded state. Reconstruct the timeline and publish it rather than picking either end.
13. An absent department is not an absent function: read the roster whole, check alternate names, and look for the work distributed across other teams.
14. Keep "not covered by the research" separate from "does not exist", and state the scope of any negative inline.
15. Community corpus: flatten nested threads, score themed keyword buckets (negators lower, never exclude), dedup on the first 200 characters, save top-N as JSON, then read the top ones whole.
16. Before trusting small-sample agreement between a rule and human labels, sweep every threshold for inert parameters and falsify across other entities; report the degrees of freedom spent.
17. A residual `else` tier that agrees with a human's middle label is not a recovered class: test it against the property the human named.
18. A structural signal must be proven template-wide by a census over the whole corpus, not the labelled sample, and re-run when the corpus grows.

### Office files and sites
19. Google-exported docx: tabs are `Title`-styled paragraphs (no heading hierarchy), content hides in tables, `docProps/core.xml` is absent, and in-body annotations carry no author.
20. A docx run styled as a link to `mailto:` is an @-mention chip pointing AT a person, not a byline by them. Getting it backwards turns an open ask into a resolved one.
21. Prove docx coverage by zero orphan `<w:t>` nodes (every text node under a walked paragraph), not by character totals; anchor comments to their whole paragraph.
22. Sweep docx text for URLs with a regex: a rels parse misses plain-text URLs, and an issue-key regex misses board and project URLs.
23. Read client xlsx twice (formulas, then cached values) to separate derived from pasted numbers. A literal with 12 decimals and no formula was computed elsewhere.
24. A Sheets-export `max_row` is the grid (`A1:Z1000`), not the data: scan for the last non-empty cell. Many sheets at exactly 500 rows is an export cap; test it against the summary tab.
25. openpyxl `read_only=True` is about 50 times faster but cannot see conditional formatting or validations; conditional-format formulas can hold the client's business rules.
26. Summary totals are where spreadsheet errors live: when one aggregate is wrong, re-verify every aggregate in that table, and prefer shipping the per-item table.
27. Before reverse-engineering a handed-over process, prove it ever ran: header-only logs, empty output dirs, blank "filled in during setup" fields, docProps revision 1 with created equal to modified.
28. Inventory a pptx zip before parsing: no `ppt/charts` means every chart is a pasted image. Slide footnotes are not provenance; check the screenshot's own UI chrome.
29. Duplicated slides keep the source's numbers; re-derive TOTAL rows and ratios; model a per-section `as_of`, since a month-labelled deck mixes refresh cadences.
30. Script-generated decks (`PptxGenJS`) have zero placeholders, non-unique bracket tokens and a 10 x 5.62 in canvas; address shapes by `(slide, shape id)`.
31. Before reporting that a PDF displays something wrong, look at a render: the text layer and the pixels can differ. Pull link annotations; `/Creator` tells you the tool that made it.
32. Byte-identical HTML across a site's URLs is a client-rendered SPA with no SSR: every page serves the root page's title and meta.
33. HTTP 999 (LinkedIn) and uniform anti-bot 403s are non-results, never evidence either way.
34. When unsure which artifact is authoritative, split the answer by axis (structure, metric selection, tone, construction method) rather than naming one file.

## Examples
- 1: a person was found only as three ASR spellings of a short first name; surname and role-title searches returned zero.
- 3: 19 files matched a name; genuine testimony came from 5 people.
- 10: "sole tech-SEO capability" reached a client report though a second person doing that work was named in its own appendix.
- 20: an @-mention asking a person to add a link was nearly read as that person supplying it.
- 24: a sheet declaring 1,000 rows held 20.
- 27: a handed-over "monthly reports" package had never produced a single report.
- 31: 21 bracketed placeholders in a resume's text layer were confirmed only after reading the rendered pages.
