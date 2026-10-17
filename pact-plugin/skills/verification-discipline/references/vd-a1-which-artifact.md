# VD-A1: which artifact did you check?

Context: read this when a claim rests on something you looked at. The question is whether the
thing you read was the artifact that answers the question, or a description, a model, a stale
ref, another environment or another version of it. The shipped-artifact half, with git refs,
pins, boot paths and running processes, is in
[vd-a1-refs-runtime-and-shipped-artifact.md](vd-a1-refs-runtime-and-shipped-artifact.md), and the "does anything use it" half is
in [vd-a1-existence.md](vd-a1-existence.md).

## Canonical rules

### Ask the artifact, not its description
- **A1.1** Name the artifact whose state answers the question, then read that artifact. A tag, job status, summary, comment, HANDOFF or plan only describes it.
  - Tell: a sentence you could not have written with the artifact open, such as a commit message written from a report instead of from `git diff --cached`.
  - A before/after reading on the SAME artifact turns a sighting into a measurement. The earlier state is the control.
- **A1.2** Classify on the payload, never the envelope. Exit 0, HTTP 200, "success" and `ok: true` report the call. Pair every green with a negative control such as a bogus credential or a nonsense path.
  - An async 200-on-receipt (a webhook with no response node) means delivery, not completion. Ask which event fires any success message a human sees.
  - An endpoint that gives the no-credential control the same answer authenticates nobody.
  - The envelope can be honest while the payload was mangled before it was sent. Read the stored payload back, and sweep for the signature a deletion leaves, such as a double space.
  - A verdict can be right for the wrong reason. When a correction lands, check the mechanism is settled, not only the verdict.
- **A1.3** An error is not proof nothing happened. When a failure is load-bearing (a re-dispatch, an escalation, a defect), spend one call on the effect. Counters can be wrong in both directions.
  - A probe can fail for reasons unrelated to the data: shell substitution, casing, `ensure_ascii` escaping. Compare a COUNT before and after, and print an excerpt of the stored text.
  - A lane that accepts a refusal without probing it quietly re-scopes itself from doing the work to describing it.

### Models, refs, versions and environments
- **A1.4** An ORM schema is a model of the database. Triggers, functions, RLS, grants and constraint bodies are invisible to it and to most drift checkers, so check the migration chain or the pg catalog.
  - Prove it behaviourally: seed an old timestamp, run an UPDATE that never names the column, then read it back.
  - A blanket `updated_at` trigger is not a provenance discriminator. Use a source column or job metadata instead.
  - Write the negative into the docstring. A correction must also reach a claim already sent by chat.
  - Flag a contradicting docstring left by another lane, even after that lane's task is completed.
- **A1.5** An empty git range is ambiguous. `main...HEAD` is empty by construction at the merge base, and a log range comes back empty when the remote ref is missing. Establish state positively.
  - Use `git rev-list --count HEAD..main`, `git merge-base --is-ancestor`, a two-dot diff, or `git rev-parse --verify` on the remote ref.
  - A count that disagrees with what you were told is a signal: reason from the discrepancy toward its cause. Sound early controls do not rescue an unchecked final hop.
- **A1.6** Read what a run executed from its own record (for n8n, the execution's embedded workflowData and version id), never the live config. Confirm the changed step ran on an input that exercises it.
  - Group runs by version before aggregating, and cite the version id beside any "the fix works".
  - Evidence ranks history above current state above the run record. Current state cannot separate "landed then reverted" from "never landed".
  - `created_at` dates an INSERT, never an UPDATE backfill. Before trusting any success count, grep for error-suppressing options and fallback recipients.
  - A messaging API's `ok: true` confirms posting, not delivery, so report resolution and delivery as separate claims.
- **A1.7** Told to mirror a file that is absent from the tree? Recover it from git history (the delete commit's parent) before designing from memory, and flag the absence in the HANDOFF.
- **A1.8** A conflict-free rebase proves the patches applied, not that the symbols they reference survived. Run the suite, or at least import the test module. An import-hygiene PASS never means a file imports.
  - Re-run every sabotage mutation after a rebase, including other agents' mutations. Pin the pre-rebase tip on a safety branch.
- **A1.9** A baseline run from a `git archive` copy re-imports YOUR code when the package is installed editable. Uninstall first. The two pass counts must differ, or the import never moved.
  - A missing-dependency failure is evidence about dependencies only. Probe the subsystem you are about to make a claim about (`pg_isready`).
- **A1.10** A new enum or config row is inert only if the DEPLOYED code understands it. Verify the deployed SHA first, insert the row last, and trace the quiet path where nothing throws.
- **A1.11** A grep guard over source finds only names someone wrote down. To catch drift between the tree and a live system, query the live catalog for the whole class, from something that actually runs.
  - A text guard catches a bad new commit, and a catalog query catches a bad current state. Say which of the two a guard answers.
- **A1.12** Before explaining historical data, date the code, constants and tables to the incident window. A fix commit often introduces the very config and audit table you would reason from.
- **A1.13** A proof covers the state it ran against. An object verified in one environment says nothing about environments rebuilt from migrations. Moving, copying or regenerating the artifacts lapses the proof.
  - Rebuild a database from the migration chain and run the integration tests. Re-hash after any move, worktree switch, codegen run or formatter pass.

## Examples
- A1.1: prod ran an untagged orphan digest that its `1.4.0` tag had moved off. Only `containerStatuses[].imageID` showed it.
- A1.2: one build had four false greens (a public page served 200 with no credentials, a sourced `.env` dropped 5 of 11 keys at exit 0, GraphQL put errors inside a 200, an MCP call returned empty strings).
- A1.3: a job logged 257 of 260 items as failed while 254 rows were written, and in another run 924 were reported as succeeded against 848 written.
- A1.4: "maintained only by explicit writers" was false. A BEFORE UPDATE trigger in migration 0001 stamps every update.
- A1.5: the three-dot diff was empty while the two-dot diff showed 5 insertions and 190 deletions. The branch was 3 commits behind the fix.
- A1.6: 94 runs in one day spanned 4 workflow versions, and 4 of 6 edited nodes never executed in the run cited as proof.
- A1.7: a "mirror this workflow" brief named a file a cleanup commit had deleted. Its parent commit held the real node topology.
- A1.8: 4 commits rebased with no conflicts, and the suite then failed at collection on a deleted helper.
- A1.9: a pristine-baseline run agreed with the modified code because the editable finder resolved to the worktree.
- A1.10: detector enrolment rows were about to be inserted while their guard existed only on an undeployed branch.
- A1.11: a catalog query for SECURITY DEFINER functions found six, one of which no task, census or guard had ever named.
- A1.12: the cadence constants and the audit table used to explain a 14-day run of zeros were created by the fix commit two months later.
- A1.13: a unique index existed in prod through a push tool but in no migration, so every rebuilt environment lacked the upsert key.
