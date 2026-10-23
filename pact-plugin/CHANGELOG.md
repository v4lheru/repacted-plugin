# Changelog

Release notes for rePACTed, newest first. Entries start at the first public release.

## 2.1.2 (2026-10-23)

Fixes from an outside review of 2.1.1, and the upstream PACT 4.8.2 to 4.8.6 fixes that apply. Known limits in the release notes.

- A merge, force-push, branch delete or push to main now needs your answer to the approval question for that exact command, given in the main session in the last 15 minutes. Only an option you picked counts.
- The checks read more of a command before they judge it: a command whose program or script is built while it runs is refused, a git alias is expanded, and a variable set to one plain value is read as that value. Common shell setup lines such as `eval "$(ssh-agent -s)"` and `<tool> completion <shell>` stay allowed.
- Specialists can no longer write the task log, the approval records or the session transcripts, including through folder copies and archives.
- A spawn is refused, not allowed, when the dispatch check times out. Deleting a task no longer stops all spawning, and the stop message is in plain words.
- `repact-task fsck` fails and `repact-task show` says `unverified` when a task's recorded evidence no longer checks out.
- CLAUDE.md markers inside a code fence are no longer mistaken for the managed block (upstream PACT 4.8.3).

Two fixes for checks that failed in the 2.1.0 test matrix. Known limit in the release notes.

- `repact-doctor` no longer reports the `repact` command as a warning on a fresh install before your first session; it shows it as skipped until a session puts the command in place.
- The knowledge-base transcript reader now refuses a line with an integer over 4,300 digits or a number too large for a float on every Python version, not only where Python itself caps integer digits.

## 2.1.0 (2026-10-19)

A simpler team that is stricter about what counts as done. Upgrade steps and known limits are in the release notes.

- Four commands for you: `repact work`, `repact inspect`, `repact decide` and `repact resume`, with matching slash commands.
- Specialists run as background subagents by default; Agent Teams is optional.
- Each workflow step is one `repact` command (`dispatch`, `submit`, `accept`, `reject`, `amend`) that is safe to run again.
- Work is accepted only on a recorded passing test run of the current files, with a failing run first for fixes and new behaviour, and with the acceptance criteria's exact commands.
- `repact inspect <task id>` prints a change receipt; `repact accept --feature` accepts a feature on its merged tree.
- Autopilot works through the task log on its own; an unattended waiver parks the task for you instead of closing it.
- Task files, session state and Claude Code's settings are protected from direct writes by agents.
- Renamed and removed commands and tools are listed in the release notes.

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
