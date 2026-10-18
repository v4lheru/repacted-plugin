# rePACTed

You describe the work. rePACTed turns Claude Code into a small engineering team that plans it, builds it, tests it and reviews it, and keeps every step on disk.

One session becomes the **lead**. It sizes your request, picks a workflow and hands the pieces to **specialists**: a preparer for research, an architect, backend, frontend, database and devops coders, a test engineer, a security reviewer and a QA engineer. You decide what gets merged; the rest runs on its own.

## What makes it different

- **Work lives in tasks, not in chat.** Every piece of work is a task with an owner, a brief and a status. Before a specialist starts, it says the brief back in its own words (a [teachback](docs/concepts/teachback.md)) and the lead checks it. When it finishes, it writes a report (a [HANDOFF](docs/concepts/handoff.md)) that the lead checks before it accepts the work. Corrections are written onto the task, so nothing gets lost in a scrollback.
- **The right amount of process.** The lead scores each request for novelty, scope, uncertainty and risk. A small fix gets one specialist (`/repacted:comPACT`), a feature gets the full cycle of prepare, architect, code and test (`/repacted:orchestrate`), and a large or risky change gets a plan first (`/repacted:plan-mode`).
- **A project plan that survives sessions.** If you allow it, the project's task log lives in your repository (`.repacted/tasks.jsonl`, with a readable `docs/progress.md`). Come back next week, run `/repacted:next`, and the lead knows what is done and what comes next.
- **It remembers.** Decisions and lessons go into a memory the lead can query, and an optional [knowledge base](docs/kb.md) turns past sessions into reviewed lessons for you and your team.
- **A safety net in every session.** [Guards](docs/concepts/guards.md) stop a leaked secret, a commit that holds a credential, and a force-push, merge or branch delete you did not approve. They work even without teams.

