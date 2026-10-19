# Dispatch briefs and premises

Context: the lead's brief is the one input in a team that arrives with authority and no reviewer.
Read this while writing a dispatch or task description, while deciding whether to delegate at all,
and before handing anyone a destructive or irreversible instruction.

## Canonical rules
1. Label every factual premise in a brief VERIFIED (I ran it, when), INHERITED (from whom) or ASSUMED; uncertainty is scored on premise reliability, not on how open the questions look. An INHERITED premise about a sibling branch or an earlier commit cites the commit (`git show <sha>`), never your memory of its summary.
2. Verify a brief's assertions before dispatching it; a higher uncertainty score does not make a false premise true, and the specialist still pays to discover it.
3. A premise that NARROWS a lane ("this part is correct, leave it") needs the same falsification as one that widens it: a narrowing error closes green and never surfaces.
4. State the derivation beside each figure (queried, summed, recalled, inferred); query-derived figures survive, arithmetic and recall break. Report per-segment splits, never merged aggregates.
5. Re-verify locators (line numbers, byte offsets, counts, paths) at the moment you quote them into a brief; mechanisms hold, locators drift. Cite a line with its commit, an offset with the file size.
6. Brief the problem and the acceptance test, not your fix: a fix you already have goes in as a labelled suggestion the teachback may beat. When a brief frames a choice as "A or B", add "or something better you can defend; tell me if the axis is wrong", and gate the teachback on the enumeration, not the choice.
7. Scope by the files a change FORCES, not the files it is about: ask what must change to typecheck and for the inputs to reach it, then re-check who owns those files.
8. A contract word such as "BUILD-TIME" is a claim about a mechanism: if the current types cannot express the check, authorise the enabling edit or downgrade the word.
9. Exercise a capability once before asserting it in a brief, state permissions as defeasible ("if it refuses, stop and tell me"), and broadcast a permission correction to every recipient.
10. "Safe" or "degrade gracefully" in a spec is a claim about the CONSUMING control: check what reads the degraded value and what it means to that reader before writing it.
11. A brief written in instruction voice launders claims: label each item live-verified or document-derived as you write it, and never write "verified" for something nobody ran.
12. Before a destructive instruction (checkout, stash, clean, rm, DROP, rotate) state the precondition it assumes, verify it yourself, and tell the recipient to verify before running it.
13. Name the JOIN between lanes in both briefs, say who owns each side, and list the files whose headers a lane must re-read before finishing, not only the files it may edit.
14. Say a dispatch once, in the task text; let the message point at it. Two declarations of one instruction drift apart, and the executor follows the newer one.
15. At dispatch, ask which of your recorded traps the task can plausibly hit and put them in the brief; a known trap nobody broadcast is indistinguishable from an unknown one.
16. Never hand a downstream agent your enumeration (keys, files, carriers) as the population; you remember what you asked for, the store records what happened.
17. By default, make small edits to an automation tool's workflows yourself, over its API; delegate only a genuine development project.
18. By default, do an investigation yourself when you need its answer in order to decide (a few reads, one query, "where does X live"); delegate reading that a specialist will then read again, broad sweeps and real development.
19. By default, make small targeted fixes directly, in the persona's §8 direct-edit lane (known change, ~3 files and ~40 lines, not high risk); new features, anything past the lane and anything needing design are delegated.
20. A per-dispatch score cannot hold an arc-level hazard: once per arc, check the workspace is recoverable and nothing irreversible was instructed without its precondition verified.
21. Scope a harvest or a sweep by "everything not yet processed", never by an id range; range edges are where work disappears.
22. At dispatch, check each reserved-file list against the diff of every open sibling branch (`git diff --name-only <base>...<branch>`), not only the current worktree, and against the files of other authors' open PRs (`gh pr view <n> --json files`): a change on one branch can force an edit in a file another branch reserved. When two units must touch one file, the later-merging coder owns the rebase conflict.
23. Name in the brief each resource only one agent can use at a time (a Docker daemon, a port, a device) with its lock: teammates run `repact-lock acquire <name> --owner <agent>@<team> --wait <seconds>` and release it in the same turn (the owner is name@team; a bare agent name is the same holder in every session). Do not arbitrate it by message (a grant lands only at the asker's turn end); `repact-lock status` shows the holder.
24. A brief whose output includes text an end user will read names the reader, the register and a length cap. Without them the build copies the house style, and every later check asks whether the text is true, never whether that reader can read it.
25. Bind every project task a unit owns at dispatch (`repact-task start <ref> --ptask <team>/<id>`, or `--ref T-…` on `repact dispatch`) and say in the brief that the HANDOFF owes `handoff.refs` a disposition per ref (`"T-<id>": "done: <evidence>"`, or `partial:`/`deferred:`/`dropped:` with a reason). The gate returns a HANDOFF that misses one; a ref only written in the brief's text is a mention and is never closed.
26. Before the first dispatch in a repo, read its `<repo>/.claude/pact-dispatch-notes.md` and copy the lines that apply into each brief: task claiming (default: the lead claims before dispatch, and a claim script that pushes is never a teammate's step), commit scopes and hooks, repo-wide tests before a push, other authors' reserved files, standing constraints. When the file is absent, carry on and offer to create it from `<plugin-root>/templates/pact-dispatch-notes.md`; when a teammate surfaces a house rule the notes lack, add it there, never only to your memory.
27. A brief that ends in outward steps (commit, push, PR) says so up front: "commit and push before the stop line". Context runs out mid-task, and a stop-line HANDOFF with the work unpushed leaves those steps to you. The stop line is per task (a large stamp raises it; `metadata.context_budget_kb` sets it): [workflows](../../../protocols/workflows.md), Context budget.
28. Every CODE brief carries a context-budget line (grep ranges, `tcount` summaries, build the first slice before reading on, a partial HANDOFF with `remaining:` at a clean point), and a unit above about 15 files is split into build-first slices before dispatch: a coder that fills its context before anything builds hands you nothing.

## Detail

**Why premises carry the variance.** An open question announces itself; a wrong assertion
propagates silently, because the specialist builds on it. In one eight-lane consultation six premise
errors were found, four of them the lead's, and in every later arc the errors kept landing on what
the lead ASSERTED, never on what it ASKED. An instruction is a claim, and the executing agent is its
reviewer; saying that out loud in the brief makes the check licensed procedure, not insubordination.
A teammate reporting PRESENCE beats your scan reporting ABSENCE by default; a false negative seals
itself ("nothing to do here").

**Access errors are evidence about the call.** A denial tells you your call was refused, not whether
the object exists or holds what you assume. Verify existence and shape separately from permission,
or a permission fix yields a confident query for a column that does not exist.

**A definition is not a consumer.** Before briefing on a symbol, grep for something that READS it: an
unmounted router, an unfetched endpoint, a model nothing constructs all pass their own tests.

**Scoring corollaries (variety mechanics live in the persona).** Score on the deliverable's
function, not the edit's shape: a small diff that gates an irreversible action outranks a large one
that does not, and a deliverable that decides whether an irreversible action is permitted is an
authorization control. Two dispatches with equal scores can differ in blast radius (how far a wrong
answer travels and how late it is found); ask that separately. Never let a workflow consequence
drive a score.

**Delegation economics.** A sub-agent loads a large context to answer what is often a handful of
targeted reads or API calls, and every correction wakes it with that context reloaded; the paraphrase
hop also separates you from the evidence. Standing n8n discipline when doing it yourself: back up as
a separate command before any write, PUT then GET and assert on the nodes' own parameters, and treat
activation state after a PUT as version-dependent (check it).

## Examples
- A brief told a lane its surfaces were a mixture, some already correct; every one was wrong, and
  only the teachback caught it before a half-fix shipped green.
- "Register a codec or parse at the call site" was the wrong axis; the lane found the real question
  was which of two pools, and that the answer needed both.
- A spec said "falling back to the URI is safe"; the fallback could never match the exclusion
  sentinel, so a suppression control evaluated cleanly to "not excluded".
- Four overnight lanes were told Edit reached their worktrees; not one questioned it, and two found
  out when their tools refused them mid-task.
