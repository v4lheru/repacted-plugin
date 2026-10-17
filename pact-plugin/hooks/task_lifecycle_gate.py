#!/usr/bin/env python3
"""PostToolUse hook (matcher='TaskCreate|TaskUpdate'): PACT task lifecycle invariants.

Cannot deny (post-action): emits advisories via additionalContext, writes back
completion_disputed on a self-completion, wires a teachback A/B pair's blocking edges,
mirrors dispatch/teachback/handoff facts into the session journal, and on a claim adds the
task to its owner's background-work launch records. Its own writebacks carry
metadata.gate_writeback=true so they never re-trigger the gate. Fail-closed-as-advisory on
load failure; hookEventName on every output path.

Map: _bounded_error_text, _emit_gate_health_event, _emit_load_failure_advisory,
artifact backstop (_phase_artifact_requirement, _artifact_paths_event_present), writebacks
(_writeback_dispute, _writeback_audit_recovery, _writeback_edge, _wire_teachback_pair),
dispatch_site emit (_dispatch_site_*, _emit_dispatch_site), handoff emit (_resolve_b2_occupant,
_emit_lead_side_agent_handoff), _emit_per_write_snapshot, validators (_validate_*), band
resolution (_band_from_total .. _resolve_required_band_via_blocks), evaluate_lifecycle,
_journal_lifecycle_decision, _extend_launch_records_on_claim, main.
Rules and rationale: the design notes
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import sys
import time
from typing import NoReturn

# The shared renderer (hooks/_fail_closed.py): one copy, pinned as this hook's twin.
from _fail_closed import bounded_error_text as _bounded_error_text


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


# Cap on exception text put into context-bound output (it can carry attacker-influenced
# content); stderr keeps the full text.
_ERROR_TEXT_MAX = 200

# Cap on the crash-path stdin read: bounds memory only (an over-cap frame fails to parse and
# the emitter stays silent). MUST equal bootstrap_gate._STDIN_READ_MAX.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


def _emit_gate_health_event(
    stage: str, error_text: str, input_data: dict | None
) -> None:
    """Best-effort journal emit of a crash-path gate_health event. Lazy imports so it works on the
    import-stage crash path (stdin is then read here, capped). Never raises.
    """
    try:
        import shared.pact_context as _lazy_pact_context
        import shared.session_journal as _lazy_session_journal

        if input_data is None:
            input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
        if not isinstance(input_data, dict):
            return
        if not _lazy_pact_context.is_initialized():
            _lazy_pact_context.init(input_data)
        # tool_name is attacker-set stdin on the import-stage path: sanitize and bound it too.
        tool_name = input_data.get("tool_name", "")
        if not isinstance(tool_name, str):
            tool_name = f"<non-str {type(tool_name).__name__}>"
        tool_name = "".join(ch if ch.isprintable() else " " for ch in tool_name)
        if len(tool_name) > _ERROR_TEXT_MAX:
            tool_name = tool_name[:_ERROR_TEXT_MAX] + "...[truncated]"
        event = _lazy_session_journal.make_event(
            "gate_health",
            hook="task_lifecycle_gate",
            status="failed",
            stage=stage,
            error=error_text,
            tool_name=tool_name,
        )
        written = _lazy_session_journal.append_event(event)
        if not written:
            try:
                print(
                    "task_lifecycle_gate: gate_health journal emit skipped "
                    "(append_event returned False)",
                    file=sys.stderr,
                )
            except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
                pass
    except BaseException:  # noqa: BLE001 — the crash handler must not crash:
        # Lazy imports run module bodies; a SystemExit/KeyboardInterrupt here would exit nonzero after
        # the marker printed, and stdout JSON is honored only on exit 0.
        try:
            print(
                "task_lifecycle_gate: gate_health journal emit unavailable "
                "(late import or init failed)",
                file=sys.stderr,
            )
        except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
            pass


def _emit_load_failure_advisory(
    stage: str, error: BaseException, input_data: dict | None = None
) -> NoReturn:
    """Stdlib-only fail-advisory for load failure (PostToolUse cannot deny): advisory + exit 0, a
    raw-stdout pactGateHealth key, a systemMessage mirror and a best-effort journal event.
    """
    # Call-site fallback is a raise-proof constant; type(error).__name__ could raise again.
    try:
        error_text = _bounded_error_text(error)
    except BaseException:  # noqa: BLE001 — floor must survive any renderer defect
        error_text = "<error text unavailable>"
    advisory = (
        f"PACT task_lifecycle_gate {stage} failure — lifecycle "
        f"rule enforcement skipped this turn. "
        f"{error_text}. Investigate hook "
        "installation and shared module availability."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advisory,
        },
        "systemMessage": advisory,
        "pactGateHealth": {
            "v": 1,
            "hook": "task_lifecycle_gate",
            "status": "failed",
            "stage": stage,
            "error": error_text,
        },
    }
    print(json.dumps(output))                                   # floor FIRST
    # Guarded full-text rendering: this line runs AFTER the floor printed,
    # but an unguarded str(error) raising here would exit nonzero — and
    # stdout JSON is only honored on exit 0, voiding the floor retroactively.
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-0 path
        error_full = "<exception str() raised>"
    try:
        print(
            f"Hook load error (task_lifecycle_gate / {stage}): {error_full}",  # full text
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    _emit_gate_health_event(stage, error_text, input_data)      # bonus LAST
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ─────────────────────────
try:
    import re

    import shared.pact_context as pact_context
    from shared.actor import resolve_actor
    from shared.agent_handoff_marker import (
        already_emitted,
        handoff_already_emitted,
        handoff_content_key,
        handoff_unclaim,
        is_signal_task,
        occupant_hash,
        sanitize_path_component,
        unclaim,
    )
    import shared.dispatch_site as _dispatch_site_mod
    from shared.dispatch_helpers import (
        is_owner_bearing_write,
        is_pact_specialist_owner,
        variety_stamp_as_of_write,
    )
    from shared.handoff_schema import (
        HANDOFF_SCHEMA_ECHO,
        normalise_handoff,
        validate_handoff_schema,
    )
    from shared.intentional_wait import is_self_complete_exempt, is_teachback_exempt
    from shared.session_journal import (
        UNRESOLVED_ATTRIBUTION,
        append_event,
        append_event_checked,
        get_journal_path,
        make_event,
        read_events,
    )
    from shared.task_metadata_snapshot import (
        PER_WRITE_MIRROR_KEYS,
        emit_task_metadata_snapshot,
        late_mirror_key,
    )
    from shared.task_utils import is_teachback_subject as _is_teachback_subject
    from shared.task_store import (TaskRef, TaskStore, iter_team_task_jsons, read_task_json,
                                   update_task_file)
    from shared.teachback_schema import (
        DISPATCH_VARIETY_KEYS,
        TEACHBACK_OBJECT_FIELDS,
        TEACHBACK_RECOMMENDED_BAND_MIN,
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
        TEACHBACK_REQUIRED_FIELDS,
        TEACHBACK_REQUIRED_SUBKEYS,
        TEACHBACK_SCHEMA_ECHO,
        TEACHBACK_VARIETY_ACK_VALID_VALUES,
        normalise_teachback,
        resolve_variety_total,
        validate_reasoning_reconstruction,
    )
    from shared.tool_response import extract_tool_response
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


# ─── constants ────────────────────────────────────────────────────────────────

# Self-completion carve-out lives in shared.intentional_wait.is_self_complete_exempt and keys
# on the team-config agentType, not the owner name.

# Teachback-exempt dispatch carve-out resolution is delegated entirely to
# is_teachback_exempt(owner, team_name) in shared.intentional_wait.

# Handoff schema comes from shared.handoff_schema (reasoning_chain is recommended, so FIVE
# required fields; a local six-field copy was wrong).

# Teachback schema constants and validators come from shared.teachback_schema (SSOT).

# D11: four per-dimension rationales, each about THIS dispatch.
_VARIETY_REQUIRED_RATIONALES = (
    "novelty_rationale",
    "scope_rationale",
    "uncertainty_rationale",
    "risk_rationale",
)

# Sentinel: rationales fine but no resolvable total; R4 keys on this exact value.
_NO_RESOLVABLE_TOTAL = (
    "no resolvable total (need an integer 4-16 under key 'total', "
    "or a recoverable fallback)"
)


# artifact_paths backstop: only PREPARE and ARCHITECT must emit a durable artifact pointer
# (CODE/TEST output is git-tracked; ATOMIZE/CONSOLIDATE produce none). Values mirror the
# session_journal artifact_paths workflow enum.
_ARTIFACT_EMIT_PHASE_WORKFLOWS = {
    "PREPARE:": "prepare",
    "ARCHITECT:": "architect",
}


def _phase_artifact_requirement(subject: str) -> "tuple[str, str] | None":
    """(workflow, feature) for a PREPARE/ARCHITECT subject "PHASE: feature", else None. Pure."""
    if not isinstance(subject, str):
        return None
    for prefix, workflow in _ARTIFACT_EMIT_PHASE_WORKFLOWS.items():
        if subject.startswith(prefix):
            _, sep, suffix = subject.partition(": ")
            feature = suffix.strip()
            if not sep or not feature:
                return None
            return workflow, feature
    return None


def _artifact_paths_event_present(workflow: str, feature: str) -> bool:
    """True iff the current session journal has an artifact_paths event for (workflow, feature).

    Only called in a canonical-journal frame, so the implicit read_events() is right here. Fails
    open: a read error reads as absent and nudges (a false nudge is harmless, silence is not).
    """
    events = read_events("artifact_paths")
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("workflow") == workflow and event.get("feature") == feature:
            return True
    return False


# _is_teachback_subject lives in shared/task_utils.py (one definition; do not copy the regex).


# ─── metadata.completion_disputed writeback (direct FS) ──────────────────────


def _writeback_dispute(task_id: str, disputed: bool = True) -> bool:
    """Set completion_disputed and gate_writeback on the task file (locked direct write), or,
    with disputed=False (the lead completing the task), remove completion_disputed.

    gate_writeback stops the gate re-firing on its own write. Fail-open: returns False on error;
    the advisory is the load-bearing signal. True iff the write landed.
    """
    if not task_id or not isinstance(task_id, str):
        return False
    # sanitize_path_component also strips NUL/C0: a NUL reaching exists() raises ValueError and
    # would suppress every rule this turn.
    sanitized_id = sanitize_path_component(task_id)
    if not sanitized_id:
        return False
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", team_name):
        return False

    def mutate(task: dict) -> bool:
        metadata = task.get("metadata")
        if not disputed:
            return isinstance(metadata, dict) and metadata.pop("completion_disputed", None) is not None
        if not isinstance(metadata, dict):
            metadata = task["metadata"] = {}
        metadata["completion_disputed"] = True
        metadata["gate_writeback"] = True
        return True

    return update_task_file(team_name, sanitized_id, mutate)


# #906 auditor verdict ranks. A lowered rank (e.g. RED->GREEN) only escalates the advisory's
# severity; preservation never depends on rank.
_AUDIT_SIGNAL_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def _audit_signal_rank(audit_summary: object) -> "int | None":
    """Return the severity rank of an audit_summary's `signal`, or None if the
    shape/signal is unrankable. Pure; never raises."""
    if not isinstance(audit_summary, dict):
        return None
    signal = audit_summary.get("signal")
    if not isinstance(signal, str):
        return None
    return _AUDIT_SIGNAL_RANK.get(signal.strip().upper())


def _is_destructive_audit_downgrade(prior: object, incoming: object) -> bool:
    """Return True iff `incoming` LOWERS `prior`'s verdict severity (e.g.
    RED->GREEN). Unknown signals on either side → False (cannot rank → no
    escalation). Pure; never raises. Used ONLY for advisory wording — preservation
    is unconditional regardless of the return value."""
    prior_rank = _audit_signal_rank(prior)
    incoming_rank = _audit_signal_rank(incoming)
    if prior_rank is None or incoming_rank is None:
        return False
    return incoming_rank < prior_rank


def _writeback_audit_recovery(task_id: str, updates: dict) -> bool:
    """#906 writeback (same locked path as _writeback_dispute, sets gate_writeback).

    MIRROR: audit_summary_authored = authored verdict (captured at author time, before a lead
    can overwrite it). RECOVER: lead_close_note = the lead's overwriting value. Fail-open;
    True iff the write landed.
    """
    if not task_id or not isinstance(task_id, str):
        return False
    # sanitize_path_component also strips NUL/C0 (see _writeback_dispute).
    sanitized_id = sanitize_path_component(task_id)
    if not sanitized_id:
        return False
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", team_name):
        return False

    def mutate(task: dict) -> bool:
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            metadata = task["metadata"] = {}
        metadata.update(updates)
        metadata["gate_writeback"] = True
        return True

    return update_task_file(team_name, sanitized_id, mutate)


# ─── teachback pair wiring (direct FS) ───────────────────────────────────────

_OPEN_TASK_STATUSES = ("pending", "in_progress")


def _writeback_edge(task_id: str, team_name: str, key: str, other_id: str) -> bool:
    """Set a task's blocks or blockedBy to [other_id] in the team dir (locked replace; never
    creates a file), under the host's task lock. Fail-open; True iff landed.
    """
    if key not in ("blocks", "blockedBy"):
        return False
    sanitized_id = sanitize_path_component(task_id)
    if not sanitized_id or sanitized_id != task_id:
        return False
    target = TaskStore().path(TaskRef(team_name, sanitized_id))
    try:
        if target is None or not target.is_file():
            return False
    except OSError:
        return False
    def mutate(task: dict) -> bool:
        if str(task.get("id", "")) != task_id:
            return False
        task[key] = [other_id]
        return True

    return update_task_file(team_name, sanitized_id, mutate)


def _id_list(value: object) -> "list[str] | None":
    """A task's blocks/blockedBy as a list of strings; None when malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return value


