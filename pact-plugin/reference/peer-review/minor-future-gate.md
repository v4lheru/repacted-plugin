# /repacted:peer-review: minor and future findings

> Loaded on demand from [peer-review.md](../../commands/peer-review.md) at step 4 whenever the findings table has any Minor or Future row. It holds the greedy-fix mode and the default per-finding gate (Steps A to C), moved here verbatim. "Step 5" below is peer-review's merge-readiness step; "the Blocking branch above" is peer-review's step 4 Blocking branch; Verify-Only Re-Review is in peer-review.md.

## Minor + Future

     **Greedy-fix mode** <!-- ANCHOR-STABLE: GREEDY-CONFIG-REF --> — consult the injected **PACT Runtime Config** block (resolved into the orchestrator's context at session start):
       - **`PR greedy-fix: OFF`, or the block is absent** → use the default per-finding gate (**Step A → Step B → Step C** below), UNCHANGED. Absence of the block means every option is at its default (OFF) — this is the behavior for every consumer who has not opted in.
       - **`PR greedy-fix: ON`** → follow the **Greedy remediation path** immediately below, which PREEMPTS Step A's "review minors?" gate (do not ask it).

     **Greedy remediation path** <!-- ANCHOR-STABLE: GREEDY-PATH --> (only when `PR greedy-fix: ON`):

       **What greedy auto-authorizes — and ONLY this** <!-- ANCHOR-STABLE: GREEDY-REVERSIBILITY -->: exactly one action class — **reversible, git-tracked auto-delegation of fixes**. Greedy batches findings into the same specialist remediation path the **Blocking** branch above already uses (`/repacted:comPACT` / `/repacted:orchestrate`); every fix lands as a revertible commit on the working branch. Greedy only changes WHO decides to dispatch a reversible fix — the orchestrator, instead of a per-finding user prompt.

       **Never auto-authorized — these stay user-gated**: **merge, force push, branch deletion, PR close, any other push, publish, or any other irreversible / outward-facing / destructive action**. Greedy NEVER merges, closes, or pushes on the user's behalf; reaching merge readiness (step 5) and the merge checkpoint itself are UNCHANGED — the user still approves them explicitly. Greedy scoped to reversible delegation is precisely what keeps it safe: a directive that appeared to auto-authorize an irreversible action would be correctly refused wholesale.

       **Hard SACROSANCT carve-out** <!-- ANCHOR-STABLE: GREEDY-SACROSANCT -->: greedy NEVER overrides a SACROSANCT rule or an established PACT design pattern to satisfy a finding. This exclusion is ABSOLUTE — stronger than, and separate from, the "widely out of scope" soft exclusion below: a fix that is in-scope, cheap, and reversible is STILL excluded if applying it would violate SACROSANCT or an explicit design pattern.

       **Behavior when `PR greedy-fix: ON`:**
       1. Batch **all** minor findings and all future findings into the existing remediation path — the same `/repacted:comPACT`-vs-`/repacted:orchestrate` scope-selection the Blocking branch uses above — and dispatch the reversible fixes.
       2. **Exclude** a finding from the batch when it is any of: **widely out of scope**, **extremely expansive** (would balloon the diff well beyond the PR's stated scope), or **design-pattern- or SACROSANCT-violating**.
       3. **Surface the exclusions** as a single **end-of-run summary — one line per excluded finding with its rationale** (out-of-scope / expansive / SACROSANCT-or-pattern). Do NOT convert exclusions into per-finding `AskUserQuestion` prompts, and do NOT silently drop them — surfacing preempts the per-finding gate without losing anything. Excluded out-of-scope findings remain eligible for GitHub-issue tracking.
       4. **Inherit existing safety, UNCHANGED**: dispatched fixes re-run the verify-only re-review, and the 2-cycle → `/repacted:imPACT` termination applies exactly as for the Blocking branch.

     **No-ask branch** (default path only; checked before Step A): when the user's standing rules say not to ask about reversible choices, or the user is away, do not put Step A or Step C to them. Fix each minor that is small and inside the reviewed diff, through the Blocking branch's remediation path and its verify-only re-review. File each future item as a project task (`repact-task add "<title>" --priority <P0-P3>`; a GitHub issue when the repo keeps no task log). Report both lists with the findings table. Ask (Step A to Step C) only otherwise: for a minor that is large or reaches beyond the reviewed diff. Merge, close and push stay with the user.

     **Step A — Initial Gate Question** (Yes/No only) — **default path; preempted when `PR greedy-fix: ON`** (see Greedy-fix mode above):

     **Pre-form a gate-level recommendation BEFORE constructing the `AskUserQuestion` call.** The gate recommendation is BINARY (Yes or No across the entire minor+future population) — distinct from Step C's per-finding recommendations. Apply these POPULATION-LEVEL criteria:
       - **Aggregate Substance** — Are the minors/futures substantive (convention violations, latent risks, real ergonomic wins) or cosmetic-only (whitespace, naming preference, stylistic nits)? Substantive → recommend Yes. Cosmetic-only → recommend No.
       - **PR size + scope-tightness signals from the session** — If the user has expressed scope-tight preferences earlier and findings are cosmetic, recommend No. If PR is small and findings are substantive, recommend Yes.
       - **Time/cost of remediation** — If minors batch cleanly into one remediation cycle (Yes path is cheap), lean Yes. If each minor would require its own dispatch + verify cycle (Yes path is expensive) AND findings are not high-substance, lean No.

     **Bake the recommendation into the option set** using the same `(recommended)` suffix + one-line rationale convention as Step C (see C.2). When no clear recommendation exists, apply the C.4 carve-out (mark the most-defensible option as recommended with uncertainty named in the rationale, OR omit the suffix and state "No clear recommendation; user judgment" in the question prose).

     **Worked example.** Concrete rationale shape for 2 substantive minors on a small PR: `Yes (recommended) — Two substantive convention-alignment minors; small PR, low remediation cost`. See C.3 for the full option-block convention.

     - Use `AskUserQuestion` tool: "Would you like to review the minor and future recommendations?"
       - Options: **Yes** (review each item) / **No** (skip to merge readiness)
     - If **No**: Skip to step 5 directly
     - If **Yes**: Continue to Step B

     **Step B — Preemptive Context Gathering**:
     - Before asking per-recommendation questions, gather and present context for ALL minor and future recommendations
     - For each recommendation, provide:
       - Why it matters (impact on code quality, maintainability, security, performance)
       - What the change would involve (scope, affected areas)
       - Trade-offs of addressing vs. not addressing
     - Keep each entry concise (2-3 sentences per bullet).
     - Present as a formatted list (one entry per recommendation) so user can review all context at once.
     - After presenting all context, proceed to Step C.

     **Step C — Per-Recommendation Questions** (after context presented):

     **C.1 — Pre-form a recommendation BEFORE constructing the `AskUserQuestion` call.** <!-- ANCHOR-STABLE: C.1 --> For every minor and future finding, you MUST decide which option you would recommend BEFORE you call `AskUserQuestion`. Asking the user "what do you recommend?" or constructing an option set where no option carries a `(recommended)` suffix is a protocol violation — the user-facing checkpoint surfaces your prior, it does not elicit one. Apply these criteria when forming the recommendation:
       - **Scope** — Does fixing now stay inside this PR's stated scope, or does it expand the diff into adjacent concerns? In-scope → lean toward fix now; out-of-scope → lean toward defer/issue.
       - **Convention alignment** — Does the finding flag a divergence from an established project convention (pinned rules, repeated patterns, audit anchors)? Convention violation → lean toward fix now even if minor.
       - **Risk** — What is the cost of NOT addressing? Latent correctness, security, or data-integrity risk → lean toward fix now; pure ergonomics with no failure mode → lean toward defer.
       - **Substance** — Is the change a real behavioral or contract improvement, or a stylistic/cosmetic preference? Substantive → lean toward fix now; cosmetic → lean toward defer or skip.

     **C.2 — Bake the recommendation into the option set.** <!-- ANCHOR-STABLE: C.2 --> The recommended option's label MUST carry the `(recommended)` suffix (plugin-wide convention; precedent in `comPACT.md`). The option's description MUST carry a one-line rationale grounded in the criteria above. Exactly ONE option per question carries the suffix.

     - For each **minor** recommendation, ask "Address [recommendation] now?" with options:
       - **Yes** — Fix it in this PR
       - **No** — Skip for now
       - **More context** — Get additional details (if more detail is needed)
     - For each **future** recommendation, ask "What would you like to do with [recommendation]?" with options:
       - **Create GitHub issue** — Track for future work
       - **Skip** — Don't track or address
       - **Address now** — Fix it in this PR
       - **More context** — Get additional details (if more detail is needed)
     - > **Tool mapping**: Every option listed above MUST appear as a named `option` in the `AskUserQuestion` call. For minor recommendations: **Yes**, **No**, **More context**. For future recommendations: **Create GitHub issue**, **Skip**, **Address now**, **More context**. Do not omit any option and rely on the tool's built-in "Other" freeform input — each is a first-class option, not a fallback. Apply the `(recommended)` suffix + rationale per C.2 to exactly one option per question.

     **C.3 — Worked example.** <!-- ANCHOR-STABLE: C.3 --> Concrete pattern for a minor finding "Inconsistent error-log prefix across two helpers":

     > **Example**:
     > - **Yes (recommended)** — In-scope convention alignment; 2-line diff, no behavioral risk
     > - **No** — Defer; cosmetic-only inconsistency
     > - **More context** — Get additional details

     **C.4 — When no clear recommendation exists.** <!-- ANCHOR-STABLE: C.4 --> You MUST still construct the question with the full option set — never short-circuit by skipping the question. Two acceptable shapes:
       - Mark the **most-defensible** option as `(recommended)` and write a description that names the uncertainty (e.g., "Tentative — scope is on the boundary; lean defer if PR is already large").
       - OR omit the `(recommended)` suffix entirely and state "No clear recommendation; user judgment" in the question prose. Use this shape sparingly; it shifts cognitive load back to the user and should appear only when the criteria genuinely don't converge.

     **C.5 — Edge cases.** <!-- ANCHOR-STABLE: C.5 -->
       - **Conflict with earlier session signals** — If your pre-formed recommendation contradicts a stance the user took earlier in the session (e.g., user previously said "keep this PR tight" and you now recommend "fix now"), surface the conflict explicitly in the option description (e.g., "Recommend fix now despite earlier scope-tight signal — convention violation tips balance"). Do not silently invert the signal.
       - **Batching when shape aligns** — When multiple findings share recommendation shape (same recommended option, same rationale class, comparable scope/cost), batch them into a single `AskUserQuestion` question (e.g., "Address findings A, B, C now?") rather than asking three near-identical questions. Only batch when the rationale legitimately collapses; do NOT batch to save tool calls if findings diverge in cost or convention weight.
     - Note: Tool supports 2-4 options per question and 1-4 questions per call. If >4 recommendations exist, make multiple `AskUserQuestion` calls to cover all items.
       - **Handling "More context" responses**:
         - When user selects "More context", provide deeper explanation beyond the preemptive context (e.g., implementation specifics, examples, related patterns)
         - After providing additional context, re-ask the same question for that specific recommendation (without the "More context" option)
         - Handle inline: provide context immediately, get the answer, then continue to the next recommendation
       - **Collect all answers first**, then batch work:
         - Group all minor=Yes items AND future="Address now" items → Select workflow based on combined scope:
           - Workflow by combined scope as in [peer-review.md](../../commands/peer-review.md) (independent → comPACT, concurrently; shared files or PREPARE/ARCHITECT → orchestrate)
         - Group all future="Create GitHub issue" items → Create GitHub issues
       - If any items fixed (minor or future addressed now) → re-run review to verify fixes only (see Verify-Only Re-Review above)

