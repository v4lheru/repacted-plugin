# Concepts

rePACTed adds two things to Claude Code.

1. **Guards.** Hooks check each tool call before it runs. They stop secrets from leaking and stop acts you cannot undo, such as a force-push, until you approve. Guards work in every session.
2. **A team.** Your main session becomes the [lead](../glossary.md#lead). It splits work into [tasks](../glossary.md#task) and hands each task to a [teammate](../glossary.md#teammate). Teammates report back in a fixed shape, and a report is accepted only on a recorded passing test run of the files as they are now.

Read the pages in this order. Each one is short and has one small example.

| Page | What you learn |
|---|---|
| [Lead and teammates](lead-and-teammates.md) | Who does what, and how a session starts |
| [Teachback](teachback.md) | How a teammate proves it understood the brief before it starts, and when that is accepted automatically |
| [HANDOFF](handoff.md) | How a teammate reports its work, and what `repact accept` checks before the work counts as done |
| [Task files and waits](task-files-and-waits.md) | Where tasks live, which command writes what, and how waiting works |
| [Worktrees](worktrees.md) | How each feature gets its own checkout and branch |
| [Guards](guards.md) | What the hooks refuse, and how to get past a refusal the right way |
| [Memory](memory.md) | What the team remembers between sessions, and where |
| [Knowledge base guide](../kb.md) | How to distill lessons from your sessions, review them and share them |

A typical round, in one picture:

```
you ──> lead ──> repact dispatch ──> work task + brief ──> teammate starts
                                                             │
                     repact submit --teachback ──> says back the brief
                         │ accepted automatically, or by the lead (repact accept)
                         v
                 teammate works, runs tcount ──> repact submit (HANDOFF)
                                                             │
                                                             v
                     repact accept: a passing run on the current files? ──> task completed
                                    otherwise the rejection goes back to the teammate
```

New words are in the [glossary](../glossary.md). Start with the [quickstart](../quickstart.md) if you have not installed rePACTed yet.
