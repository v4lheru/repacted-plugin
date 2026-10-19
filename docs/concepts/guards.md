# Guards

A [guard](../glossary.md#guard) is a hook that reads a tool call before it runs. It can let the call through, refuse it, or hide a secret in its output. Guards work in every session, whether specialists run or not.

## The main guards

| Guard | What it stops |
|---|---|
| `env_guard` | Reading, printing, sending or staging a `.env`-class file (`.env*`, `*.env`, `.envrc`, `.netrc`), and dumping the environment. Use [`env-get`](../glossary.md#env-get) instead. |
| [`output_secret_scan`](../glossary.md#output-secret-scan) | Replaces a secret value in Bash output with `[REDACTED:...]`. |
| `egress_guard` | A credential leaving your machine through `curl`, `gh`, `scp`, an MCP tool, WebFetch or an Artifact. |
| `git_commit_check` | A commit whose added lines hold a secret. |
| `write_checks` | A commit message with AI attribution, a body, or more than one sentence, once you turn the rule on (see below). |
| [`merge_guard_pre`, `merge_guard_post`](../glossary.md#merge-guard) | A merge, PR close, force-push, branch delete or push to main, until you approve it. |
| `process_table_guard` | Reads such as `ps aux` that print other processes' command lines, which can hold tokens. |
| `worktree_guard` | Edits outside the active [worktree](worktrees.md). |

## The commit-message rule

The commit-message rule ships off: set `modes.commit_rule` to `"gate"` in `bin/checks.json` to refuse commits with AI attribution, a body or more than one sentence, and list any words to refuse in `commit_rule.deny_words` (an empty list checks none).

`bin/checks.json` is inside the installed plugin. For a GitHub install it is `~/.claude/plugins/cache/repacted/repacted/<version>/bin/checks.json`; for a [pinned folder install](../quickstart.md#1-install) it is `pact-plugin/bin/checks.json` in your clone. There is no user-level copy. An update replaces the file, and so does checking out another tag in the clone, so make your change again after each one.

## What a refusal looks like

A [refusal](../glossary.md#refusal) is one line:

```
<check>: <reason> → <instead> [rule: <id>]
```

It says what was refused and why, what to do instead, and the [rule id](../glossary.md#rule-id).

## Approving an act you cannot undo

The merge guard refuses, and its way through tells Claude to ask you with `AskUserQuestion`, with the exact command in the option you choose. Your approval is short-lived and covers that command only.

## Example

Claude tries `git push --force origin main`, and the merge guard refuses it. To see why, ask Claude to run [`repact inspect`](../glossary.md#repact-inspect) `--refusals last`:

```
2026-01-15T09:32:00Z  merge_guard_pre  deny  mg.force_push.explicit
  A force-push requires an approval bound to this exact command.
  instead: Ask the user to approve this exact command with AskUserQuestion, the literal command in the chosen option, then run it unchanged.
  code 4af758b6ddc2  target d20c97f7d082  frame lead  event PreToolUse  tool Bash
  doc docs/troubleshooting.md#mg-force-push-explicit
```

The `doc` line points to the [troubleshooting section](../troubleshooting.md#mg-force-push-explicit) for that rule. The journal keeps hashes, never the command you typed.

## Checking your setup

The [doctor](../glossary.md#doctor), `repact-doctor`, prints one line per check, then the [execution mode](../glossary.md#execution-mode) (`execution: subagents (default)`, or `execution: teams` when you turned Agent Teams on) and the tested-host line. The guards work the same in either mode.

## Limits

- Guards read words, not intent. A command they cannot parse is refused or scanned as widely as possible.
- A script built at run time, such as `eval "$(...)"`, is not followed.
- The output scan covers Bash output only.

Never edit a settings file to get past a refusal. Follow the `→` part, or ask the user.
