# Changelog

Release notes for rePACTed, newest first. Entries start at the first public release.

## 2.0.0 (2026-10-18)

Knowledge base, team features, skill packs and unattended comPACT in CI. Known limits are in the release notes.

- Knowledge base (off by default): `repact-distill` proposes lessons from agent notes, with a dry run that shows what it would read and cost, through `claude -p`, the Anthropic API or the Batch API. Review one at a time or 20 at a time; `repact-distill promote` copies accepted lessons into a repository's `.repacted/kb/`. A session can queue its notes for distilling when it ends.
- Team policy: a committed `.repacted/policy.json` can only make the rules stricter.
- Your own specialists: `repact-agent new <role>` scaffolds a project agent the lead can spawn.
- Cost preview and cap: `/repacted:orchestrate` and `/repacted:comPACT` print a cost estimate before they dispatch, and `session.cost_cap_usd` refuses a spawn past the cap.
- Team lessons: `repact-distill team stage` and `team push` share reviewed lessons through a repository the team owns.
- Unattended comPACT in CI: a recipe and a workflow template that stop at a pull request.
- `repact-report --since` counts work across sessions, with a JSON export.
- Skill packs: an installed plugin can add recall fragments. The first pack, `repacted-n8n`, installs with `/plugin install repacted-n8n@repacted`.
- Hook latency: hooks record their run time, `repact-report` prints p50 and p95 per hook, and CI fails past a budget.

## 1.7.2 (2026-10-17)

- Tests: the task-log replay timing test no longer fails on Python 3.14 when a full garbage collection lands inside its timed window. No change to the plugin's behaviour.

## 1.7.1 (2026-10-17)

First-run fixes from a clean-install walkthrough.

- The `CLAUDE.md` lock file lives in `${XDG_STATE_HOME:-~/.local/state}/repacted/locks/`, not in the project, so `git add -A` no longer picks it up.
- `/repacted:init` adds `CLAUDE_CODE_ENABLE_TODO_TOOLS="1"` next to the Agent Teams flag, and its last line says what the doctor's exit code means.
- `repact-doctor` leaves out the rows that only apply to the plugin's own source repository when they do not apply.
- Python 3.9 works as the requirements say: scratch cleanup, the task-file reader and two time and number checks used behaviour that only Python 3.11 and newer have.

## 1.7.0 (2026-10-17)

First public release.
