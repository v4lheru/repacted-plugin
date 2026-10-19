---
description: Show where the work stands, or what one task changed and why it was accepted
argument-hint: "[<task id> | --feature <name> | --refusals [last|CODE] | --rule <id>]"
disable-model-invocation: true
---
# Inspect

Use it to see the open tasks, waits and the cost line, to read one task's change receipt before you trust it, or to find out why a check refused.

Run `repact inspect $ARGUMENTS` and show its lines as printed. With no argument it prints the status screen; `<task id>` prints that task's receipt; `--feature <name>` a feature's receipt; `--refusals` the last refusals; `--rule <id>` one rule's sentence and the way through. `repact inspect --help` lists the rest.
