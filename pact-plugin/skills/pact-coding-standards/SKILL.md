---
name: pact-coding-standards
description: "Use when writing, refactoring or reviewing code in the PACT Code phase: clean-code principles, error handling, logging, naming, and the lint and unused-import checks."
---

# PACT Coding Standards

The Code-phase standards, in short form. Each section links to the full text with examples:
[standards-in-depth](references/standards-in-depth.md), [error-handling-patterns](references/error-handling-patterns.md),
[clean-code-principles](references/clean-code-principles.md), [import-hygiene-and-cli-examples](references/import-hygiene-and-cli-examples.md).

## Core principles

1. **Single responsibility**: each function, class or module has exactly one reason to change.
2. **DRY**: extract duplicated logic.
3. **KISS**: the simplest solution that works; a plain config object beats a factory-builder chain.
4. **Defensive programming**: guard clauses first, safe property access (`?.`, `??`), clamp
   invalid values, fail with a typed error.

Examples: [standards-in-depth §Core Principles](references/standards-in-depth.md). SOLID, smells and
refactoring: [clean-code-principles](references/clean-code-principles.md).

## Naming

- Functions: verb + noun (`getUser`, `createOrder`, `validateEmail`, `calculateTotal`, `formatDate`);
  boolean checks `isActive`, `hasPermission`, `canEdit`.
- Variables: descriptive nouns (`userCount`, not `n`); booleans take `is/has/can/should`;
  collections are plural (`users`); maps name the key (`userById`, `priceByProductId`).
- Constants: `SCREAMING_SNAKE_CASE`; config objects frozen (`Object.freeze`).
- Classes: PascalCase nouns (`UserRepository`, `EmailValidator`); TypeScript interfaces are
  adjectives for capabilities (`Cacheable`) and nouns for contracts.

## Error handling

- **Fail fast, recover gracefully**: validate early, check business rules, catch specific errors,
  log them with context, and re-throw what you did not expect.
- **One error hierarchy**: an `AppError` base with `code` and `statusCode`;
  `ValidationError` 400 (with details), `UnauthorizedError` 401, `NotFoundError` 404
  (resource + id), `ConflictError` 409.
- No silent failures; user-facing messages stay friendly.

Code and language-specific patterns (Express/Flask handlers, retries, circuit breakers, error
response format): [error-handling-patterns](references/error-handling-patterns.md).

## Logging

| Level | When |
|---|---|
| ERROR | failures that need attention (database connection failed) |
| WARN | potentially harmful situations (rate limit approaching) |
| INFO | normal operational events (user created, order placed) |
| DEBUG | detail for debugging (parameters, intermediate values) |

Log structured objects (ids, codes, the error and its stack), never concatenated strings; tag every
request with a request id and log its completion with status and duration.

## Code organization

- Files ≤ 500 lines, functions ≤ 50 lines, lines ≤ 100 characters, nesting ≤ 3 levels.
- Module order: imports (external, then internal) → constants → the main export → private
  helpers → the export statement.

## Interpreting design-doc optionals

When a design doc marks a cross-link sentence, closing paragraph or sister-section reference as
OPTIONAL ("CODE may include or drop", "optional closing cross-link", "optional discursive
paragraph"), judge its relation to the preceding paragraph before writing it:
- a **strengthening clause** (reinforces, qualifies or extends that directive, e.g. where the next
  level of detail lives): FOLD it into the preceding paragraph;
- a **discursive observation** (a new framing or an adjacent concern): KEEP it standalone.

Never default to either form mechanically. Worked example: [standards-in-depth](references/standards-in-depth.md).

## Import hygiene check (run before the suite)

Behavioural tests cannot see an unused import. Run the check over EXACTLY the `.py` files you
modified, one separately quoted absolute path per file (a filename is untrusted shell input):

```bash
bash <skill-directory>/scripts/lint-check.sh --files '/abs/path/a.py' '/abs/path/b.py'
```

With a plugin root in your dispatch the script is `<plugin-root>/skills/pact-coding-standards/scripts/lint-check.sh`.
A bare `lint-check.sh` is refused; never present it as a lint command. Whole-tree detection needs a
directory argument (`./scripts/lint-check.sh /path/to/project`).

The LAST stdout line is always one verdict. Record it verbatim in your HANDOFF `produced` field:

| Verdict | Meaning | Exit |
|---|---|---|
| `IMPORT-HYGIENE: PASS` | no findings in the checked files | 0 |
| `IMPORT-HYGIENE: FINDINGS (n)` | n findings printed above | 1 |
| `IMPORT-HYGIENE: SKIPPED (<reason>)` | could not run (no arguments, no checkable .py file, no usable checker) | 0 |

Fix every finding in files you modified (delete the import or fix the name); never lint-fix files
you did not touch. A deliberate side-effect import or re-export keeps a reasoned suppression:
`# noqa: F401  # re-export: <why>`, never a bare `# noqa`. If the script is missing (never search the disk
for it) or errors, skip it and say so in the HANDOFF. How the checker ladder works: [import-hygiene-and-cli-examples](references/import-hygiene-and-cli-examples.md).

## Teaching examples for agent-facing CLIs

Build every example as `python3 "<script-path>" <verb> ...` (bare `python3`, quoted path). `--help`'s
epilog lists all examples; a usage error appends exactly one example (the offending subcommand's
own) after a blank line. Keep each CLI's usage-error exit code.

## Checklist before leaving the Code phase

- Structure: functions ≤ 50 lines, files ≤ 500, one responsibility each, nesting ≤ 3.
- Naming: descriptive, consistent, no abbreviations beyond id/url/api, boolean prefixes.
- Errors: every async operation handled, context in errors, no silent failures.
- Documentation: comments on complex logic, docstrings on public APIs, no commented-out code, no
  TODO without a ticket.
- Quality: no magic numbers, no duplication, consistent style, logging at the right level.
