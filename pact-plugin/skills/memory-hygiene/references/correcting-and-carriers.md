# Correcting a claim in every carrier

Context: a wrong claim rarely lives in one place. It sits in a skill reference, a head, an inbox, a
vault page, several fields of a pact-memory record and a project note, and the copy that loads
automatically is the one most likely to be stale. This reference is how to retire it everywhere,
and how to write memory that is easy to correct later.

## Canonical rules
1. The correction's scope is the CLAIM, not the record you were told about. Before closing, ask "where else is this asserted?".
2. Search by the claim's distinctive PHRASING, not by an id: an id finds only the copy you already knew.
3. Sweep every carrier: the plugin source repo (skills, core), every head and inbox, the vault, pact-memory, project notes, and any outbound draft.
4. Rewrite the wrong rule in place, stated as the correct rule. Never append the correction beside it and never leave a strike-through: every reader of the old copy keeps reading it.
5. Provenance goes in the commit sentence (repo) or a `Corrected YYYY-MM-DD:` suffix (vault, memory). Alternatives rejected on purpose: a pointer appended at the stale site, and `--replace` in one store alone.
6. If you widen the scope beyond what you were asked, say so plainly and offer to revert; silent scope-broadening is the worse failure.
7. pact-memory `update` APPENDS to list fields: a correction saved that way sits beside the stale item and both are served. Retire a claim with `--replace` and the COMPLETE list.
8. `--replace` is scoped to the fields present in the payload. The safe default is one field per call; a combined payload is safe only when every list in it was built from a fresh `get`.
9. Snapshot the record to a file before a `--replace`, and diff after: every untouched item survives, scalars are unchanged, and the retired phrasing is gone from each item. Count matching alone proves nothing.
10. Entities with the same `name` do not merge: a changed note appends a second entity. Filter out every member with that name, append the corrected one, assert names are unique, then `--replace`.
11. Scalars are replaced only if you pass them. Pass a rewritten `context` in the same call whenever a list change makes it stale; `context` is the preview field every search shows first.
12. Enumerate the carrier FIELDS from the schema, not from memory: `context`, `goal`, `active_tasks`, `lessons_learned`, `decisions` (decision, rationale, alternatives), `entities[].notes`, `reasoning_chains`, `agreements_reached`, `disagreements_resolved`.
13. Verify per item, not by global count: each item that still holds the stale phrase must also hold the correction marker. Your own retraction quotes the claim, so a global count never balances.
14. Build every payload in a file and pipe it in. Never pass memory text through a shell: backticks run as command substitution, the term vanishes, and the CLI still reports success. Read the stored prose back and check the words you meant are there.
15. Anchor a rewrite on the shortest substring verified unique in the STORED text, or replace by index behind an identity assert. A long anchor rebuilt from your own draft matches nothing.
16. A stale INSTRUCTION ("ask the lead for X", "re-derive Y") outranks a stale figure for replacement: the figure is only believed, the instruction is acted on.
17. A memory may own a fact, never another file's current wording. Name a cross-reference with a date ("as recorded on ..."), not in the present tense.
18. Whatever must survive a crop goes first: the head of `lessons_learned[0]` and of `context`.
19. Record every opaque id with where it was read and which kind of evidence you hold: a schema source proves the field exists; a populated record proves what it means. An empty value on sampled records is a fact about the data, not a doubt about the mapping.
20. Timestamp every state claim, your own included. The tell of a stale claim is the present tense over a fact acquired earlier ("the service is on X").
21. Never put a count in durable text ("this misled two readers"): it ages in the wrong direction. Name the mechanism instead.

## Detail

**Why the automatic copy loses.** Agent memory loads into the system prompt through the head;
pact-memory answers only when queried. A fresh agent meets the stale copy first and has no reason
to ask the store that holds the correction. So a correction that lands only in pact-memory is
still served false.

**Correction order.** Grep by phrasing across all stores, list every hit with its field, rewrite
each in place, then run the per-item check. Report which carriers you touched; if you went beyond
the one the requester named, say so.

**Filing so the next correction is cheap.** One lesson per line in the inbox, with its evidence
path; the property it teaches in the words, not the project it happened in; a candidate label on
any generalisation drawn from a single instance.

## Examples
- A correction landed in two pact-memory fields and missed the record's `context` and an agent
  memory file; two days later a briefing restated both retracted claims.
- An update inlined through a shell lost a backticked term; the CLI returned ok and the stored
  sentence read "triggered by the teammate's own .".
- A "withdrawn" figure survived in two `decisions[].rationale` strings because the sweep had
  hand-picked the fields to scan.
