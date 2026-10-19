# Quickstart

In about ten minutes you install rePACTed, set it up, and hand your first piece of work to a [team](glossary.md#team): a lead that plans it and specialists that build, test and review it. On the way you see a [guard](glossary.md#guard), the safety net that runs in every session, refuse a risky command.

You need the Claude Code CLI and Python 3.9 or newer. macOS is tested. Linux and WSL are expected to work.

## 1. Install

In Claude Code, run these two commands:

```
/plugin marketplace add v4lheru/repacted-plugin
/plugin install repacted@repacted
```

Or from a shell:

```
claude plugin marketplace add v4lheru/repacted-plugin
claude plugin install repacted@repacted
```

Do not turn on rePACTed and the original PACT plugin at the same time. Both would run their hooks on every event. Restart Claude Code after the install.

**To pin a version**, clone a release tag and add the folder as the marketplace instead (if you already added the GitHub marketplace, first run `claude plugin marketplace remove repacted`):

```
git clone --branch repacted--v2.1.1 https://github.com/v4lheru/repacted-plugin.git ~/repacted-plugin
claude plugin marketplace add ~/repacted-plugin
claude plugin install repacted@repacted
```

A folder marketplace runs the plugin from that folder. Keep the folder: if you move or delete it, the plugin breaks, and if you check out another tag in it, the version you run changes.

**What the plugin writes on its own.** At the first session after the install, before you run init, the plugin writes:

- a managed block in your repository's `.claude/CLAUDE.md`, between `<!-- PACT_MANAGED_START` and `<!-- PACT_MANAGED_END -->`;
- links to its agents in `~/.claude/agents/` (`pact-*.md`);
- a link `~/.claude/protocols/pact-plugin` to its protocols;
- session journals and state under `~/.claude/pact-sessions/`;
- lock files for `CLAUDE.md` writes under `${XDG_STATE_HOME:-~/.local/state}/repacted/locks/`.

The [README](../README.md#uninstall) says how to remove them.

## 2. See a guard refuse something

Guards work in every session, with or without teams. Ask Claude:

```
Run cat .env
```

You do not need a real `.env` file. The `env_guard` hook refuses the command before it runs. Claude gets this line (trimmed in the middle):

```
env_guard: Blocked: this would print the contents of .env (a .env-class file) into the transcript; [...] → Pass the value to the command that needs it with `env-get FILE KEY -- <command>` (or `env-get --keys FILE` for the key names); do not print the file. [rule: env.read.operand]
```

Every [refusal](glossary.md#refusal) has this shape: what was refused and why, then `→` and what to do instead, then the [rule id](glossary.md#rule-id). To see the last refusals again, ask Claude to run `repact inspect --refusals`. Each rule id has a section on the [troubleshooting page](troubleshooting.md#env-read-operand).

## 3. Set up with init

Open Claude Code in your project and run:

```
/repacted:init --repo
```

[Init](glossary.md#init) first shows you a diff and changes nothing. It changes your settings only after you say yes.

- For your machine, it adds `CLAUDE_CODE_ENABLE_TODO_TOOLS` (current models get no task tools without it) and allow rules for the plugin's own tools to your user `settings.json`. Specialists run as plain subagents, so no other setting is needed. To run them as Agent Teams instead, add `--teams`: it also writes the [Agent Teams flag](glossary.md#agent-teams-flag) and `REPACT_EXECUTION=teams`.
- With `--repo`, it also adds `"agent": "repacted:pact-orchestrator"` to `.claude/settings.json`, creates `.repacted/`, and adds `.repacted/tasks.jsonl merge=union` to `.gitattributes`.

Init never removes or overwrites a value you already have. It prints `# kept yours:` for each one. Leave out `--repo` to set up only your machine.

## 4. Start the lead

Changed settings work only in a new session. Quit Claude Code and start it again. Then ask Claude to run `repact-doctor`. It prints one line per check, then the [execution mode](glossary.md#execution-mode):

```
execution: subagents (default)
execution: teams (REPACT_EXECUTION=teams)
```

Either is fine. If you ran init with `--repo`, Claude Code already starts as the [lead](glossary.md#lead) in this project. In any other folder, start it like this:

```
claude --agent repacted:pact-orchestrator
```

At the first session the plugin also puts a `repact` command in `~/.local/bin`. From a terminal, `repact work "<ask>"` starts a session on an ask, `repact work` takes the next row of the project's task log, and `repact resume` picks up the last session.

**Try it first: `/repacted:demo`.** The lead copies a toy project to a scratch folder, sends one specialist to fix its failing test, and prints the session report. It writes nothing to your repository. [Your first run](examples/first-run.md) shows each step.

Then give the lead work in your own project. For example, in a small command-line tool:

```
/repacted:comPACT backend add a --verbose flag to the greet command
/repacted:orchestrate add a config file that sets the default greeting
```

- [`comPACT`](glossary.md#compact) sends one or more specialists on a small task.
- [`orchestrate`](glossary.md#orchestrate) runs the full cycle in [phases](glossary.md#phase): prepare, architect, code, test.

Or just type `/repacted:work <ask>` and let the lead pick between them.

Each teammate first says back its brief (a [teachback](glossary.md#teachback)). On a small task that is accepted automatically; on a larger one it waits for the lead. At the end the teammate hands in a report (a [HANDOFF](glossary.md#handoff)), and the lead's `repact accept` takes the work only on a recorded passing test run of the files as they are now.

While work runs, `/repacted:inspect` shows where it stands, and `/repacted:decide` lists every question that waits on you.

## 5. Where to go next

- [Concepts](concepts/index.md): how the lead, teammates, tasks and guards fit together.
- [Glossary](glossary.md): every word and key in one table.
- [Troubleshooting](troubleshooting.md): one section for each rule id.
- [Command reference](reference/commands.md): every command and its options.
