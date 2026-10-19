---
name: pact-handoff-harvest
description: |
  Secretary HANDOFF harvest, script-first: Standard, Incremental, Consolidation, orphans.
  Triggers: harvest HANDOFFs, process HANDOFFs, incremental, consolidation, handoff recovery.
---

# PACT Handoff Harvest

`scripts/harvest.py` does the mechanical work (discovery, the ledger, the Step 3 selection and union, coverage, orphans, the D6 archive); you judge, dedupe and save. Every rule in long form, with its reasons: `references/harvest-long-form.md`. Read the matching section there when a step below surprises you.

Variants: **Standard** (orchestrate, comPACT, peer-review, plan-mode), **Incremental** (after remediation: the delta), **Consolidation** (wrap-up, pause, a second feature). The subject/description selects the workflow, never what is in scope.

**DISCARD ANY SCOPE YOU ARRIVED WITH.** A dispatch that names tasks, phases, dates or paths gave you a hint, never the population: the script builds it from the journal, the task-file census and the ledger. Keep ADDRESSES (team id, task-list directory, session directory); discard SCOPES. Say which set you ran over.

**Propagation.** Every `save` carries `--no-sync`. Run the pact-memory `sync` command once, after your last `save`/`update`/`delete`, only when the dispatch or a later team-lead request carries `Then propagate the store into the Working Memory block.`; otherwise report `Working Memory: not propagated`. The calibration record is outside that set.

---

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

## Standard Harvest Workflow

### Step 0: Resolve the Session Directory (do this once)

You run off-lead, where a path-less journal read returns nothing. Resolve once and pass the value to every verb; on a nonzero exit report the gap and STOP:

```bash
if ! SESSION_DIR=$(python3 "{plugin_root}/hooks/shared/pact_harvest.py" \
       resolve-session-dir --context-file "{context_file}"); then
  echo "HARVEST GAP: could not resolve session_dir; reporting and stopping." >&2
fi
```

### Step 1: Candidates

`python3 "{plugin_root}/scripts/harvest.py" candidates --session-dir "$SESSION_DIR" --team {team_id}` prints JSON: `population` (unprocessed tasks: journal `agent_handoff` events, every completed task carrying metadata and any task carrying a HANDOFF, minus the ledger; a unit whose `status` is not `completed` is reported as not yet accepted: hold it), `units` (one per (agent, subject) identity, the HANDOFF already merged: latest emit, task file preferred except a present-and-empty field), `coverage` (which carrier held each HANDOFF; `flag` = none did), and `reports` (drained file, identity mismatch, key-case mismatch, no HANDOFF found: relay each). Empty population: report "No pending HANDOFFs to review" and complete. A completed task is not a closed one: to collect content written after an earlier pass, add `--include <ids>`.

### Step 2: Sibling keys and artifacts

For keys a HANDOFF references, read the task file, else the `task_metadata_snapshot` events (`snapshot_keys` names them); an amendment supersedes what it addresses. Read each feature's `artifact_paths` through `pact_harvest.py resolve-artifacts`, one feature at a time, while the worktree is live, and synthesize ONE entry per work unit from the HANDOFF and its artifact.

### Steps 3-6: Extract, snapshot state, dedupe, save

Read ALL the HANDOFFs before saving any, so you dedupe and consolidate across them. Extract, and capture where the project stands (phases, blockers, scope changes):

| Save | Skip |
|---|---|
| Architectural decisions with rationale | File locations (agent memory holds them) |
| Cross-agent integration points | Framework conventions (agent memory) |
| Stakeholder decisions and constraints | Debugging techniques (agent memory) |
| Patterns established for this project | Implementation details without broader impact |
| Risks, uncertainties and known issues | Routine changes that follow existing patterns |

Route by owner: knowledge of ONE agent's craft goes to that agent's own memory (it saves it); project knowledge other agents or sessions need goes to pact-memory (you); a broad session observation or user preference is left to auto-memory; anything else needs no save.

Before every save run `search --query "{topic}" --limit 5`: high overlap means `update` (additive; `--replace` only for a superseded conclusion), else `save` with `context`, `goal`, `decisions`, `lessons_learned`, `entities`, and `--no-sync`.

