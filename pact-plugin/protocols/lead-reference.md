# Lead reference (team-lead)

Tables, templates and long-form mechanics behind the orchestrator persona. The persona states each per-turn rule; read a section here when the persona points at it. Coordination (concurrent dispatch, task hierarchy, stalls, completeness, concurrent audit): [coordination.md](coordination.md).

## Wait Instruments

**Instrument the wait.** Waiting on a job you do not control (CI, a reviewer, a suite)? set up a watcher AT THAT MOMENT; the timeout is not optional. That terminating condition must BE the awaited event, never a correlate of it. Launch it with `run_in_background: true` — that flag is what makes its exit re-invoke you. Never background it with a shell `&` inside a foreground call instead. It passes the termination test: one call, silence, the event. Past its timeout with no result, the watcher is presumed dead: check once and re-arm if still pending. A watcher that FIRES is spent, and firing does not re-arm it. After each delivery, ask what is still pending and armed. Confirm the replacement is LIVE before you kill the incumbent, never after. When one delivers or dies on the human's work, send a `PushNotification` in the same turn. Report the findings surface, not only completion.

The watcher template (bounded, launched with `run_in_background: true`, keyed on the awaited event):

```bash
SHA=<head>
for i in $(seq 1 30); do
  OUT=$(gh api repos/{owner}/{repo}/commits/$SHA/check-runs \
        --jq '.check_runs[]|"\(.name): \(.status)/\(.conclusion // "-")"')
  PENDING=$(printf '%s\n' "$OUT" | grep -c 'in_progress\|queued')
  if [ "$PENDING" -eq 0 ]; then
    echo "ALL CHECKS COMPLETE after $((i-1)) minutes"; printf '%s\n' "$OUT"
    exit 0
  fi
  sleep 60
done
echo "TIMED OUT after 30 minutes — still pending:"; printf '%s\n' "$OUT"; exit 1
```

- macOS has no `timeout` (a watcher on it dies at once): use `perl -e 'alarm N; exec @ARGV' cmd`.
- If the awaited event cannot be tested directly, poll until it can be; never exit on a proxy (an mtime, a new directory, a vanished process).
- Session end or task reaping kills a watcher, so keep its bound in mind; when you report a wait, state the deadline in that report ("no result by ~T means the watcher died: prompt me").
- `PushNotification` refuses while the terminal is active; send it anyway. Never describe its mobile leg as delivered.
- A job you launched yourself with `run_in_background: true` wakes you with its own notice, and no hook asks for a cron beside it. Where completion may not reach you (a job you did not launch, a wait on a person or a teammate), schedule a recurring `CronCreate` alongside it, at an interval sized to the job. Key the cron on the DELIVERABLE, never on whether anyone reported. Delete it once when the wait resolves. It does not cover a dead session. Without cron tools, arm the watcher and proceed.
- Handing a watch over: get confirmation it runs; a check that nothing runs cannot tell "nobody is coming" from "starting up".
- An in-process teammate is not re-invoked by its own background job: it splits the work or hands you the watch (teammate-waiting §Wait Discipline for Self-Started Work).
- **Teammate waits.** When the task store drained, read the session journal, branch state, and filesystem mtimes; an mtime is a last-change time, not a liveness signal, and `missed_wake_scan` is the existing fallback machinery. The wait's fields: teammate skill §Waiting and waking. `wait_stale` (30 min) marks a flag stale; `missed_wake_scan` re-surfaces `awaiting_lead_completion` waits past it, `peer` waits once two or more owners are each past 30 min from `covers_since` (else `since`), and background-launch waits with no valid `covers_since`; inspect other stale waits yourself. Past 30 min the teammate should re-SET with a fresh `since` and `covers_since` carried forward unchanged. A re-SET that moves or drops `covers_since` has widened what the wait covers: ask. Never SET `intentional_wait` on your own task.
- **Tools**: `repact-status` shows each teammate busy or idle, its last tool and its context size; `repact-report` prints the session's spend, wakes and rejections; `repact-doctor` runs the health checks and lists open user actions.

