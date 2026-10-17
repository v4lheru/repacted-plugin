# Teachback (full reference)

Not on the happy path: the checklist in [SKILL.md](../SKILL.md#teachback) is enough to write a teachback. Read this file when a teachback advisory or rejection names a field, or when the variety band cannot be resolved. The Common mistakes rows 1-4 sit in [SKILL.md](../SKILL.md#common-mistakes), because hooks cite them by number.

## Canonical schema at a glance

The full teachback payload — all required fields plus the optional method-reconstruction sub-object — at the correct nesting level. Skim this first if you only have time for one section; the rest of the skill explains the why and the surrounding choreography.

```
TaskUpdate(taskId, metadata={

    # Top-level metadata key #1: teachback_submit
    # 5 canonical fields. variety_acknowledgment is an OBJECT (not a string).
    # reasoning_reconstruction is a sibling field of the other 4 (not nested
    # inside any of them, not placed on metadata.handoff).
    "teachback_submit": {
        "understanding":         "<...>",
        "most_likely_wrong":     "<...>",
        "least_confident_item":  "<...>",
        "first_action":          "<...>",
        "variety_acknowledgment": {
            "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
            "concern": "<required when value != 'yes'>"
        },
        # Sibling field — optional below variety 11, required at 11+.
        # The 3 sub-keys are EXACTLY these three names, no substitutions.
        "reasoning_reconstruction": {
            "decision_attribution": "<...>",
            "assumption_trace":     "<...>",
            "contingency_clause":   "<...>"
        }
    },

    # Top-level metadata key #2: intentional_wait
    # SEPARATE top-level sibling of teachback_submit — NOT nested inside it.
    # Written via a SECOND TaskUpdate call after the notify SendMessage; see
    # Action: store teachback now below for the load-bearing 3-step ordering.
    "intentional_wait": {
        "reason":            "awaiting_lead_completion",
        "expected_resolver": "lead",
        "since":             "<canonical_since() output>",
        "covers_since":      "<the same value as since>"
    }
})
```

Each field's full purpose, the variety-band trigger for `reasoning_reconstruction`, and the 4 common wrong-shape mistakes appear in the sections below. The Common mistakes section enumerates the 4 wrong shapes the runtime advisory layer at `hooks/task_lifecycle_gate.py` catches at write time.

## What a teachback is

Invoking this skill means you are about to submit a teachback for your
current task. Do not proceed to implementation work until you have stored
it AND received the team-lead's acceptance.

A teachback is a Pask Conversation Theory verification gate. Before you
start implementing, you restate your understanding of the task so the
team-lead can catch misunderstandings early, before you burn context on a
wrong implementation.

Under the Task A + Task B dispatch shape, your teachback is the deliverable
of Task A. Task B (the primary work) is `blockedBy=[A]`: it remains VISIBLE and
readable in your `TaskList` (annotated as blocked) but is not claimable — the
`blockedBy` edge filters it from the claimable set, it does not hide or lock it —
until the team-lead accepts your teachback by transitioning Task A to `completed`,
which makes Task B claimable.

The 5 canonical fields (Step 1) are the procedure-level gate; the optional `reasoning_reconstruction` nested field adds the method-level gate at high-variety dispatches — see [§When to Method-Reconstruct](#when-to-method-reconstruct) below. Variety-band thresholds live at the SSOT in `hooks/shared/variety_scorer.py` (`COMPACT_MAX` / `ORCHESTRATE_MAX` / `PLAN_MODE_MAX` + `route_workflow`); do not hard-code the 6 / 10 / 14 thresholds.

## Action: store teachback now

> **Ordering invariant** (audit anchor): the three steps below MUST execute in the order Step 1 → Step 2 → Step 3 — `metadata.teachback_submit` write FIRST, then notify `SendMessage`, then `intentional_wait` SET. This ordering is load-bearing for the team-lead's [Read-Trigger Precondition](../../../protocols/completion.md#read-trigger-precondition): the lead must wait for teammate's wake-signal `SendMessage` before treating the raw JSON read as authoritative, but the `SendMessage` is only safe to send AFTER the metadata write has landed on disk. Reversing Step 1 and Step 2 produces false-empty raw reads on the lead side that have triggered false-positive teachback rejection cycles. Reversing Step 2 and Step 3 (idle before `SendMessage`) silently strands the lead — they will never see the wake-signal because you went idle without sending it. Editors of this skill: do NOT re-order these steps. The write persists the one copy; the notify carries only the task id and digest, so the lead reads that copy once and the digest match is their audit.

**Step 1 — write the teachback to task metadata** (5 canonical fields + 1 optional nested field):

> ⚠️ **Write the full sub-object in ONE call.** `TaskUpdate` merges metadata at the TOP LEVEL only. A write that carries part of a nested sub-object REPLACES that sub-object. It erases each field you omit, and it reports no error. A write to one TOP-LEVEL key does not change a different TOP-LEVEL key, which is why Step 1 and Step 3 are different calls. If you must correct one field after the write, RE-SEND THE FULL OBJECT, and include each other field unchanged. Then read the task file back and enumerate the keys of `metadata.teachback_submit`.

```
TaskUpdate(taskId, metadata={"teachback_submit": {
    "understanding": "<what you understand you're building, key constraints, interfaces>",
    "most_likely_wrong": "<the part of your understanding you are least confident about>",
    "least_confident_item": "<one specific assumption you'd like the team-lead to confirm>",
    "first_action": "<the first concrete step you will take after teachback approval>",
    # ANTI-PATTERN: a free-text STRING here is rejected. variety_acknowledgment
    # MUST be an OBJECT with the 2 keys shown below. The runtime advisory
    # `variety_acknowledgment_schema_invalid_at_write_time` fires at TaskUpdate
    # if you submit a string. See the teammate skill's Common mistakes row 1.
    "variety_acknowledgment": {                  # REQUIRED — see trigger paragraph below the JSON
        "rationale_articulates_this_dispatch": "yes" | "no" | "concern",
        "concern": "<required when value != 'yes'; names the smell>"
    },

    # ANTI-PATTERN: reasoning_reconstruction belongs HERE — a sibling field on
    # teachback_submit, NOT inside metadata.handoff. The handoff carries
    # reasoning_chain (the sender's own reasoning); the teachback carries
    # reasoning_reconstruction (your reconstruction of the upstream's
    # reasoning). Symmetric concept, different slot. The runtime advisory
    # `reasoning_reconstruction_in_handoff` fires if you place it on the
    # handoff. See the teammate skill's Common mistakes row 2.

    # OPTIONAL: include per §When to Method-Reconstruct (below)
    # (required at variety >= 11; recommended at 7-10; skipped at 4-6)
    # ANTI-PATTERN: the 3 sub-keys are EXACTLY these three names —
    # decision_attribution / assumption_trace / contingency_clause. Substituting
    # alternatives (e.g. "what-I-learned", "falsification-attempts",
    # "most-likely-wrong-prediction") is rejected as
    # reasoning_reconstruction_subkeys_invalid at write time and as
    # malformed_reasoning_reconstruction at lead-side completion-time review.
    # See the teammate skill's Common mistakes row 3.
    "reasoning_reconstruction": {
        "decision_attribution": "I understand <upstream agent> chose <decision> because <their stated reason>",
        "assumption_trace":     "This reasoning depends on <assumption A>, <assumption B>, ...",
        "contingency_clause":   "If <assumption A or B> changes, the decision should change to <alternative>"
    }
}})
```

Read Task B's `metadata.variety` (via Task A's `blocks[0]`); `TaskGet` is metadata-blind, so read the task file: `cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.variety`. Judge each of the four per-dimension rationales against THIS dispatch's complexity and record `yes` / `no` / `concern`; when not `yes`, name the smell in `concern`. **A rationale reading `WITHHELD: ignorance-dependent dispatch` is deliberate, not malformed — record `concern` QUOTING that literal verbatim; do NOT ask for a re-stamp.** Full review workflow: [`protocols/workflows.md` §Variety Calibration Record](../../../protocols/workflows.md#variety-calibration-record).

**Step 2 — notify the team-lead** (task id and digest, never the payload):

```
SendMessage(
    to="team-lead",
    message=(
        "[<your-agent-name>→team-lead] Teachback submitted on Task #<A_id>, digest <d>. "
        "Idling on awaiting_lead_completion."
    ),
    summary="Teachback submitted: <topic>"
)
```

`<d>` is the 12-hex digest your write's stored line printed (`Teachback stored on #<A_id> (owner <you>), digest <d>`). The lead reads the one stored copy with `repact-payload <A_id> --digest <d>`; a match proves it read what you stored. Repeating the teachback in the message doubles what both of you read.

> One line; the `summary` never carries payload content (it truncates at 200 chars).
> After writing the teachback, read the task JSON back and confirm every field is present, non-empty, and ends on its intended final content — a sender-side output cut lands mid-JSON and surfaces as a write error, not as silence. This check holds at any size.

> # ANTI-PATTERN: Step 1 and Step 3 are SEPARATE `TaskUpdate` calls writing
> SEPARATE top-level metadata keys. Nesting `intentional_wait` INSIDE
> `teachback_submit` (one combined `TaskUpdate`) hides it from
> `missed_wake_scan.py`, which reads `metadata.intentional_wait` and does
> not descend into nested objects, so no missed-wake alarm is emitted.
> `is_self_complete_exempt` governs self-completion exemption instead and
> does not read the field in either placement. The runtime advisory
> `intentional_wait_nested_in_teachback_submit` fires at write time. See
> the teammate skill's Common mistakes row 4.

**Step 3 — SET `intentional_wait` and idle**:

```
TaskUpdate(taskId, metadata={"intentional_wait": {
    "reason": "awaiting_lead_completion",
    "expected_resolver": "lead",
    "since": "<canonical_since() output: tz-aware ISO-8601 UTC>",
    "covers_since": "<the same value as since>"
}})
```

Then run `repact-wait <Task A id>` in the foreground instead of ending the turn ([SKILL.md](../SKILL.md#waiting-and-waking) item 6). If you do end the turn, end it with ONE short line and never restate the teachback in prose: your turn-end text reaches the lead as the idle notification, so a restatement delivers the teachback a second time.

> **The clock read (the one exception).** `since` is a clock reading, never a guess. Right before this write, run `date -u +%Y-%m-%dT%H:%M:%S+00:00` and use its output for `since` and `covers_since`. It and `repact-wait` are the only shell commands allowed before your teachback is accepted, the exceptions your spawn text names. Run it alone, exactly as written: no pipe, `&&`, `;`, redirect, substitution, env prefix or second command.

Do NOT begin Task B until Task A's status transitions to `completed`. `repact-wait` returns on that write; once your turn has ended, the team-lead's wake-signal `SendMessage` confirms acceptance — you cannot self-wake to poll `TaskList` while idle.

**On rejection** (team-lead writes `metadata.teachback_rejection`, then sends the one-line wake; read it with `repact-payload <id> --key rejection`): see [§On Rejection](teammate-handoff.md#on-rejection-wake-signal-receipt).

## When to include reasoning_reconstruction

Include the nested `reasoning_reconstruction` sub-object whenever the dispatching task's variety score is **11+** (`ROUTE_PLAN_MODE` / `ROUTE_RESEARCH_SPIKE`). At variety 7-10 (`ROUTE_ORCHESTRATE`) it is **recommended but not required** — the lead may request reconstruction on follow-up if upstream decisions are non-trivial. At variety 4-6 (`ROUTE_COMPACT`) it is **skipped** — absence is the expected default.

| Variety score | Workflow route | reasoning_reconstruction | Lead behavior on absence |
|---|---|---|---|
| 4–6 | `ROUTE_COMPACT` | Skipped — not expected | Accept teachback; absence is the expected default. |
| 7–10 | `ROUTE_ORCHESTRATE` | Recommended (NOT required) | Accept teachback; lead MAY ask for reconstruction in a `teachback_rejection` correction if upstream decisions are non-trivial. |
| 11–14 | `ROUTE_PLAN_MODE` (plan-mode + orchestrate) | REQUIRED | Reject teachback with `metadata.teachback_rejection{reason="missing_reasoning_reconstruction"}`, the request in its `corrections`, then the one-line wake. |
| 15–16 | `ROUTE_RESEARCH_SPIKE` | REQUIRED (treated identically to plan-mode) | Same as `ROUTE_PLAN_MODE` — reject on absence. |

Source of truth for the band cuts: `hooks/shared/variety_scorer.py` (`COMPACT_MAX` / `ORCHESTRATE_MAX` / `PLAN_MODE_MAX`); this table is the only copy in this file — do not paraphrase the `ROUTE_*` literals. Variety **10** is the TOP of `ROUTE_ORCHESTRATE` (recommended-not-required); variety **11** is the BOTTOM of `ROUTE_PLAN_MODE` (required). The 10|11 boundary is the cut.

The three sub-keys are three cognitive operations on the upstream's HANDOFF: `decision_attribution` restates what the upstream decided and why; `assumption_trace` lists the falsifiable propositions the reasoning depends on; `contingency_clause` names a concrete alternative if those assumptions are false. Vague answers are lead-side reject signals; the reasons the lead records are in [§When to Method-Reconstruct](#when-to-method-reconstruct) below.

If you are dispatched as an owner in `TEACHBACK_EXEMPT_AGENT_TYPES` (currently `{pact-secretary}` per `hooks/shared/intentional_wait.py`), the entire teachback gate is bypassed — including this sub-field. No carve-out logic needed.

### When to Method-Reconstruct

Hook advisories and lead rejections cite this section by name. The band table above is the gate: it runs against the dispatching task's variety score (see `hooks/shared/variety_scorer.py` — `COMPACT_MAX`, `ORCHESTRATE_MAX`, `PLAN_MODE_MAX`, and `route_workflow`). Constants live at the SSOT; do not hard-code the 6 / 10 / 14 thresholds in skill prose, doc prose, or test code.

The lead-side validation gate emits one of 3 rejection reasons in `metadata.teachback_rejection.reason`:

| Reason enum | Trigger | Source gate |
|---|---|---|
| `missing_reasoning_reconstruction` | Required-band teachback omits the field | Presence gate (variety ≥ 11) |
| `malformed_reasoning_reconstruction` | Field present but not a 3-key dict | Schema gate |
| `empty_reasoning_reconstruction_field` | Sub-key missing, non-string, or empty/whitespace | Schema gate |

The `variety_score=None` handling is a **transitional permissiveness**. A future plugin version SHOULD deprecate the None-tolerance and require `variety_score` on every feature task at dispatch time (enforced via `task_lifecycle_gate` or a new dispatch-time predicate). The conservative-now / fail-loud-later trajectory surfaces failures early without breaking pre-existing dispatch state. Until that deprecation lands, `variety_score=None` is treated as the **Recommended band** (equivalent to variety 7–10).

## Ordering rule

You must store your teachback (`metadata.teachback_submit` write) before any `Edit`/`Write`/`Bash` call used for implementation work. Reading files to understand the task (`Read`, `Glob`, `Grep`) is permitted before teachback; those are understanding actions, not implementation actions. The shell exceptions are the clock read in [Step 3](#action-store-teachback-now) and `repact-wait` after the notify.

Under the Task A + Task B dispatch shape, this ordering is structurally reinforced: Task B is gated behind `blockedBy=[A]` (visible but not claimable — that is the gate, not a desync) until Task A's status transitions to `completed`. The `metadata.teachback_submit` write IS your teachback delivery; the team-lead's `TaskUpdate(A, status="completed")` paired with a wake-signal `SendMessage` IS approval.

## Post-store behavior

Idle on `awaiting_lead_completion` until the team-lead's wake-signal arrives. Do NOT speculatively begin Task B; the team-lead's status flip is the gate. When the wake-signal arrives, claim Task B FIRST — `TaskUpdate(<Task B id>, status="in_progress")` BEFORE any `Edit`/`Write`/`Bash` — then begin: Task B is pre-assigned to you (owner already set) but still `pending`, so YOU flip it to `in_progress` (the lead does not), preserving the lead's "work started" signal.

If you have other claimable, unblocked tasks unrelated to this dispatch (a separate Task A from a different mission), you may claim and work them. The wait is per-task, not per-agent.

**If you background work, flag the wait before you end the turn.** A `Bash` call with `run_in_background`, or a command a shell `&` sends to the background, may finish while you are idle, and when you run in-process its completion does not wake you — SET `metadata.intentional_wait` naming it, on every task the wait covers.

## Exception

Consultant questions (a peer asks you something) do not require a teachback. You only teachback on task dispatches.