def _wire_teachback_pair(
    task_id: str, owner: object, team_name: str
) -> "tuple[str, str]":
    """Wire Task A to Task B on an owner write so the lead need not send addBlocks.

    Returns (outcome, detail): wired, already, none (no pair yet), half (A.blocks written, B.blockedBy
    failed) or refused. Unambiguous or
    nothing: current team, byte-exact owner, exactly one open A and one open B, the written task
    is one of them, and any existing edge already points at the other task or is empty.
    """
    if not (isinstance(owner, str) and owner and team_name and task_id):
        return "none", ""
    owned = [
        t for t in iter_team_task_jsons(team_name)
        if t.get("owner") == owner and t.get("status") in _OPEN_TASK_STATUSES
    ]
    gates = [t for t in owned if _is_teachback_subject(t.get("subject"))]
    works = [t for t in owned if not _is_teachback_subject(t.get("subject"))]
    if not gates or not works:
        return "none", ""
    if len(gates) != 1 or len(works) != 1:
        return "refused", (
            f"owner {owner!r} has {len(gates)} open teachback task(s) and "
            f"{len(works)} open work task(s); the hook wires only one of each"
        )
    task_a, task_b = gates[0], works[0]
    a_id, b_id = str(task_a.get("id", "")), str(task_b.get("id", ""))
    if any(not i or sanitize_path_component(i) != i for i in (a_id, b_id)):
        return "refused", "a task id is not a plain file name"
    if task_id not in (a_id, b_id):
        return "refused", "the written task is not one of the owner's pair"
    prefix = f"{owner}: "
    if not all(isinstance(t.get("subject"), str) and t["subject"].startswith(prefix)
               for t in (task_a, task_b)):
        return "refused", f"a subject does not start with {prefix!r}"
    a_blocks = _id_list(task_a.get("blocks"))
    b_blocked_by = _id_list(task_b.get("blockedBy"))
    if a_blocks is None or a_blocks not in ([], [b_id]):
        return "refused", f"Task {a_id} already blocks {task_a.get('blocks')!r}"
    if b_blocked_by is None or b_blocked_by not in ([], [a_id]):
        return "refused", f"Task {b_id} is already blocked by {task_b.get('blockedBy')!r}"
    if a_blocks and b_blocked_by:
        return "already", ""
    written = []
    if not a_blocks:
        if not _writeback_edge(a_id, team_name, "blocks", b_id):
            return "refused", f"the write to Task {a_id} failed"
        written.append("blocks")
    if not b_blocked_by:
        if not _writeback_edge(b_id, team_name, "blockedBy", a_id):
            if written:
                return "half", f"Task {a_id} blocks {b_id} but Task {b_id}'s blockedBy write failed"
            return "refused", f"the write to Task {b_id} failed"
        written.append("blockedBy")
    try:
        append_event(make_event(
            "task_pair_wired", teachback_task_id=a_id, work_task_id=b_id,
            owner=owner, written=written,
        ))
    except Exception:
        pass
    return "wired", f"{a_id}->{b_id}"


