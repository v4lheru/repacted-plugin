---
name: pact-lead-playbook
description: 'Use when you are the PACT lead: writing a dispatch brief, stating premises, ruling or relaying mid-flight, accepting or rejecting a teachback or HANDOFF, deciding whether to delegate (by default investigations stay with the lead), sizing a HALT, calling the stop on review loops, launching long jobs, or running the distill pass at wrap-up.'
---

# PACT lead playbook

Context: the lead's field practice, distilled from the orchestrator's own memory: how to write
briefs whose premises can be checked, rule and relay without losing the ruling, accept work on
evidence, decide what to do yourself, and stop, size or own the risky moments. PACT's protocol
rules (completion authority, teachback and HANDOFF mechanics, variety routing, the dispatch gate)
stay in the persona and `protocols/`; this skill links them by name and does not restate them.

## Workflow
1. Before dispatch: label premises VERIFIED, INHERITED or ASSUMED; scope the files the change
   FORCES, not the files it is about; name the join between lanes in both briefs; check reserved
   files against every open sibling branch; copy the repo's `<repo>/.claude/pact-dispatch-notes.md`
   lines that apply into each brief, and those of `<repo>/.repacted/profile.md` (`repact-profile build`) into
   the work task's CONTEXT; say the dispatch once, in the task text; for text an
   end user will read, name the reader, the register and a length cap.
   → `references/dispatch-briefs-and-premises.md`
2. Decide who does it. By default the lead keeps an investigation whose
   answer the lead needs in order to decide (a few reads, one query); reading what a specialist
   will then read again is delegated. A known, small, not-high-risk code change is the lead's too
   (the persona's §8 direct-edit lane: ~3 files, ~40 lines). Everything else is delegated.
   → `references/dispatch-briefs-and-premises.md`
3. Mid-flight: a post-dispatch directive is always an `amend_<n>` key on the task; message only an
   idle owner (a busy in-process owner gets it by the relay), as the completion protocol has it;
   quote the ruling; re-verify the premise of any conditional instruction; relay facts that close
   open items in the same turn; broadcast permission corrections to all; on an
   `idleReason=failed` idle, read disk and re-scope, never resend.
   → `references/rulings-and-relays.md`
4. At teachback and HANDOFF: gate on the enumeration, and read the teammate's doubts as a map of
   where to push. A count the run ledger lacks is a note to you, never a return. Treat a flag as a
   question, never a verdict, though an `evidence.*` refusal from `repact accept` is a fact about
   the run ledger. A teammate's SendMessage is not a finding. Commission an adversarial non-author pass for authorization controls AT DISPATCH.
   → `references/acceptance-and-verification-gates.md`
5. On a HALT or a flagged risk: size it (what is new, where it went, the discriminating case, the
   blast radius) before stopping everyone; call the stop on verification loops that no longer move
   the verdict. → `references/long-jobs-halts-and-loops.md`
6. Long jobs: launch from the lead's session with a detached launcher and an on-disk ledger, never
   from a teammate shell. Heavy compute uses at most 2 workers and `nice -n 19` by default. Irreversible
   sequences have a single named owner confirmed by both parties.
   → `references/long-jobs-halts-and-loops.md`
7. At `/repacted:wrap-up`: run the distill pass (`distill-inbox list` → `plan [--suggest]` → edit
   the skill references → `apply`), then `check-index --all`.
   → repacted:memory-hygiene `distill-pass.md`

## Map
- `references/dispatch-briefs-and-premises.md` briefs, premises, scoping, delegation.
- `references/rulings-and-relays.md` rulings, supersessions, relays, pointers, message economy.
- `references/acceptance-and-verification-gates.md` status gates, risk intake, the evidence
  check.
- `references/long-jobs-halts-and-loops.md` halts, loops, long jobs, dual drivers, wrap-up.
- `protocols/self-refine.md` and `refinement-log.md` keep the skill current.

## Related skills
- repacted:multi-agent-collaboration: the teammate side of the same exchanges.
- repacted:verification-discipline: HOW to check a claim; this skill says WHEN to demand it.
- repacted:pact-field-notes: when the runtime itself misbehaves, including the merge-guard recipe.
- repacted:memory-hygiene: the distill pass and index policy.
- `repacted:orchestrate`: the workflow itself.
No secretary is assumed at session start; "query the secretary" spawns one when history is needed.
Merge and push approval is the user's core rule; it is not restated here.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
