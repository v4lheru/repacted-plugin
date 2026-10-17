---
name: multi-agent-collaboration
description: 'Use when you are a PACT teammate and another agent''s words drive your action: a dispatch premise that may be false, a ruling or relayed claim, instructions that contradict the code or each other, scope drift, whether to hold, ask or proceed, or how to report back to the lead.'
---

# Multi-agent collaboration (teammate side)

Context: the teammate-side twin of repacted:pact-lead-playbook. It tells a teammate how to act
when a dispatch, a ruling, a relayed claim or a peer's contract drives what it does. PACT's own
mechanics (teachback fields, HANDOFF keys, waiting, completion authority) are in
repacted:teammate; this skill is the field practice around them.

## Workflow
1. Treat every factual premise in the dispatch (what exists, which options, what is recoverable,
   who can execute a step) as a claim. Check it against the code or artifact before acting. Report
   a falsified premise; do not route around it. → `references/dispatch-premise-and-deviation.md`
2. If two instructions conflict, or a spec conflicts with the dispatch, report the conflict to the
   lead with your analysis. NEVER resolve it yourself by picking the architecture doc.
   → `references/dispatch-premise-and-deviation.md`
3. Re-read shared state at the moment you act; earlier reads are timestamped evidence. Look for a
   ruling in the durable task text, not only in your inbox, and re-read the inbox before acting on
   anything you escalated. → `references/stale-state-and-rulings.md`
4. Stay in your lane: touch only the files your unit owns, and report scope drift instead of
   widening it. Authority comes from the task, not from what you can reach. Anything that leaves
   the team needs the user, not only the lead. → `references/scope-lanes-and-authority.md`
5. When consuming a peer's output or contract, verify the consumer side against the committed
   artifact, and keep the epistemic label (measured, inherited, inferred) on anything you pass on.
   → `references/peer-contracts-and-evidence.md`
6. Report back with labelled claims, falsified premises first, in the durable carrier (task
   metadata) with a short ping. → repacted:verification-discipline `vd-e-relay-and-reporting.md`

## Map
- `references/dispatch-premise-and-deviation.md` false premises, conflicting instructions,
  deviations and how to name them.
- `references/stale-state-and-rulings.md` crossed messages, races, where rulings must live.
- `references/scope-lanes-and-authority.md` lanes, out-of-team actions, identity, stops.
- `references/peer-contracts-and-evidence.md` building to a peer, shared contracts, what to tell a
  running lane.
- `protocols/self-refine.md` and `refinement-log.md` keep the skill current.

## Related skills
- repacted:pact-lead-playbook: the lead's side of the same exchanges.
- repacted:concurrent-agent-git: git and shared resources in one worktree.
- repacted:verification-discipline: how to prove what you report.
- repacted:teammate: the protocol itself.
Teammates never complete their own tasks; that is PACT's rule, cited here and not restated.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