# Marker namespace and key live in shared.dispatch_site (SSOT for both emit legs).
DISPATCH_SITE_MARKER_NAMESPACE: str = _dispatch_site_mod.DISPATCH_SITE_MARKER_NAMESPACE
_DISPATCH_SITE_MARKER_KEY: str = _dispatch_site_mod.DISPATCH_SITE_MARKER_KEY


def _dispatch_site_already_emitted(team_name: str, task_id: str) -> bool:
    """Test-and-set the dispatch_site marker, hard-bound to its namespace and key.

    The emit path calls only this and _dispatch_site_unclaim, so claim and unclaim can never
    target different dirs, and a non-str task_id is coerced inside the marker resolver.
    Accepted residual: task ids are reused across arcs, so an old marker can suppress a later
    emit (an under-count, the safe direction). See the design notes.
    """
    return already_emitted(
        team_name,
        task_id,
        _DISPATCH_SITE_MARKER_KEY,
        namespace=DISPATCH_SITE_MARKER_NAMESPACE,
    )


def _dispatch_site_unclaim(team_name: str, task_id: str) -> None:
    """Roll back a claim whose journal write failed (hard-bound twin of the claim)."""
    unclaim(
        team_name,
        task_id,
        _DISPATCH_SITE_MARKER_KEY,
        namespace=DISPATCH_SITE_MARKER_NAMESPACE,
    )


def _dispatch_site_variety(tool_input: dict, task: dict) -> dict:
    """The dispatched Task-B's post-write variety stamp: four dimensions + total, no rationales.

    Post-write means the incoming stamp when this write names variety, else disk (never a
    union). Resolution is shared with handoff_ordering_gate; only the projection is local.
    Read-only on every input.
    """
    post_write = variety_stamp_as_of_write(tool_input, task)
    return {k: post_write[k] for k in DISPATCH_VARIETY_KEYS if k in post_write}


def _emit_dispatch_site(
    input_data: dict,
    team_name: str,
    task_id: str,
    tool_input: dict,
    task: dict,
) -> None:
    """Emit ONE dispatch_site event per dispatched Task-B, at an owner-naming TaskUpdate.

    Membership is owner-witnessed, never inferred. Q5 reads only this stream (plus the
    journal_emitter spawn leg), so changing its shape or conditions changes what Q5 measures.
    The order of the ladder below is load-bearing (see the step comments and the docs file).
    Hermetic: never raises.
    """
    try:
        # (1) Frame gate is is_canonical_journal_frame, not is_lead (a lead without --agent has no
        # agent_type and must still emit).
        if not pact_context.is_canonical_journal_frame(input_data):
            return
        # (2) Team resolvable BEFORE the exemption legs. Subsumed today by (4), and pinned by a test:
        # if (4) ever failed open, an unresolvable team would make every secretary task a site. A
        # non-str team_name also fails at .lower() in the marker path, before any sanitizer.
        if not isinstance(team_name, str) or not team_name:
            return
        # (3) task_id resolvable.
        if not task_id:
            return
        # (4) Any owner-naming write witnesses the dispatch (not the composite owner+addBlockedBy
        # shape: real dispatch sets owner and blockers in split writes); owner resolves via config.
        if not is_owner_bearing_write(tool_input):
            return
        owner = tool_input.get("owner", "")
        # An owner written BEFORE its spawn (the usual order) is not a team
        # member yet and fails here. That is not a lost site: the Agent-spawn
        # leg in journal_emitter writes it (shared/dispatch_site.py).
        if not is_pact_specialist_owner(owner, team_name):
            return
        # (5) NOT a teachback Task-A gate (by subject).
        subject = task.get("subject") or "" if isinstance(task, dict) else ""
        if _is_teachback_subject(subject):
            return
        # (6) is_teachback_exempt, not is_self_complete_exempt: different policies, never recouple.
        if is_teachback_exempt(owner, team_name):
            return
        # (7) O_EXCL dedup claim on (team, task_id).
        if _dispatch_site_already_emitted(team_name, task_id):
            return
        # (8) We OWN the marker from here, so ANY failure below must roll it
        #     back — otherwise the claim is poisoned and this task's site is
        #     suppressed forever with no journal entry to show for it.
        try:
            fields: dict = {"task_id": str(task_id)}
            variety = _dispatch_site_variety(tool_input, task)
            if variety:
                fields["variety"] = variety
            written = append_event_checked(
                make_event("dispatch_site", **fields),
                "dispatch_site",
                str(task_id),
            )
        except Exception:
            written = False
        if not written:
            _dispatch_site_unclaim(team_name, task_id)
    except Exception:
        # Hermetic: a journal-emit failure must never break the gate's
        # advisory evaluation or its exit-0 contract.
        pass


# How recent a same-id task file must be to count as the write the current
# TaskUpdate just caused. PostToolUse runs right after the platform's write, so
# seconds suffice; a short window keeps a parallel session's same-id write out.
_B2_FRESH_WRITE_SECONDS = 30


def _resolve_b2_occupant(task_id: str, team_name: str) -> tuple[dict, bool]:
    """(task record, resolved) for the task a lead-frame TaskUpdate just wrote (M12).

    A stale context team can return another team's same-id file. Evidence is the write itself:
    one COMPLETED same-id file written in the last few seconds wins; none fresh reads as
    unresolved with no content; several -> unresolved, freshest subject kept, owner dropped.
    """
    own = read_task_json(task_id, team_name)
    safe_id = sanitize_path_component(str(task_id))
    if not safe_id:
        return own, True
    own_dir = team_name.lower() if team_name else ""
    own_mtime = 0.0
    fresh: list[tuple[float, dict]] = []
    now = time.time()
    store = TaskStore()
    if not store.root.is_dir():
        return own, True  # no store to cross-check against: the record read is this write's
    for team in store.teams():
        ref = TaskRef(team, safe_id)
        key, record = store.watch_key(ref), store.get(ref)
        if key is None or record is None:
            continue
        mtime = key.mtime_ns / 1e9
        if team == own_dir:
            own_mtime = mtime
        elif (isinstance(record, dict) and record.get("status") == "completed"
                and now - mtime <= _B2_FRESH_WRITE_SECONDS):
            fresh.append((mtime, record))
    fresh = [item for item in fresh if item[0] > own_mtime]
    if not fresh:
        if own and now - own_mtime <= _B2_FRESH_WRITE_SECONDS:
            return own, True
        # EVENTS' rule: nothing was written in the window, so the record read is not this
        # completion's. Record UNKNOWN with no content rather than a stale occupant's.
        return {"subject": "", "metadata": {"handoff": {}}}, False
    if len(fresh) == 1:
        return fresh[0][1], True
    freshest = max(fresh, key=lambda item: item[0])[1]
    return {"subject": freshest.get("subject"), "status": freshest.get("status"),
            "metadata": freshest.get("metadata") or {}}, False