## Dispatch Tables

| Situation | Action |
|---|---|
| Idle teammate, same domain AND a small context (`repact-status` shows `ctx`) | Put the delta in a new task's description; send only the wake naming its id |
| Anything else (the default) | Spawn fresh, with a brief linking the prior HANDOFF |
| Reviewer found issues, now needs a fixer | The code's author if it is still running, else a fresh coder; never a reviewer, with one reviewer or several (it verifies the fix) |

A fresh agent per task is the default (LEAN-DEFAULTS: most spend is agents re-reading large contexts on every tool call); reuse an idle teammate only when its context is small AND the domain is the same (send the delta). Stop each agent once its task is accepted. The cap on agents at once and the reviewer default are in the persona's §11 Lean defaults.

**First-spawn verification (HARD-RULE)**: read this on a spawn that fails or a teammate missing task tools, before any re-spawn. Lacking `TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet` yourself means the host withholds them: tell the user (README "If specialist agents will not spawn"); don't re-spawn. A teammate missing `TaskList`, `TaskUpdate` or `SendMessage` means a malformed spawn (`Task(...)` instead of `Agent(...)`, or no `name=`/`team_name=`): HARD STOP, fix, re-spawn; never "make do".

**Amending a dispatched task**: `repact-amend next <task>` prints the next free `amend_<n>` and its metadata object `{"text", "at", "from": "team-lead", "kind": "ruling|correction|premise|addendum|note"}`. Write it with one `TaskUpdate` (one top-level key per amendment, never a list), then `SendMessage` the owner only if `repact-status` shows it idle.

**Tool signals**: trust a tool's behaviour, then its parameter list, then its prose. A gate demanding a parameter proves the parameter exists.

**Shutdown**: stop each agent once its task is accepted and nothing of its awaits verification; a reviewer with open findings stays until they are verified (persona §11). `TaskStop` terminates: it sends no `shutdown_request`, and the team config survives it. The secretary, once spawned, serves the session until pause, refresh or wrap-up stops it.

**Tracking**: a blocker is `TaskCreate(subject="BLOCKER: ...", metadata={"type": "blocker"})` + `addBlockedBy` on the agent's task; an algedonic signal is `metadata={"type": "algedonic", "level", "category"}` blocking the phase (ALERT) or feature (HALT) task; `metadata.type` is required. A downstream task names its upstream task ids; read a HANDOFF with `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.handoff`. `agent_handoff_emitter.py` skips journal emission for `metadata.type` blocker/algedonic, and `shared/task_utils.py` and `shared/session_resume.py` sort signal tasks by the same literal.

**Roster**: `pact-preparer`, `pact-architect`, `pact-backend-coder`, `pact-frontend-coder`, `pact-database-engineer`, `pact-devops-engineer`, `pact-n8n`, `pact-test-engineer`, `pact-security-engineer`, `pact-qa-engineer`, `pact-auditor` (concurrent CODE observer), `pact-secretary`.

**PR review**: ONE reviewer by default; several in parallel only when the user asks or risk ≥ 3 (`pact-architect`, `pact-test-engineer`, the domain coders), plus `pact-security-engineer` for auth, input, APIs or crypto and `pact-qa-engineer` for user-facing change; synthesize in `docs/review/`, then `/repacted:pin-memory`.

**Recommended Agent Prompting Structure** (Task B): **CONTEXT** (phase, upstream task ids, peers); **MISSION** (what, and how done is known); **INSTRUCTIONS** (steps, skills, a TEACHBACK first); **GUIDELINES** (constraints; the secretary answers queries directly; for complex work "Include reasoning_chain in your HANDOFF").

## Method Reconstruction Examples

- **Decision attribution** names a decision and its stated reason. Weak: "chosen because it makes sense". Strong: "`ORPHAN_TOKEN_MAX_AGE_SECONDS = 86400` because the issue states `TOKEN_TTL × 24 ≈ 24 hours`".
- **Assumption trace** lists falsifiable propositions. Weak: "depends on the architect being right". Strong: "(a) `TOKEN_TTL` is about 1 hour; (b) a stale-attack buffer is the goal".
- **Contingency clause** names a real alternative. Weak: "if those change, so does the decision". Strong: "if `TOKEN_TTL` is 300 s, 24× is 2 hours: a disk-hygiene threshold".

