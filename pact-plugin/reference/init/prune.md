# Init: prune a pin from CLAUDE.md

> Loaded by `/repacted:init prune` ([init.md](../../commands/init.md)). Run the steps as written.

Demote ONE pin from `## Pinned Context` to long-term memory: **save it and verify the save before removing anything** (when the pin cap of 12 is reached, [pin.md](pin.md) step 2).

### Step 1 — List the pins

Read the project CLAUDE.md (`.claude/CLAUDE.md`, else `./CLAUDE.md`). Each `### ` heading under `## Pinned Context` is a pin, numbered from 1, with its `<!-- pinned: YYYY-MM-DD -->` date. No pins: "No pins to prune.", stop.

### Step 2 — Ask the curator which pin to prune

Up to 3 candidate pins per call: ≤ 3 pins shows all + "Cancel"; more shows 3 pins + "Show more" per page, the last page ending in "Cancel" (the 4-option cap means pages, never wider). Oldest first.

```
AskUserQuestion(questions=[{header: "Pin prune",
  question: "Which pin to demote to long-term memory? (page N of M)",
  options: [{label: "Pin {index} — {heading}", description: "{chars} chars, pinned {date}"}, ...]}])
```
 Cancel: "Prune cancelled; CLAUDE.md unchanged.", stop.

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
The `pact-memory` skill carries the full rule.

### Step 3 — Save the selected pin

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/pact-memory/scripts/cli.py" save '{"context": "<pin body>", "goal": "Pinned context demoted from CLAUDE.md: <heading>"}'
```

Then `cli.py get <memory_id>` and check it returns the pin body. **Only a verified save permits the removal**; on any failure keep the pin and report.

### Step 4 — Remove the selected pin

`Edit` the pin out: its date comment, its `### ` heading and its body, nothing else. Never `Write` the file.

### Step 5 — Report

"Pruned pin {index}: {heading} — saved as {memory_id}." Commit, then **confirm `CLAUDE.md` is in the commit** (`git commit -a` silently skips an ignored file); if not, say it is unversioned. NEVER `git add -f`. This command NEVER evicts more than one pin per invocation.
