#!/usr/bin/env python3
"""PreToolUse hook (matcher="TaskUpdate"): HANDOFF ordering, dispatch variety, rev type.

Branches: (5) write repair runs FIRST in every frame (benign handoff/teachback drift is
normalised and returned as updatedInput, one schema_coerced row each); (2) #865 dispatch
variety: a terminal wiring write (specialist owner AND addBlockedBy) to a Task B with no
resolvable variety DENIES (PACT_DISPATCH_VARIETY_MODE can opt down to warn/shadow);
(4) rev-type gate DENIES a handoff that is not an object or whose rev is not a positive
integer; (3) on a completion, a line stating the HANDOFF on disk; (1) #956 nudge when a
completion lands before its HANDOFF (only if (3) said nothing). (2) and (4) are the only
fail-closed paths; everything else, including module-load failure, fails open.

Map: _evaluate, _is_valid_rev, _render, _shown_id, _describe_rev, _metadata_object,
_string_metadata_names_handoff, _evaluate_handoff_rev, _handoff_disk_line,
_variety_stamp_attempted, _is_bare_null_delete, _variety_write_drops_disk_keys,
_evaluate_dispatch_variety, _normalise_write, _repair_line, _append_row,
_journal_coercions, _with_repair, _emit_warn, _emit_allow, _emit_deny, main.
Rationale: the design notes
"""

from __future__ import annotations

# ─── stdlib first (used on the input-side fail-open BEFORE wrapped imports) ─
import json
import re
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# #865 enforcement mode, read once via shared.pact_config.get_enum: unset -> "deny" (the
# shipped default), an unrecognised value -> "warn", warn = advisory, shadow = journal only.
# The fail-open guard below is deliberately "warn": a gate that cannot read its own config
# must not refuse writes. Why arming is safe: the design notes
try:
    import shared.pact_config as pact_config
    DISPATCH_VARIETY_MODE = pact_config.get_enum("PACT_DISPATCH_VARIETY_MODE")
except BaseException:  # noqa: BLE001 — fail-OPEN: resolver unavailable → safe default
    DISPATCH_VARIETY_MODE = "warn"

# Cap on the stdin read (bounds memory; an over-cap frame fails to parse and fails open).
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


# Fail-open import wrapper: on an import failure, suppress + exit 0, never deny.
try:
    import shared.pact_context as pact_context
    from shared.actor import resolve_actor
    from shared.dispatch_helpers import (
        is_pact_specialist_owner,
        is_owner_wiring_shape,
        variety_stamp_as_of_write,
    )
    from shared.intentional_wait import (
        is_self_complete_agent_type,
        is_self_complete_exempt,
    )
    from shared.handoff_schema import normalise_handoff
    from shared.task_utils import is_teachback_subject, read_task_json
    from shared.teachback_schema import normalise_teachback, resolve_variety_total
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-OPEN catch-all (warn gate never denies)
    _IMPORTS_OK = False

# The writer's NOT STORED line, appended to each rev-type deny reason. Its own
# guard, apart from the block above: the unparseable-frame deny is emitted
# before _IMPORTS_OK is read, and a deny must not depend on this line.
try:
    from shared.stored_line import refused as _not_stored
except BaseException:  # noqa: BLE001 — the deny keeps its own text
    def _not_stored(key: str, task_id: object = None) -> str:
        return ""


