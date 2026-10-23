# rePACTed plugin

This folder is the plugin that `/plugin install repacted@repacted` installs (version 2.1.2). Setup and a first run are in the repository [README](https://github.com/v4lheru/repacted-plugin#readme) and the [quickstart](https://github.com/v4lheru/repacted-plugin/blob/main/docs/quickstart.md).

## What is here

| Folder | What it holds |
|---|---|
| `hooks/` | The guards and the other hooks. `hooks/hooks.json` registers them. |
| `agents/` | The lead and the specialist agents. |
| `commands/` | The `/repacted:...` commands, such as `init`, `comPACT` and `orchestrate`. |
| `skills/` | Skills that agents load for a kind of work. |
| `bin/` | Command-line tools such as `repact` (with `repact inspect` and `repact decide`), `repact-doctor` and `env-get`. |
| `protocols/`, `reference/`, `templates/` | Shared rules and reference files that agents read. |
| `telegram/` | The optional Telegram bridge. It is off until you run `/repacted:init telegram`. |

## Check your install

Ask Claude to run `repact-doctor`. It prints one line per check, including `execution: subagents (default)`, or `execution: teams` when you turned Agent Teams on. If teammates will not spawn, see [If specialist agents will not spawn](https://github.com/v4lheru/repacted-plugin#if-specialist-agents-will-not-spawn).

## License

MIT. rePACTed is an independent fork of PACT-Plugin v4.8.1 by Synaptic Labs, not affiliated with Synaptic Labs. See [LICENSE](LICENSE) and [NOTICE](NOTICE.md).