## Harvest Triggers

Create a secretary task naming `pact-handoff-harvest` (idempotent):

- After CODE phase specialists have reported → Standard Harvest (`orchestrate`)
- After all phases and the user's merge decision → Standard Harvest (`orchestrate`) — propagates
- After every reviewer has reported → Standard Harvest (`peer-review`) (PRIMARY, unconditional)
- After remediation → Incremental Harvest (`peer-review`)
- After comPACT specialists have reported, on demand only (the user asks, or a HANDOFF holds a decision worth keeping) → Standard Harvest (`comPACT`)
- After a planning consultation → Standard Harvest (`plan-mode`)
- During wrap-up → Consolidation Harvest (`wrap-up`) (propagation decided per branch)
- At session pause → Consolidation Harvest (`pause`)
- At a context refresh → Consolidation Harvest (`refresh`)
- After a second feature in one session → Consolidation Harvest (`orchestrate`) — propagates

Ad-hoc: `SendMessage(to="secretary", message="[team-lead→secretary] Save: {what and why}")`; a history question is the same call with `Query: {question}`.

## Working Memory Propagation

Harvest saves are `--no-sync`; only `sync` rebuilds the `CLAUDE.md` Working Memory block, run when the dispatch carries `Then propagate the store into the Working Memory block.` Agents inherit the block at spawn, so that sentence may appear only at a boundary after which no independent-judgement agent (reviewer, auditor, test engineer, or any agent whose value is reaching conclusions independently) spawns before a later harvest: after the merge decision (`orchestrate`) and `wrap-up`'s merged-PR and no-PR branches, never its open-PR branch. Every other dispatch stays quiet, covered by session_init's rebuild, started at session start (for a session NOT resuming an arc, before its first independent-judgement agent exists; its outcome reaches the session briefing) or by a deliberate hold; adding a propagation to freshen a quiet one is the failure. The test is whether an independent-judgement agent will spawn before a later harvest, never the command variant's name. A Consolidation Harvest at a resumption boundary stays quiet: treat the block as current.

## Message Delivery

Written output: [pact-communication-charter.md](pact-communication-charter.md).

Inter-agent communication uses the `SendMessage` tool. Basic call shape:

    SendMessage(to="teammate-name", message="[sender→recipient] ...", summary="5-10 word preview")

The rules below govern how messages delivered via this tool actually behave.

### Delivery Model
- Messages are queued-async, delivered at the recipient's next idle boundary.
- Agents read queued messages in FIFO order on reaching idle.
- No cancellation primitive exists — a follow-up message cannot supersede a queued earlier one.
- The only mid-turn interrupt mechanism is user-side (Escape). Agent-to-agent `SendMessage` has no equivalent.
- `SendMessage` requires a specific `to=` recipient name. There is no broadcast addressing mode; reaching multiple teammates means iterating and sending one message per recipient (see [Lead-Side HALT Fan-Out](algedonic.md#lead-side-halt-fan-out) for the canonical pattern).

### Lead-Side Discipline — Verify Before Dispatching

> Moved: see agents/pact-orchestrator.md §1.

#### Pre-Send Self-Check

> Moved: see agents/pact-orchestrator.md §1 (lead) and skills/teammate/references/teammate-messaging.md §Pre-Send Self-Check (teammate).

### Teammate-Side Discipline — Verify Before Acting + Assume Eventually-Seen

> Moved: see skills/teammate/references/teammate-messaging.md.

### Algedonic-Signal Latency Caveat
- A HALT is an `amend_<n>` (kind `halt`): a busy teammate gets it after its next tool call.
- For immediate halt of in-flight teammate work, user-side manual interrupt is required.
- The team-lead's responsibility to "surface immediately" means at the team-lead's next idle, not at arbitrary real-time.