def _evaluate(input_data: dict) -> str | None:
    """Advisory when a lead-frame completion lands on a HANDOFF-expecting task whose handoff is
    absent in this write AND on disk (#956), else None. Never denies.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name != "TaskUpdate":
        return None  # matcher already scopes this, but be defensive

    # DUAL-MODE: lead frame only (shared.actor; a lead launched without --agent
    # counts). A teammate frame emits nothing.
    if not resolve_actor(input_data).sees_handoff_disk_line():
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    if tool_input.get("status") != "completed":
        return None  # only completion transitions

    # Is handoff being set in THIS same TaskUpdate? Then it is a bundled
    # handoff+complete — no race, no warn.
    incoming_metadata = tool_input.get("metadata")
    incoming_handoff = (
        incoming_metadata.get("handoff")
        if isinstance(incoming_metadata, dict)
        else None
    )
    if isinstance(incoming_handoff, dict) and incoming_handoff:
        return None

    # Read CURRENT on-disk task state (PreToolUse: the update has NOT applied
    # yet). team_name resolved via pact_context (init seeds the context path).
    task_id = tool_input.get("taskId", "") or ""
    if not task_id:
        return None
    try:
        pact_context.init(input_data)
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name:
        return None  # no team context → cannot resolve the task → bypass

    task = read_task_json(task_id, team_name)
    if not isinstance(task, dict) or not task:
        return None  # no task data → bypass (fail-open)

    # Handoff already on disk? Then completing is fine — no race.
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    existing_handoff = metadata.get("handoff")
    if isinstance(existing_handoff, dict) and existing_handoff:
        return None

    # HANDOFF-expecting = non-empty owner AND NOT (is_self_complete_exempt OR teachback subject).
    owner = task.get("owner") or ""
    if not isinstance(owner, str) or not owner.strip():
        return None  # no owner → not a teammate work task
    subject = task.get("subject") or ""
    # If SELF_COMPLETE_EXEMPT_AGENT_TYPES grows, re-check this suppression.
    if is_self_complete_exempt(task, team_name) or is_teachback_subject(subject):
        return None  # exempt → no handoff expected, no warn

    # HANDOFF-expecting + completing + handoff absent (neither incoming nor on
    # disk) = the #956 ordering mistake. WARN with an ACTIONABLE message.
    return (
        f"PACT handoff_ordering_gate: Task {task_id} ({subject!r}, owner {owner!r}) "
        "is being completed but has no metadata.handoff yet. The agent_handoff "
        "journal event keys on handoff presence at completion time — completing "
        "now risks losing it. EITHER (a) wait for / write the teammate's "
        "metadata.handoff BEFORE marking completed, OR (b) confirm this task is "
        "genuinely handoff-exempt. A write-time backstop will re-emit if handoff "
        "is set later, but the cleanest path is handoff-then-complete."
    )


# ─── HANDOFF rev + disk-state line ─────────────────────────────────────────
# The task store holds rev as a JS number, which is exact only up to 2^53-1.
_REV_MAX = 2**53 - 1
_RENDER_MAX = 40
_SHOWN_ID_MAX = 32


def _is_valid_rev(rev: object) -> bool:
    """True iff rev is a positive integer the task store holds exactly. The
    exact-type check refuses bool (an int subclass) and every float."""
    return type(rev) is int and 1 <= rev <= _REV_MAX


def _render(value: object) -> str:
    """Bounded repr of an untrusted metadata value for hook output: repr
    escapes newlines and control characters, the cap bounds the length."""
    try:
        text = repr(value)
    except Exception:
        return "<unprintable>"
    return text if len(text) <= _RENDER_MAX else text[:_RENDER_MAX] + "..."


def _shown_id(task_id: str) -> str:
    """The task id as printed: id characters only, bounded."""
    return "".join(ch for ch in task_id if ch.isalnum() or ch in "_-")[:_SHOWN_ID_MAX] or "?"


def _describe_rev(handoff: dict) -> str:
    """'rev N', 'rev missing', or the offending value named as such."""
    if "rev" not in handoff:
        return "rev missing"
    rev = handoff["rev"]
    if _is_valid_rev(rev):
        return f"rev {rev}"
    return f"rev {_render(rev)} (not a positive integer)"


# The handoff key written as an object key, in text that cannot be parsed (a
# frame, or a metadata value sent as a JSON string). Inside a JSON string
# every quote is escaped, so text that only quotes the key never matches.
_NAMES_HANDOFF = re.compile(r'(?<!\\)"handoff"\s*:')
# Unparseable frame: metadata sent as a JSON string keeps its keys behind ONE level of
# escaping; quoted text elsewhere does not match.
_STRING_METADATA = re.compile(
    r'(?<!\\)"metadata"\s*:\s*(?="(?:\s|\\[trn]|\\u[fF][eE][fF][fF])*\{)'
)
_ESCAPED_HANDOFF_OBJECT = re.compile(r'\\"handoff\\"\s*:\s*\{')
_STRING_DECODER = json.JSONDecoder(strict=False)
_HANDOFF_SHAPE_REASON = (
    "PACT handoff_ordering_gate: HANDOFF write returned, nothing was written: "
    "metadata.handoff must be an object with an integer rev. Fix: Re-send the "
    'FULL handoff as an object, e.g. {"rev": 1, "produced": [...]}, with rev '
    "as a bare integer, then read rev back from the task file before you notify."
    f" {_not_stored('handoff')}"
).rstrip()


def _metadata_object(metadata: object) -> dict | None:
    """The metadata object of this write (parsed out of a JSON string if needed), else None.
    Raises ValueError for a string object that names the handoff key and cannot be parsed.
    """
    if isinstance(metadata, dict):
        return metadata
    if not isinstance(metadata, str):
        return None
    try:
        parsed = json.loads(metadata)
    except (ValueError, RecursionError):
        if metadata.lstrip("\ufeff \t\r\n").startswith("{") and _NAMES_HANDOFF.search(metadata):
            raise ValueError("metadata string names handoff and cannot be parsed") from None
        return None
    return parsed if isinstance(parsed, dict) else None


def _string_metadata_names_handoff(raw: str) -> bool:
    """True iff an unparseable frame holds metadata-as-JSON-string whose handoff key opens an
    object (a frame cut inside the string reads the rest as its body).
    """
    for found in _STRING_METADATA.finditer(raw):
        start = found.end()
        try:
            end = _STRING_DECODER.raw_decode(raw, start)[1]
        except ValueError:
            return bool(_ESCAPED_HANDOFF_OBJECT.search(raw, start))
        if _ESCAPED_HANDOFF_OBJECT.search(raw, start, end):
            return True
    return False


def _evaluate_handoff_rev(input_data: dict) -> str | None:
    """Deny reason when this write's metadata.handoff is not an object, or its rev is present and
    not a positive integer; else None.

    Keys on the value, in every frame. A null handoff (delete) passes; an absent rev passes
    (the secretary template has none). Pure; the caller fails open on a raise.
    """
    if input_data.get("tool_name") != "TaskUpdate":
        return None
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    try:
        metadata = _metadata_object(tool_input.get("metadata"))
    except ValueError:
        return _HANDOFF_SHAPE_REASON
    if metadata is None or metadata.get("handoff") is None:
        return None
    handoff = metadata["handoff"]
    if not isinstance(handoff, dict):
        return _HANDOFF_SHAPE_REASON
    if "rev" not in handoff:
        return None
    rev = handoff["rev"]
    if _is_valid_rev(rev):
        return None
    task_id = tool_input.get("taskId")
    shown = _shown_id(task_id) if isinstance(task_id, str) else "?"
    return (
        f"PACT handoff_ordering_gate: HANDOFF write on Task #{shown} returned, "
        f"nothing was written: metadata.handoff.rev is {_render(rev)} "
        f"({type(rev).__name__}), and rev must be a positive integer (1 on the "
        "first write, +1 on each revision, at most 2^53-1). Fix: Re-send the "
        'FULL handoff object with rev as a bare integer, e.g. "rev": 2, then '
        "read rev back from the task file before you notify."
        f" {_not_stored('handoff', shown)}"
    ).rstrip()


def _describe_digest(handoff: dict) -> str:
    """`, digest <d>` for the HANDOFF on disk: the digest its stored line gave the teammate,
    so the lead can match the notify. Empty when no digest can be computed."""
    try:
        from shared.payload_digest import payload_digest

        digest = payload_digest(handoff)
    except Exception:  # noqa: BLE001 - the line is advisory; it is stated without a digest
        digest = None
    return f", digest {digest}" if digest else ""


def _handoff_disk_line(input_data: dict) -> str | None:
    """HANDOFF disk-state line for a completion write, else None.

    Lead frame, or the self-completing secretary's own write. Reads the before-state, which is
    what the completion accepts. Never denies or raises; an unreadable file yields a visible
    'check skipped' line in the lead frame.
    """
    if input_data.get("tool_name") != "TaskUpdate":
        return None
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("status") != "completed":
        return None
    task_id = tool_input.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        return None

    lead = resolve_actor(input_data).sees_handoff_disk_line()
    try:
        pact_context.init(input_data)
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name:
        return None  # no team context → not a PACT task write → bypass

    shown = _shown_id(task_id)
    prefix = "PACT handoff_ordering_gate: "
    # Without the task there is no owner to resolve, so only the lead frame
    # (which needs no owner to qualify) gets the skipped line.
    skipped = (
        f"{prefix}HANDOFF check skipped for #{shown}: the task file is missing "
        "or unreadable. This completion is not blocked; read the task file "
        "before you rely on the HANDOFF."
    ) if lead else None
    try:
        task = read_task_json(task_id, team_name)
        if not isinstance(task, dict) or not task:
            return skipped

        owner = task.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            return None  # no owner → not a teammate work task
        subject = task.get("subject")
        if isinstance(subject, str) and is_teachback_subject(subject):
            return None  # Task A carries a teachback, not a HANDOFF
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("completion_type") == "signal":
            return None  # the task IS the signal; no HANDOFF expected
        if not lead and not is_self_complete_agent_type(owner, team_name):
            return None

        incoming_metadata = tool_input.get("metadata")
        incoming = (
            incoming_metadata.get("handoff")
            if isinstance(incoming_metadata, dict)
            else None
        )
        if isinstance(incoming, dict) and incoming:
            return (
                f"{prefix}HANDOFF in this write for #{shown}: "
                f"{_describe_rev(incoming)}. It is on disk only if this write "
                "is not returned."
            )
        on_disk = metadata.get("handoff")
        if isinstance(on_disk, dict) and on_disk:
            return (
                f"{prefix}HANDOFF on disk: {_describe_rev(on_disk)}"
                f"{_describe_digest(on_disk)} (task #{shown}). This completion "
                "accepts that copy; it is the notified one when the notify "
                "names the same rev and digest."
            )
        return (
            f"{prefix}NO HANDOFF ON DISK for #{shown}: the write was returned "
            "or never landed. Write metadata.handoff (the lead: from the "
            "notify's payload) before you rely on this completion."
        )
    except Exception:
        return skipped


def _variety_stamp_attempted(*metadatas: object) -> bool:
    """True iff any mapping carries an intended variety stamp (a `variety` key of any shape, or
    `variety_score`). Shape-blind on purpose; it only picks the deny sentence. Pure.
    """
    for metadata in metadatas:
        if isinstance(metadata, dict) and (
            "variety" in metadata or "variety_score" in metadata
        ):
            return True
    return False


def _is_bare_null_delete(
    disk_metadata: object, incoming_metadata: object,
) -> bool:
    """True iff this write deletes metadata.variety and the disk had no stamp attempt.

    A None supplies no value, so the remedy is "stamp the block", not "re-read the values".
    Reached only after the partial-replace check. Pure.
    """
    if not isinstance(incoming_metadata, dict):
        return False
    if "variety" not in incoming_metadata:
        return False
    if incoming_metadata["variety"] is not None:
        return False
    return not _variety_stamp_attempted(disk_metadata)


def _variety_write_drops_disk_keys(
    disk_metadata: object, incoming_metadata: object,
) -> bool:
    """True iff this write names metadata.variety and drops a disk stamp key.

    TaskUpdate replaces the nested dict, so a partial block (or a None delete) loses keys while
    the values are fine: the remedy is "send the whole block". Partialness, not containment
    (the disjoint case counts). Needs a non-empty disk stamp; a same-key overwrite is a value
    problem, not this. Only picks the deny sentence. Pure.
    """
    if not isinstance(incoming_metadata, dict):
        return False
    if "variety" not in incoming_metadata:
        return False
    disk_variety = (
        disk_metadata.get("variety") if isinstance(disk_metadata, dict) else None
    )
    if not isinstance(disk_variety, dict) or not disk_variety:
        return False
    incoming_variety = incoming_metadata["variety"]
    if incoming_variety is None:
        return True
    if not isinstance(incoming_variety, dict):
        return False
    return bool(set(disk_variety) - set(incoming_variety))


def _evaluate_dispatch_variety(input_data: dict) -> str | None:
    """#865: advisory when a terminal dispatch-wiring write links a Task B with no resolvable
    variety, else None (the caller picks warn/deny/shadow).

    Fires only when ONE tool_input has an owner resolving to a pact specialist AND a non-empty
    addBlockedBy. Reads Task B's stamp from disk overlaid with this write. Malformed rationales
    stay task_lifecycle_gate R4's concern. One trigger, three deny sentences (see docs).
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name != "TaskUpdate":
        return None  # matcher already scopes this, but be defensive


    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    # Composite shape (shared.dispatch_helpers.is_owner_wiring_shape): owner AND addBlockedBy in
    # one write; cheap in-memory checks first, owner resolution (disk) after the team resolve.
    if not is_owner_wiring_shape(tool_input):
        return None  # partial wiring (owner-only or blockers-only) → not terminal
    # Guaranteed a non-empty, non-whitespace str by the shape predicate above.
    owner = tool_input.get("owner", "")

    task_id = tool_input.get("taskId", "") or ""
    if not task_id:
        return None
    try:
        pact_context.init(input_data)
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name:
        return None  # no team context → cannot resolve owner/Task B → bypass

    # ORDER: after pact_context.init(), or the topology leg resolves "" and the frame gate
    # silently becomes is_lead. Frame gate is is_canonical_journal_frame (matches the
    # dispatch_site emit): admits the lead and in-process teammates, not tmux teammates.
    if not pact_context.is_canonical_journal_frame(input_data):
        return None

    # Bare owner -> team member -> agentType (never a "pact-" prefix). Unresolvable -> False ->
    # this gate fails open. The secretary passes here and is exempted below.
    if not is_pact_specialist_owner(owner, team_name):
        return None  # owner does not resolve to a pact specialist → not a dispatch

    task = read_task_json(task_id, team_name)
    if not isinstance(task, dict) or not task:
        return None  # no task data → bypass (fail-open)

    # Carve-outs: is_self_complete_exempt (secretary, signal tasks) and the teachback subject.
    # Judge the exemption against the owner AS OF THIS WRITE (disk owner is still empty at the
    # wiring write; reading it alone over-blocked every exempt owner). Read-only overlay; the
    # shared predicate is deliberately unchanged.
    subject = task.get("subject") or ""
    task_as_of_this_write = {**task, "owner": owner}
    if (
        is_self_complete_exempt(task_as_of_this_write, team_name)
        or is_teachback_subject(subject)
    ):
        return None

    # Stamp AS OF THIS WRITE: disk overlaid with tool_input, shared with the dispatch_site emit
    # (variety_stamp_as_of_write), so a write that wires and stamps at once is not refused.
    disk_metadata = task.get("metadata")
    incoming_metadata = tool_input.get("metadata")
    variety = variety_stamp_as_of_write(tool_input, task)
    # `metadata` feeds only the scalar variety_score candidate, so a metadata-level merge is safe;
    # `variety` is re-seated from the key-level merge.
    metadata: dict = {}
    for source in (disk_metadata, incoming_metadata):
        if isinstance(source, dict):
            metadata.update(source)
    metadata["variety"] = variety
    if resolve_variety_total(variety, metadata) is not None:
        return None  # stamp resolves → not a missing-stamp dispatch

    # Three states behind one None, three deny sentences (all deny). Partial-replace is tested
    # first because it is a subset of "attempted".
    if _variety_write_drops_disk_keys(disk_metadata, incoming_metadata):
        diagnosis = (
            "REPLACES the stamp already on disk without carrying it whole. "
            "TaskUpdate merges metadata at the TOP-LEVEL key, so naming "
            "variety overwrites the whole nested dict and every key this "
            "write does not name is DROPPED — and naming it with a null "
            "removes the stamp outright. Post-write the task holds only what "
            "this write sent, which does not resolve to a total. The values "
            "sent are fine; the SHAPE of the write is the defect. Re-send the "
            "ENTIRE block (novelty/scope/uncertainty/risk + total 4-16) "
            "rather than only the keys being changed"
        )
    # A null delete over a disk with no attempt falls through to "stamp the block".
    elif (
        _variety_stamp_attempted(disk_metadata, incoming_metadata)
        and not _is_bare_null_delete(disk_metadata, incoming_metadata)
    ):
        diagnosis = (
            "carries a metadata.variety stamp that does NOT resolve to a "
            "total. resolve_variety_total accepts, in order: variety.total, "
            "variety.score, metadata.variety_score, or all four of "
            "novelty/scope/uncertainty/risk — each an int (not a bool, not a "
            "numeric string) in range, 4-16 for a total and 1-4 for a "
            "dimension. The stamp is present, so re-read the VALUES rather "
            "than adding the block again"
        )
    else:
        diagnosis = (
            "carries no metadata.variety stamp at all. Stamp the D11 "
            "4-rationale block (novelty/scope/uncertainty/risk + total 4-16) "
            "on this Task B — either at TaskCreate or in this same wiring "
            "write — mirroring the block in orchestrate.md / comPACT.md / "
            "peer-review.md / plan-mode.md"
        )
    return (
        f"PACT dispatch-variety gate: Task {task_id} ({subject!r}) is being "
        f"wired into a teachback-gated dispatch (owner {owner!r}) and "
        f"{diagnosis}. Per-dispatch variety stamping is required so the hook "
        "can resolve the reasoning_reconstruction band and the "
        "concurrent-auditor trigger. To opt this enforcement down, set "
        "PACT_DISPATCH_VARIETY_MODE=warn (advisory) or =shadow (journal "
        "only); it defaults to deny."
    )


