# Quickstart

In about ten minutes you install rePACTed, set it up, watch a [guard](glossary.md#guard) refuse a risky command, and start your first [team](glossary.md#team).

You need the Claude Code CLI and Python 3.9 or newer. macOS is tested. Linux and WSL are expected to work.

## 1. Install

In Claude Code, run these two commands:

```
/plugin marketplace add v4lheru/repacted
/plugin install repacted@repacted
```

Do not turn on rePACTed and the original PACT plugin at the same time. Both would run their hooks on every event. Restart Claude Code after the install.

## 2. Set up with init

Open Claude Code in your project and run:

```
/repacted:init --repo
```

[Init](glossary.md#init) first shows you a diff and writes nothing. It writes only after you say yes.

- For your machine, it adds the [Agent Teams flag](glossary.md#agent-teams-flag) and allow rules for the plugin's own tools to your user `settings.json`.
- With `--repo`, it also adds `"agent": "repacted:pact-orchestrator"` to `.claude/settings.json`, creates `.repacted/`, and adds `.repacted/tasks.jsonl merge=union` to `.gitattributes`.

Init never removes or overwrites a value you already have. It prints `# kept yours:` for each one. Leave out `--repo` to set up only your machine.

## 3. See a guard refuse something

Guards work in every session, with or without teams. Ask Claude:

```
Run cat .env
```

You do not need a real `.env` file. The `env_guard` hook refuses the command before it runs. Claude gets this line (trimmed in the middle):

```
env_guard: Blocked: this would print the contents of .env (a .env-class file) into the transcript; [...] → Pass the value to the command that needs it with `env-get FILE KEY -- <command>` (or `env-get --keys FILE` for the key names); do not print the file. [rule: env.read.operand]
```

Every [refusal](glossary.md#refusal) has this shape: what was refused and why, then `→` and what to do instead, then the [rule id](glossary.md#rule-id). To see the last refusals again, ask Claude to run `repact-explain`. Each rule id has a section on the [troubleshooting page](troubleshooting.md#env-read-operand).

## 4. Turn on teams

Init turned on the Agent Teams flag, but changed settings work only in a new session. Quit Claude Code and start it again. Then ask Claude to run `repact-doctor`. Near the end, you see one of these lines:

```
mode: guards-only (Agent Teams flag off)
mode: full (Agent Teams on)
```

[Full mode](glossary.md#full-mode) means the [lead](glossary.md#lead) can start [teammates](glossary.md#teammate). If you ran init with `--repo`, Claude Code already starts as the lead in this project. In any other folder, start it like this:

```
claude --agent repacted:pact-orchestrator
```

On your first prompt, the lead runs [`/repacted:bootstrap`](glossary.md#bootstrap). Then give it work. For example, in a small command-line tool:

```
/repacted:comPACT backend add a --verbose flag to the greet command
/repacted:orchestrate add a config file that sets the default greeting
```

- [`comPACT`](glossary.md#compact) sends one or more specialists on a small task.
- [`orchestrate`](glossary.md#orchestrate) runs the full cycle in [phases](glossary.md#phase): prepare, architect, code, test.

Each teammate first says back its brief (a [teachback](glossary.md#teachback)). It works only after the lead accepts. At the end it writes a report (a [HANDOFF](glossary.md#handoff)), and the lead checks it.

## 5. Where to go next

- [Concepts](concepts/index.md): how the lead, teammates, tasks and guards fit together.
- [Glossary](glossary.md): every word and key in one table.
- [Troubleshooting](troubleshooting.md): one section for each rule id.
- [Command reference](reference/commands.md): every command and its options.
