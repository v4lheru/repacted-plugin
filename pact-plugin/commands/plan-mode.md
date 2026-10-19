---
description: Ask several agents for a plan before writing any code
argument-hint: [e.g., add user authentication with JWT]
---
Create a comprehensive implementation plan for: $ARGUMENTS

**This is PLANNING ONLY** — no code changes, no implementation branches, no implementation. For variety 11-14 (4-10: comPACT or orchestrate; 15-16: a research spike). On demand: [consultation-and-synthesis.md](../reference/plan-mode/consultation-and-synthesis.md) (Phases 1-2) and [plan-document.md](../reference/plan-mode/plan-document.md) (Phase 3).

## Task Hierarchy

Planning task "Plan: {feature}" (`in_progress`) while one consultation task per specialist (from `repact dispatch`) runs; completed with `metadata.artifact` = the plan path.

## Your Workflow

### Phase 0: Orchestrator Analysis

Scope, phases, complexity, specialists (preparer and test-engineer always; others when their domain is touched), and a 3-5 word kebab-case feature slug.

### Phase 1: Parallel Specialist Consultation

**Session team**: `{team_name}` is provisioned automatically by the platform; consultants go into it, never into a team you create.

Dispatch relevant specialists **in parallel** up to the agent cap and the rest in sequence as slots free (the cap outranks "always"), each in **planning-only mode**, with this template plus the per-domain questions from the reference:

```
PLANNING CONSULTATION ONLY — No implementation, no code changes.

Task: {task description}

As the {role} specialist, provide your planning perspective:

1. SCOPE IN YOUR DOMAIN
   - What work is needed in your area of expertise?
   - What's the estimated effort (Low/Medium/High)?

2. DEPENDENCIES & INTERFACES
   - What dependencies exist with other domains?
   - What interfaces or contracts need definition?
   - What do you need from other specialists?

3. KEY DECISIONS & TRADE-OFFS
   - What are the important decisions in your domain?
   - What are the options and trade-offs?
   - What's your recommendation?

4. RISKS & CONCERNS
   - What could go wrong?
   - What unknowns need investigation?
   - What assumptions are you making?

5. RECOMMENDED APPROACH
   - What's your suggested approach?
   - What sequence of steps do you recommend?
   - What should be done first?

Output your analysis with clear headers matching the 5 sections above.
Do NOT implement anything — planning consultation only.
```

A missing response is data: flag it under "Limitations" or "Open Questions"; do NOT re-invoke.

**Dispatch each consultant**: write the template above (plus its per-domain questions) to a mission file, then `repact dispatch --name {specialist-name} --agent pact-{specialist-type} --subject "{specialist}: plan consultation for {feature}" --kind docs --worktree <a linked worktree of the repo> --brief-file <mission file> --variety N,S,U,R`, then the `Agent(...)` call it prints (persona §11). Consultants only read the worktree. From the main checkout, give the first consultant `--branch plan/{feature-slug}` instead and the rest `--worktree` on the tree it made. The op writes the task, the brief and the `variety_assessed` event; the teachback restates the consultation scope.

With several consultants, dispatch each, then all their `Agent` calls in one response. Accept each consultation with `repact accept <id>`; one that misses the brief gets `repact reject <id> --reason "<text>"`; read a stored HANDOFF with `repact inspect <id> --key handoff`.

### Phase 2: Orchestrator Synthesis

Agreements; conflicts as Minor (you choose) / Major (user decides) / Blocking (partial plan marked BLOCKED); sequencing; cross-cutting concerns; roadmap with Phase Requirements (any incompleteness signal = REQUIRED); risks. Validate: 2+ meaningful inputs, no open blocking conflict.

### Synthesis Agreement Verification

`SendMessage` each contributor your synthesis of their input; on a correction update and re-verify everyone it touches.

### Phase 3: Plan Output

Save the plan to `docs/plans/{feature-slug}-plan.md` using the structure in [plan-document.md](../reference/plan-mode/plan-document.md) (load it now; it also covers an existing plan for the same slug). **Plan-doc self-check**: confirm the file exists and is non-empty before continuing; a missing plan is a failure to investigate. Then complete the planning task with `metadata={"artifact": "docs/plans/{feature-slug}-plan.md", "summary": "Planning complete. Awaiting user approval."}`.

**Emit `artifact_paths`** for the plan doc (the lead's own product, so its absolute path is known directly):

```bash
"{plugin_root}/bin/repact-journal-write" --site plan-mode.md --type artifact_paths --session-dir '{session_dir}' --stdin <<'JSON'
{"workflow": "plan-mode", "feature": "{feature-slug}", "paths": ["{absolute_plan_doc_path}"]}
JSON
```

**Trigger a harvest** (plan-mode has no later boundary that would): Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).

```
TaskCreate(subject="secretary: harvest plan-mode consultation",
  description="Harvest HANDOFFs and artifact_paths for team {team_name}. Follow the Standard Harvest workflow in your pact-handoff-harvest skill, resolving artifact_paths events to read the plan doc and any consultation artifacts off disk. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

### Phase 4: Present and Resolve

Summarize (complexity, scope). Resolve each "Require User Decision" item with `AskUserQuestion`, then re-derive Phase Requirements. **Do NOT exit plan mode** while any remain; **do NOT implement**: after approval the user runs `/repacted:orchestrate`.