# The repaired tool_input and its line, set by main() when _normalise_write
# repaired the write; every allow path below carries them.
_REPAIR: dict | None = None


def _normalise_write(input_data: dict) -> tuple[dict, list[dict]]:
    """(input_data with its TaskUpdate metadata repaired, the coercion rows).
    Unchanged input and [] when the write is not a TaskUpdate, carries no
    metadata object, or has nothing the two normalisers repair. Metadata sent
    as a JSON string comes back as an object. Pure; the caller fails open."""
    if input_data.get("tool_name") != "TaskUpdate":
        return input_data, []
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict):
        return input_data, []
    try:
        metadata = _metadata_object(tool_input.get("metadata"))
    except ValueError:
        return input_data, []
    if metadata is None:
        return input_data, []
    repaired = dict(metadata)
    coercions: list[dict] = []
    for key, normalise in (("handoff", normalise_handoff),
                           ("teachback_submit", normalise_teachback)):
        if key in metadata:
            value, rows = normalise(metadata[key])
            if rows:
                repaired[key] = value
                coercions.extend(rows)
    if not coercions:
        return input_data, []
    return {**input_data, "tool_input": {**tool_input, "metadata": repaired}}, coercions


def _repair_line(task_id: object, coercions: list[dict]) -> str:
    shown = _shown_id(task_id) if isinstance(task_id, str) else "?"
    done = "; ".join(f"{row['key']}.{row['field']}: {row['coercion']}" for row in coercions)
    return (f"PACT handoff_ordering_gate: repaired your write on Task #{shown} before it "
            f"landed ({done}). The task file holds the repaired object. Write the canonical "
            "shape next time (teammate skill, Common mistakes).")


