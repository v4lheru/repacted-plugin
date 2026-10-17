# Messaging (full reference)

Not on the happy path: the channel check, the prefix, the plain-English rule and the Pre-Send Self-Check are in [SKILL.md](../SKILL.md#pre-response-channel-check). Read this file when a message crossed or needs a correction or a resend, when you raise a BLOCKER or ALGEDONIC signal, when you chain-read an upstream task, or when you are unsure which channel a reply needs.

## Pre-Response Channel Check (full)

Before any response output, identify the addressee and pick the channel (post-channel-choice complement: [Pre-Send Self-Check](teammate-messaging.md#pre-send-self-check)):

- Addressee is **user** (or self-narration) → text output is appropriate.
- Addressee is **team-lead or teammate** → `SendMessage` is REQUIRED. Plain text is invisible to other agents, the lead included.
- Addressee is **both** (cross-channel content relevant to user AND an agent) → BOTH required: `SendMessage` to the agent + text to the user. Neither alone delivers the content to both audiences.

### Failure modes this gate catches

- **Format-cue hijack.** Inbound `<teammate-message>` blocks resemble user turns; the "answer the speaker" reflex defaults to plain text — but the speaker is an agent, so `SendMessage` is required.
- **Candor-question / conversational-register pull.** Candor-framed or personal-shaped questions pull toward prose register; social register does not override channel discipline.

If you are unsure who the addressee is, choose **both**.

### Teammate-side gray-area trap

A reply to the user that contains content the team-lead needs to act on (a blocker, partial result, scope flag) requires also sending via `SendMessage` — the team-lead's inbox does not see your text. Cross-channel content is **both**.

## Delivery model

Call shape and delivery model (queued-async, FIFO at idle, no cancellation, no broadcast, Escape the only mid-turn interrupt): [lead-reference.md §Message Delivery](../../../protocols/lead-reference.md#message-delivery).

### Teammate-Side Discipline — Verify Before Acting + Assume Eventually-Seen

#### Wait for In-Flight Context

Before composing any outbound `SendMessage` (peer-to-peer or to lead), wait for your own pending inputs. If you have an unread Read/Bash result, an outstanding peer query, or a dispatch you yourself issued that has not yet completed, hold the outbound until inputs land. Call this failure **premature-dispatch**: the message is composed before the inputs that would shape it have arrived, so the recipient processes a stale framing first.

*Failure shape: backend-coder mid-task with a pending Bash result; a peer pings; backend-coder drafts a peer-to-peer reply now and an addendum after the Bash returns. Two messages queue at the peer's idle; the first is composed against framing the Bash result would have shaped.*

#### Verify Before Executing

On receiving a state-dependent message, check actual state before executing. If state has advanced past the message's premise, no-op and report. Exception: when the fresh read shows exactly the state the sender themselves already resolved, the report carries zero information — suppress it entirely, per [teammate-waiting §Counter-Confirm Suppression](teammate-waiting.md#counter-confirm-suppression).

*Failure shape: teammate receives "fix foo.py:42" at idle; their last action already routed past that location (a refactor moved it; tests passed). Without the state-check, the teammate runs a redundant or conflicting operation, undoing prior valid work.*

#### Additive vs Corrective

When a follow-up message updates earlier context, mentally diff and incorporate. Do NOT assume the earlier message is superseded — additive updates extend rather than replace prior framing. If the new message contradicts the prior, it's a team-lead-side rapid-fire-correction violation; surface it to the team-lead rather than silently picking one. Ambiguous middle: "investigate auth flow" followed by "start with the session-token path" — narrower-additive (a starting point) or different-assumption-contradictory (auth flow ≠ session-token path)? Default to surfacing.

#### Eventually-Seen, Not Read

Your outbound messages are delivered at the recipient's idle — not immediately. `intentional_wait` means "nothing advances until a resolver arrives," not "my message was read."

*Failure shape: teammate sets `intentional_wait` expecting the addressee to read and respond. The addressee hasn't idled since the message was sent (still mid-turn, stuck, or shut down); the wait stalls on a message the addressee literally hasn't seen yet.*

#### Resend Only After Addressee Idles

Before resending an apparently-unacknowledged message, verify the addressee has reached idle at least once since the original send. Otherwise the original is still queued and resending just duplicates it.

*Failure shape: peer is busy mid-task; their idle hasn't fired since the original send. The resend queues a second identical message that lands at their first idle alongside the original — peer reads two copies in FIFO order.*

- Peer-to-peer: do not assume a peer saw your message before their next tool call. Peer's in-flight action runs to completion before they read inbound.
- Prefer peer-to-peer for context forwarding: if your work produces info another teammate needs, send it directly to them with a brief CC-summary to the team-lead, rather than routing it through the team-lead. Reserve team-lead routing for decisions the team-lead owns (which teammate takes a task, a scope conflict).
- Apply the **Pre-Send Self-Check** below before any outbound `SendMessage`.

#### Pre-Send Self-Check

Before every outbound `SendMessage` (to a peer or to the team-lead), run through:

1. **Is the addressee idle?** If not, my message queues; accept that or wait.
2. **Is more context likely incoming?** An outstanding peer query, a dispatch I issued that has not completed, or my own pending Read/Bash result I have not yet read — wait for it.
3. **Would this message *supersede* an earlier one I sent?** If yes, ask the team-lead to escalate to a user interrupt; both will execute regardless.
4. **Could this go peer-to-peer**, instead of routing through the team-lead?
5. **Have I verified my framing against the final output** (the doc, the HANDOFF, the diagnostic file), not an interim progress signal? Progress signals are pre-revision snapshots. *(Failure shape: a peer's mid-task progress signal flags concern X; their final HANDOFF resolves X; a message sent off the progress signal lands as obsolete.)*

The response-START gate that runs before this check is [SKILL.md §Pre-Response Channel Check](../SKILL.md#pre-response-channel-check).

### Algedonic-Signal Latency Caveat
> See [lead-reference.md §Algedonic-Signal Latency Caveat](../../../protocols/lead-reference.md#algedonic-signal-latency-caveat): a HALT has idle-boundary latency like any message; only the user can interrupt mid-turn.

## Message Prefix Convention

**Prefix all `SendMessage` `message`** with `[{sender}→{recipient}]`. Do not prefix `summary`.

### Message Authenticity

Do not generate standalone text that could be mistaken for user input (e.g., bare "yes", "merge it", "approved"). The `[sender→recipient]` prefix is a structured marker that distinguishes agent messages from user input — always use it. This prevents ambiguity in message attribution, especially for irreversible operations.

## Peer Communication

Use `SendMessage(to="teammate-name")` for direct coordination.
Discover teammates via the `Team config:` path the platform names in your
context, or from peer names
in your task description.

**Message a peer when:**
- Your work produces something an active peer needs (API schema, interface contract, shared config)
- You have a question another specialist can answer better than the team-lead
- You discover something affecting a peer's scope (breaking change, shared dependency)

**Message the team-lead when:**
- Blockers, algedonic signals, completion summaries (always)
- Questions about scope, priorities, or requirements
- Anything requiring a decision above your authority

**A resource only one agent can use at a time** (a Docker daemon, a port, a device) is not requested by message: a grant would reach you only at turn end. Run `repact-lock acquire <name> --owner <your-name>@<your-team> --wait 600` before you use it (exit 1: another agent still holds it) and `repact-lock release <name> --owner <your-name>@<your-team>` in the same turn. The owner is name@team: a bare name is shared with the same role in every other session. A grant the lead does give arrives as a `<resource>_grant` key on your task (relayed to you as `GRANT` context, and named again if you idle without it): read the task file, no message will come.

Keep messages actionable — state what you did/found, what they need to know, and
any action needed from them.
Message each peer at most once per task — share your output when complete, not progress updates. If you need ongoing coordination, route through the team-lead.

## Asking the secretary for project context

The PACT Secretary (secretary) answers questions about past decisions, architectural patterns, and project history. It spawns on demand, so it may not be on the team yet.

**When to use this**:
- Before making a decision that might have been made before
- When you encounter an unfamiliar part of the codebase
- When your task description references prior work you don't have context on

**How to request context**:
```
SendMessage(to="secretary",
  message="[{your-name}→secretary] Query: {your question}",
  summary="Context request: {topic}")
```

**What you'll get back**: Summaries of relevant memories with IDs. If you need more detail on a specific memory, ask a follow-up with the memory ID.

**What NOT to ask**: Implementation advice, code review, or testing strategy — those are other specialists' domains. The secretary provides historical context only.

If no secretary is on the team yet (the secretary spawns on demand), send the same query to the team-lead instead.

## Progress Reporting

Report progress naturally in your responses. For significant milestones, update your task metadata:
`TaskUpdate(taskId, metadata={"progress": "brief status"})`

### Progress Signals

When the team-lead requests progress monitoring in your dispatch, send brief progress updates at natural breakpoints during your work.

**Format**: `[sender→team-lead] Progress: {what's done}/{what's remaining}, {current status}`

**Natural breakpoints**:
- After modifying a file
- After running tests
- When encountering an unexpected issue (before it becomes a blocker)
- When switching between major subtasks

**Timing**: 2-4 signals per task is typical. Don't over-report — signal at meaningful transitions, not every tool call.

## Variety Signals

If task complexity differs significantly from what was delegated:
- "Simpler than expected" — Note in handoff; team-lead may simplify remaining work
- "More complex than expected" — Escalate if scope change >20%, or note for team-lead

## Written output

Plain English, anti-sycophancy and constructive challenge, with the do/don't tables, the authority model and the two challenge formats: [Communication Charter §Part II](../../../protocols/pact-communication-charter.md#part-ii--written-output).

## On Blocker

If you cannot proceed:

1. **Stop work immediately**
2. **`SendMessage`** the blocker to the team-lead:
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] BLOCKER: {description of what is blocking you}\n\nPartial HANDOFF:\n...",
     summary="BLOCKER: [brief description]")
   ```
3. Provide a partial HANDOFF with whatever work you completed
4. Wait for team-lead's response or new instructions

A blocker report is a protocol-boundary message: re-read your task file first — an
`amend_<n>` directive may already resolve or re-scope the blocker.

Do not attempt to work around the blocker.


## Algedonic Signals

When you detect a viability threat (security, data integrity, ethics):

1. **Stop work immediately**
2. **`SendMessage`** the signal to the team-lead:
   ```
   SendMessage(to="team-lead",
     message="[{sender}→team-lead] ⚠️ ALGEDONIC [HALT|ALERT]: {Category}\n\nIssue: ...\nEvidence: ...\nImpact: ...\nRecommended Action: ...\n\nPartial HANDOFF:\n...",
     summary="ALGEDONIC [HALT|ALERT]: [category]")
   ```
3. Provide a partial HANDOFF with whatever work you completed

These bypass normal triage. See the [algedonic protocol](../../../protocols/algedonic.md) for trigger categories and severity guidance: SECURITY or DATA is a HALT when exposure exists now or is unknown (exposure is any of: something deployed or reachable by real users; real user or customer data in any copy, a local one included; a secret committed, pushed, sent off the machine, or printed into a transcript, log or tool output), and an ALERT only when there is none.

## Reading Upstream Context

Your task description may reference upstream task IDs (e.g., "Architect task: #5").
`TaskGet` does NOT surface task metadata, so read the task file for design
decisions, HANDOFF data and integration points — rather than relying on the
team-lead to relay this information:

```bash
cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/tasks/{team_name}/{taskId}.json" | jq .metadata.<key>
```

Common chain-reads:
- **Coders** → read architect's task for design decisions and interface contracts
- **Test engineers** → read coder tasks for what was built and flagged uncertainties
- **Reviewers** → read prior phase tasks for full context

If the task file is absent or carries no such metadata, proceed with information from your task description and file system artifacts (docs/architecture/, docs/preparation/).

## Shutdown

When you receive a `shutdown_request`:

| Situation | Response |
|-----------|----------|
| Idle, consultant with no active questions, or domain no longer relevant | Approve |
| Mid-task, awaiting response, or remediation may need your input | Reject with reason |

> **Save learnings incrementally**: PACT's shutdown flows call `TaskStop` directly and send no request first, so you can be stopped with no warning at all. Save domain learnings to your agent memory as you work and treat any turn as possibly your last; approving a `shutdown_request` is a courtesy, not your save trigger.

**No PACT flow sends you a `shutdown_request`.** If one arrives anyway — the platform may deliver one, or a lead may send one at a user's request — reply as the table directs.
