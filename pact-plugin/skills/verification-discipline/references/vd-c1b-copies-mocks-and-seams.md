# VD-C1b: copies, mocks and seams

Context: read this when a test exercises a copy of the rule, a mock of the deciding layer, a
hand-built intermediate value, a shared helper, or two layers that are each tested but never
joined. A test can only fail when the thing it runs can change; a copy cannot observe its
original. Harnesses, wrappers and doubles that disagree with production are in
[vd-c1-harnesses-doubles-and-quirks.md](vd-c1-harnesses-doubles-and-quirks.md). Entry file:
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

### Copies of the rule
- **C1b.51** A test or gate that transcribes or reimplements the rule it checks tests its own copy. Extract the rule into one exported function, import it at both ends, and mutate the SHIPPED original.
  - An honest "transcribed from X" label is the trap, not a mitigation: it reads as considered and stops the author re-checking. "I extracted it" is not evidence; the red on the shipped rule is.
  - A contract test that is red on day one against code you believe correct usually means a missing transport step between the two sides; route both through the shipped function, never reconcile in the test.
  - Extraction fixes consumer-side blindness only; the producer still needs a contract test over its real output.
  - Read a test's imports before its assertions; if the rule is inline in a handler, there is no seam and the test is a copy. Never write another copy to unblock yourself; ask the owner and wait.
  - A gate that reimplements the semantics it checks is blind to the artifact: mutate the ARTIFACT and watch the suite stay green. Such a green means "broke nothing else", never "the change is right".
  - Prove a hazard with the refactor someone would actually type (a bare `.trim()`), not with a warning comment.
- **C1b.52** Transcription vs pin depends on WHICH SIDE of the assertion the literal sits. A literal expected against an imported source is a pin; a local copy used instead of the source is a transcription.
  - Ask: if the source changes and my literal does not, does this go red? Yes means pin. "Do not restate lists" is not the rule; deleting a pin for that reason removes a control.
  - A refactor can make an existing check true by construction (a union composed from its parts); re-ask what each surviving assertion can still fail on. Add disjointness and no-duplicate checks.
  - Pin MEMBERSHIP, not counts, when sets carry meaning: swapping one name each way keeps both lengths. Assert distinctness where values mean something only by differing (`new Set(tags).size === N`).
  - A brittle hardcoded subject can be the detector: iterating whatever a category holds passes over an empty one. When a category legitimately empties, inject the vocabulary rather than skip.
  - A skip gate needs three states: run when configured, FAIL by default when not, skip only under an explicit opt-out that prints what stayed unverified.
  - Run mutations in a throwaway `git archive` copy, never the shared worktree: a teammate reading `git diff` mid-window sees your mutant as a real change.
- **C1b.53** When one side builds a shared format or contract, test it through the OTHER side's real parser. One builder feeding both sides of a join moves both together and stays green.
  - Mutate the format to one the foreign parser rejects. For lanes coding against an unwritten API, build a scratch tree of the consumer's commit plus the producer's working tree, derive inputs from the producer's code, and pin a shared constant.

### Mocks, intermediates and layers
- **C1b.54** Well-tested layers do not make a tested seam. If every consumer test injects the value, or `vi.mock`s the module, no test ever runs the producer's real output into the consumer.
  - Grep the test tree for the producer's name and its literal reason strings; grep for `vi.mock("<path>")` and whether anything imports the module unmocked; grep for importers of the composition root.
  - Assert at the outermost boundary you can reach (stub `fetch`, not an injected transport), and mutate the wiring itself. Two defects either side of an unasserted seam can cancel, so fixing one alone breaks it.
- **C1b.55** A test that hand-builds an intermediate value bypasses the producer that decides it. Drive the real input-to-output path and confirm the mutated function is actually called.
  - If the mutated function's name does not appear in the test file's call graph, the suite cannot see the mutation; mock one layer lower. A suspiciously low red count is the tell.
  - Before a mock backend serves as evidence of the wire, read the serialiser, not the schema's description string.
  - Never hand-set a field a framework populates later (`LogRecord.exc_text` is `None` at filter time); drive the real Logger to Formatter path, assert on the final output, and pair it with a known-good control.
  - Build fakes through the producer's real inputs, not by assigning the derived flag another lane may turn into a read-only property.
- **C1b.56** A check that shares the implementation, variable or predicate of what it verifies agrees vacuously. Verify against an independent source or in a different idiom, with a dirty and a clean control.
  - "Remove X" followed by "X is absent" is where sharing is most tempting: a shared pattern builder with a broken `\b` both failed to remove and failed to find.
  - A copy verifier's destination side applies NO predicate: hash everything found, partition the source into copy / skip / FATAL, and print an extension histogram.
  - A parts-vs-sum check over a ledger walk telescopes and cannot fail; call it a tolerance-drift check. Only re-reading the source of truth at the point of use is independent; two outputs agreeing prove nothing.
  - Derive the write-side contract from code before reading data; refuse on ambiguity rather than picking the nearest; collapse duplicate call sites; sequence an inert guard early.
  - A runtime claim restated across several hops (a docstring, a review, a memory, a dispatch) has usually never been executed. Ask "did anyone RUN this?", and include a positive control in the probe.
- **C1b.57** A test asserting the right property against a MOCK of the layer where it is decided guards nothing. Name the one function where each rule is decided, and mutate there.
  - Highest-risk layers: parsers and coercions behind a mocked client, fail-closed branches that nothing exercises, and any behaviour whose correct result is an absence.
## Examples
- C1b.51: narrowing a route's rule would have left all 6 assertions of a test that declared its own copy green; after extraction the same mutation turned 3 of 6 red.
- C1b.51: a simulator committed with 45 gate tests stayed OK with the emitted query's regex class replaced by `ZZZ_DELIBERATELY_BROKEN_REGEX`.
- C1b.52: two classified sets pinned by length (6 and 5) stayed green after one name swapped each way; a membership pin killed it.
- C1b.53: a consumer's tests stubbed the producer's SQL with its own column aliases; 22 of 22 passed and every production run would have raised `KeyError`.
- C1b.54: mutating a producer's core expression changed nothing across 846 tests; its only referencing test file mocked it.
- C1b.55: collapsing 403 into 401 turned exactly one of fifteen tests red, because rendering tests built their own `{kind}` objects.
- C1b.55: a redaction filter test set `exc_text` by hand; a real `logger.exception()` leaked the canary with the filter installed.
- C1b.56: a copy verifier that filtered both sides with the same extension predicate would agree perfectly while `.HEIC` files were missing.