def _emit_lead_side_agent_handoff(
    team_name: str,
    task_id: str,
    owner: str,
    subject: str,
    task_metadata: dict,
    resolved: bool = True,
) -> None:
    """Emit one agent_handoff event at the lead's acceptance-commit (b2, #869).

    b1 (TaskCompleted) never fires for a task that completed mid-turn, so b2 covers it.
    Eligibility mirrors b1 (owner, not teachback, not signal, handoff dict) and the dedup marker
    is shared, keyed on occupant + handoff content: a revision emits again, a duplicate resolves
    by latest timestamp. resolved=False records agent null, attribution "unresolved". Fail-open.
    """
    try:
        # #1165: sanitize task_id at intake like b1, so the event field and marker key match b1's.
        task_id = sanitize_path_component(str(task_id))
        # Eligibility mirrors b1; a whitespace-only owner would claim the marker then fail the schema.
        if not resolved:
            owner = ""
        elif not owner or not owner.strip():
            return
        if _is_teachback_subject(subject):
            return
        if is_signal_task(task_metadata):
            return
        # A non-dict handoff would claim the marker then fail the journal schema; defer instead.
        handoff = task_metadata.get("handoff")
        if not resolved:
            # b1 parity: an unresolved occupant is recorded even with no content.
            handoff = handoff if isinstance(handoff, dict) else {}
        elif not isinstance(handoff, dict) or not handoff:
            return
        # Substitute the sentinel before the claim. "(no subject)" is mirrored in
        # skills/pact-handoff-harvest/SKILL.md Step 3; change both together.
        if not subject or not subject.strip():
            subject = "(no subject)"
        # Same writability precondition as b1 (agent_handoff_emitter.main, its twin): never claim a
        # marker the write cannot follow. Contract: shared/agent_handoff_marker.already_emitted.
        if not get_journal_path():
            return
        # Hash the content from the same mapping passed to make_event (never a disk re-read) so the
        # key matches b1's.
        occupant = occupant_hash(owner, subject)
        content_key = handoff_content_key(handoff)
        if handoff_already_emitted(team_name, task_id, occupant, content_key):
            return
        # We own a fresh claim: roll it back if the write fails or raises, so a later fire can emit.
        try:
            written = append_event(
                make_event(
                    "agent_handoff",
                    agent=owner if resolved else None,
                    task_id=task_id,
                    task_subject=subject,
                    handoff=handoff,
                    **({} if resolved else {"attribution": UNRESOLVED_ATTRIBUTION}),
                )
            )
        except Exception:
            written = False
        if not written:
            handoff_unclaim(team_name, task_id, occupant, content_key)
    except Exception:
        # Fail-open: a journal-emit failure must never break the gate's
        # advisory evaluation or its exit-0 contract.
        pass


def _emit_per_write_snapshot(
    team_name: str,
    task_id: str,
    delta: dict,
    task: dict | None = None,
) -> None:
    """Per-write journal mirror (task_metadata_snapshot). Hermetic, read-only on its inputs; a
    None-valued delta key is a platform delete and is dropped from the overlay.
    """
    try:
        if task is None:
            task = read_task_json(task_id, team_name) if team_name else {}
        if not isinstance(task, dict):
            task = {}
        disk_md = (
            task.get("metadata")
            if isinstance(task.get("metadata"), dict)
            else {}
        )
        emit_task_metadata_snapshot(
            team_name,
            task_id,
            task.get("subject") or "",
            task.get("owner"),
            {**disk_md, **{k: v for k, v in delta.items() if v is not None}},
        )
    except Exception:
        # Exit-0 advisory contract: a mirror failure must never block or
        # annotate the hooked tool call.
        pass


def _created_task_id(tool_response: object, tool_input: dict) -> str:
    """Id of the task a TaskCreate just made: tool_response.task.id, else tool_input.taskId, else ""."""
    created_task = (
        tool_response.get("task") if isinstance(tool_response, dict) else None
    )
    new_task_id = ""
    if isinstance(created_task, dict):
        new_task_id = str(created_task.get("id") or "")
    if not new_task_id:
        new_task_id = str(tool_input.get("taskId") or "")
    return new_task_id


# ─── core evaluation ─────────────────────────────────────────────────────────


def _validate_variety_acknowledgment(ack: object) -> str | None:
    """None if variety_acknowledgment is well-formed (D10), else a short reason. Never raises."""
    if not isinstance(ack, dict):
        return f"must be object, got {type(ack).__name__}"
    value = ack.get("rationale_articulates_this_dispatch")
    if value not in TEACHBACK_VARIETY_ACK_VALID_VALUES:
        return (
            f"rationale_articulates_this_dispatch must be one of "
            f"{TEACHBACK_VARIETY_ACK_VALID_VALUES}, got {value!r}"
        )
    if value != "yes":
        concern = ack.get("concern")
        if not isinstance(concern, str) or not concern.strip():
            return (
                "concern field required (non-empty string) when "
                "rationale_articulates_this_dispatch != 'yes'"
            )
    return None


def _validate_teachback_submit_schema(teachback: object) -> str | None:
    """None if teachback_submit has the 5 canonical fields, else a short reason."""
    if not isinstance(teachback, dict):
        return (
            f"metadata.teachback_submit must be object, "
            f"got {type(teachback).__name__}"
        )
    missing = [f for f in TEACHBACK_REQUIRED_FIELDS if f not in teachback]
    if missing:
        return (
            f"metadata.teachback_submit missing required fields: "
            f"{', '.join(missing)}"
        )
    # Strings must be non-empty; object fields (TEACHBACK_OBJECT_FIELDS) have their own validator.
    string_fields = tuple(
        f for f in TEACHBACK_REQUIRED_FIELDS if f not in TEACHBACK_OBJECT_FIELDS
    )
    empty = [
        f for f in string_fields
        if not isinstance(teachback.get(f), str) or not teachback[f].strip()
    ]
    if empty:
        return (
            f"metadata.teachback_submit fields empty/non-string: "
            f"{', '.join(empty)}"
        )
    ack_problem = _validate_variety_acknowledgment(
        teachback.get("variety_acknowledgment")
    )
    if ack_problem:
        return (
            f"metadata.teachback_submit.variety_acknowledgment "
            f"{ack_problem}"
        )
    return None


def _validate_variety_schema(
    variety: object, metadata: object = None
) -> str | None:
    """None if metadata.variety is well-formed (D11), else a short reason. Never raises.

    Checks the four rationales, then that a total resolves via resolve_variety_total (the same
    resolver the read-time band uses, so anything valid at write time resolves at read time).
    """
    if not isinstance(variety, dict):
        return f"must be object, got {type(variety).__name__}"
    missing = [
        r for r in _VARIETY_REQUIRED_RATIONALES if r not in variety
    ]
    if missing:
        return (
            f"missing required per-dimension rationales: "
            f"{', '.join(missing)}"
        )
    empty = [
        r for r in _VARIETY_REQUIRED_RATIONALES
        if not isinstance(variety.get(r), str) or not variety[r].strip()
    ]
    if empty:
        return (
            f"per-dimension rationales empty/non-string: "
            f"{', '.join(empty)}"
        )
    if resolve_variety_total(variety, metadata) is None:
        return _NO_RESOLVABLE_TOTAL
    return None


def _band_from_total(total: int) -> str:
    """Map a resolved variety total to a reasoning_reconstruction band
    string ("required" / "recommended" / "skipped"). Shared by the direct
    Task-B resolution path and the C2 parent-inheritance fallback so both
    apply the identical threshold logic (one band-cut SSOT)."""
    if total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN:
        return "required"
    if total >= TEACHBACK_RECOMMENDED_BAND_MIN:
        return "recommended"
    return "skipped"