Investigate a thin HANDOFF (no rationale, HIGH uncertainty without detail, an area with recurring problems): message its agent directly with 1-2 targeted questions, never an interview; ask the team-lead only for broader context; never block completion on it; for an agent that has shut down, use the files and git history; report what you found.

### Step 7.5: Propagate (only when dispatched)

With the propagate sentence, run `sync` once now: it rebuilds the Working Memory section as the newest three records of this project, each under its own date. Record `sync_status` and `memory_ids`; on `empty`, record `project_id` too (an unexpected one is a misconfiguration to report).

### Step 8: Record

`python3 "{plugin_root}/scripts/harvest.py" record --session-dir "$SESSION_DIR" --team {team_id} --ids <every id you processed>` appends one line to `pact-sessions/<project>/processed-tasks/<team>.jsonl`. The ledger is append-only and names its session; the old agent-memory file is read as a fallback and never written.

### Step 9: Report and archive

```
SendMessage(to="team-lead",
  message="[secretary→team-lead] HANDOFF review complete. Saved N memories from M HANDOFFs.
- {memory summary 1}
Working Memory: {sync_status | empty, project {project_id}}, {N} entries projected | not propagated, saves reported {save sync_status}
Coverage: {tasks flagged with no HANDOFF carrier}. Gaps: {thin or missing HANDOFFs}",
  summary="HANDOFF review complete: N memories from M HANDOFFs")
```

Then run `python3 "{plugin_root}/scripts/harvest.py" archive --session-dir "$SESSION_DIR" --team {team_id}` and, in the same turn, delete ONLY the ids in its `deletable` list with `TaskUpdate(taskId, status="deleted")`. It copies harvested, completed task JSON to `<session>/tasks-archive/` first, never lists an open task, one an open task is blocked by or cites (`#id`, `task id`, its file path, a `blocks` edge), or a pending ALERT/BLOCKER, and refuses outright if a task file does not parse (`--dry-run` shows `would_archive` and deletes nothing).

### Step 10: Calibration

Read `variety_assessed` for the feature task (its id is the one value you take from the dispatch; latest `ts`, `scope` not `dispatch`, total via `resolve_variety_total`), count blockers and reruns, take the `DIFFICULTY RATING:` line from your dispatch (ask the team-lead only if absent), then save a CalibrationRecord with entities `['orchestration_calibration', '{domain}']`. Snapshots are never a second source for these figures.

## Incremental Harvest Workflow

Build the population as the Standard Harvest population rule above requires; a dispatch does not narrow it. Run Step 1 (with `--include` for a re-opened task), Steps 3-6 with `update` where remediation superseded a decision, Step 8 with `--kind incremental`, and report only the delta. Never propagate; never delete the journal.

## Consolidation Harvest Workflow

Build the population as the Standard Harvest population rule above requires. Run the Standard workflow first on anything Step 1 still finds (the last pass that will look), then:

### Step 3: Consolidate and Prune

Merge overlapping memories and update or delete superseded ones. The new ledger is never pruned; leave the old agent-memory file alone.

### Step 4: Reconcile Working Memory

Fix records, never the section text. With the propagate sentence, run `sync` after the merges, so the section shows the store as consolidated: the newest three records of this project. Otherwise skip and say so.

### Step 5-6: Retrospective and report

The retrospective and rating are in your dispatch (`ORCHESTRATION RETROSPECTIVE:`, `DIFFICULTY RATING:`); fold them into the one write and never wait for a message.

Save the session retrospective as calibration data, then report merged and pruned counts, `Working Memory: {sync_status | empty, project {project_id}}`, the calibration saved and any gaps. Process a compact summary a task names with `python3 "{plugin_root}/scripts/harvest.py" compact-summary --session-dir "$SESSION_DIR" --archive` (a move, never a delete).

## Orphaned Handoff Recovery

The Layer 4 fallback: `python3 "{plugin_root}/scripts/harvest.py" orphans --session-dir "$SESSION_DIR" --team {team_id}` lists every session, the active one included, whose `agent_handoff` tasks no ledger line covers. Read them with the Step 3 rule, record them with `record --kind orphan --origin-session-dir <that dir>`, and report how many carriers held each. Never remove a journal or a task file you read.
