---
name: pact-memory
description: |
  PACT institutional memory (SQLite): save and search decisions, lessons and entities across sessions.
  Triggers: memory, save memory, search memory, lessons learned, remember, recall
---

# PACT Memory Skill

Structured, searchable memory shared by every PACT agent and session. The long form of every section below (the inspection procedure, dependencies, storage, offline saves, full examples, the memory layers): `references/memory-long-form.md`; usage patterns: `references/memory-patterns.md`.

## Store access: the two routes

<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->

The rule is a policy, not a claim about the tool: never run `setup --db-path`, and never `python3 setup_memory.py init`; report and ask instead. To INSPECT: check `memory.db-wal` and `memory.db-shm` by full name; if either is present stop and report (never delete a sidecar); else open `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)`. Never `cp` the store (use `VACUUM INTO`).

## Commands

All commands print `{"ok": true, "result": ...}` to stdout, or an error envelope to stderr. Use `--stdin` for large payloads.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" save '{"context": "...", "goal": "...", "lessons_learned": ["..."], "entities": [{"name": "AuthService", "type": "component"}]}'
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "rate limiting tokens" --limit 5
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" get <memory_id_or_prefix>
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" get <memory_id_or_prefix> --brief  # id, date, first line of context and goal
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" get <memory_id_or_prefix> --fields goal,decisions  # only those fields
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update <memory_id_or_prefix> '{"lessons_learned": ["new lesson"]}'
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" sync
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" status
```

| Command | Description | Output |
|---------|-------------|--------|
| `save <json>` | Save a memory object; `--no-sync` leaves CLAUDE.md's Working Memory untouched | `{"memory_id": "<hex>", "sync_status": "..."}` |
| `save --stdin` | Save from piped JSON | `{"memory_id": "<hex>", "sync_status": "..."}` |
| `search <query>` | Semantic search | `[{"id": "...", "context": "...", ...}, ...]` |
| `search <query> --limit N` | Search with limit | `[...]` (default: 5) |
| `search <query> --current-file <path>` | Search with graph boosting | `[...]` (boosts file-related memories) |
| `list` | List recent memories | `[{"id": "...", "context": "...", ...}, ...]` |
| `list --limit N` | List with limit | `[...]` (default: 20) |
| `get <id\|prefix>` | Get memory by full ID or unique prefix (>= 7 chars, case-insensitive). Ambiguous prefix returns `AMBIGUOUS_PREFIX` with `matches: [...]`, `matches_capped`, `total_matches`; too-short returns `PREFIX_TOO_SHORT` | `{"id": "...", "context": "...", ...}` |
| `update <id\|prefix> <json>` | Update memory fields (list fields merge additively). Same prefix-resolution rules as `get`; ambiguous prefix is refused | `{"memory_id": "<hex>"}` |
| `update <id\|prefix> --stdin` | Update from piped JSON | `{"memory_id": "<hex>"}` |
| `update <id\|prefix> <json> --replace` | Replace list fields wholesale instead of merging | `{"memory_id": "<hex>"}` |
| `delete <id\|prefix>` | Delete a memory. Same prefix-resolution rules as `get`; ambiguous prefix is refused | `{"deleted": true, "memory_id": "<hex>"}` |
| `sync` | Rebuild CLAUDE.md's Working Memory from this project's newest 3 memories (replaces the section; `--claude-md-root` bounds the write) | `{"sync_status": "wrote", "projected": 3, "memory_ids": [...], "project_id": "..."}` |
| `status` | System status | `{"memory_count": N, "db_path": "...", ...}` |
| `setup` | Initialize system | `{"status": "ready", "message": "..."}` |

Exit codes: 0 success; 1 user error; 2 validation error (an unknown field or sub-object key; the envelope lists `allowed_fields`). A save verifies persistence by reading back; exit 2 with `SYSTEM_ERROR` means it did not persist.

## Memory Structure

A payload key outside the **On save** set is REJECTED with exit 2 and nothing is written.

| Field | Type | On save | Description |
|-------|------|---------|-------------|
| `context` | string | you supply | Current working context description |
| `goal` | string | you supply | What you're trying to achieve |
| `active_tasks` | list | you supply | Tasks with status and priority |
| `lessons_learned` | list | you supply | What worked or didn't work |
| `decisions` | list | you supply | Decisions with rationale and alternatives |
| `entities` | list | you supply | Referenced components, services, modules |
| `reasoning_chains` | list | you supply | How key decisions connect — "X because Y, which required Z" |
| `agreements_reached` | list | you supply | What was verified via teachback or agreement check |
| `disagreements_resolved` | list | you supply | Where agents disagreed and how it was settled |
| `project_id` | string | optional | Accepted in the payload; auto-detected from the environment when you omit it |
| `session_id` | string | optional | Accepted in the payload; auto-detected from the environment when you omit it |
| `files` | list | **never** | Read-only. Returned by `get`/`search`/`list`; REJECTED as a payload key — see below |

`files` is read-only: the CLI cannot set it; name files in an entity's `notes` instead. Formats: task `{"task", "status", "priority"}`; decision `{"decision", "rationale", "alternatives"}`; entity `{"name", "type", "notes"}`.

**Update semantics.** Scalars replace; list fields merge with content-hash dedup, so a repeat is idempotent, and an entity, task or decision whose name matches an existing one REPLACES it. `--replace` overwrites whole lists (only for a superseded conclusion); `--sync` also rebuilds the Working Memory block.

**What a save holds.** The argument is a hint: `context` as 3-5 sentences, `goal` as the objective, 3-5 `lessons_learned` that explain the why, `decisions` with rationale and alternatives, `entities` for graph retrieval. Auto-memory and agent memory load only their index head: see the index-upkeep rule in `teammate-memory`.

### Sync Command

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" sync
```

The Working Memory section of CLAUDE.md is a derived view of the store. `sync` replaces it with this project's newest 3 memories, each under its own date; to change what the section shows, `update` or `delete` the record, then run `sync` again. A `sync_status` of `empty` means the project has no memories and the file was not touched; check the envelope's `project_id` against the project you expected. The section is budgeted, so it can show fewer than 3; the full history stays searchable.

## Search and setup

Semantic search needs `pysqlite3`, `sqlite-vec` and `model2vec` (`setup` installs them); without them search falls back to keywords (`status` says which). `--current-file <path>` boosts memories linked to that file. A first save fetches the embedding model once; `HF_HUB_OFFLINE=1`, set before the process starts, guarantees no network.
