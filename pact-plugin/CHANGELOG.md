# Changelog

Release notes for rePACTed, newest first. Entries start at the first public release.

`Unreleased` lists what has changed on the main line since the last release and becomes the next version's notes. Each entry says what changed in behaviour and, where it matters, the files involved. An entry marked **Fork action** needs a step from anyone running a clone with local changes (a setting, a migration, a file that moved).

## 2.2.0 (2026-11-09)

Unattended mode denies permission prompts instead of waiting on them, warns before your sign-in runs out, and the red-first check reads `node --test` and `cargo test`. Forks with their own `hooks/hooks.json` must merge in the new hook. Known limits in the release notes.

- While unattended mode is on, a tool call that would wait on a permission prompt is denied at once, so the agent takes another route instead of stalling until someone comes back (`hooks/unattended_prompt_gate.py`). Turning unattended mode on applies it without a restart; with the mode off nothing changes. Claude Code's own `permissions.denyInsteadOfPrompting` setting is not used, because it also takes away the agent's questions to you; `repact doctor` mentions it as an option only. **Fork action:** if your clone has its own `hooks/hooks.json`, merge in the new `PermissionRequest` entry.
- Turning unattended mode on, or launching an unattended run, warns when your claude.ai sign-in ends within 48 hours.
- The hooks that run after each tool call can be cut off mid-write without leaving a broken file (Claude Code 2.1.319 gives them 10 seconds after an interrupt). This covers the merge-approval token and the session journal.
- The state reminder written before a compaction stays under Claude Code's 10,000-character limit, with the most important lines first, so the cut never drops them.
- Agents are told to re-read only the part of a file they need: since Claude Code 2.1.317 every re-read returns the whole file. Rules on message delivery and long commands note what was checked on 2.1.319, and `/clear` is now safe while background agents are still working.
- A call denied instead of prompted shows up as a tool error; agents are told not to retry it but to take another route.
- The red-first check reads `node --test` and `cargo test` runs: setup and resource flags, bare runs and globs as node reads them, cargo test filters, sub-directory paths, and bench and example targets. A red run is covered only by a green from the same runner, git root, load order and cargo narrowing.
- A `produced` path that holds spaces stays one path when a HANDOFF is submitted (`repact submit`).

## 2.1.3 (2026-11-03)

The shell guards also check Monitor commands, each specialist gets its own compaction cap, the lead no longer asks you to compact, a message you type ends unattended mode, and the red-first check reads .NET tests. Known limits in the release notes.

- The shell guards check Monitor commands as well as Bash ones. Claude Code ignores a hook's rewrite of a Monitor command, so a rewrite becomes a refusal there. Monitor output is not scanned for secrets.
- Each specialist caps its own compaction window (120,000 tokens for the secretary and auditor, 200,000 for the rest); the secretary runs at low effort and the auditor at medium.
- The test suite is far smaller: the guard decisions the old suite checked are kept as recorded tables, replayed through the real hooks.
- The lead no longer warns about its own context or asks you to compact; Claude Code compacts on its own and the work goes on.
- A message you type in the session turns unattended mode off. Teammate messages, notifications, scheduled wake-ups and the launch prompt of an unattended run do not.
- The red-first check credits a `dotnet test --filter` red, and a red run on the work with the fix reverted. A green that only sets worker or build flags still covers the red.
- Teammates are told never to leave work waiting on an approval prompt, and to spell commit and write commands literally.

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