def _append_row(event: dict, task_id: str) -> None:
    """The journal write of one schema_coerced row (its own seam for tests)."""
    from shared.session_journal import append_event_checked
    append_event_checked(event, "schema_coerced", task_id=task_id)


def _journal_coercions(input_data: dict, coercions: list[dict]) -> None:
    """One schema_coerced row per repair: field and kind, never the value.
    Best effort: a journal that cannot be written loses the row, not the write."""
    from shared.session_journal import make_event
    pact_context.init(input_data)
    tool_input = input_data.get("tool_input") or {}
    task_id = tool_input.get("taskId") if isinstance(tool_input, dict) else None
    task_id = task_id if isinstance(task_id, str) else ""
    for row in coercions:
        _append_row(make_event("schema_coerced", hook="handoff_ordering_gate", task_id=task_id,
                               key=row["key"], field=row["field"], coercion=row["coercion"]),
                    task_id)


def _with_repair(specific: dict) -> dict:
    """hookSpecificOutput with the repaired input and its line added, when there is one."""
    if _REPAIR is None:
        return specific
    context = "\n".join(filter(None, (_REPAIR["line"], specific.get("additionalContext"))))
    return {**specific, "updatedInput": _REPAIR["tool_input"], "additionalContext": context}


def _emit_warn(advisory: str) -> None:
    """WARN output path: additionalContext advisory + exit 0 (never denies).
    Shared by the #956 nudge and the dispatch-variety warn mode."""
    print(json.dumps({
        "hookSpecificOutput": _with_repair({
            "hookEventName": "PreToolUse",
            "additionalContext": advisory,  # advisory — NOT permissionDecision
        })
    }))
    sys.exit(0)  # exit 0 — advisory, never deny / exit-2