# A completed or deleted task is finished work: its variety stamp scored a
# dispatch that is over, so a new teachback must never be gated on it (M12,
# field report row 134). Open statuses (pending / in_progress / absent) read.
_CLOSED_TASK_STATUSES = frozenset({"completed", "deleted"})


def _is_closed_task(task: dict) -> bool:
    """True when the task's on-disk status is completed or deleted."""
    return task.get("status") in _CLOSED_TASK_STATUSES


def _inherit_band_from_parent(task_b: dict, team_name: str) -> str | None:
    """#891: Task B unstamped -> inherit the band from its parent (task_b.blocks[0] when it is a
    singleton and the parent carries a resolvable total), else None. Never guesses.
    """
    blocks = task_b.get("blocks")
    # Singleton-only: >1 entry is ambiguous (which is the parent?); 0/None
    # has no parent pointer. Either way fail open.
    if not isinstance(blocks, list) or len(blocks) != 1:
        return None
    parent_id = blocks[0]
    if not isinstance(parent_id, str) or not parent_id:
        return None
    parent = read_task_json(parent_id, team_name)
    if not parent or _is_closed_task(parent):
        return None
    parent_metadata = parent.get("metadata")
    if not isinstance(parent_metadata, dict):
        return None
    parent_variety = parent_metadata.get("variety")
    if not isinstance(parent_variety, dict):
        return None
    parent_total = resolve_variety_total(parent_variety, parent_metadata)
    if parent_total is None:
        return None
    return _band_from_total(parent_total)


def _resolve_required_band_via_blocks(
    task_a: dict, team_name: str
) -> str:
    """Band for reasoning_reconstruction via Task A -> Task B (blocks[0]).

    Returns required / recommended / skipped / unresolvable (no link, no or closed Task B, or no
    resolvable total on B and on its parent). Totals resolve via resolve_variety_total.
    """
    blocks = task_a.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return "unresolvable"
    # Convention: Task A blocks Task B (the work task). The first blocked
    # ID is the canonical work-task pointer in the Teachback-Gated Dispatch
    # shape — multi-block teachback tasks are not in current convention.
    task_b_id = blocks[0]
    if not isinstance(task_b_id, str) or not task_b_id:
        return "unresolvable"
    if not team_name:
        return "unresolvable"
    task_b = read_task_json(task_b_id, team_name)
    # A closed Task B is a stale pointer, not an unstamped one: concede
    # "unresolvable" without consulting the parent fallback either.
    if not task_b or _is_closed_task(task_b):
        return "unresolvable"
    metadata = task_b.get("metadata")
    # A non-dict metadata or variety on Task B also falls through to the parent inheritance.
    variety = metadata.get("variety") if isinstance(metadata, dict) else None
    total = (
        resolve_variety_total(variety, metadata)
        if isinstance(variety, dict)
        else None
    )
    if total is None:
        # Task B variety absent/malformed/untotaled → try parent inheritance
        # (Opt2) before conceding "unresolvable". Floor preserved: a parent
        # that also fails to resolve returns None → "unresolvable".
        return _inherit_band_from_parent(task_b, team_name) or "unresolvable"
    return _band_from_total(total)


