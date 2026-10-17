# Teachback-Gated Dispatch

> Read before your first specialist dispatch in a session, from `/repacted:orchestrate`, `/repacted:comPACT`, `/repacted:peer-review` and `/repacted:plan-mode`. This is the one copy of the Task A/B skeleton, the variety stamp, the wiring and the spawn form. Each dispatch site in those commands keeps its mission fields inline: the teammate's name and `subagent_type`, the two subjects, the Task B mission, and any journal write the command owns. Bound-approval prompts stay in their commands.

Every specialist dispatch creates **two tasks**, not one:

- **Task A** — TEACHBACK gate. `subject = "{name}: TEACHBACK for {feature}"`, owner = teammate. Description: teachback expectations + dispatch context.
- **Task B** — primary work. `subject = "{name}: {mission}"`, owner = teammate, `blockedBy = [<Task A id>]`.

Both are created BEFORE the `Agent(...)` spawn call so the teammate sees them on first `TaskList`. The teammate claims A, submits teachback metadata, idles on `awaiting_lead_completion`. You review the TEACHBACK, then accept via the two-call atomic pair: `TaskUpdate(A, status="completed")` FIRST, then the one-line wake (`[team-lead→<teammate>] Task #<id>: accepted on disk, digest <d>.`) — see [Teachback Review](../../protocols/completion.md#teachback-review) for the rationale. The teammate wakes to claim B.

## Dispatch sequence

**Dispatch sequence (replaces single-task dispatch)**:

```
# ORDER: Task A and Task B may be created in either order; no hook reads which id is lower (1.4.1 probe). Task A names Task B by subject, never by id. The blocking wiring below names both ids because it runs AFTER both tasks exist.
# 1. Create Task A (teachback gate)
A_id = TaskCreate(
    subject="{name}: TEACHBACK for {feature}",
# <!-- protocol:dispatch-task-a -->
    description="DOGFOOD TEACHBACK GATE for {feature}.\n\n"
                "Submit TEACHBACK by writing metadata.teachback_submit using the CANONICAL field schema (do NOT improvise key names): understanding, most_likely_wrong, least_confident_item, first_action, variety_acknowledgment (an OBJECT). See the teammate skill for field semantics. "
                "Then send the notify SendMessage with the task id and the digest the stored line printed, never the teachback itself (teammate skill, Teachback step 5). "
                "SET intentional_wait{reason=awaiting_lead_completion, expected_resolver=lead, since=<canonical_since() output>, covers_since=<the same value as since>}. Idle. "
                "DO NOT mark this task completed — team-lead-only completion. Lead will mark completed "
                "after teachback acceptance, then send the one-line wake; Task B is then claimable. "
                "If TEACHBACK is rejected, team-lead writes metadata.teachback_rejection and sends the one-line "
                "wake; read it with repact-payload <id> --key rejection and revise on this same task.\n\n"
                "When Task B unblocks, claim it (TaskUpdate status=in_progress) BEFORE any implementation tool-use — it is pre-assigned to you but still pending; you flip it, not the lead. Task B stays blocked until the lead accepts this teachback: that is the gate, not a desync.\n\n"
                "Mission for Task B: the primary-work task assigned to you in your TaskList (the work task, NOT this TEACHBACK gate task), identified by its subject (the '{name}: {mission}' pattern). Claim it after this teachback is accepted."
# <!-- /protocol -->
)

# 2. Create Task B (primary work) — per-dispatch variety stamping per workflows.md §Variety
B_id = TaskCreate(
    subject="{name}: {primary mission}",
    description="{full mission per Recommended Agent Prompting Structure}\n\n"
# <!-- protocol:dispatch-task-b -->
                "FIRST claim this task (TaskUpdate status=in_progress) before any implementation tool-use — it is pre-assigned to you but still pending; you flip it, not the lead.\n\n"
                "DO NOT mark this task completed yourself. "
                "After staging artifacts, write metadata.handoff, send notify SendMessage with task id, rev and digest (teammate skill, HANDOFF step 6) to team-lead, "
                "SET intentional_wait{reason=awaiting_lead_completion}. Idle. Lead will mark "
                "completed after HANDOFF validation. If team-lead rejects, team-lead writes metadata.handoff_rejection; "
                "read it with repact-payload <id> --key rejection and revise on this same task.\n\nWrite the HANDOFF whatever the size of the diff — a task whose deliverable is a JUDGEMENT rather than a CHANGE carries its value in the rationale, not in the diff.\n\nUpstream: TEACHBACK Task #{A_id}.",
# <!-- /protocol -->
    metadata={
        "variety": {
            "novelty":               N,
            "novelty_rationale":     "<1-sentence: the shape of this dispatch's novelty — never what you expect to find>",
            "scope":                 N,
            "scope_rationale":       "<1-sentence: the shape of this dispatch's scope — never what you expect to find>",
            "uncertainty":           N,
            "uncertainty_rationale": "<1-sentence: the shape of this dispatch's uncertainty — never what you expect to find>",
            "risk":                  N,
            "risk_rationale":        "<1-sentence: the shape of this dispatch's risk — never what you expect to find>",
            # Ignorance-dependent dispatch: stamp all four `*_rationale` as "WITHHELD: ignorance-dependent dispatch" — real scores, no prose.
            "total":                 N
        }
    }
)
TaskUpdate(A_id, owner="{teammate-name}")   # owner only: the hook writes A.blocks=[B] once B has the same owner
TaskUpdate(B_id, owner="{teammate-name}", addBlockedBy=[A_id])
TaskUpdate(B_id, metadata={"project_task": "<T-ref>"})   # only when the work is a task in the repo's task log

# 3. Spawn the teammate via the canonical Agent() form below.
```

## Steps at each dispatch site

1. `TaskCreate(subject=<the site's Task A subject>, description="<teachback gate brief; cross-ref to Task B for the mission>")` — Task A, with the description from the skeleton above. No owner write yet: the gate task gets its owner in step 3.
2. `TaskCreate(subject=<the site's Task B subject>, description=<the site's mission>, metadata=<the variety stamp>)` — Task B. `metadata` carries per-dispatch variety stamping per workflows.md §Variety (4-rationale schema); the wiring write below is denied without it. The mission is followed by the skeleton's Task B lines (claim first, never self-complete, HANDOFF notify, rejection, `Upstream: TEACHBACK Task #{A_id}`). A mission whose output includes text an end user will read names the reader, the register and a length cap.
3. `TaskUpdate(A_id, owner="{teammate-name}")`: the owner only. When B gets the same owner, the hook writes `A.blocks=[B]`; it warns (`teachback_addblocks_missing`) only when it cannot pair them (two open teachback or work tasks for one owner, an edge to another task), and then you wire A with `addBlocks=[B_id]`.
4. `TaskUpdate(B_id, owner="{teammate-name}", addBlockedBy=[A_id])`, then `TaskUpdate(B_id, metadata={"project_task": "<T-ref>"})` when the work is a task in the repo's task log (`repact-task next`): the acceptance bridge links it and marks it active, and your acceptance closes it on the HANDOFF's evidence.
5. Journal as the site says: the hook journals `variety_assessed` from Task B's stamp; a command writes only the events its site names.
6. Spawn the teammate with the canonical dispatch form, the site's name and `subagent_type` filled in (the `prompt` leads with the `YOUR PACT ROLE: teammate ({teammate-name})` marker):

```
Agent(
  name="{teammate-name}",
  team_name="{team_name}",
  subagent_type="pact-{teammate-type}",
  description="Spawn {teammate-name} specialist",
  prompt="YOUR PACT ROLE: teammate ({teammate-name}).\n\nYou are joining team {team_name}. As your FIRST action, Invoke Skill(\"repacted:teammate\") to record your identity. Then check `TaskList` for tasks assigned to you."
)
```

The `Agent()` `prompt` does NOT change shape — the Teachback-Gated Dispatch is encoded in the surrounding `TaskCreate` sequence, not in the `Agent()` call. The teammate discovers Task A + Task B via `TaskList` and follows the teammate skill §On Start.

**Carve-outs** — single-task dispatch still applies for:

- **Small tasks (complexity total 6 or less, LEAN-DEFAULTS)**: the work task alone, with its stamp and owner; no teachback task, no `addBlockedBy`. The teammate claims it (`in_progress`) and starts; its HANDOFF stays about 10 lines.
- **Auditor signal-tasks** (`metadata.completion_type="signal"`): no TEACHBACK, no Task B; self-completes tasks.
- **Secretary** (`member.agentType="pact-secretary"`): no TEACHBACK (single-task dispatch); self-completes its session briefing + memory-save tasks.
- **imPACT force-termination**: `TaskStop` + team-lead-set `metadata.terminated=true` is its own out-of-band path. See [imPACT.md](../../commands/imPACT.md).