Also: blocked work gets triaged (`/repacted:imPACT`), finished work gets a review (`/repacted:peer-review`), progress can reach you on Telegram, [packs](#packs) add new domains (an n8n pack ships), and a [recipe](#run-compact-unattended-in-ci) runs comPACT unattended in CI up to a pull request.

rePACTed is an independent fork of [PACT-Plugin](https://github.com/Synaptic-Labs-AI/PACT-Plugin) v4.8.1 by Synaptic Labs. It is not affiliated with or endorsed by Synaptic Labs. See [NOTICE](pact-plugin/NOTICE.md).

## Install

In Claude Code, run:

```
/plugin marketplace add v4lheru/repacted-plugin
/plugin install repacted@repacted
```

From a shell, the same two steps are `claude plugin marketplace add v4lheru/repacted-plugin` and `claude plugin install repacted@repacted`. Then restart Claude Code. Do not turn on rePACTed and the original PACT plugin at the same time: both would run their hooks on every event.

**Pin a version.** `/plugin marketplace add` has no option for a tag. Clone a release tag (named `repacted--v<version>`) and add the folder instead:

```
git clone --branch repacted--v2.0.0 https://github.com/v4lheru/repacted-plugin.git ~/repacted-plugin
claude plugin marketplace add ~/repacted-plugin
claude plugin install repacted@repacted
```

A folder marketplace runs the plugin from that folder. Keep the folder: if you move or delete it, the plugin breaks, and if you check out another tag in it, the version you run changes.

## Turn on teams

Open Claude Code in your project and run:

```
/repacted:init --repo
```

Init shows a diff first and changes your settings only after you say yes. It turns on the Agent Teams flag (an experimental Claude Code setting) and the task tools flag in your user `settings.json`, and with `--repo` it makes Claude Code start as the lead in this project. Every file it changes is first copied to `<file>.bak-<time>`. Leave out `--repo` to set up only your machine.

Restart Claude Code, then ask Claude to run `repact-doctor`. Near the end you see one of these lines:

```
mode: guards-only (Agent Teams flag off)
mode: full (Agent Teams on)
```

In full mode, try it first with `/repacted:demo`: the lead fixes a failing test in a scratch copy of a toy project ([what happens](docs/examples/first-run.md)). Then give the lead your own work, for example:

```
/repacted:comPACT backend add a --verbose flag to the greet command
/repacted:orchestrate add a config file that sets the default greeting
```

The [quickstart](docs/quickstart.md) walks through all of this in about ten minutes. It also lists the few files the plugin writes on its own at the first session.

### If specialist agents will not spawn

If every specialist spawn is refused for a missing task assignment, the session has no task tools. Init sets `CLAUDE_CODE_ENABLE_TODO_TOOLS="1"`, which turns them on; start a new session after it. The [troubleshooting page](docs/troubleshooting.md#if-specialist-agents-will-not-spawn) has the check and the full story.

## Try the guards

Guards work in every session, with or without teams. Ask Claude:

```
Run cat .env
```

You do not need a real `.env` file. The `env_guard` hook refuses the command before it runs. Claude gets this line (trimmed in the middle):

```
env_guard: Blocked: this would print the contents of .env (a .env-class file) into the transcript; [...] → Pass the value to the command that needs it with `env-get FILE KEY -- <command>` (or `env-get --keys FILE` for the key names); do not print the file. [rule: env.read.operand]
```

Every refusal has this shape: what was refused and why, then `→` and what to do instead, then the rule id. Ask Claude to run `repact-explain` to see the last refusals again.

Other guards stop a credential leaving your machine, a commit that holds a secret, and a force-push, merge or branch delete until you approve it. The [guards page](docs/concepts/guards.md) lists them all.

## Requirements

- The Claude Code CLI. Version 2.1.302 is tested, locally and in CI. Other Claude Code apps are not tested.
- Python 3.9 or newer, as `python3` on your `PATH`, and git.
- macOS (tested) or Linux (expected to work). On Windows, use WSL (expected to work, not tested). Native Windows is not supported: the hooks need POSIX file locks.
- Optional: Telegram updates from agents. They are off until you run `/repacted:telegram-setup`.

## Uninstall

From a shell:

```
claude plugin uninstall repacted@repacted
claude plugin marketplace remove repacted
```

Uninstall also deletes the plugin's data folder. Add `--keep-data` to keep it. These stay behind; remove them by hand. The original PACT plugin uses the same `pact-sessions` folder and may use the same link names, so the lines that would also remove its files are commented out: if you do not use it, remove the `#` before running them; if you do, check where the links point first (`ls -l`) and keep `pact-sessions`.

```
C="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/repacted"     # lock files for CLAUDE.md writes
# find "$C/agents" -name 'pact-*.md' -type l -delete    # links to the plugin's agents
# rm "$C/protocols/pact-plugin"                          # link to its protocols
# rm -rf "$C/pact-sessions"                              # session journals and state
```

- In each repository you used, the block from `<!-- PACT_MANAGED_START` to `<!-- PACT_MANAGED_END -->` in `.claude/CLAUDE.md`. Delete those lines, or the file if nothing else is in it.
- Settings that init changed. Restore them from the `.bak-` copies, or remove by hand: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `CLAUDE_CODE_ENABLE_TODO_TOOLS` and the allow rules init added in your user `settings.json`; with `--repo`, the `"agent"` line in `.claude/settings.json`, the `.repacted/` folder and the `.repacted/tasks.jsonl merge=union` line in `.gitattributes`.

## Learn more

- [Quickstart](docs/quickstart.md): install, set up, first team, in about ten minutes.
- [Concepts](docs/concepts/index.md): the lead, teammates, tasks, guards and memory.
- [Glossary](docs/glossary.md): every word and key in one table.
- [Troubleshooting](docs/troubleshooting.md): one section for each rule id.
- [Knowledge base](docs/kb.md): distill lessons, review them and share them with your team.

## Build a knowledge base

From 2.0.0, `repact-distill` turns notes the agents already wrote, and if you allow it your lead session transcripts, into proposed lessons for a folder you own. It is off by default (`distill.enabled`). A dry run shows what it would read and cost and sends nothing. It can send through `claude -p`, the Anthropic API or the Batch API. You or the lead review the proposals one at a time or 20 at a time, and you can promote accepted lessons into a repository for your team. Read the [knowledge base guide](docs/kb.md) first: it covers privacy, cost and what to check before you distill work sessions.

## Packs

A pack is a second plugin in the same marketplace that adds skills and specialists. The first one adds n8n skills and a `pact-n8n` specialist for building workflows over the n8n REST API:

```
/plugin install repacted-n8n@repacted
```

## Run comPACT unattended in CI

A recipe and a workflow template run `/repacted:comPACT` on a labelled issue in GitHub Actions and stop at a draft pull request that a person reviews. Read the [unattended CI recipe](pact-plugin/docs/recipes/unattended-ci.md) before the first run: it covers the setup, what holds the run back and what it costs.

## Hook latency

Hooks run on every tool call. On a recent Mac (Apple silicon, Python 3.14, 30 recorded runs) a Bash call adds about 250 ms at p50: the wall time of its two hook processes, Pre plus Post, from spawn to exit. `repact-report` prints p50 and p95 for each hook from your own sessions, and CI fails if a hook slows down past its budget.

## Contributing, security, license

- [CONTRIBUTING](CONTRIBUTING.md): sign-off, and which files take pull requests here.
- [SECURITY](SECURITY.md): report a vulnerability privately.
- [Code of conduct](CODE_OF_CONDUCT.md).
- MIT License: see [LICENSE](LICENSE) and [NOTICE](pact-plugin/NOTICE.md).
