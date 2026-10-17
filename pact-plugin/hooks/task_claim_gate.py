#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_claim_gate.py
Summary: Teammate-side PreToolUse gate (matcher="Edit|Write|Bash") that closes
         the "Task B owned-but-pending while work is live" gap mechanically
         (#961 Cycle 2; defense-in-depth behind the Cycle-1 prose fix). When a
         teammate begins implementation work (Edit/Write/Bash) but has not yet
         flipped its pre-assigned, just-unblocked Task B from `pending` to
         `in_progress`, the gate nudges (M1) and, in the tmux topology with a
         registry-confident identity + exactly one candidate, auto-claims it
         (M2). Advisory only (additionalContext); NEVER denies.
Used by: hooks.json PreToolUse hook (matcher="Edit|Write|Bash")

WHY THIS GATE (the #961 backstop): the orchestrator dispatches Task B
pre-assigned (owner set) but `pending`, so the TEAMMATE flips it to
`in_progress` — preserving the lead's "work started" signal. The Cycle-1 prose
fix asks the teammate to claim-before-work; this hook is the mechanical
defense-in-depth for when the prose is missed.

THREE BEHAVIORAL TIERS, keyed on a STRUCTURAL signal (never a mode flag):
  • Lead frame            — is_lead(stdin) (agent_type ∈ LEAD_AGENT_TYPES) → NO-OP
                            (cheapest early-exit; the highest-frequency actor).
  • Teammate, in-process  — session_id == leadSessionId → identity collapses on
                            the shared session_id (the registered NAME last-wins-
                            collapses), so the registry CANNOT attribute a
                            specific owner → generic, attribution-free advisory
                            only, F3 relevance-guarded; NEVER auto-flips. When
                            the frame's agent_type names a team member, only
                            that member's tasks count toward the nudge.
  • Teammate, tmux        — session_id != leadSessionId → the distinct session_id
                            disambiguates identity via the registry → enforce:
                            M1 advisory floor / M2 conditional auto-heal of the
                            single owned-unblocked-pending task.

M1 + M2 (both implemented; the split is the single-candidate branch):
  • M1 (advisory floor)   — complete, working, fail-open, never-deny advisory
                            gate (is_lead/topology/mine-filter/F1/F2/F3 + the
                            three advisory shapes).
  • M2 (auto-heal)        — the tmux single-candidate path attempts a direct
                            atomic `pending → in_progress` write of the task JSON
                            (_atomic_claim), falling back to the M1 nudge on any
                            write failure.

POSTURE — FAIL-OPEN, NEVER DENY: every exception or unresolved precondition →
suppress + exit 0. Module-load failure → suppress + exit 0. This gate never
emits permissionDecision:"deny" / exit 2. A fail-CLOSED gate on the
high-frequency Edit|Write|Bash matcher would brick the session, so the posture
is fail-open on EVERY path (a crashed PreToolUse hook is itself non-blocking on
the platform — the explicit catches just keep the exit code clean and the
output well-formed).

IDENTITY IS COORDINATION, NOT AUTHORIZATION: the registry identity and is_lead
are coordination signals only. The registry value is self-asserted/forgeable
(labeling-only per session_registry's trust-boundary docstring); this resolution
MUST NOT leak into any authz/trust predicate.

FORGE INVARIANT (canonical; mirrored verbatim in the trust-partition test
comment): The auto-flip targets a task whose owner == the registry-resolved name.
Identity is COORDINATION-ONLY (the registry is forgeable/labeling). A forged or
last-wins-collapsed registry entry could resolve to a DIFFERENT member's name, so
the flip is NOT guaranteed to act on the acting teammate's OWN task. No-escalation
holds NOT via 'own-task-only' but because the same OS user already has full
TaskUpdate/FS access and the only mutation is a benign pending→in_progress flip —
the gate crosses no privilege boundary.

FRAME/REGISTRY CONFLICT: when the frame's agent_type names a team member and
that member differs from the registry-resolved name (a stale leadSessionId can
send an in-process frame down the tmux branch with the shared session's row),
identity is treated as unconfident and nothing flips. A registry value that
disagrees with a frame naming no member is not caught by this.

THREAT-MODEL BOUNDARY (security F-2): every no-escalation claim above rests on a
single assumption — ALL team frames (lead, in-process teammate, tmux teammate)
run as the SAME OS user, who ALREADY holds unrestricted TaskUpdate + filesystem
access to ~/.claude/tasks/. The gate therefore grants no capability the actor
lacks; its only mutation is one that actor could already perform directly. This
guarantee does NOT extend to a multi-user / cross-UID model: if a DIFFERENT OS
user could write ~/.claude/tasks/ or forge the registry, the "benign own-flip"
framing would no longer bound the blast radius and these coordination signals
would need a real authz boundary. PACT is same-OS-user-only; this note marks the
assumption EXPLICITLY so a future multi-user deployment re-evaluates it rather
than inheriting it silently.

ADVISORY-CHANNEL BOUND: an allow-path PreToolUse additionalContext reaches the
model together with the tool result, after the call has run. The advisory-only
paths (in-process / identity-unconfident / multi-candidate) therefore nudge
AFTER the first Edit/Write/Bash has already happened, never before it; they
are the SOFTER backstop, with the prose layer as the load-bearing universal
fix. M2's auto-heal does not depend on the channel on the path that matters
most (tmux): it mutates the task JSON directly, so the flip lands whether or
not the advisory is acted on.

Input: JSON from stdin with tool_name, tool_input, agent_type, session_id, etc.
Output: JSON with hookSpecificOutput.additionalContext (advisory case) or
        {"suppressOutput": true} (NO-OP / passthrough). ALWAYS exit 0.
"""

from __future__ import annotations

# ─── stdlib first (used on the input-side fail-open BEFORE wrapped imports) ──
import json
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Cap on the stdin read. Real PreToolUse Edit|Write|Bash frames carry a small
# tool_input and stay well under this; an over-cap frame truncates mid-JSON →
# JSONDecodeError → input-side fail-open. Bounds memory only; does not reject
# sub-cap input. Mirrors the sibling gates' 8 MB cap.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


# ─── fail-OPEN wrapper on cross-package imports ──────────────────────────────
# This gate must NEVER deny. If an import below raises, we suppress + exit 0
# (fail-open) rather than emitting a deny — unlike the fail-CLOSED deny gates
# (bootstrap_gate / pin_*_gate). A crashed hook (exit 1) is ALSO non-blocking on
# PreToolUse, so even an un-caught raise degrades to fail-open; the explicit
# catch keeps the exit code clean (0) and the output well-formed.
try:
    import shared.pact_context as pact_context
    # SSOT delegation: the shared _read_lead_session_id implementation lives
    # in shared.pact_context (session_registry keeps the one remaining inline
    # copy, forced by its self-contained-leaf invariant). The from-import
    # binds the name as THIS module's attribute, preserving the test seam
    # that monkeypatches task_claim_gate._read_lead_session_id.
    from shared.pact_context import _read_lead_session_id
    from shared.frame import is_lead_frame
    from shared.session_registry import resolve as registry_resolve
    from shared.task_utils import (
        iter_team_task_jsons,
        is_teachback_subject,
        update_task_file,
    )
    from shared.intentional_wait import is_self_complete_exempt
    from shared.session_state import is_safe_path_component
    from shared.agent_handoff_marker import sanitize_path_component
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-OPEN catch-all (this gate never denies)
    _IMPORTS_OK = False


# ─── advisory copy (semantics fixed by the spec; wording refined here) ───────
_NUDGE_PREFIX = "PACT task_claim_gate: "

# Generic, attribution-free nudge — emitted when identity is UNCONFIDENT
# (in-process collapse OR tmux registry-miss): we know SOMETHING is claimable
# (F3) but cannot attribute which task is the actor's, so we never name an id.
_GENERIC_CLAIM_NUDGE = (
    _NUDGE_PREFIX
    + "If you have a pre-assigned Task B that is still `pending` AND unblocked "
    "— every id in its `blockedBy` resolves to a `completed` task — claim it "
    "first — `TaskUpdate(<id>, status=in_progress)` — before implementation "
    "work, so the lead's work-started signal stays accurate."
)


def _claim_nudge_single(task_id: str) -> str:
    """Task-specific nudge: identity is confident and exactly one owned task is
    claimable. Names the id; the teammate flips it (M1) — M2 may auto-flip."""
    return (
        _NUDGE_PREFIX
        + f"Your pre-assigned Task #{task_id} is still `pending` while you are "
        f"doing implementation work. Claim it first — "
        f"`TaskUpdate({task_id}, status=in_progress)` — so the lead's "
        "work-started signal stays accurate."
    )


def _claim_nudge_multi(task_ids: list[str]) -> str:
    """Task-specific list nudge: identity is confident but MORE THAN ONE owned
    task is claimable. Lists the ids; the teammate picks. NEVER auto-flips —
    the gate must not guess which task the actor is working on."""
    ids = ", ".join(f"#{tid}" for tid in task_ids)
    return (
        _NUDGE_PREFIX
        + f"You own multiple unblocked `pending` tasks ({ids}) while doing "
        "implementation work. Claim the one you are working on — "
        "`TaskUpdate(<id>, status=in_progress)` — before continuing, so the "
        "lead's work-started signal stays accurate."
    )


def _auto_claimed_note(task_id: str) -> str:
    """Transparency note emitted after a successful M2 auto-claim. Non-deny."""
    return (
        _NUDGE_PREFIX
        + f"Auto-claimed your pre-assigned Task #{task_id} "
        "(`pending → in_progress`) to preserve the lead's work-started "
        "signal. No action needed."
    )


# ─── topology / plumbing helpers ─────────────────────────────────────────────


def _split_name_team(resolved: object) -> "tuple[str | None, str | None]":
    """Split a registry `resolve()` value into (name, team).

    Returns (None, None) on any miss/malformed value — a None name means
    identity is UNCONFIDENT and the gate must NEVER guess a typed owner (F2).
    """
    if not isinstance(resolved, str) or "@" not in resolved:
        return (None, None)
    name, _, team = resolved.partition("@")
    if not name or not team:
        return (None, None)
    return (name, team)


def _stdin_team(stdin: dict) -> str:
    """Last-resort team_name fallback: a stdin-provided team_name (often ABSENT
    on tmux PreToolUse frames). Returns "" when missing/non-string."""
    team = stdin.get("team_name")
    return team if isinstance(team, str) else ""


# ─── claim-candidate predicates ──────────────────────────────────────────────


def _is_unblocked(task: dict, by_id: dict) -> bool:
    """§7 CORRECTED unblocked predicate: every ``blockedBy`` id resolves to a
    ``completed`` task.

    NOT "blockedBy empty" — the platform RETAINS completed-blocker ids in the
    raw ``blockedBy`` list, and this hook reads raw JSON. Under a literal
    "empty" filter, a just-unblocked Task B (whose sole blocker — the teachback
    Task A — is now completed) would still carry ``blockedBy=[A]`` and be
    misclassified as blocked, making the gate a silent no-op on its own target
    scenario.

    An unresolvable blocker (id with no matching task in ``by_id`` — deleted or
    cross-team) is treated as RESOLVED (no live dependency): permissive per the
    spec, bounded by the single-candidate + owned + pending conjunction at the
    call site.

    A blocker whose completion task_lifecycle_gate disputed (``metadata.completion_disputed``,
    e.g. a teammate self-completed its teachback) is NOT completed until the lead clears it.
    """
    for bid in (task.get("blockedBy") or []):
        blocker = by_id.get(str(bid))
        if blocker is None:
            continue  # unresolvable/deleted blocker → treated as resolved (§7)
        if blocker.get("status") != "completed":
            return False  # an open dependency remains
        metadata = blocker.get("metadata")
        if isinstance(metadata, dict) and metadata.get("completion_disputed"):
            return False  # a disputed completion does not unblock
    return True


def _frame_member(input_data: dict, team_name: str) -> str | None:
    """The member this frame names, or None.

    An in-process teammate's frame carries its own member name in `agent_type`
    (and an `agent_id` built from it); a tmux frame carries a type. The name is
    trusted only when it is a member of the team config. Imported here so a
    failure leaves the gate on its attribution-free path instead of disabling it.
    """
    try:
        from shared.background_work import agent_type_names_a_member

        agent_type = input_data.get("agent_type")
        if agent_type_names_a_member(agent_type, team_name, agent_id=input_data.get("agent_id")):
            return agent_type
    except Exception:
        pass
    return None


def _frame_agent_id(input_data: dict):
    """The frame's raw `agent_id`. Read for identity confidence only (does the
    registry row name THIS frame?), never to tell the lead from a teammate."""
    return input_data.get("agent_id")


def _any_unclaimed_claim_candidate(
    tasks: list, by_id: dict, team_name: str, owner: str | None = None
) -> bool:
    """F3 relevance-guard for the attribution-free generic advisory.

    The in-process and identity-unconfident branches cannot flip a specific
    task, so the generic nudge must not fire on every Edit/Write/Bash. Emit
    ONLY if there is ≥1 task that is `pending` AND unblocked AND
    not-teachback-subject AND not-self-complete-exempt AND has a NON-EMPTY owner
    (a pre-assigned Task B still pending). With `owner` (the member the frame
    names), only that member's tasks count, so a teammate whose own Task B is
    claimed is not nudged about a peer's. Without it, any owner counts.
    """
    for task in tasks:
        task_owner = task.get("owner")
        if not isinstance(task_owner, str) or not task_owner.strip():
            continue  # must be a pre-assigned (owned) task
        if owner is not None and task_owner != owner:
            continue
        if task.get("status") != "pending":
            continue
        if not _is_unblocked(task, by_id):
            continue
        if is_teachback_subject(task.get("subject") or ""):
            continue
        if is_self_complete_exempt(task, team_name):
            continue
        return True
    return False


# ─── M2 atomic auto-heal write (mirrors task_lifecycle_gate._writeback_dispute) ─


def _atomic_claim(task_id: str, team_name: str) -> bool:
    """M2 auto-heal: flip a single owned-unblocked-pending task
    ``pending → in_progress`` via a direct, atomic filesystem write of the WHOLE
    task JSON.

    Mirrors ``task_lifecycle_gate._writeback_dispute`` in shape: through
    ``shared.task_utils.update_task_file``, read the whole dict under the host's
    own task lock (the ``<id>.json.lock`` directory, which the platform's
    TaskUpdate also takes) → RE-VALIDATE ``status == "pending"`` (the no-clobber
    guard against a race between the scan and this write) → flip the TOP-LEVEL
    status → replace the WHOLE json from a per-write temp file (atomic;
    preserves every sibling top-level key — the shallow-merge pin).

    Fail-OPEN: any failure (unsafe ids, missing task, status moved, lock not
    taken, OSError) returns False and the caller degrades to the
    CLAIM_NUDGE_SINGLE advisory. The flip — not the advisory — is the
    load-bearing signal. Never raises.

    CONCURRENCY (issue #968, closed by the host-lock fix): hook writers and the platform's
    file-backed task writes now serialise on that one lock, so a racing platform
    write is no longer lost in the read→replace window. Before it, the hook
    locked a flock sidecar the platform never took, and left a regular file of
    the platform's lock name behind (ENOTDIR on the next TaskUpdate).

    Returns True iff the flip was written.
    """
    # sanitize_path_component strips C0/\x00 + path-traversal fragments; an empty
    # result (e.g. a pure-traversal id) → abort. Mirrors _writeback_dispute's
    # NUL-byte defense (a bare re.sub would let \x00 reach .exists() → ValueError).
    sanitized = sanitize_path_component(task_id)
    if not sanitized:
        return False
    # team_name was already validated in _evaluate (Step 2); re-check here as
    # defense-in-depth for any future caller — via the CANONICAL
    # is_safe_path_component (the SSOT allowlist SAFE_PATH_COMPONENT_RE =
    # [A-Za-z0-9_-]+) instead of a duplicated inline regex. DRY: one validator,
    # behaviorally identical to the prior re.fullmatch (verified 0-divergence).
    if not is_safe_path_component(team_name):
        return False

    # No-clobber re-validation, under the host's task lock: another actor may
    # have moved the task between the scan and now. Only flip a still-pending
    # task; never overwrite a status another actor already changed. (owner /
    # unblocked were validated pre-scan.) update_task_file re-reads under the
    # lock, uses a per-write temp name and refuses a tasks dir that resolves
    # outside tasks/ (the readers' symlink containment).
    def mutate(task: dict) -> bool:
        if task.get("status") != "pending":
            return False
        task["status"] = "in_progress"  # TOP-LEVEL flip (the case M0 proved honored)
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            metadata = task["metadata"] = {}
        # gate_writeback: convention-parity with the _writeback_* recursion
        # guard; NON-LOAD-BEARING here (a direct FS write emits no TaskUpdate).
        metadata["gate_writeback"] = True
        return True

    return update_task_file(team_name, sanitized, mutate)


# ─── core decision (pure-ish read; M2 adds one conditional write) ────────────


def _evaluate(stdin: dict) -> str | None:
    """Return the advisory string to surface, or None for NO-OP.

    Every step is fail-open: any exception or unresolved precondition returns
    None (the caller suppresses + exits 0). The ONLY mutation is the M2
    auto-heal write on the tmux, identity-confident, exactly-one-candidate
    branch (_atomic_claim); every other path is read-only.
    """
    # ── Step 0: role early-exit (cheapest; covers the highest-frequency actor) ─
    if is_lead_frame(stdin):
        return None  # lead NO-OP, both modes; a lead launched without --agent included

    session_id = stdin.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None  # fail-safe

    # ── Step 1: identity + team via the registry-DIRECT primitive (F2) ────────
    # resolve() is members[]-validated, self-lookup only, never raises. A None
    # name → identity UNCONFIDENT → advisory, NEVER a typed guess (no
    # resolve_agent_name type-strip fallback).
    confident_name, team_from_registry = _split_name_team(registry_resolve(session_id))

    # ── Step 2: team_name (registry @team half wins; context/stdin fallbacks) ─
    # The @team half survives the in-process name-collapse, so it is the primary
    # team source in BOTH modes; the context-file + stdin fallbacks cover a
    # registry miss.
    team_name = team_from_registry
    if not team_name:
        try:
            pact_context.init(stdin)
        except Exception:
            pass
        team_name = pact_context.get_team_name() or _stdin_team(stdin)
    if not team_name or not is_safe_path_component(team_name):
        return None  # cannot locate config → fail-safe NO-OP

    # ── Step 3: topology discriminator (the final D3 signal — a config read) ──
    # CURRENCY (this gate's blast-radius bound; the generic currency note lives
    # on the shared helper): the compare below assumes config.leadSessionId is
    # CURRENT. A stale value (e.g. after a session resume) can misclassify an
    # in-process frame as tmux or vice-versa — bounded and benign HERE: the
    # worst outcome is a benign pending→in_progress auto-flip of the teammate's
    # OWN owned-unblocked-pending task (the very action it was supposed to
    # take) or a missed nudge. The owner == confident_name conjunction bounds
    # it to the resolved teammate's task; no wrong-teammate flip, no
    # escalation.
    lead_session_id = _read_lead_session_id(team_name)
    if not lead_session_id:
        # ASYMMETRY (design note): an UNKNOWN topology (config unreadable / no
        # leadSessionId) NO-OPs SILENTLY — no advisory at all — whereas a
        # CONFIRMED in-process topology DOES emit the generic advisory (below).
        # Both are "cannot attribute a specific owner" states, yet they diverge by
        # design: unknown-topology means we cannot even confirm we are a teammate
        # in a known team, so the most conservative fail-safe (total NO-OP) is
        # correct; confirmed-in-process is a KNOWN, safe state where the
        # attribution-free nudge is warranted. The extra suppression on the
        # unknown path is intentional, not an oversight.
        return None  # unknown topology → fail-safe total NO-OP (no advisory)
    in_process = session_id == lead_session_id

    # ── Step 4: load the team task set ONCE (also feeds the unblocked predicate) ─
    tasks = list(iter_team_task_jsons(team_name))
    by_id = {str(t.get("id")): t for t in tasks if t.get("id") is not None}

    # The member the frame names, if any. It scopes the generic nudge to the
    # actor's own tasks; with no member named, the nudge stays team-wide.
    frame_member = _frame_member(stdin, team_name)

    if in_process:
        # IN-PROCESS: the registry row is shared by every in-process member, so
        # it cannot identify the actor → generic non-mutating advisory only,
        # F3 relevance-guarded. NEVER auto-flip.
        if _any_unclaimed_claim_candidate(tasks, by_id, team_name, frame_member):
            return _GENERIC_CLAIM_NUDGE
        return None

    # ── TMUX branch: enforce ──────────────────────────────────────────────────
    if frame_member is not None and frame_member != confident_name:
        # The frame and the registry name DIFFERENT members (M12: a stale
        # leadSessionId makes an in-process frame look separate-process, and the
        # shared session's registry row names a peer). Identity is in conflict,
        # so it is unconfident: never flip a task on the registry's word.
        confident_name = None
    agent_id = _frame_agent_id(stdin)
    if (agent_id is not None and frame_member != confident_name
            and agent_id != f"{confident_name}@{team_name}"):
        # A separate-process teammate's own frames carry no agent_id. An id here is
        # another frame in that session, such as an Agent-tool subagent the
        # teammate started, or a stale leadSessionId's in-process
        # frame: unless the frame names the same member (checked above), the
        # registry row does not name it, so never flip on its word.
        confident_name = None
    if not confident_name:
        # Identity UNCONFIDENT (registry miss / unregistered teammate / conflict).
        # NEVER guess a typed owner; fall back to the generic, attribution-free nudge.
        if _any_unclaimed_claim_candidate(tasks, by_id, team_name, frame_member):
            return _GENERIC_CLAIM_NUDGE
        return None

    mine = [
        t
        for t in tasks
        if t.get("owner") == confident_name
        and t.get("status") == "pending"
        and _is_unblocked(t, by_id)  # §7 — all blockers completed
        and not is_teachback_subject(t.get("subject") or "")
        and not is_self_complete_exempt(t, team_name)
    ]

    # F1 idempotency: nothing claimable → NO-OP. After a flip the task is
    # in_progress (not pending) → never re-nagged, never re-flipped.
    if not mine:
        return None

    if len(mine) == 1:
        # Identity-confident + exactly one candidate → M2 auto-heal: attempt the
        # atomic pending→in_progress flip. On any write failure (_atomic_claim
        # returns False — incl. the no-clobber re-validation aborting) degrade to
        # the advisory (fail-open, never deny).
        task_id = str(mine[0].get("id"))
        if _atomic_claim(task_id, team_name):
            return _auto_claimed_note(task_id)
        return _claim_nudge_single(task_id)

    # len(mine) > 1: multiple owned-unblocked-pending → list, NEVER guess.
    return _claim_nudge_multi([str(t.get("id")) for t in mine])


def main() -> None:
    # Input-side fail-open: an unreadable / oversized / malformed stdin frame
    # suppresses + exits 0 (never blocks the tool call).
    try:
        stdin = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Module-load failure or non-dict stdin → suppress + exit 0 (fail-open).
    if not _IMPORTS_OK or not isinstance(stdin, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        advisory = _evaluate(stdin)
    except Exception:
        # Fail-OPEN on any logic error. A gate that bricks Edit/Write/Bash is
        # far worse than a missed nudge. NEVER deny.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if advisory:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",  # MUST be present (#658 invariant)
                "additionalContext": advisory,  # advisory — NOT permissionDecision
            }
        }))
        sys.exit(0)  # exit 0 — advisory, never deny / exit-2

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
