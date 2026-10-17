---
name: verification-discipline
description: Use BEFORE you report or rely on a finding, number, count, absence ("none", "never", "no callers"), "tests pass", "fixed", "deployed", a relayed claim, or a guard, test or monitor you trust or write. Router of 8 questions with references on artifacts vs descriptions, stale observations, tests that cannot fail, fail-open controls, denominators, provenance and deploy proof.
---

# Verification discipline

Context: the most expensive defect in agent work is a claim reported or relied on that
was never actually checked. Most such claims come from a well-formed, truthful-looking reading
of the wrong thing. Put these questions to a claim before it leaves you. Each sub-question
names the one reference that answers it: open only that file.

## Workflow
1. Name the claim with its scope and as-of time: what, over which population, observed when.
   If you cannot fill in all three, you have not measured it yet.
2. Ask the artifact, not its description (a tag, job status, summary, comment, HANDOFF, model,
   stale ref).
   - [A description, model, version or env?](references/vd-a1-which-artifact.md)
   - [A ref, pin, process, or the bytes that ship?](references/vd-a1-refs-runtime-and-shipped-artifact.md)
   - [Does anything read, run or reach it?](references/vd-a1-existence.md)
   - [Does the guard run; scan code or prose?](references/vd-a1-control-claims-and-scans.md)
