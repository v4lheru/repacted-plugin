# VD-F: proving a deploy or a write landed

Context: read this before you say "deployed", "live", "shipped", "merged", "written", "fixed in prod"
or "it damaged the data". Each of these is proven only by the running thing or the destination
itself, never by the process that produced it.
This file carries the general
rules.

## Canonical rules
- **F.1** Prove "deployed" from the running workload's own self-report. A green pipeline, "install succeeded", a tag or a passing health check are all compatible with the old image still running.
  - Compare the pinned manifest digest with the running pods' `imageID` field, not `image` (the config digest, which never equals the pin). Filter to Running pods and require matched equals total.
  - Get two independent witnesses: the app's own version endpoint, and a rollout condition (NewReplicaSetAvailable) timestamped by THIS deploy. A pod that "started today" may be a reschedule.
  - Until the pin matches, STOP before any post-deploy check that assumes the new code. An instrument pointed at the old code passes honestly.
- **F.2** Before pressing install, the pipeline's diff must SHOW the change, or nothing applies. A top-level CI block (`workflow:`, `include:`, variables) REPLACES an included component's, not merges.
- **F.3** Resolve a digest from the REGISTRY at pin time, never from a build log, and fail if a tag resolves to more than one digest. Report prod as a digest plus the tag it was pinned as, because tags move.
  - A wrong but real digest turns every check green while prod runs the wrong bytes. Never "helpfully" repoint an untagged pinned digest at a tag.
- **F.4** Before answering "is it deployed?", enumerate every deploy target the repo feeds (web, worker, cron, function) and name the one you checked.
  - Distinct image digests prove a rebuild, not a source change. When a deploy record carries no commit SHA, the base you derive from timestamps is a derivation, so label it that way.
  - Prefer behavioural evidence from the running code, such as a column only the new code writes, over any claim by the system that deployed it.
- **F.5** A zero from grepping a minified bundle is evidence about that symbol in that build only. Run a control grep for something known to be present, or grep a copy string, which survives minification.
- **F.6** A CLI's success line reports what it REQUESTED. After a merge command, assert on the repository: is-ancestor against the fetched target, or the MR read back as merged with a merge commit.
- **F.7** Before alleging a system wrote or damaged data, read the destination state or the actual write payload, never the trigger or an intermediate. A transformation step severs input-to-output inference.
  - Never write `TARGET ← "text"` arrows for trigger data. Escalation raises the evidence bar, and your own collected artifacts are the first place to look.
  - Say whether a retraction was self-caught or user-caught. One is the process working, and the other is the process failing.
- **F.8** Ask whether the symptom would clear on its own if the fix did nothing (a quota reset, a TTL, a cache expiry, a cron cycle, a month boundary). If it would, recovery in production is not evidence.
  - Validate with checks that do not depend on the clock: a test that fails against today's code, a negative control, a mutation in both directions. Score "can we prove it?" at dispatch.
  - Re-verify the symptom right before the fix lands. Never tell a stakeholder "fixed, the numbers are back" when a reset could explain it.
- **F.9** A corrupt output is the only evidence the corruption happened. Copy it to a second path before regenerating. Check the artifact's STRUCTURE (one `+++` per file in a patch), not the trigger.
  - A failure you cannot reproduce is not an all-clear. Four negative probes do not cancel one positive observation.
- **F.10** A state claim is a fact with a timestamp. Re-measure right before the IRREVERSIBLE step, not before announcing it. Typing a state claim ("the tree is clean") is the cue to re-measure.
- **F.11** Rollout history survives in the ReplicaSet list long after the fact. A missing record is not evidence of failure. A CI image tag may use more hex characters than the short SHA it looks like.
- **F.12** For a local process, compare the newest source mtime with the process start (A1.17 in [vd-a1-which-artifact.md](vd-a1-which-artifact.md)). A restart that someone else performed is not your control.
- **F.13** When no agent can view the deployed UI (it sits behind SSO or a VPN), say so BEFORE the rollout and ask the user for the UI check. Until the user has looked, the message says "deployed, UI not checked by anyone", never "live and working".
  - The API, the data and a local render with mock data prove the API, the data and the local build. A redirect to the login page proves only that the login gate answers.

## Examples
- F.1: helm printed `STATUS: deployed`, but the pods ran the previous image. A post-deploy check had already passed against the old code (verified 2026-09-22).
- F.1: comparing the pin against `.image` failed 100% on a healthy deploy, because `.image` is the config digest (verified 2026-09-22, Kubernetes pod status).
- F.2: a new top-level `workflow:` silently removed the included helm component's prod values path. The MR pipeline stayed green against base values.
- F.3: a build log held seven unlabelled sha256 values, and a later rebuild moved the `1.7.0` tag off the digest that prod was running.
- F.4: the web service showed health 200 while its cron-poller sibling had not built for 12 days.
- F.5: two symbols known to be present returned 0 in one production bundle, while others from the same code hit.
- F.6: `glab mr merge` printed "ok merged" for two MRs. One was merged, the other had only queued an auto-merge with state=opened (verified 2026-09-22).
- F.7: "561 ticket descriptions rewritten" was built from trigger texts. An LLM sat between trigger and write, and the extractor's unread body column already refuted it.
- F.8: a monthly API quota ran out in about 11 days, so data would return at the month reset whether or not the fix worked.
- F.9: an rtk-wrapped `git diff > file.patch` wrote a summary with a `[206 more lines]` marker. Regenerating over the same path destroyed the only evidence.
- F.10: a line number, a clean pre-commit index and "the tree is clean" were each true when measured and false minutes later in a shared worktree.
- F.11: ReplicaSet creation times rebuilt a six-deploy timeline 54 days later.
- F.13: a prod frontend answered 307 to every agent (SSO). "It is live" went out on the API, the data and a mock render, and the user's five-minute look then found the main defect (observed 2026-10-02).
