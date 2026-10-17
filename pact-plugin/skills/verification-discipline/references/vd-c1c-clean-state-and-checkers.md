# VD-C1c: clean state, target environments and checkers

Context: read this before reporting a green that was produced on your machine, in your
working tree, in an environment you built, or by a linter or checker. Each of those answers a
narrower question than "does the shipped thing work". The entry file for runner and pipeline
scope is [vd-c1c-runners-and-toolchain.md](vd-c1c-runners-and-toolchain.md).

## Canonical rules

### Whose green is it
- **C1c.7** A green that depended on anything outside the repository is a fact about the machine. State the runtime with every green; treat an untracked artifact as load-bearing until disproved.
  - When two agents report contradictory environment behaviour, run the command yourself rather than arbitrating. A populated `node_modules` is not evidence an install ran.
  - An untracked `node_modules` symlink, an engine pin the box cannot meet, and an empty CI stage all live between the repo and the machine, where no reviewer of the diff can see them.
- **C1c.8** Local gates test the working directory; you ship the committed tree. Build and test a `git archive <sha>` extract when others can write, you did not author every change, or the tree is dirty.
  - The trigger is any divergence between disk and commit, and in a shared tree the commonest is a PEER's uncommitted edit, not your own forgotten `git add`. A clean `git status` does not clear it.
  - A skip count is the part of the report that says what was NOT tested. Identify every skip before accepting a run; an extract silently skips tests whose fixtures live outside the repo unless the suite's require-flag is set.
  - The same extract proves causation: cold trees differing by one variable each (pristine, plus one file, plus the fix) settle a build argument.
  - Extraction mechanics (symlinking `node_modules`, shared-tree hazards): repacted:concurrent-agent-git.
- **C1c.9** A green counts only if the documented one-command entry point reaches it from clean. Purge, run it, check the exit code and that nothing was left behind; do it twice, and report the sequence.
  - A prerequisite the entry point cannot satisfy is a defect in the entry point. A new phase must fail on a non-zero exit AND on exit 0 without its artifact.
  - Anything that makes your environment special (a carry-over cache) hides the bug from you alone. Remediation advice you have never run is as untrustworthy as an assertion never seen failing.

### Environments you built
- **C1c.10** An environment you built cannot surprise you; one that cannot reproduce the target's CONSTRAINT reports it satisfied. List how it differs structurally and count a pass there as UNMEASURED.
  - Real data inside a constructed scenario is still a constructed scenario. Privilege model, auth method and instance identity are the usual gaps.
  - Budget a live smoke run as a mandatory line item, not an end-of-task option.
  - A zero from data where the failure was nearly impossible is not evidence: design the measurement to concentrate the dangerous regime.
  - An error message can name the wrong object (a nonexistent role reported as a password failure).
- **C1c.11** An executable structural claim (key-less import, mockable boundary) is verified by running it, not by reading the code. Import with an empty environment, then run the key-less suite.
  - `env -i PATH="$PATH" python3 -c "import pkg.a, pkg.b"`, then the suite. A missing pytest is a gap on your side: use `unittest`, or import the module as a package and call its helpers against a temp-dir fixture of the exact on-disk shape.

### Checkers and linters
- **C1c.12** A checker rule you have never seen fail is not a rule. Plant a violation of THAT rule, watch it go red, keep a negative control green, and name the tool that covered the property.
  - A planted syntax error proves only that the invocation ran; a config loading zero rules still reports parse errors. Run both probes and cite only the rule-level one.
  - Rule-pack selection (eslint `extends`, ruff `select`, semgrep rulesets, tfsec skips) can make a tool green because it is blind. Say plainly when you did not run the linter.
  - Source normalisation (strip comments, blank strings, strip JSX) is safe per rule, never per file: blanking strings erases import specifiers.
  - A regex reading a JSX element's opening row misses the props prettier moves to later rows: an element's own props sit exactly one indent level (two spaces) deeper. Exclude and REPORT unmodelled formatting; never guess.
  - Publish the PREDICATE with every count, and attribute a baseline disagreement by flipping one rule until your tool reproduces the other number.
  - A matcher-generated baseline cannot reveal what the matcher cannot see: reconcile against a cruder independent count and put every item in exactly one bucket.

### Carried from the same audits (design diagnosis)
- **C1c.13** A consumer that silently drops exactly the complement of some type's field set usually has the WRONG BUILDER; widening the whitelist cannot express the missing inputs.
  - Trace which builder the ORIGINATING surface uses. Before swapping builders, verify the join (relation, predicate, left vs inner), not the signature.
  - A stated rationale can be right and mis-applied: check the referent, not only the reasoning.
  - A mapped type guarantees nothing while an `as X` cast or computed-key spread sits downstream: remove the cast, and enforce no-cast with a lint.
  - Settle a measured-zero semantic divergence now; once a colliding value exists the same change reads as a regression. When re-measuring against a dated figure, check the domain size too.
  - A conceded uncertainty in a teachback or HANDOFF is a TODO addressed to you, not a disclaimer. Name a contingency clause by its TRIGGER, not its instance.

## Examples
- C1c.7: three local greens on one branch all depended on an untracked `node_modules` symlink; `npm ci` could not run at all under engine-strict.
- C1c.8: 547 tests passed with an imported module still untracked. Another green (301 passing) held only because a peer's uncommitted edit fixed the tree; the extract failed `tsc` with 3 errors. 432 passed / 7 skipped became 439 / 0 once the out-of-repo fixture was required.
- C1c.9: "179 checks green" came from a manual priming run's leftover fixture; from clean the lead measured 92/7 and 68/2.
- C1c.10: a spill-page cap of 5 against 20 in a sibling module passed 374 unit tests and a copied-page replay; the live corpus had 77 extractions against it. Scratch Postgres passed a role change that managed Postgres forbids.
- C1c.11: 42 key-less tests ran green in 0.009 s under an empty environment, confirming a mockable-boundary claim structurally.
- C1c.12: eslint extending only `next/core-web-vitals` let a planted unused variable and a planted `any` through. A lint rule on blanked source matched `from ""` and reported clean on every file.
- C1c.13: a six-key filter whitelist was exactly one options type's six fields; widening it could not have fixed 10 of the 11 dropped filters.
