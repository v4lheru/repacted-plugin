# /repacted:peer-review: reviewer selection and review detail

> Loaded on demand from [peer-review.md](../../commands/peer-review.md): the task-tree example, the verify-only comparison, the reviewer-selection heuristics, reviewer teachback, output style and signal handling.

**Example structure:**
```
[Review] "Review: user authentication"
├── [Agent] "architect: design review"
├── [Agent] "test-engineer: coverage review"
├── [Agent] "backend-coder: implementation review"
├── [Agent] "security-engineer: security review" (conditional)
├── [Agent] "qa-engineer: runtime verification" (conditional)
├── [Remediation] (dynamic, for major issues)
│   └── [Agent] "fix: auth vulnerability"
├── [Step] "User: review minor issues"
├── [Remediation] (dynamic, for "fix now" minors)
│   └── [Agent] "fix: input validation"
└── [Approval] "Awaiting merge decision"
```

### Verify-Only Re-Review

After remediation fixes are applied, re-review is **verify-only** — not a fresh review:

| Aspect | Verify-Only | Full Review |
|--------|-------------|-------------|
| **Scope** | Only files changed during remediation | Full PR diff |
| **Depth** | Confirm each finding was addressed | Fresh architectural analysis |
| **Agent** | The reviewer who found the issues (one reviewer and a small diff: the lead may read it) | One reviewer, or several in parallel |
| **Format** | Checklist: finding → resolved / not resolved / new issue | Full review with severity ratings |
| **Duration** | Significantly faster than initial review | Full review cycle |

> **New issue verification**: When a verify-only reviewer reports a "new issue" not in the original checklist, verify the finding against the actual file state before dispatching a fix agent. Verify-only reviewers see a narrow remediation diff and may flag issues that were already addressed in the original code or earlier commits. Check the file directly (`grep` or `Read`) before treating it as actionable.

## Reviewer selection

The default is **ONE reviewer**, of the type that fits the PR's main risk. Dispatch several in parallel only when the user asks or the risk score is 3 or more.

Standard combination for a multi-reviewer review:
- **pact-architect**: Design coherence, architectural patterns, interface contracts, separation of concerns
- **pact-test-engineer**: Test coverage, testability, performance implications, edge cases
- **Domain specialist coder** (selected below): Implementation quality specific to the domain

Conditional reviewers (included when relevant):
- **pact-security-engineer**: When PR touches auth/authorization, user input handling, API endpoints, data serialization, or crypto/token code. File-pattern heuristics:
  - Path segments: `auth`, `login`, `password`, `token`, `encrypt`, `crypto`, `session`, `permission`, `middleware/auth`, `security`
  - Input handling: files processing user input, form data, request bodies, query parameters
  - Config: files with CORS settings, CSP headers, security headers, secrets management
- **pact-qa-engineer**: When project has a runnable dev server and PR includes UI or user-facing changes

Select the domain coder based on PR focus:
- Frontend changes → **pact-frontend-coder** (UI implementation quality, accessibility, state management)
- Backend changes → **pact-backend-coder** (Server-side implementation quality, API design, error handling)
- Database changes → **pact-database-engineer** (Query efficiency, schema design, data integrity)
- Infrastructure changes → **pact-devops-engineer** (CI/CD quality, Docker best practices, script safety)
- Multiple domains → Coder for domain with most significant changes, or all relevant domain coders if changes are equally significant

### Reviewer Teachback

Each reviewer should state their understanding of the PR's intent before diving into review. This catches cases where a reviewer misunderstands the purpose and produces irrelevant findings.

**Mechanism**: The reviewer's understanding of the PR intent is captured by the **Teachback-Gated Dispatch** above — the reviewer's Task A teachback states the review angle and understanding of intent, is submitted as `metadata.teachback_submit`, and the reviewer idles on `awaiting_lead_completion` until the team-lead accepts (blocking). See [teammate-teachback.md](../../skills/teammate/references/teammate-teachback.md) for the mechanism.

---

## Output Conciseness

See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.

**Default: Concise output.** User sees synthesis, not each reviewer's full output restated.

| Internal (don't show) | External (show) |
|----------------------|-----------------|
| Each reviewer's raw output | Recommendations table + `See docs/review/` |
| Reviewer selection reasoning | `Invoking architect + test engineer + backend coder` |
| Agreement/conflict analysis details | `Ready to merge` or `Changes requested: [specifics]` |

**User can always ask** for full reviewer output (e.g., "What did the architect say?" or "Show me all findings").

| Verbose (avoid) | Concise (prefer) |
|-----------------|------------------|
| "The architect found X, the test engineer found Y..." | Consolidated summary in `docs/review/` |
| "Let me synthesize the findings from all reviewers..." | (just do it, show result) |

## Signal Monitoring

Monitor for blocker and algedonic signals (`SendMessage` from teammates; `TaskList` blocker metadata or stalled status; after each reviewer or remediation dispatch and on any unexpected stoppage) and handle each as in [orchestrate §Signal Monitoring](../orchestrate/assessment-and-signals.md#signal-monitoring): a signal task, `addBlockedBy` on the agent's task, ALERT blocks the phase task and HALT the feature task, the user resolves, completion unblocks.
