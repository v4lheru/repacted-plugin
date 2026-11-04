# Real tmux teammate register: pre-merge gate plan

Moved from `test_session_registry_trust_partition.py` (`TestRealTmuxRegisterPreMergeGatePlan`, whose one test
was always skipped). Run it by hand under a live tmux teammate spawn; the in-process suite cannot.

```text
The real-tmux production-register pre-merge smoke gate: exercise the REAL
register flow under a real separate-process (tmux) teammate. The earlier
in-build investigation used a printf/marker PROXY, never the real helper, so
the assembled bash invocation string is untested against the real
session_registry.py end-to-end.

The register->resolve round-trip through the real CLI is partially de-risked
in-build (the real module ran from an unrelated cwd with exit 0, zero
shared.* dependency — proving script-mode self-containment), and the RESOLVE
+ composition path is covered by the real-subprocess acceptance test
(test_session_init_teammate_peer_inject_acceptance.py). This gate closes the
last gap: a real tmux teammate running the agent-def register imperative.

TWO RUNTIME PROPERTIES THIS GATE MUST CONFIRM (the unrelated-cwd + subprocess
tests cannot):
  1. NO PreToolUse gate DENIES the register Bash when it fires turn-1 BEFORE
     a teachback_submit exists. If a gate does deny it, the register step
     needs a carve-out — flag as a follow-up, do NOT block the merge (the
     register is fail-safe-skip; a denied register degrades to current
     behavior).
  2. Registration LANDS BEFORE the consuming hooks (file_tracker /
     session_init) need it — i.e. the agent-def first-action ordering holds
     so a teammate's name@team is resolvable by the time a later hook calls
     resolve_agent_name / the session_init teammate-branch.

This is a documented PLACEHOLDER executed under a live tmux flip; it is skipped
here because it requires a real multi-process teammate spawn the in-process
unit suite cannot stand up. The TURNKEY RUNBOOK below makes it mechanical for
whoever flips tmux — no design re-derivation needed.

------------------------------------------------------------------------
TURNKEY PRE-MERGE RUNBOOK (execute under a real tmux teammateMode flip)
------------------------------------------------------------------------
PRECONDITIONS
  * Operator teammateMode = tmux (N separate processes, N:1 session:team) —
    NOT in-process. Confirm a teammate runs as its OWN OS process / OWN
    session_id (session_id != leadSessionId).
  * The plugin symlink invariant holds (SessionStart's setup_plugin_symlinks),
    so the agent-def direct-path
    `python3 <plugin_root>/hooks/shared/session_registry.py register --name ...`
    resolves. If absent, register degrades fail-safe (no block) — see prop 1.

SETUP / OBSERVATION POINTS
  R = ~/.claude/pact-sessions/.teammate-registry.jsonl  (the global registry)
  Before the flip, snapshot R's lines (or its absence) so new lines are
  attributable to this run.

STEP 1 — spawn a real separate-process (tmux) teammate of a known team.
  Capture the teammate's own session_id (its $CLAUDE_CODE_SESSION_ID) and the
  lead's team name; the expected registry value is "<teammate-name>@<lead-team>".

STEP 2 — REGISTER-FIRST ORDERING + RUNTIME PROPERTY 1 (no turn-1 gate DENY).
  The agent-def first-action imperative MUST fire the register Bash on turn 1,
  BEFORE any teachback_submit exists.
    PASS: the register Bash is NOT denied by a PreToolUse gate (e.g. a
          no-Bash-before-teachback gate). The register step is scoped-exempt
          (verified structurally by the skills_structure suite); confirm it
          holds at RUNTIME here.
    IF DENIED: the register needs a carve-out — file a FOLLOW-UP, do NOT block
          the merge. register is fail-safe-skip, so a denied register degrades
          to current behavior (labeling stays degraded, nothing breaks).

STEP 3 — name@team CORRECTNESS + env session_id SOURCE.
  After the register fires, inspect R:
    PASS: R gained exactly one line {"session_id": <the teammate's OWN
          $CLAUDE_CODE_SESSION_ID>, "value": "<teammate-name>@<lead-team>"}.
    CHECK the session_id came from the teammate's ENV ($CLAUDE_CODE_SESSION_ID),
          not the lead's — it must equal the teammate's OWN session_id (tmux
          topology: != leadSessionId).
    CHECK the @team half is the LEAD's team (the datum a teammate cannot
          otherwise compute), not generate_team_name(own session) (which would
          be pact-<own hash>).
    CHECK the line parses as JSON, is <=512B, and the file mode is 0o600.

STEP 4 — RUNTIME PROPERTY 2 (registration LANDS BEFORE consuming hooks).
  A later hook (resolve_agent_name Step 3.5 / the session_init teammate-branch)
  must be able to self-look-up the teammate's OWN session_id and recover the
  friendly name@team.
    PASS: the consuming hook recovers "<teammate-name>@<lead-team>" via
          own-session_id self-lookup — i.e. the register landed BEFORE the
          consumer needed it (agent-def first-action ordering holds).
    OBSERVE: the teammate's SessionStart additionalContext carries the
          marker-free peer body (the resolved team's peer list), and the
          teammate is NOT mis-roled as orchestrator.

OVERALL GATE
  Merge-blocking: STEP 3 + STEP 4 must PASS (real register lands with correct
  name@team + a real consumer recovers it). STEP 2 is informational — a turn-1
  DENY is a follow-up, not a blocker (fail-safe-skip). If NO deterministic
  register form lands at all (symlink absent AND no fallback), that is a design
  break -> STOP + imPACT (considered unlikely: agent-def imperative fires 2/2,
  symlink verified present, module self-contained; only the assembled bash
  string was untested against the real helper).
```
