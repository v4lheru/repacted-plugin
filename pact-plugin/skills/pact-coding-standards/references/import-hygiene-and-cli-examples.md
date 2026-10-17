# Import hygiene check and CLI examples

The full contract of the `lint-check.sh` import-hygiene check and the rule for teaching agent-facing CLIs. The router ([../SKILL.md](../SKILL.md)) carries the commands and the verdict contract in short form.

## Scripts

### Import Hygiene Check (--files mode)

Behavioral tests cannot see an unused import — a dead `import os` passes
every test green. The import-hygiene check closes that blind spot
mechanically. Run it over EXACTLY the `.py` files you modified before
running the test suite:

```bash
bash <skill-directory>/scripts/lint-check.sh --files '/abs/path/to/modified_a.py' '/abs/path/to/modified_b.py'
```

One separately quoted absolute path per file, never one quoted list — a filename is untrusted shell input.

(When your dispatch names a plugin root, the script lives at
`<plugin-root>/skills/pact-coding-standards/scripts/lint-check.sh`.)

A bare `lint-check.sh` with no arguments is refused. Do not present it as a lint command.

### Lint Check Script (legacy directory mode)

Whole-tree project-type detection is reachable only with a directory argument:

```bash
# From within the skill directory:
chmod +x scripts/lint-check.sh
./scripts/lint-check.sh /path/to/project
```

**Verdict-line contract** — the LAST stdout line is always exactly one of:

| Verdict | Meaning | Exit code |
|---------|---------|-----------|
| `IMPORT-HYGIENE: PASS` | No findings in the checked files | 0 |
| `IMPORT-HYGIENE: FINDINGS (n)` | n findings printed above the verdict | 1 |
| `IMPORT-HYGIENE: SKIPPED (<reason>)` | Check could not run (no arguments given, arguments given but none a checkable .py file, or no usable checker) | 0 |

Record the verdict line verbatim in your HANDOFF `produced` field — every
run ends in a verdict, and a SKIPPED must be visible, never silent.

**What to do with findings:**

- Fix every finding in files you modified: delete the unused import, or fix
  the undefined name.
- Never lint-fix files you did not touch — the check's scope is your diff.
- A deliberate side-effect import or re-export keeps a reasoned suppression
  at the import site: `# noqa: F401  # re-export: <why>`. Never a bare
  `# noqa` without a reason.
- If the script is missing or errors, state that in your HANDOFF instead of
  skipping silently.

**How it checks** (internal — the coder-facing contract is just the command
and the verdict): an execution-probed ladder tries ruff, then pyflakes, then
flake8, each restricted to the unused-import/undefined-name classes only
(`--select F401,F821`) so you are never asked to fix style noise; when no
linter is installed it falls back to the stdlib AST checker
`scripts/check_unused_imports.py` (unused imports only, try/except-scoped
imports treated as advisory). A checker crash degrades to `SKIPPED` — the
check fails open and never blocks you on its own breakage.

### Teach examples for agent-facing CLIs

When you add or change an agent-facing CLI, teach it with examples that
paste-execute verbatim: build every example as
`python3 "<script-path>" <verb> ...` — bare `python3`, never a pinned
interpreter, and always quote the script path. In `--help`, the epilog lists
all of the CLI's examples; a usage error appends exactly one example — the
offending subcommand's own — separated from the error line by a blank line.
Keep each CLI's pre-existing usage-error exit code; teaching examples never
changes an exit contract.
