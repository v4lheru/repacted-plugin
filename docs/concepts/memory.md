# Memory

rePACTed keeps what is worth keeping in a few places. Each one has one job.

| Where | What it holds | Who writes it | What loads by itself |
|---|---|---|---|
| [pact-memory](../glossary.md#pact-memory), `~/.claude/pact-memory/memory.db` | Decisions, lessons and named parts of your system, searchable | agents, through the `repacted:pact-memory` skill | The newest few, as Working Memory in `CLAUDE.md` |
| [Agent memory](../glossary.md#agent-memory), `~/.claude/agent-memory/<agent>/` | Craft notes for one agent type, such as a framework habit | each agent | Only the top of `MEMORY.md` |
| The [managed block](../glossary.md#managed-block) in your project `CLAUDE.md` | Session pointers, [pins](../glossary.md#pin) and Working Memory | rePACTed and the pin commands | All of it, every session |
| Your [knowledge base](../glossary.md#kb), optional | Notes you own and read | you, and agents through a gate (`kb-note`) | Nothing; it is off until you set `paths.kb`. See the [knowledge base guide](../kb.md) |

## What goes where

When a teammate learns something, it asks one question: would a different agent type need this?

- Yes, it is about the project (a design choice, a reason): it goes in the [HANDOFF](handoff.md). The [secretary](../glossary.md#secretary) later saves it in pact-memory.
- No, it is about the craft (a file layout, a tool habit): it goes in the teammate's own agent memory.

## The inbox

An agent never adds a new lesson to the top of its `MEMORY.md`. That top part loads into every start, so it stays small. Instead it adds one line to its [inbox](../glossary.md#inbox), `MEMORY-inbox.md`:

```
2026-01-15 | greet tests need NO_COLOR=1 or the snapshot fails | tests/test_greet.py
```

The shape is `date | lesson | where the proof is`. At wrap-up, the lead runs the [distill pass](../glossary.md#distill-pass) (`distill-inbox`): it moves each inbox line into a skill, a doc or your knowledge base.

## pact-memory

The store is a SQLite file. Agents save and search only through the skill's CLI. A save takes fields such as `context`, `goal`, `decisions` and `lessons_learned`.

`sync` rebuilds the Working Memory part of `CLAUDE.md` from the project's newest three records. To change it, update or delete the record and run `sync` again.

## Pins

A [pin](../glossary.md#pin) is context you want in every session, such as "the CLI must stay on Python 3.9". Add one with `/repacted:init pin`. Remove pins with `/repacted:init prune`, which shows them page by page.

## Example

You finish a feature and run `/repacted:wrap-up`. The secretary saves the HANDOFF decisions in pact-memory, and the lead runs the distill pass over the inboxes. Next week, in a new session, the lead runs a pact-memory search for "config file" and finds why you chose a TOML file. You do not have to explain it again.
