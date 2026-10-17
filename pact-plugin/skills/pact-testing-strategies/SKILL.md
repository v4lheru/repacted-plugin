---
name: pact-testing-strategies
description: "Use when designing or reviewing tests in the PACT Test phase: test pyramid and coverage, unit/integration/E2E patterns, mocking, counter-test-by-revert, craft pitfalls."
---

# PACT Testing Strategies

The Test-phase rules, in short form. Full text and examples:
[test-design-by-level](references/test-design-by-level.md) (pyramid, unit, integration, E2E,
organization, checklist) and [counter-test-and-craft-patterns](references/counter-test-and-craft-patterns.md)
(counter-test-by-revert and the codified craft patterns). Deeper topics:
[test-pyramid](references/test-pyramid.md), [integration-patterns](references/integration-patterns.md),
[performance-testing](references/performance-testing.md).

## Test pyramid and coverage

| Layer | Target | Focus | Speed |
|---|---|---|---|
| Unit (many) | 80%+ line coverage | business logic, edge cases | < 1 s per test |
| Integration (some) | key paths | API contracts, data flow | < 10 s per test |
| E2E (few) | critical flows only | user journeys, happy paths | < 60 s per test |

## Unit tests

- Arrange-Act-Assert; one behaviour per test.
- Test behaviour, not implementation: assert the outcome (a user saved with a hashed password),
  not the call count on a collaborator.
- Mock only external dependencies you do not own (third-party APIs, the network).
- Cover edge cases with a table (`it.each`): empty, boundary, malformed, unicode, the error path.

## Integration tests

- **Non-mocked seam rule (MUST).** Any hook or component whose observable value depends on an
  integration seam (task-directory resolution, the real session journal or inbox, an env-keyed
  `CLAUDE_*` path, the real platform task store) needs at least one test that exercises that seam
  FOR REAL: a temp git repo, a real on-disk task JSON, a real journal write. A fully mocked suite
  can pass while the hook never fires live, because the broken seam is the one every test stubs.
  Resolve `{config_dir}` (`$CLAUDE_CONFIG_DIR` if set, else `$HOME/.claude`) from a path already
  in your context; never assume `~/.claude`. In repacted's own tree the seam-dependent set is `SEAM_DEPENDENT_HOOKS` in
  `hooks/shared/hook_infra_classifier.py`; the worked exemplar is
  `tests/test_missed_wake_scan_integration.py`. Platform-runtime-only behaviour gets a documented
  manual smoke note, not a shipped runtime hook.
- API contract tests: real app, test database, status codes and body shapes on success and error.
- Database tests: a test database (Docker or in-memory), migrated once, cleared before each test.

## E2E tests

Cover the critical user journeys only, end to end, with stable `data-testid` selectors, a test
user logged in per test, and seeded data.

## Organization and naming

Separate `unit/`, `integration/` and `e2e/` directories, with shared `fixtures/`, `helpers/`
(factories, matchers) and `mocks/`.
Name tests `should <expected behaviour> when <condition>`.

## Decision logs

Read the Code phase's `docs/decision-logs/{feature}-{domain}.md`: areas of uncertainty (where bugs
hide), assumptions (test them), known limitations (test the boundaries), trade-offs (verify the
accepted behaviour).

## Counter-test-by-revert (prove a regression test is coupled to its fix)

- Revert the production fix and assert the targeted tests fail with the expected cardinality;
  document it as `{N fail, M pass}` in the design or the test docstring.
- Restore crash-atomically: `git revert -n <sha>` then `git revert --abort`; if it cannot isolate
  the fix, the red-run recipe in repacted:concurrent-agent-git. Never `git stash` (shared stack)
  or a `/tmp` copy. Afterwards `git diff --quiet -- <paths>` exits 0.
- **Bundled commits** (new source + its new tests): measure with a SOURCE-ONLY revert
  (`git checkout <sha>^ -- <source files>`); a whole-commit revert also removes the new tests and
  masks the protection (measured: `{1 fail}` vs `{33 fail + 1 collection error}`). Name the
  technique in the verification matrix.
- **In-process mutation** is undone by any `importlib.reload`: apply it from a per-test hook or edit
  the source on disk. A total non-flip is an instrument alarm first: re-run one expected-red arm alone.

## Testing-craft patterns

- **Author-blindness in HANDOFF arithmetic**: an author's own cardinality, set-membership or
  fidelity claims carry errors self-review misses. Every such claim gets a literal recount by an
  agent in a different role-context (cross-stream verification). Distinct from the
  aspirational-HANDOFF pattern (a claim believed from the prompt, never checked).
- **`count_active_tasks` fixture completeness**: register every task owner in
  `team_config.members[]`, or name the unknown-owner exclusion as the mechanism under test and prove
  the assertion holds without it; otherwise `count == 0` is green for the wrong reason.
- **Sibling-file convention**: a parametrized N×M noise-budget regression test lives in a sibling file
  that cross-references the primary file, never inside the primary phase-specific file.
- **Positive control**: every 'expect refusal' test also proves the same request succeeds for the
  rightful caller; a refusal alone passes on a missing fixture or wrong id.

## Checklist before leaving the Test phase

- Coverage: unit 80%+ on business logic, integration on API contracts, E2E on critical journeys,
  edge and error cases.
- Quality: independent tests, behaviour-describing names, no flaky tests, fast (unit < 1 s,
  integration < 10 s).
- Maintenance: factories and fixtures, minimal focused mocks, realistic data, CI runs everything.
- Security: access-control tests, input-validation edge cases, no sensitive data in errors, rate
  limiting tested.