3. Ask what the check would print if the thing were broken; "the same" means it is no check.
   ALWAYS run a positive AND a negative control in the same call shape before you report a
   quiet result (a zero, "none", an empty diff, no prompt).
   - [What did this search answer, over what scope?](references/vd-a2-absence-and-search.md)
   - [My pattern's miss, or missing data?](references/vd-a2-search-misses-and-absence-diagnosis.md)
   - [Could this instrument show a positive?](references/vd-a2-instruments-and-controls.md)
   - [A zero, a tidy number, a new instrument?](references/vd-a2-calibration-and-zero-signals.md)
   - [What did this status or probe answer?](references/vd-a2-probes-and-auth.md)
   - [The real limit, request and effect?](references/vd-a2-api-limits-and-envelopes.md)
4. For a test or guard, ask why it passes, and whether your sabotage landed in what the tool
   actually reads.
   - [Have I seen it fail?](references/vd-c1a-prove-it-can-fail.md)
   - [What can this check see?](references/vd-c1a-what-a-check-can-see.md)
   - [Is the mutation run honest?](references/vd-c1a-mutation-harness.md)
   - [Reproduced? Which test killed it?](references/vd-c1a-reproduce-and-kill-attribution.md)
   - [What does a survivor mean?](references/vd-c1a-survivors-and-direction.md)
   - [A constraint, clock, copy or UI gate?](references/vd-c1a-gates-and-guarantees.md)
   - [Can it only agree with me?](references/vd-c1a-falsify-dont-confirm.md)
   - [Will this measurement settle it?](references/vd-c1a-discriminating-measurements.md)
   - [Why does this assertion pass?](references/vd-c1b-assertions-and-fixtures.md)
   - [Which expected value?](references/vd-c1b-assertion-targets.md)
   - [Where should the check point?](references/vd-c1b-where-the-risk-lives.md)
   - [Did it run the branch it names?](references/vd-c1b-reach-and-mechanisms.md)
   - [Can the fixture tell right from wrong?](references/vd-c1b-fixture-realism.md)
   - [Do the fixtures cover the states?](references/vd-c1b-fixture-coverage.md)
   - [A copy, a mock, an unjoined seam?](references/vd-c1b-copies-mocks-and-seams.md)
   - [A harness or double; a fake red?](references/vd-c1-harnesses-doubles-and-quirks.md)
   - [Did the runner run what I think?](references/vd-c1c-runners-and-toolchain.md)
   - [Green only on my machine?](references/vd-c1c-clean-state-and-checkers.md)
5. For a control, default or degrade path, ask what it does on unknown, empty or failed input,
   and whether it is armed where it runs.
   - [Unknown, empty or failed input?](references/vd-c2a-fail-open-and-unknown-states.md)
   - [A sentinel, fallback or lookup?](references/vd-c2a-projections-fallbacks-and-lookups.md)
   - [More than two honest outcomes?](references/vd-c2a-outcomes-and-status.md)
   - [Deletes, reconciles or retries?](references/vd-c2a-destructive-sync-and-idempotency.md)
   - [A job or recorded state?](references/vd-c2a-jobs-and-recorded-state.md)
   - [Unreachable or guarded; caps, nouns?](references/vd-c2b-controls-and-construction.md)
   - [Can a change elsewhere silence it?](references/vd-c2b-construction-patterns.md)
   - [Do two surfaces agree on one fact?](references/vd-c2b-contracts-and-derivation.md)
   - [Is it armed, and loud?](references/vd-c2b-armed-and-loud.md)
   - [Has the monitor ever fired?](references/vd-c2b-monitors-and-witnesses.md)
   - [Keyed on the harm or a proxy?](references/vd-c2b-guard-scope-and-proxies.md)
   - [Can the detector's input change form?](references/vd-c2b-detectors-and-identity.md)
6. Ask when this was observed and whether the source has moved since, and whether the reason
   is still true or only the conclusion.
   - [Is this observation still current?](references/vd-b1-snapshots-and-corrections.md)
   - [A journal, clearance or record?](references/vd-b1-journals-records-and-decay.md)
   - [Did the correction reach every carrier?](references/vd-b1-corrections-propagate.md)
   - [Is this spec or ruling still true?](references/vd-b2-claims-comments-guards.md)
   - [Is this comment still true?](references/vd-b2-comments-that-decay.md)
   - [Does the citation or name hold?](references/vd-b2-citations-and-names.md)
   - [Changing code near a guard?](references/vd-b2-guard-maintenance.md)
7. Ask what the number is a count OF: which window, which population, which unit, and whether
   the label says so.
   - [What is this number a count of?](references/vd-d1-denominators-and-frames.md)
   - [Sum, dedupe or bucket?](references/vd-d1-aggregation-and-rollups.md)
   - [Unit, label, ID or version?](references/vd-d1-units-labels-and-provenance.md)
   - [Which directory, clock or baseline?](references/vd-d1-frames-and-instruments.md)
   - [A metric, threshold or sample?](references/vd-d1-metrics-and-thresholds.md)
   - [A figure that looks wrong?](references/vd-d1-diagnosing-a-number.md)
   - [Which window or period?](references/vd-d2-time-identity-and-zeros.md)
   - [Same set, key or matcher?](references/vd-d2-matching-and-identity.md)
   - [A real zero, or not measured?](references/vd-d2-fabricated-zeros.md)
8. Ask whether two agreeing routes could share one origin, and whether the claim kept its label
   (measured, inherited, inferred) across the relay.
   - [Where did this claim come from?](references/vd-e-relay-and-reporting.md)
   - [Who did it; which record?](references/vd-e-attribution-and-records.md)
   - [Can the recipient weigh it?](references/vd-e-evidence-in-reports.md)
   - ["Done", "fixed", "covered"?](references/vd-e-completion-claims.md)
   - [A figure or a severity?](references/vd-e-figures-and-severity.md)
   - [What will the reader conclude?](references/vd-e-copy-and-comments.md)
   - [Where two lanes or contracts meet?](references/vd-e-joins-and-contracts.md)
9. For "deployed", "merged", "written" or "done", NEVER accept an envelope (a green job,
   "install succeeded", ok: true). Prove it from the running thing's own self-report, or from
   the destination: [deploy and write proof](references/vd-f-deploy-and-write-proof.md).
10. Report with the label (MEASURED, INFERRED, RELAYED) and the scope in the same sentence as
    the claim. If a check you could run cheaply would settle it, run it instead of writing
    "unverified".

Each reference opens with "Canonical rules" (numbered ids), then "Examples".

## Related skills
- repacted:shell-tooling-traps covers how to obtain a count or exit code (`tcount`, rtk, pipes).
  This skill covers what the count means.
- repacted:concurrent-agent-git covers verifying the committed tree in a shared worktree.
- repacted:pact-testing-strategies covers generic test design.

## Refine
At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and
record what it changed.
