# rePACTed

rePACTed is a Claude Code plugin. It adds guards that stop leaked secrets and risky git acts in every session, and it lets one session lead a team of specialist agents.

It is an independent fork of [PACT-Plugin](https://github.com/Synaptic-Labs-AI/PACT-Plugin) v4.8.1 by Synaptic Labs. It is not affiliated with or endorsed by Synaptic Labs. See [NOTICE](pact-plugin/NOTICE.md).

## Install

In Claude Code, run:

```
/plugin marketplace add v4lheru/repacted-plugin
/plugin install repacted@repacted
```

Then restart Claude Code. Do not turn on rePACTed and the original PACT plugin at the same time: both would run their hooks on every event.

**Pin a version.** `/plugin marketplace add` takes a GitHub repository, a URL or a local folder. It has no option for a tag. To stay on one release, clone this repository at a release tag and add the clone's folder as the marketplace. Release tags are named `repacted--v<version>`, for example `repacted--v1.7.0`.

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

#### If specialist agents will not spawn

**Symptom.** Every attempt to start a specialist is refused, and the refusal blames a missing task assignment. Creating that task is itself impossible, because the tools that create tasks are absent from the session. The bootstrap step does not complete, so file edits and later spawns stay blocked as well. For the cause, read the vendor statement below.

**First, confirm this is what you are hitting.** In the stuck session, ask Claude:

```
Can you see all four of these tools right now: TaskCreate, TaskUpdate, TaskList,
TaskGet? If your tools load on demand, run ToolSearch with the query
select:TaskCreate,TaskUpdate,TaskList,TaskGet and tell me which of the four
are present.
```

- **Any of the four missing** — this is the problem, and the setting below fixes it.
- **All four present** — this is *not* the problem, and the setting below will not help. Something else is refusing the spawn; this check rules out one cause, not the rest.

**The setting.** Add `CLAUDE_CODE_ENABLE_TODO_TOOLS` to the `env` block of your `~/.claude/settings.json`, alongside the Agent Teams setting, then start a new session:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"
  }
}
```

The value is the string `"1"`, which is the form measured to work.

**Why the Agent Teams setting does not cover this.** The two values gate different things, and one does not replace the other. `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` enables Agent Teams. `CLAUDE_CODE_ENABLE_TODO_TOOLS` restores the task tools. A session with Agent Teams enabled and no task tools shows the symptom above. That is why the Agent Teams setting did not prevent it.

**Where this comes from.** The vendor documents the gate: "In Claude Code v2.1.233 and later, the following tools aren't available on Opus 4.8, Sonnet 5, Fable 5, Mythos 5, or later versions of those families unless you opt in: `TodoWrite`, `TaskCreate`, `TaskGet`, `TaskUpdate`, and `TaskList`." See [Task tool availability](https://code.claude.com/docs/en/tools-reference#task-tool-availability), which records other opt-in routes as well. This page gives the `env` block, because it persists across sessions.

**If you set `DISABLE_GROWTHBOOK` on earlier advice, it is retired, and it is not neutral.** With `CLAUDE_CODE_ENABLE_TODO_TOOLS` absent, that setting forces the declared default, and the declared default withholds the tools. So it reproduces the symptom above rather than only failing to help. It is not the one setting that does this. `DISABLE_TELEMETRY` reaches the same declared default by a different route. Those are two known routes, and not a full list.

**Setting `CLAUDE_CODE_ENABLE_TODO_TOOLS` is the remedy. Removal of a retired setting is hygiene.** The two together are measured to work, so a reader who sets the new value and keeps a retired setting is safe. A reader who removes a retired setting and sets nothing can stay blocked, because a second route reaches the same default.

The retired setting is blunt: it stops the remote configuration channel of Claude Code fully, each remotely-controlled option reverts to its built-in default, and Remote Control stops working. That channel is also the fastest route the vendor has to send a mitigation between releases. It is not a standing recommendation, so remove it. If you set it deliberately to close the remote-configuration channel, keep it and set `CLAUDE_CODE_ENABLE_TODO_TOOLS` as well, because what you keep is one cause of the symptom above.

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

This release has an early knowledge-base distiller, `repact-distill`, off by default (`distill.enabled`). It turns notes the agents already wrote into proposed lessons for a folder you own, and you review each one. Next: distilling from session transcripts, reviewing proposals in batches, and a guide to building your knowledge base. No dates yet.

## Contributing, security, license

- [CONTRIBUTING](CONTRIBUTING.md): sign-off, and which files take pull requests here.
- [SECURITY](SECURITY.md): report a vulnerability privately.
- [Code of conduct](CODE_OF_CONDUCT.md).
- MIT License: see [LICENSE](LICENSE) and [NOTICE](pact-plugin/NOTICE.md).
