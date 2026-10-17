# VD-A1: git refs, runtime state and the shipped artifact

Context: read this before a claim about what a branch, a deployment, a running process or a
shared file contains. Each of these can silently answer about a different tree, target or
session than the one you mean. The general "which artifact" rules are in
[vd-a1-which-artifact.md](vd-a1-which-artifact.md).

Also read this before calling a change verified when the thing users receive is produced
from your code: a query, a bundle, a document, an export, a request to a vendor, a patch handed
to someone else. It is the "check the thing that ships" half of
[vd-a1-which-artifact.md](vd-a1-which-artifact.md). For proof that a deploy or a write landed,
see [vd-f-deploy-and-write-proof.md](vd-f-deploy-and-write-proof.md).

## Canonical rules

### Git refs, pins and runtime state
- **A1.14** A constraint said to be "pinned in CLAUDE.md": resolve which file, and check whether it is committed. A pin above the repo boundary guards one laptop, not the project.
- **A1.15** Before pointing a local process at production, read its boot path and follow every delegation to the callee. Find the declaration a quoted docblock belongs to before citing it.
  - A log line naming a dangerous operation may be the guard announcing itself. A guard is a backstop, not a licence.
  - It is safer, and stronger evidence, to measure prod's data shape read-only and seed a local database to match.
- **A1.16** A bare branch name reads the stale LOCAL ref, with no warning. Fetch and qualify the ref with `origin/`, or resolve the deployment to an exact SHA, then prove containment with is-ancestor.
  - A branch name is not evidence of what shipped, and the working tree may be ahead of both.
- **A1.17** To prove a running process serves the code under test, compare its start time with the newest source mtime, never with commit time or a pid change. Re-check at the end of a long verification.
  - A restart another agent performed is not your control.
- **A1.18** An app may resolve a backend through several independent config paths. Redirect all of them, then confirm the target from the running process (its startup log, or `pg_stat_activity`).
  - Watch for synthesis with a short-circuit (`if (X) return;` before building X from parts). It makes a partial redirect silent.
- **A1.19** A verification is a statement about a tree. After a squash, rebase or amend, compare the `^{tree}` hashes to carry it forward, because re-running the checks proves less.
- **A1.20** `git log --all` spans every ref, so it cannot tell mainline from a throwaway branch. Test membership with `git merge-base --is-ancestor` or `git branch -a --contains`.
- **A1.21** A well-formed artifact at a shared, fixed path may belong to another session. Discriminate by duplicated prose, not duplicated ids, and check the other direction too: the owner is missing it.
- **A1.22** Repeated snapshots overwrite values while the key count keeps growing. Harvest across all snapshots, and diff hashed VALUES, not key sets.

### Verify the shipped artifact
- **A1.23** Verify with the exact code path that ships. Extract the SQL or constant with `ast` and run that string, or drive the real formatter on replayed real data. Never check a retyped copy.
  - Extract BOTH sides of a seam and diff them (the SQL columns against the dataclass fields), reporting missing and extra.
  - Probe a pod read-only by piping the script over stdin (`kubectl exec -i <pod> -- python3 -`). State which layer was verified, and where.
  - Keep the render script committed and side-effect-free by default (sending is opt-in). State the derivation basis of each figure inline.
- **A1.24** For a frontend change, assert on the BUILT bundle with a negative control. A route probe or a source grep cannot see what ships.
  - Grep for a VALUE you introduced (a `data-testid` or copy string), which survives minification. Add a negative control on the same pod, same command shape.
  - Check the string is new with `git grep -c` at the pre-release SHA.
  - Ask whether an inherited check would read differently with your change reverted. If it would not, the check is inert for your change.
  - After retiring a claim, grep the SHIPPED output (the static and server output dirs, not the build cache) for a short retired phrase. Paraphrased inline copies escape every symbol grep.
  - Presence in the bundle is not correctness. A completeness claim needs rendered behaviour.
- **A1.25** Open the produced artifact and check it the way its consumer will. Text extraction, request parameters and a matching shape prove presence, not function.
  - For a PDF, rasterise every page and look. Sweep for an orphaned half-sentence (a lowercase word straight after a full stop).
  - For a download, check four things independently: rows, filename, column order, and any embedded manifest against the button label.
  - When a request names a format, run that format's consumer (its decoder, schema or script) on the output before saying existing work already satisfies it.
