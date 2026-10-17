# Skills

A skill is a folder of instructions that Claude loads only when a task needs it. Each folder
holds a `SKILL.md` (a short router with a one-line description that decides when the skill
applies) and, often, a `references/` folder with the detail, loaded on demand.

You rarely call a skill yourself. Claude picks one when its description matches the task, an
agent loads the skills its definition lists, and you can ask for one by name, for example
`/repacted:pact-memory`.

## Shipped skills

### Working as a team

| Skill | What it is for |
|---|---|
| `teammate` | The protocol every specialist agent follows: register, explain the task back, hand off, wait. |
| `multi-agent-collaboration` | What to do when another agent's instructions, claims or rulings drive your work. |
| `pact-lead-playbook` | For the lead agent: writing briefs, ruling mid-task, accepting or rejecting work. |
| `concurrent-agent-git` | Safe git when several agents share a repository, worktrees, an index or a stash. |
| `worktree-setup` | Create an isolated git worktree on a feature branch before starting work. |
| `worktree-cleanup` | Collect a finished worktree's documents, then remove the worktree and its branch. |
| `pact-handoff-harvest` | Turn the agents' end-of-task reports into saved project memory. |
| `pact-memory` | Save and search decisions and lessons across sessions (a local SQLite store). |
| `memory-hygiene` | Keep agent memory files small, correct and free of duplicates. |
| `pact-field-notes` | Known quirks of the framework itself and how to get past each one. |
| `telegram-guide` | When to notify you, or ask you a question, over the optional Telegram bridge. |

### The four phases: prepare, architect, code, test

| Skill | What it is for |
|---|---|
| `pact-prepare-research` | Research before building: compare libraries, read API docs, gather requirements. |
| `research-discipline` | Read sources in full, count carefully, and say what was checked versus believed. |
| `pact-architecture-patterns` | Component boundaries, C4 diagrams, API contracts and common anti-patterns. |
| `architecture-heuristics` | Rules of thumb for system shape: contracts, sync design, idempotency, data integrity. |
| `pact-coding-standards` | Clean code, error handling, naming, and lint and unused-import checks. |
| `pact-testing-strategies` | The test pyramid, unit to end-to-end patterns, mocking, and proving a test can fail. |
| `verification-discipline` | Check a claim before you report it: counts, absences, "tests pass", "deployed". |

### Security and safe operations

| Skill | What it is for |
|---|---|
| `pact-security-patterns` | OWASP Top 10, authentication flows, encryption and data protection. |
| `security-review-patterns` | Reviewing code for credential handling, sessions, SSRF, redaction and access rules. |
| `safe-ops` | Handling secrets and destructive actions (deleting files, rows or branches) safely. |

### Stacks and tools

| Skill | What it is for |
|---|---|
| `shell-tooling-traps` | When shell output misleads: macOS versus GNU tools, pipes, exit codes, test counts. |
| `stack-traps` | Node, TypeScript and Python tooling problems: modules, type generation, mocks, venvs. |
| `postgres-supabase` | Postgres and Supabase schema, migrations, grants, row-level security and query plans. |
| `frontend-verification` | Building and checking UI: rendering, theming, layout, focus, accessibility. |
| `llm-pipeline-patterns` | Using an LLM as a pipeline stage: structured output, caching, classifiers, evals. |
| `google-workspace` | Apps Script and clasp, and the Drive, Docs, Sheets and Gmail APIs. |
| `vendor-gotchas` | Known traps of common third-party APIs such as Slack, Jira, Telegram bots and scrapers. |

## Writing your own

Skill names use kebab-case; framework skills start with `pact-`. Keep `SKILL.md` short and put
detail in `references/`. Many skills carry `protocols/self-refine.md`, a short routine for
improving the skill at the end of a session that used it; it records each change in the
skill's `refinement-log.md`, which the routine creates on first use.
