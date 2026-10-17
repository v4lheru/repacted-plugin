# Security policy

rePACTed runs hooks on every tool call, and some of them guard secrets. A flaw in a guard can leak a secret or let a risky act through, so please report it privately.

## Report a vulnerability

1. Open the repository on GitHub and go to the **Security** tab.
2. Choose **Report a vulnerability**. This opens a private security advisory that only you and the maintainer can see.
3. Describe what you found and how to reproduce it.

Please do not open a public issue or pull request for a vulnerability.

## What to include

- The rePACTed version (the `version` in `pact-plugin/.claude-plugin/plugin.json`) and your Claude Code version (`claude --version`).
- Your operating system.
- The smallest command or input that shows the problem, and what happened.
- Never include a real secret. Use a made-up value that looks like one.

## What counts

Examples of what to report:

- A command that gets past a guard and prints, sends or commits a secret.
- A risky git act (force-push, branch delete, merge) that runs without the approval step.
- A hook that runs code or reads files it should not, based on text from a tool call.

A guard that refuses something safe is a bug, not a vulnerability. Open a normal bug issue for that.

## Supported versions

Only the latest release gets security fixes.

## After you report

The maintainer answers in the advisory, works on a fix there with you, and publishes the advisory when the fixed release is out. You are credited in the advisory unless you ask not to be.