- **A1.26** A layer between input and engine (an LLM expansion, a parser, a form) changes the question. Before blaming the vendor or the market, diff the intended request against the bytes sent.
  - A rewrite can drop constraints or add them, and a mandatory form field is a rewrite. A count from a rewritten request measures our search, not the world.
  - An else-fallthrough that fires a specific wrong message is worse than silence, because a claim about the system's own configuration reads as checked.
  - Scope query-syntax guidance to the input whose text reaches the engine unmodified. Pin it with a negative test plus a positive control.
- **A1.27** Script the plan's written procedure against a newly shipped operational mechanism on a scratch instance, and report exit codes. Each can be right alone and combine into a stuck state.
  - Run the suites on a `git archive` export of the integration head. That is where cross-lane breaks show up.
- **A1.28** Validate a handed-over artifact the way the recipient will use it. Run `git add -N` on new files, diff against the freshly fetched HEAD, apply the patch to a clean checkout, and run the gates there.
  - Exclude paths the recipient edited upstream, or your older copy silently reverts their work.
  - Derive a test's title and its fixture from one constant, and add a control AT the threshold.
- **A1.29** Ask which environment a claim is about, and whether your instrument runs in it. `tsc` and node-environment tests cannot see a client bundle that imports a server-only module. Only the build can.
- **A1.30** Derive a data contract by EXECUTING the real consumer on a simulated producer, then diffing against the reference, over a fixture built to hit every branch.
  - The argument type of shared helpers shows which layer aggregates. A field the consumer never reads is not part of the contract, and reference SQL can be the opposite shape.
- **A1.31** Verify UI against data as three legs, SQL, then API JSON, then DOM, and do it for every item. An unexplained gap between your number and the one on screen is the finding, not noise.
  - SQL against JSON differing is a server transform (ask the filter's owner). JSON against DOM differing is a render defect. Check your query params against the route's parser.

## Examples
- A1.14: a "never rebuild" pin lived in a `.claude/` folder one level above the repo and was invisible to any clone.
- A1.15: a boot-time orphan sweep looked unguarded, but the guard sat in the callee, and the `[unmanaged]` log line was the guard itself.
- A1.16: grepping local `master` reported a shipped fix missing, and a stale values file suggested a phantom missing release merge.
- A1.17: a QA pass started at 22:12:59, while two source files had mtimes of 22:14:48 and 22:15:11.
- A1.18: one app built its data pool from one URL and its auth pool from five separate parts.
- A1.19: the lead squashed a verified commit. The tree hashes matched, so the verification carried over with nothing re-run.
- A1.20: a "still on mainline history" claim was false. The commit was only on a throwaway branch.
- A1.21: 26 KB of one project's compact summary was archived into another project's session dir under that session's resume timestamp.
- A1.22: a pointer and the origin of a later-retracted claim survived only in superseded snapshots.
- A1.23: replaying an archived capture through the real eligibility funnel reproduced the documented 3,765 → 96 → 90 → 9 exactly.
- A1.24: after a registry tooltip was fixed, a paraphrased copy on a KPI card still shipped. The build cache gave 24 false hits for the removed phrase.
- A1.25: a resume PDF passed text extraction while its body type was unreadable, and a hidden span had removed a sentence's verb.
- A1.26: a URL parser dropped a 10-company filter and a mandatory title field injected three job titles. The vendor was blamed for both.
- A1.27: hand-applied migrations plus a self-recording one left the new ledger runner refusing both `--baseline` and a plain run.
- A1.28: a handoff patch omitted 6 new files, including an imported module, and targeted a base the lead had already committed over.
- A1.29: `next build` failed on a server-only module while `tsc` was clean and 1,143 node-environment tests passed.
- A1.30: a simulated producer driving the real reducer returned "IDENTICAL, 0 differences" over 5 rows that hit 6 sections.
- A1.31: a "KPI inflated 2.28x" defect came from skipping the API leg. The route already deduplicated and had a comment saying so.
