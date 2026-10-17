# rePACTed

rePACTed is a Claude Code plugin. It adds guards that stop leaked secrets and risky git acts in every session, and it lets one session lead a team of specialist agents.

It is an independent fork of [PACT-Plugin](https://github.com/Synaptic-Labs-AI/PACT-Plugin) v4.8.1 by Synaptic Labs. It is not affiliated with or endorsed by Synaptic Labs. See [NOTICE](pact-plugin/NOTICE.md).

## Install

In Claude Code, run:

```
/plugin marketplace add v4lheru/repacted
/plugin install repacted@repacted
```

Then restart Claude Code. Do not turn on rePACTed and the original PACT plugin at the same time: both would run their hooks on every event.

**Pin a version.** `/plugin marketplace add` takes a GitHub repository, a URL or a local folder. It has no option for a tag. To stay on one release, clone this repository at a release tag and add the clone's folder as the marketplace. Release tags will be named `repacted--v<version>`. No release tag exists yet.

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

## Turn on teams

Open Claude Code in your project and run:

```
/repacted:init --repo
```

Init shows a diff first and writes nothing until you say yes. It turns on the Agent Teams flag (an experimental Claude Code setting) in your user `settings.json`, and with `--repo` it makes Claude Code start as the lead in this project. Every file it changes is first copied to `<file>.bak-<time>`. Leave out `--repo` to set up only your machine.

Restart Claude Code, then ask Claude to run `repact-doctor`. Near the end you see one of these lines:

```
mode: guards-only (Agent Teams flag off)
mode: full (Agent Teams on)
```

In full mode, give the lead work:

```
/repacted:comPACT backend add a --verbose flag to the greet command
/repacted:orchestrate add a config file that sets the default greeting
```

The [quickstart](docs/quickstart.md) walks through all of this in about ten minutes.

## Requirements

- The Claude Code CLI. Version 2.1.302 is tested. Other Claude Code apps are not tested.
- Python 3.9 or newer, as `python3` on your `PATH`, and git.
- macOS (tested) or Linux (expected to work). On Windows, use WSL (expected to work, not tested). Native Windows is not supported: the hooks need POSIX file locks.
- Optional: Telegram updates from agents. They are off until you run `/repacted:telegram-setup`.

## Uninstall

From a shell:

```
claude plugin uninstall repacted@repacted
claude plugin marketplace remove repacted
```

Uninstall also deletes the plugin's data folder. Add `--keep-data` to keep it. Settings that init changed stay. Restore them from the `.bak-` copies, or remove them by hand: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` in your user `settings.json`, and the `"agent"` line in the project's `.claude/settings.json`.

## Learn more

- [Quickstart](docs/quickstart.md): install, set up, first team, in about ten minutes.
- [Concepts](docs/concepts/index.md): the lead, teammates, tasks, guards and memory.
- [Glossary](docs/glossary.md): every word and key in one table.
- [Troubleshooting](docs/troubleshooting.md): one section for each rule id.

## Coming next

Knowledge-base distillation: turn the lessons that agents write during your sessions into notes in a folder you own. It is not in this release and has no date yet.

## Contributing, security, license

- [CONTRIBUTING](CONTRIBUTING.md): sign-off, and which files take pull requests here.
- [SECURITY](SECURITY.md): report a vulnerability privately.
- [Code of conduct](CODE_OF_CONDUCT.md).
- MIT License: see [LICENSE](LICENSE) and [NOTICE](pact-plugin/NOTICE.md).
