# Claim and capability verification

Context: proving that a claimed capability, product fact, market claim or contribution is real,
before it goes into a report, a brief or a client deliverable; plus the requirements checklist for
research that feeds a build. Read this when a README, a status flag, a registry, a vendor page or a
brief asserts something you are about to repeat. Code-level claims (tests, deploys) belong to
repacted:verification-discipline.

## Canonical rules
1. A capability ships only with four signals: code volume, an entry point (CLI choice, route), tests, and wiring (the handler is imported and dispatched). A same-named plan file proves nothing.
2. A loop is closed only if you can name the return edge: the file and call where a later stage's output is read back by an earlier one. A human re-running a stage is a manual loop, never "autonomous".
3. Check that a loop's hardcoded paths and flags resolve (`ls` the script, find the flag in its argparse). A dangling path is a loop that cannot close.
4. Read which branch calls a gate: a readiness check consulted only in `dry_run` means the mechanism is automated and the trigger is not.
5. Count an overloaded term's referents, not its hits: one word can name a data pipeline, a tuning loop and a manual protocol in the same repo.
6. An `accepted` ADR is a decision, not a capability. Check for an implementing plan AT ALL (glob the plan dir for its nouns), its status, and whether its dependencies are installed.
7. A doc contradicting the code is often a stale doc: `git log` the file or dependency before calling it an open conflict. A deliberate removal closes the question.
8. A prominently named orientation doc can be a fossil of a renamed repo: verify its structure against `ls`.
9. Separate engine maturity from product maturity up front; a generated status rollup is the fastest ground truth for "does the product exist".
10. A platform status flag (`ACTIVE_HEALTHY`, `SUCCESS`) proves infrastructure is up, not that anything uses it. The freshest row in a usage or log table is the live proof.
11. `git log --reverse -1` returns the LAST commit (`-1` applies first); identical first and last dates across an estate is the tell.
12. Enumerate every author identity (`git log --all --format='%an <%ae>' | sort | uniq -c`); unconfigured defaults (`Your Name`) are unattributable.
13. A repo cloned from a template misleads both ways: scope authorship to project paths (`git log -- src migrations package.json`) and report both denominators.
14. The name a person remembers may be a private wrapper, not the public engine; a private repo is uncitable however large the contribution.
15. A registry name returning 200 may belong to a stranger: read `maintainers`, `repository` and `author`, with positive and negative controls.
16. PR line totals conflate docs with code: classify by extension and publish the code figure beside the total. Large PRs truncate per-file stats; trust the PR header for totals.
17. External merged PRs from strangers beat stars as evidence; a same-day-closed PR to a famous repo is a liability, not a credential.
18. Before quoting a classifier's headline F1, map the caller's target countries or classes onto its leaf taxonomy; the target may not be in the output space at all.
19. Coverage is not accuracy: "19 of 27 have a parseable prefix" says nothing about how many are right. Never put the two in one sentence.
20. A "signal never reaches the model" claim is a claim about a regex: enumerate the realistic input forms and run them through the actual expression.
21. Phone dialling codes locate the numbering plan, not the person; +1, +7 and +44 are shared; number portability never crosses borders.
22. Python `urllib.robotparser` silently ignores `*` and `$` rules and returns a false "allowed". Use a matcher of your own (longest match wins, Allow wins ties) and positive-control it.
23. robots.txt is a crawler directive, not a licence: report it as evidence narrowing a legal question, never as the answer.
24. AdsBot ignores `User-agent: *`; ad destination-not-working disapprovals prove the platform already fetches the landing page.
25. For page-change detection, measure the noise floor first (fetch twice, about 20 s apart) and diff only brand-mentioning sentences.
26. Falsify market and hiring claims with first-party data (public ATS board APIs, `html.unescape` first, exact phrases) and report the base rate.
27. Check a brief's named competitors' organic footprint before building against them; some have almost none.
28. Before an acronym enters a keyword map, read its related-phrase set: contamination inflates volume while deflating difficulty (`dpo` is mostly pregnancy and accounting).
29. Name-collision checks: search the name plus category, the domain, recent launches and SERP rank. Trademark diligence is out of scope; flag it to the lead.
30. A uniform anti-bot status, a parser returning the same answer for every input, or a suspiciously total zero indicts the instrument before the data.
31. In dev tooling, docs and blog posts earn the non-brand traffic; marketing pages rank only for brand. Technique names and literal paper titles are cheap (low KD) but perishable.
32. Names found taken in AI and dev tools (May 2026): Prism (OpenAI), Atlas, Pulse, Beacon. Re-check before recommending; the list dates fast.

## Requirements checklist (research that feeds a build)
- Primary question and success criteria written down; constraints (stack, licensing, timeline) named.
- Sources ranked: official docs and repos first, maintainer content next, community and AI-generated text only when verified; version numbers and dates stated.
- API research: auth tested with positive and negative controls, rate limits, error codes, pagination and SDK availability recorded with the date checked.
- Each requirement testable (clear pass or fail), traceable (an ID, a source) and free of vague words ("fast", "easy").
- Non-functional needs named where they decide the design: performance, security, data handling, compatibility.
- Every "not found" states the search that ran and its scope.

## Examples
- 1: a repo held `docs/plans/<x>-plan.md` beside a real module of the same name; only the four signals separated them.
- 7: "the ADR says X, the code lacks X" was a deliberate removal the owner had asked for.
- 10: of 7 databases reporting healthy, row recency changed 5 verdicts (1 live, 4 stale, 2 empty).
- 15: three "npm packages" returning 200 all belonged to strangers.
- 20: of six realistic ways a phone number appears in an email, one was stripped and five reached the model.
- 22: `Disallow: */search/*` and `Disallow: /corp/*.pdf$` both came back allowed from the stdlib parser.
