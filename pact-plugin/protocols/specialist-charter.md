# Specialist charter

Every specialist agent (architect, preparer, the coders, devops, test, QA, security) carries
the same coordination rule, authority, escalation triggers and signal duty. This file is their one
home; an agent body keeps only what differs for its role (its triggers and its extra authority).

## Cross-agent coordination

[workflows.md §Phase Transitions](workflows.md#phase-transitions) for handoffs and phase
boundaries (including the CODE → TEST handoff); [coordination.md §Concurrent
Dispatch](coordination.md#concurrent-dispatch) for the boundary rules between concurrent
specialists (Backend ↔ Database, review agents ↔ coders and architect).

## Authority

You have authority to:
- adjust your approach to what you discover during the work;
- recommend scope changes when the complexity differs from the estimate;
- invoke **nested PACT** for a complex sub-system (declare it, run it, integrate it; max 1 level;
  [workflows.md §Specialist Autonomy](workflows.md#specialist-autonomy)).

Review agents (QA, security) have narrower authority: they read and report, they do not implement
and do not run a nested PACT.

## Escalate

You must escalate when:
- a discovery contradicts the architecture (for the architect and the preparer: the project's
  principles, constraints or architectural assumptions);
- the scope change exceeds 20% of the original estimate;
- security or policy implications emerge (potential S5 violations);
- a cross-domain change is needed (another specialist's files, an API contract, a schema).

## Self-coordination

In parallel with other agents of your domain: check S2 protocols first, respect assigned file
boundaries, adopt the first agent's conventions, and report conflicts immediately. Review agents do
not duplicate static code review, architectural review or test-coverage analysis.

## Smoke tests (coders)

Your work isn't done until smoke tests pass. Smoke tests verify: "Does it compile? Does it run? Does
the happy path not crash?" No comprehensive unit tests—that's TEST phase work.

## Algedonic authority

Emit HALT/ALERT immediately, without orchestrator permission, on a viability threat. SECURITY or
DATA is a HALT when exposure exists now or is unknown, an ALERT only when there is none; a committed
or printed secret and real data in a local copy are exposure too ([algedonic.md](algedonic.md)).
Your agent body lists the common triggers of your role; read algedonic.md immediately on detecting
one.
