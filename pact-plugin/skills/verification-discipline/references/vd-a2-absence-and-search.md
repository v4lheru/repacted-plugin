# VD-A2: absence and search

Context: read this before reporting "none", "never", "no callers", "not found", "does not
exist" or any count of a population, and before relying on a search that came back clean or
that "found it". A search result is a fact about your pattern, your scope and your corpus
first, and about the world only after that. How searches themselves fail (wrapped text,
aliases, parsers, credentials), and diagnosing WHY data is missing, are in
[vd-a2-search-misses-and-absence-diagnosis.md](vd-a2-search-misses-and-absence-diagnosis.md). Whether the instrument itself could
see anything is in [vd-a2-instruments-and-controls.md](vd-a2-instruments-and-controls.md).

## Canonical rules

### A clean answer to a narrower question
- **A2.1** A clean or successful result often answers a narrower question than the one asked. Before promoting it to a claim, say word for word which question it answered.
  - A hit stops the search, so read the whole set: every rule, column, match and branch, and what each one does. The defect lives in the member you did not read.
  - For a compound claim, name which conjunct you tested and which you inherited. A corroborating check must NAME a value, never count values (`max()` on a TEXT version column is lexical).
  - Read the tool's own "does not catch" notes, and check which mode or branch CI runs it in. Write the negative next to the check.
  - Control a hand-built model of a system: its "before" state must reproduce a real past event.
  - One surfaced instance of a masking pattern is rarely the only one. Grep the neighbours for the same EXPRESSION SHAPE (`?? <substitute>`, `str(e)` in a response body).
  - When explaining a difference, iterate until the two sides are equal. The first divergence is only the earliest one, not the most important.
  - A check can pass correctly while the hazard stays live: a section count proves no section was lost, not that content landed in the right one.
  - A shortlist-then-verify method finds only candidates that look alike. Measure how many inputs your metric cannot score, because an unscoreable input reads as unique.
- **A2.2** A match answers existence, not distribution. Enumerate every match before characterising grain, count, shape or topology. If you cannot, write "at least one call site does X".
  - "I stopped it at mine" is a distribution claim: measure whether the spread is a chain or a fork. Two defects in one file are not one defect.
  - Config states intent, while the list of jobs that actually ran states behaviour. Resolve inheritance (`extends`) before counting.
- **A2.3** A top-N frequency listing hides rare values by construction. To claim X does not occur, run the predicate that IS the claim. The scope of a search is part of its result, so keep it in the sentence.
  - Restate an unverified recollection as a predicate before querying. The more someone will act on your refutation, the higher the bar.
  - Do not state a current-state fact ("dormant") as a fact about a window. For contiguous rates, the baseline window length is a correctness parameter.
- **A2.4** Absolute words (never, always, all, none, zero) need an exhaustive check. A capped, sampled, paginated or time-boxed scan supports only "0 in the N I scanned".
  - "Not yet verified" is an honest third state. Before stating a finding, write down what you looked at and what you would have missed.
  - Say in the same sentence whether a number was measured or derived. The claim right after good empirical work is the one most likely to be unchecked.
  - A check whose result is identical under both hypotheses verifies nothing. Persisting an unverified conclusion strips its hedge and is worse than saying it.
- **A2.5** A property of one member is not a property of the class. State the population you probed in the claim, count the members tested against those that exist, and look for an enumeration endpoint.
  - Inferences stack: count the hops between what you MEASURED and what you ASSERT, and mark each. A tool surface is not a transport, which is not an active code path.
  - Before a rejection or a HALT, name the one observation that would dissolve your premise, and go and get it. Find a cheap discriminator (a valid id returns 200, an invalid one 404).
- **A2.6** A negative is scoped to the space and corpus searched. Search every identifier space (keys AND titles), every location and every filename, and run a positive control before declaring absence.
  - When an observer contradicts your API evidence, find the world where both are true. Ask whether your evidence would look different if the opposite hypothesis held.
  - Identify the corpus by file count, because several vaults, checkouts or projects can hold similar trees. An instruction file is a claim about the filesystem, and it decays.
- **A2.7** A negative's strength depends on whether the source is enumerable and was fully enumerated. Only then may absence become closure. Otherwise state its scope and time.
  - Push absence to closure with arithmetic after listing the container (part sizes against the total). A closure claimed on an open world is worse than a bare absence.
  - Presence evidence survives a change of scope, and absence evidence does not. A teammate's observed presence beats an unverified scan (a non-recursive glob misses subdirectories).
  - Mark not-examined rows in a coverage table explicitly, because a missing row reads as "absent". Silence in your inbox is not a fact. Name which carrier you checked.
  - A close or status event proves completion only if it sits next to the last substantive event. Check a ledger's silence against a case you know was recorded.
  - When relaying, keep open-world hedging verbatim ("not surfaced via the endpoints I could reach"). The hedge is the precision.
- **A2.56** A leak-absence check (a password, token or demo value not in the output) must first decode what the text search cannot read: unpack zips and trace archives, and decode base64 blobs in HTML reports. A plain grep over an encoded artifact returns 0 whether or not the value is there.

## Examples
- A2.1: seven clean, true results in one session each answered a narrower question (a three-dot diff, an ORM census, `ls` versus committed files, a grep that omitted DELETE, a drift check in its default mode).
- A2.2: a grep found a domain-keyed fetcher and stopped. A per-URL fetcher sat 26 lines later and inverted a quota estimate.
- A2.3: a date sentinel occurred twice in 555 rows, invisible to a top-six listing, and a colleague's correct recollection was "refuted".
- A2.4: "has NEVER executed" came from a time-boxed sweep. The node had run 9 times.
- A2.5: one widget with no mention field became "the API has no mention data". The stack of inferences that followed held a deploy on an import statement.
- A2.6: a key search reported an item absent while the owner found it by title. The title had been transliterated into key shape.
- A2.7: a non-recursive transcript scan reported one credential 5 times. A recursive scan found it 63 times across 8 files.
- A2.56: greps over a Playwright run's diff, test results and report dir found no demo login value; a reviewer found it in `playwright-report/index.html`, inside a base64 zip (a fill step's title).