def evaluate_lifecycle(input_data: dict) -> list[tuple[str, str]]:
    """Return a list of (rule, message) advisories; an empty list allows silently."""
    advisories: list[tuple[str, str]] = []
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input") or {}
    # extract_tool_response reads tool_response or the legacy tool_output; a miss here would
    # silently disable every advisory. Do not remove.
    tool_response = extract_tool_response(input_data)
    if not isinstance(tool_input, dict):
        tool_input = {}

    # ① Recursion guard (self-completion writeback self-trigger): skip
    # silently if THIS update is the gate's own writeback. Checked FIRST
    # before any other rule.
    incoming_metadata = tool_input.get("metadata") or {}
    if isinstance(incoming_metadata, dict) and incoming_metadata.get("gate_writeback") is True:
        return []

    # team_name resolved once; "" fails closed in both exemption predicates.
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""

    # (2a) #906 auditor-verdict MIRROR / RECOVER (doc: #906 auditor verdict).
    if tool_name == "TaskUpdate":
        incoming_audit = (
            incoming_metadata.get("audit_summary")
            if isinstance(incoming_metadata, dict)
            else None
        )
        ah_task_id = tool_input.get("taskId", "") or ""
        if incoming_audit is not None and team_name and ah_task_id:
            ah_task = read_task_json(ah_task_id, team_name)
            ah_meta = ah_task.get("metadata") if isinstance(ah_task, dict) else None
            authored = (
                ah_meta.get("audit_summary_authored")
                if isinstance(ah_meta, dict)
                else None
            )
            if resolve_actor(input_data, team_name).audit_verdict_mode() == "RECOVER":
                # RECOVER — only when a DIFFERENT authored verdict exists.
                if authored is not None and authored != incoming_audit:
                    downgrade = _is_destructive_audit_downgrade(
                        authored, incoming_audit
                    )
                    routed = _writeback_audit_recovery(
                        ah_task_id, {"lead_close_note": incoming_audit}
                    )
                    advisories.append((
                        "audit_summary_overwrite",
                        f"PACT task_lifecycle_gate: lead TaskUpdate "
                        f"{'DESTRUCTIVELY DOWNGRADED' if downgrade else 'overwrote'} "
                        f"Task {ah_task_id}'s auditor audit_summary. The auditor's "
                        "authored verdict is PRESERVED in "
                        "metadata.audit_summary_authored "
                        + ("and the lead's value was routed to metadata.lead_close_note — "
                           "the verdict is not lost. " if routed else
                           "but the lead's value could NOT be copied to lead_close_note. ")
                        + (
                            "DESTRUCTIVE DOWNGRADE (verdict severity lowered, "
                            "e.g. RED->GREEN / terminal->reopen): confirm the "
                            "auditor's concern was genuinely resolved before "
                            "closing. "
                            if downgrade
                            else ""
                        )
                        + "Never infer auditor silence from a read-after-write "
                        "timeout — the window is unbounded.",
                    ))
            else:
                # MIRROR — snapshot/refresh the authored verdict. Idempotent:
                # skip the FS write when the mirror already matches.
                if authored != incoming_audit and not _writeback_audit_recovery(
                        ah_task_id, {"audit_summary_authored": incoming_audit}):
                    advisories.append((
                        "audit_summary_mirror_failed",
                        f"PACT task_lifecycle_gate: Task {ah_task_id}'s audit_summary could "
                        "NOT be mirrored to audit_summary_authored; write it again.",
                    ))

    # ② TaskCreate rules — teachback addBlocks + work-task addBlockedBy
    if tool_name == "TaskCreate":
        subject = (tool_input.get("subject") or "")
        is_teachback = _is_teachback_subject(subject)
        owner = tool_input.get("owner") or ""
        if not isinstance(owner, str):
            owner = ""

        # Cheap checks first; the disk-reading exemption runs last and is cached for R4.
        _teachback_exempt: bool | None = None

        def _exempt() -> bool:
            nonlocal _teachback_exempt
            if _teachback_exempt is None:
                _teachback_exempt = is_teachback_exempt(owner, team_name)
            return _teachback_exempt

        if (
            not is_teachback
            and owner.startswith("pact-")
            and not tool_input.get("addBlockedBy")
            and not _exempt()
        ):
            advisories.append((
                "work_addblockedby_missing",
                f"PACT task_lifecycle_gate: pact-* Task created "
                f"(owner={owner!r}) without addBlockedBy=[<teachback_id>]. "
                "Work tasks must block on teachback acceptance.",
            ))

        # R4 keeps only the present-but-malformed variety checks; the absent-stamp arm moved to
        # handoff_ordering_gate (#865) where the wiring write is observable.
        if (
            not is_teachback
            and owner.startswith("pact-")
            and not _exempt()
        ):
            incoming_metadata = tool_input.get("metadata") or {}
            incoming_variety = incoming_metadata.get("variety")
            # ABSENT variety is no longer enforced here (moved to the wiring
            # write). Only validate a variety that IS present.
            if incoming_variety:
                # Rationale problem first, then the total problem (the _NO_RESOLVABLE_TOTAL sentinel).
                schema_problem = _validate_variety_schema(
                    incoming_variety, incoming_metadata
                )
                if schema_problem == _NO_RESOLVABLE_TOTAL:
                    advisories.append((
                        "variety_missing_on_dispatch_task",
                        f"PACT task_lifecycle_gate: pact-* Task created "
                        f"(owner={owner!r}) with metadata.variety carrying "
                        "no resolvable total (need an integer 4-16 under "
                        "key 'total', or a recoverable fallback). Per "
                        "workflows.md §Variety, the canonical key is 'total'. The "
                        "hook's read-time band resolver requires a "
                        "resolvable total; stamping without one trips a "
                        "band-unresolvable advisory at teachback-submit time.",
                    ))
                elif schema_problem:
                    advisories.append((
                        "variety_missing_on_dispatch_task",
                        f"PACT task_lifecycle_gate: pact-* Task created "
                        f"(owner={owner!r}) with malformed "
                        f"metadata.variety — {schema_problem}. Per D11 "
                        "schema, all four per-dimension rationales must "
                        "be present as non-empty strings.",
                    ))

        # #955 dispatch_variety emit on TaskCreate of a Task-B with metadata.variety (owner is still
        # empty here, so no owner gate). Canonical-frame gated; best-effort.
        if pact_context.is_canonical_journal_frame(input_data):
            create_variety = (
                incoming_metadata.get("variety")
                if isinstance(incoming_metadata, dict)
                else None
            )
            if isinstance(create_variety, dict) and create_variety:
                new_task_id = _created_task_id(tool_response, tool_input)
                if new_task_id:
                    # Project only the 4 dimensions + total (DISPATCH_VARIETY_KEYS), no rationales.
                    projected_variety = {
                        k: create_variety[k]
                        for k in DISPATCH_VARIETY_KEYS
                        if k in create_variety
                    }
                    # No dedup needed (a Task-B is created once). The bool is captured: a dropped event looks like
                    # an unstamped dispatch.
                    try:
                        append_event_checked(
                            make_event(
                                "dispatch_variety",
                                task_id=new_task_id,
                                variety=projected_variety,
                            ),
                            "dispatch_variety",
                            new_task_id,
                        )
                    except Exception:
                        pass  # fail-open: emit failure never breaks the gate

        # task_metadata_snapshot, TaskCreate leg: a created task carrying a PER_WRITE_MIRROR_KEYS key
        # is mirrored at once. Own frame gate, sibling of the block above, not nested.
        if (
            isinstance(incoming_metadata, dict)
            and any(k in PER_WRITE_MIRROR_KEYS for k in incoming_metadata)
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            new_task_id = _created_task_id(tool_response, tool_input)
            if new_task_id:
                _emit_per_write_snapshot(
                    team_name, new_task_id, incoming_metadata
                )

    # ③ TaskUpdate-to-completed rules — paired-send, handoff presence,
    # handoff schema, self-completion
    if tool_name == "TaskUpdate" and tool_input.get("status") == "completed":
        task_id = tool_input.get("taskId", "") or ""
        # Final post-state: prefer tool_response.task, else the bare dict.
        task = tool_response.get("task") if isinstance(tool_response, dict) else None
        if not isinstance(task, dict):
            # Fall back to disk if harness output didn't include the task.
            task = read_task_json(task_id, team_name) if team_name else {}

        subject = task.get("subject") or ""
        is_teachback = _is_teachback_subject(subject)
        owner = task.get("owner") or ""
        if not isinstance(owner, str):
            owner = ""
        raw_metadata = task.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

        # The work-task handoff PRESENCE check was retired (it keyed on a "pact-" owner prefix that
        # never occurs); the schema arm lives below as handoff_schema_invalid_at_completion.

        # b2 agent_handoff emit at acceptance-commit (#869); canonical-frame gated.
        if pact_context.is_canonical_journal_frame(input_data):
            # M12: a record read from disk goes through the session context's
            # team, which can be stale; re-resolve it before emitting. A
            # platform-supplied task is the post-state itself and stands.
            if not isinstance(tool_response.get("task") if isinstance(tool_response, dict) else None, dict):
                emit_task, emit_resolved = _resolve_b2_occupant(task_id, team_name)
            else:
                emit_task, emit_resolved = task, True
            emit_md = emit_task.get("metadata")
            _emit_lead_side_agent_handoff(
                team_name, task_id,
                emit_task.get("owner") if isinstance(emit_task.get("owner"), str) else "",
                emit_task.get("subject") or "",
                emit_md if isinstance(emit_md, dict) else {},
                emit_resolved,
            )
            # task_metadata_snapshot at lead completion, before the task-store drain. {**disk, **incoming}
            # mirrors the platform's shallow merge; None values are deletes and are dropped. Hermetic.
            try:
                incoming_md = (
                    incoming_metadata
                    if isinstance(incoming_metadata, dict)
                    else {}
                )
                emit_task_metadata_snapshot(
                    team_name,
                    task_id,
                    subject,
                    owner,
                    {
                        **metadata,
                        **{
                            k: v
                            for k, v in incoming_md.items()
                            if v is not None
                        },
                    },
                )
            except Exception:
                pass

        # artifact_paths backstop (#927, doc: Journal emits); fail-open, never blocks.
        if (
            metadata.get("skipped") is not True
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            requirement = _phase_artifact_requirement(subject)
            if requirement is not None:
                workflow, feature = requirement
                if not _artifact_paths_event_present(workflow, feature):
                    advisories.append((
                        "artifact_paths_emit_missing",
                        f"PACT task_lifecycle_gate: phase Task {task_id} "
                        f"({subject!r}) completed without an artifact_paths "
                        f"journal event for (workflow={workflow!r}, "
                        f"feature={feature!r}). The lead-frame emit (glob the "
                        f"phase's docs/ output + write --type artifact_paths) "
                        "appears to have been skipped — the GC-durable recovery "
                        "pointer is missing, so this phase's disk artifact will "
                        "degrade to HANDOFF-only at harvest. Emit the "
                        "artifact_paths event for this phase before the worktree "
                        "is torn down. Advisory only — not blocking.",
                    ))

        # Work-task HANDOFF schema advisory at completion: gated on present, dict and non-empty. A
        # non-dict handoff is covered only at write time; do not widen this guard (see docs).
        completed_handoff = metadata.get("handoff")
        if isinstance(completed_handoff, dict) and completed_handoff:
            handoff_problem = validate_handoff_schema(completed_handoff)
            if handoff_problem:
                advisories.append((
                    "handoff_schema_invalid_at_completion",
                    f"PACT task_lifecycle_gate: Task {task_id} "
                    f"{handoff_problem}. {HANDOFF_SCHEMA_ECHO}",
                ))

        # Teachback completion checks: R1 missing/empty, R2 present but malformed (disjoint).
        if is_teachback and owner:
            teachback_submit = metadata.get("teachback_submit")
            if not teachback_submit:
                advisories.append((
                    "teachback_submit_missing",
                    f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                    f"completed without metadata.teachback_submit. The "
                    "teammate skipped the canonical teachback gate "
                    "(teammate skill).",
                ))
            else:
                schema_problem = _validate_teachback_submit_schema(
                    teachback_submit
                )
                if schema_problem:
                    # Append the full canonical schema so one relayed rejection covers every sub-reason.
                    advisories.append((
                        "teachback_submit_schema_invalid",
                        f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                        f"{schema_problem}. {TEACHBACK_SCHEMA_ECHO}",
                    ))

            # #955 teachback_ack emit at the lead's accepting TaskUpdate(A, completed); the ack is read
            # from the on-disk Task A. Canonical-frame gated; best-effort.
            if isinstance(teachback_submit, dict) and pact_context.is_canonical_journal_frame(
                input_data
            ):
                ack = teachback_submit.get("variety_acknowledgment")
                if isinstance(ack, dict):
                    flag = ack.get("rationale_articulates_this_dispatch")
                    if isinstance(flag, str) and flag:
                        ack_fields = {
                            "task_id": str(task_id),
                            "rationale_articulates_this_dispatch": flag,
                        }
                        concern = ack.get("concern")
                        if isinstance(concern, str) and concern.strip():
                            ack_fields["concern"] = concern  # optional field
                        # No dedup needed (one acceptance per teachback); the bool is captured.
                        try:
                            append_event_checked(
                                make_event("teachback_ack", **ack_fields),
                                "teachback_ack",
                                str(task_id),
                            )
                        except Exception:
                            pass  # fail-open

            # A per-completion paired-wake detector was removed (#897): inboxes are written async after
            # this hook, so it was ~100% false positive. Do not restore it against the same store.

        # Teammate self-completion check. Carve-out is a single boolean
        # via is_self_complete_exempt(task, team_name) — the predicate is
        # the SSOT for both surfaces (team-config agentType + signal-task).
        actor = resolve_actor(input_data, team_name)
        if (
            owner
            and not actor.may_complete(owner)
            and not is_self_complete_exempt(task, team_name)
        ):
            marked = _writeback_dispute(task_id)
            who = (f"teammate {actor.name!r} self-completed" if actor.source == "host"
                   else f"an actor this frame cannot name ({actor.source}) completed")
            advisories.append((
                "self_completion",
                f"PACT task_lifecycle_gate: {who} "
                f"Task {task_id} without carve-out. "
                "Lead-only completion authority — see "
                "completion.md §Completion Authority. "
                + ("Task marked metadata.completion_disputed=true; the lead's own "
                   "TaskUpdate(status=\"completed\") on it clears the mark." if marked else
                   "The completion_disputed mark could NOT be written (task file busy): "
                   "treat the completion as disputed."),
            ))
        elif actor.is_lead:
            # The lead's completion is the acceptance: lift any dispute (read on disk, since the
            # response may omit metadata), so the claim gate counts this task as done again.
            _writeback_dispute(task_id, disputed=False)

    # (4) TaskUpdate write-time rules (status != completed, so they never double R1/R2). Rule names
    # are grep anchors for skills/teammate/SKILL.md's Common mistakes table; rename both together.
    if (
        tool_name == "TaskUpdate"
        and tool_input.get("status") != "completed"
    ):
        # handoff_ordering_gate already normalised these before the write (D3); idempotent.
        incoming_teachback = normalise_teachback(
            incoming_metadata.get("teachback_submit")
            if isinstance(incoming_metadata, dict)
            else None
        )[0]
        incoming_handoff = normalise_handoff(
            incoming_metadata.get("handoff")
            if isinstance(incoming_metadata, dict)
            else None
        )[0]

        # reasoning_reconstruction belongs on teachback_submit, never inside handoff.
        if isinstance(incoming_handoff, dict) and "reasoning_reconstruction" in incoming_handoff:
            task_id = tool_input.get("taskId", "") or ""
            advisories.append((
                "reasoning_reconstruction_in_handoff",
                f"PACT task_lifecycle_gate: Task {task_id} "
                "placed reasoning_reconstruction inside metadata.handoff "
                "(wrong slot). Correct destination depends on task type: "
                "for a Teachback Task, place reasoning_reconstruction at "
                "top-level on metadata.teachback_submit. For a work task "
                "carrying a HANDOFF, the HANDOFF schema has "
                "reasoning_chain (sender's view) — not "
                "reasoning_reconstruction (the receiver-side teachback "
                "field). See teammate skill Common mistakes row 2.",
            ))

        # Write-time HANDOFF schema advisory, to the author. An absent handoff key stays silent (None
        # is also the platform's delete).
        if incoming_handoff is not None:
            handoff_problem = validate_handoff_schema(incoming_handoff)
            if handoff_problem:
                task_id = tool_input.get("taskId", "") or ""
                advisories.append((
                    "handoff_schema_invalid_at_write_time",
                    f"PACT task_lifecycle_gate: Task {task_id} "
                    f"{handoff_problem}. {HANDOFF_SCHEMA_ECHO} Correct it with "
                    "one more TaskUpdate before the lead accepts — the write "
                    "has already landed, so nothing is lost and nothing is "
                    "blocked. Advisory only — not blocking.",
                ))

        # One shared task_a read for every write-time rule below.
        task_id = tool_input.get("taskId", "") or ""
        task_a = read_task_json(task_id, team_name) if team_name else {}
        if not isinstance(task_a, dict):
            task_a = {}
        subject = task_a.get("subject") or ""

        # dispatch_site emit: one event per dispatched Task-B; the helper owns the whole ladder.
        _emit_dispatch_site(input_data, team_name, task_id, tool_input, task_a)

        # Pair wiring by the hook; teachback_addblocks_missing fires only when wiring was refused and
        # the teachback task is still unwired ("none" stays silent).
        if tool_input.get("owner") and task_a.get("owner") == tool_input.get("owner"):
            outcome, detail = _wire_teachback_pair(task_id, tool_input.get("owner"), team_name)
            if outcome == "half":
                advisories.append((
                    "teachback_pair_half_wired",
                    f"PACT task_lifecycle_gate: {detail}, so the work task is NOT gated. "
                    "Fix it with TaskUpdate(<B_id>, addBlockedBy=[<A_id>]).",
                ))
            if outcome == "refused" and not tool_input.get("addBlocks"):
                unwired = [
                    t for t in iter_team_task_jsons(team_name)
                    if t.get("owner") == tool_input.get("owner")
                    and t.get("status") in _OPEN_TASK_STATUSES
                    and _is_teachback_subject(t.get("subject"))
                    and not t.get("blocks")
                ]
                if unwired:
                    ids = ", ".join(str(t.get("id", "")) for t in unwired)
                    advisories.append((
                        "teachback_addblocks_missing",
                        f"PACT task_lifecycle_gate: Teachback Task(s) {ids} "
                        f"left unwired: {detail}. Wire each by hand with "
                        "TaskUpdate(<A_id>, addBlocks=[<B_id>]).",
                    ))

        # task_id, subject, task_a are hoisted to L776-780 (sibling-of-cross-slot-handoff-check); this branch consumes the hoisted values.
        if isinstance(incoming_teachback, dict):
            if _is_teachback_subject(subject):
                # R5: variety_acknowledgment presence (schema is the next rule's job).
                if "variety_acknowledgment" not in incoming_teachback:
                    advisories.append((
                        "variety_acknowledgment_missing",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} submitted without "
                        "variety_acknowledgment. Per D10, the teammate "
                        "must record judgment of the orchestrator's "
                        "variety scoring before lead-side teachback "
                        "review. See teammate skill.",
                    ))
                else:
                    # Present but malformed variety_acknowledgment (disjoint with R5).
                    ack_problem = _validate_variety_acknowledgment(
                        incoming_teachback["variety_acknowledgment"]
                    )
                    if ack_problem:
                        advisories.append((
                            "variety_acknowledgment_schema_invalid_at_write_time",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} submitted with malformed "
                            f"variety_acknowledgment — {ack_problem}. "
                            "Per D10, variety_acknowledgment is an OBJECT "
                            "(NOT a free-text string) with "
                            f"rationale_articulates_this_dispatch in "
                            f"{TEACHBACK_VARIETY_ACK_VALID_VALUES} + concern "
                            "(when != 'yes'). See teammate skill "
                            "Common mistakes row 1.",
                        ))

                # reasoning_reconstruction present but malformed (disjoint with R3).
                if "reasoning_reconstruction" in incoming_teachback:
                    rr_problem = validate_reasoning_reconstruction(
                        incoming_teachback["reasoning_reconstruction"]
                    )
                    if rr_problem:
                        advisories.append((
                            "reasoning_reconstruction_subkeys_invalid",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} reasoning_reconstruction "
                            f"rejected — {rr_problem}. Canonical 3 "
                            f"sub-keys are {TEACHBACK_REQUIRED_SUBKEYS} "
                            "as non-empty strings. Lead will reject with "
                            "the same reason enum. See teammate "
                            "skill Common mistakes row 3.",
                        ))

                # intentional_wait must be top-level: missed_wake_scan does not look inside teachback_submit.
                if "intentional_wait" in incoming_teachback:
                    advisories.append((
                        "intentional_wait_nested_in_teachback_submit",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} placed intentional_wait inside "
                        "teachback_submit. It must be a top-level "
                        "metadata key (separate TaskUpdate call per "
                        "the canonical Step 1 / Step 3 ordering). No "
                        "missed-wake alarm will fire for your idle: "
                        "missed_wake_scan.py reads "
                        "metadata.intentional_wait and does not descend "
                        "into nested objects. is_self_complete_exempt "
                        "governs self-completion exemption, not the "
                        "wait. See teammate skill Common "
                        "mistakes row 4.",
                    ))

                # R3: reasoning_reconstruction required at the >= 11 band; an unresolvable band advises, not blocks.
                band = _resolve_required_band_via_blocks(task_a, team_name)
                if band == "required":
                    rr = incoming_teachback.get("reasoning_reconstruction")
                    if not isinstance(rr, dict) or not rr:
                        advisories.append((
                            "reasoning_reconstruction_missing_at_required_band",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} submitted at REQUIRED band "
                            "(Task B variety >= 11) without "
                            "reasoning_reconstruction. Per "
                            "teammate-teachback.md §When to "
                            "Method-Reconstruct, lead will reject; "
                            "revise before submission.",
                        ))
                elif band == "unresolvable":
                    advisories.append((
                        "reasoning_reconstruction_band_unresolvable",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} cannot resolve the variety band. One "
                        "of: Task A.blocks is missing/empty; team context "
                        "is unavailable; the Task B file is missing or "
                        "the task is already completed/deleted; or "
                        "Task B.metadata.variety is absent OR present but "
                        "carries no resolvable total (no integer 4-16 under "
                        "'total' or a recoverable fallback). Advisory only "
                        "— not blocking. If the variety stamp is the cause, "
                        "re-stamp Task B with a canonical "
                        "metadata.variety.total (see workflows.md §Variety, "
                        "Per-Dispatch Variety Stamping).",
                    ))

        # #956 backstop (doc: Journal emits); deliberately not idempotent on changed content.
        if (
            isinstance(incoming_handoff, dict)
            and incoming_handoff
            and isinstance(task_a, dict)
            and task_a
            and task_a.get("status") == "completed"
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            # M12: task_a came through the session context's team; re-resolve
            # it by the write this TaskUpdate just caused (see b2 above).
            bs_task, bs_resolved = _resolve_b2_occupant(task_id, team_name)
            if not bs_task:
                bs_task, bs_resolved = task_a, True
            owner_bs = bs_task.get("owner") or ""
            subject_bs = bs_task.get("subject") or ""
            # Pass the incoming handoff merged onto disk metadata (order-independent).
            meta_bs = bs_task.get("metadata") if isinstance(bs_task.get("metadata"), dict) else {}
            meta_for_emit = {**meta_bs, "handoff": incoming_handoff}
            _emit_lead_side_agent_handoff(
                team_name, task_id, owner_bs, subject_bs, meta_for_emit, bs_resolved
            )

        # task_metadata_snapshot post-completion backstop (doc: Journal emits); keep it separate from
        # the handoff backstop. Wider than the open-task legs: late lead keys count (late_mirror_key).
        if (
            isinstance(incoming_metadata, dict)
            and any(late_mirror_key(k) for k in incoming_metadata)
            and isinstance(task_a, dict)
            and task_a
            and task_a.get("status") == "completed"
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            try:
                disk_md = (
                    task_a.get("metadata")
                    if isinstance(task_a.get("metadata"), dict)
                    else {}
                )
                emit_task_metadata_snapshot(
                    team_name,
                    task_id,
                    task_a.get("subject") or "",
                    task_a.get("owner"),
                    {
                        **disk_md,
                        **{
                            k: v
                            for k, v in incoming_metadata.items()
                            if v is not None
                        },
                    },
                )
            except Exception:
                pass

        # task_metadata_snapshot open-task leg (doc: Journal emits). The predicate's team resolver and
        # this leg's persisted team differ on purpose; do not unify.
        if (
            isinstance(incoming_metadata, dict)
            and any(k in PER_WRITE_MIRROR_KEYS for k in incoming_metadata)
            and (
                task_a.get("status") != "completed"
                if isinstance(task_a, dict)
                else True
            )
            and pact_context.is_canonical_journal_frame(input_data)
        ):
            _emit_per_write_snapshot(
                team_name, task_id, incoming_metadata, task=task_a
            )

    return advisories


# ─── journal emit ────────────────────────────────────────────────────────────


def _journal_lifecycle_decision(
    input_data: dict, advisories: list[tuple[str, str]]
) -> None:
    """Write a lifecycle_decision journal event, only when there are advisories. Fail-open."""
    if not advisories:
        return
    try:
        rules = [rule for rule, _ in advisories]
        messages = [msg for _, msg in advisories]
        event = make_event(
            "lifecycle_decision",
            tool_name=input_data.get("tool_name", ""),
            advisories_count=len(advisories),
            rules=rules,
            advisories=messages,
            verdict="advisory",
        )
        # Bool captured (see append_event_checked): a dropped advisory record
        # reads downstream as a write the gate allowed without comment.
        append_event_checked(event, "lifecycle_decision")
    except Exception:
        pass


def _extend_launch_records_on_claim(input_data: dict) -> None:
    """On a claim, add the task to its owner's live background-work records. Best-effort; the
    owner comes from disk, the team from frame_team_and_name.
    """
    try:
        tool_input = input_data.get("tool_input")
        if input_data.get("tool_name") != "TaskUpdate" or not isinstance(tool_input, dict):
            return
        if tool_input.get("status") != "in_progress":
            return
        task_id = tool_input.get("taskId")
        if not task_id:
            return
        from shared.background_work import extend_records_for_claim, frame_team_and_name

        team_name, _name = frame_team_and_name(input_data)
        if not team_name:
            return
        task = read_task_json(str(task_id), team_name)
        owner = task.get("owner") if isinstance(task, dict) else None
        if not isinstance(owner, str) or not owner:
            return
        extend_records_for_claim(owner, task_id, team_name=team_name)
    except Exception:  # noqa: BLE001 — best-effort; the lifecycle verdict stands
        pass


# ─── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → no-op (input-side failure is harness's domain).
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Short-circuit on tools we don't care about (defensive — matcher should
    # already restrict to TaskCreate|TaskUpdate, but PostToolUse hooks can
    # be invoked from a co-located matcher entry alongside other tools).
    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("TaskCreate", "TaskUpdate"):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        # init() MUST run before evaluate_lifecycle(): the frame gates resolve the team through it,
        # and without it a canonical-frame emit is silently lost.
        pact_context.init(input_data)
        advisories = evaluate_lifecycle(input_data)
    except Exception as e:  # noqa: BLE001 — runtime catch-all → advisory
        _emit_load_failure_advisory("runtime", e, input_data)
        return  # unreachable; helper exits

    _extend_launch_records_on_claim(input_data)
    _journal_lifecycle_decision(input_data, advisories)

    if not advisories:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n\n".join(msg for _, msg in advisories),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