def _emit_allow() -> None:
    """ALLOW output path: the repaired input and its line, or suppress; exit 0."""
    if _REPAIR is None:
        print(_SUPPRESS_OUTPUT)
    else:
        print(json.dumps({"hookSpecificOutput": _with_repair({"hookEventName": "PreToolUse"})}))
    sys.exit(0)


def _emit_deny(reason: str) -> None:
    """DENY output path: permissionDecision deny + exit 2."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(2)


def main() -> None:
    global _REPAIR
    _REPAIR = None   # main() can run more than once in one process (tests)
    # Input-side fail-open, except a frame naming metadata.handoff that cannot be parsed (or
    # metadata-as-string carrying a handoff object): that is refused, its rev unchecked.
    try:
        raw = sys.stdin.read(_STDIN_READ_MAX)
    except (ValueError, OSError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    try:
        input_data = json.loads(raw)
    except (ValueError, RecursionError):
        if _NAMES_HANDOFF.search(raw) or _string_metadata_names_handoff(raw):
            _emit_deny(_HANDOFF_SHAPE_REASON)
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not _IMPORTS_OK or not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Write repair (branch 5) FIRST, so every branch below judges the write
    # the task file will hold. Fail-OPEN: a logic error leaves the write as
    # sent, and the branches judge it as before.
    try:
        repaired, coercions = _normalise_write(input_data)
    except Exception:
        repaired, coercions = input_data, []
    if coercions:
        input_data = repaired
        tool_input = input_data["tool_input"]
        _REPAIR = {"tool_input": tool_input,
                   "line": _repair_line(tool_input.get("taskId"), coercions)}
        try:
            _journal_coercions(input_data, coercions)
        except Exception:
            pass

    # HANDOFF rev-type gate: a deny on a confirmed non-integer rev, in every
    # frame. Fail-OPEN on any logic error, like every branch below.
    try:
        rev_problem = _evaluate_handoff_rev(input_data)
    except Exception:
        rev_problem = None
    if rev_problem:
        _emit_deny(rev_problem)

    # #865 branch first: a denied wiring write is refused before any completion nudge. Logic
    # errors fail open.
    try:
        variety_gap = _evaluate_dispatch_variety(input_data)
    except Exception:
        variety_gap = None  # fail-OPEN on any logic error
    if variety_gap:
        if DISPATCH_VARIETY_MODE == "deny":
            # Deny (shipped default). Honoured and atomic (observed 2026-07-28): the caller must re-issue
            # the whole write.
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": variety_gap,
                }
            }))
            sys.exit(2)
        if DISPATCH_VARIETY_MODE == "warn":
            _emit_warn(variety_gap)
        # shadow → fall through to suppress (journal-only telemetry is the
        # PostToolUse R4/journal surface; here shadow simply does not surface).

    # Completion branches: disk-state line, then the #956 nudge only if the line said nothing.
    # Each fails open on its own.
    for branch in (_handoff_disk_line, _evaluate):
        try:
            line = branch(input_data)
        except Exception:
            line = None
        if line:
            _emit_warn(line)

    _emit_allow()


if __name__ == "__main__":
    main()
