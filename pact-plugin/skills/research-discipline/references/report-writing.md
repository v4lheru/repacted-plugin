# Writing inventories, audits, reviews and research reports

Context: how to write the document a research or audit task produces so its reader can act on it
and check it. Read this before you write an inventory, catalogue, registry, audit, review, research
report or technology comparison.

## Canonical rules
1. Filter to signal: list only items that match what the document is FOR. Exclude empty stubs, exact duplicates, abandoned variants and off-purpose artifacts by default.
2. When exclusions carry signal (shadow IT, governance), collapse them into ONE line ("excluded as non-agentic: X, Y, Z"); never give each a full card.
3. Fold helper and component scripts into their parent solution as chain components, not standalone rows.
4. Write findings and decisions, not the process that produced them.
5. Mark every claim checked or believed, with its scope and as-of date; put a staleness banner on any snapshot.
6. State the command or query that regenerates a count beside the count: the count ages, the command does not.
7. Name the layer or artifact in the claim ("in the generated JSON, not in the builder line") so opening a different file cannot falsify it.
8. Record errata in place with what still stands; never leave a corrected figure live in its original field.
9. When a correction is needed, report the verified figure; do not invent a tidy story for how the wrong one arose.
10. Prefer shipping the per-item table over the headline total, and state the unit you counted ("84 numeric values", not "96 cells").
11. No personification in reviews: write "the code reads X", never "the code wants X" or "the guard thinks".
12. Review and audit output is objective: address "the security review" or "downstream reviewers", never a named individual, and frame findings by severity, evidence and remediation.
13. Verify any stakeholder name a briefing introduces before carrying it into briefs or reports; a relayed name can be fabricated.
14. State a negative with its scope ("not found by X across the 13 per-person analyses"); keep "not covered by the research" separate from "does not exist".
15. When the user has annotated a file, check its mtime: edits may still be unsaved in their editor. Ask them to save before applying, and offer your own cut-list meanwhile.
16. Label rows the corpus cannot answer "undeterminable from the documents" rather than inferring them.

## Templates
- `../templates/technology-comparison.md`: quick comparison, weighted scoring, decision record, risk table.
- `../templates/api-exploration.md`: an API record (auth tests with controls, limits, endpoints, errors, pagination, date checked).

## Examples
- 1: an agent registry listed every Apps Script project in an account, including an empty stub and a byte-identical duplicate; only a few were real agents.
- 10: a duplicated-slide defect was reported as "96 of 96 cells identical"; the defensible figure was 84 of 84 numeric values.
- 13: a stakeholder name from a briefing was carried into 7 briefs and several review docs; it was bogus.
