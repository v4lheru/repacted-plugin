"""
Location: pact-plugin/hooks/shared/ops/submit.py
Summary: `repact submit`, the coder's one call, from Bash so it
works where task tools do not (`claude -p`). `teachback` stores metadata.teachback and accepts
it at once in auto mode when the answer is yes; `handoff` repairs what is mechanical (rev, a
relative produced path, a missing list key), refuses everything else in ONE message, stores
metadata.handoff and previews what accept will say (exit 3 when accept would refuse).
Used by: bin/_repact_ops.py; the SubagentStop backstop (caller kind backstop,
relayed=True).

Input is hostile until checked: the CLI parses it strictly (task_store._parse: no NaN or
Infinity, no integer over 4300 digits), and this module caps its size, refuses a key that only
looks like a canonical one (NFKC or case), refuses placeholders left from the skeleton, and
refuses a produced path that resolves outside the task's worktree.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import PurePosixPath

from shared import amendments as AM, ref_dispositions as RD
from shared.handoff_schema import HANDOFF_CANONICAL_FIELDS, normalise_handoff
from shared.teachback_schema import (
    TEACHBACK_OBJECT_FIELDS,
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
    normalise_teachback,
    validate_reasoning_reconstruction,
)

from . import OpError, Result, frame, records, sides
from . import evidence as EV
from .accept import ac_problems
from .brief import is_placeholder

TEACHBACK_MAX = 64 * 1024
HANDOFF_MAX = 256 * 1024
LIST_KEYS = ("decisions", "uncertainty", "open_questions")
REQUIRED_KEYS = ("produced", "integration")
PREVIEW_SHOWN = 4
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085  ]")
_TAIL = re.compile(r":\d+(?::\d+)?$")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _key_problems(obj: dict, canonical, where: str) -> list:
    """A key with a control character, or one that folds to a canonical key it is not."""
    folded = {_fold(key): key for key in canonical}
    problems = []
    for key in obj:
        if _CONTROL.search(key):
            problems.append(f"{where}: key {key!r} holds a control character")
        elif key not in canonical and _fold(key) in folded:
            problems.append(f"{where}: key {key!r} looks like {folded[_fold(key)]!r}; spell it exactly")
    return problems


def _size_problem(obj, limit: int, what: str) -> list:
    try:
        size = len(json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return [f"{what} is not plain JSON"]
    return [f"{what} is {size} bytes; the most is {limit}"] if size > limit else []


def _placeholders(value, where: str) -> list:
    if is_placeholder(value):
        return [f"{where} is still the skeleton's placeholder"]
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in _placeholders(item, f"{where}.{key}")]
    if isinstance(value, list):
        return [p for index, item in enumerate(value) for p in _placeholders(item, f"{where}[{index}]")]
    return []


def _open(task: dict, caller) -> dict:
    frame.check("submit", caller, task)
    dispatched = records.dispatched(task)
    if task.get("status") != "in_progress":
        raise OpError("ops.wrong_state", [f"task #{task.get('id')} is {task.get('status')}, not in_progress"])
    return dispatched


# -- the teachback ---------------------------------------------------------------------------

def check_teachback(teachback, total: int) -> list:
    if not isinstance(teachback, dict):
        return ["the teachback must be a JSON object"]
    problems = _size_problem(teachback, TEACHBACK_MAX, "the teachback")
    problems += _key_problems(teachback, TEACHBACK_REQUIRED_FIELDS + ("reasoning_reconstruction",), "teachback")
    for field in TEACHBACK_REQUIRED_FIELDS:
        value = teachback.get(field)
        if field in TEACHBACK_OBJECT_FIELDS:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"teachback.{field} must be a non-empty string")
    ack = teachback.get("variety_acknowledgment")
    answer = ack.get("rationale_articulates_this_dispatch") if isinstance(ack, dict) else None
    if answer not in TEACHBACK_VARIETY_ACK_VALID_VALUES:
        problems.append("teachback.variety_acknowledgment.rationale_articulates_this_dispatch must be "
                        "yes, no or concern")
    elif answer != "yes" and not (isinstance(ack.get("concern"), str) and ack["concern"].strip()):
        problems.append("teachback.variety_acknowledgment.concern must say why when the answer is not yes")
    reconstruction = teachback.get("reasoning_reconstruction")
    if reconstruction is not None or total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN:
        why = validate_reasoning_reconstruction(reconstruction)
        if why:
            problems.append(f"teachback.reasoning_reconstruction: {why} (required at variety "
                            f"{TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+)")
    return problems + _placeholders(teachback, "teachback")


def teachback(ctx: records.Context, task_id: str, given) -> Result:
    task = ctx.task(task_id)
    dispatched = _open(task, ctx.caller)
    given, _repairs = normalise_teachback(given)
    total = int((dispatched.get("variety") or {}).get("total") or 0)
    problems = check_teachback(given, total)
    if problems:
        raise OpError("ops.invalid_input", problems)
    stored = records.metadata(task).get("teachback")
    if isinstance(stored, dict) and stored.get("accepted") and stored.get("submit") != given:
        raise OpError("ops.wrong_state", [f"the teachback on #{task_id} is already accepted; go on with the work"])
    answer = given["variety_acknowledgment"]["rationale_articulates_this_dispatch"]
    auto = dispatched.get("teachback") == "auto" and answer == "yes"

    def mutate(current: dict) -> None:
        if (current["metadata"].get("teachback") or {}).get("accepted"):
            return  # the same teachback, already accepted: keep the acceptance
        at = records.now()
        current["metadata"]["teachback"] = {"submit": given, "at": at,
                                            "accepted": {"by": "auto", "at": at} if auto else None}

    op_id = records.op_id_of("submit_teachback", task_id, records.digest(given))
    records.execute(ctx, "submit_teachback", task_id, records.digest(given), mutate, [
        ("journal", lambda: sides.journal(ctx, "teachback_ack", task_id=task_id,
                                          rationale_articulates_this_dispatch=answer, op_id=op_id)
         if auto else None),
        ("workflow", lambda: sides.workflow(ctx, task_id, dispatched.get("worktree"))),
    ])
    if auto:
        return Result(task_id=task_id, lines=[f"Teachback stored on #{task_id}. Accepted automatically. "
                                              "Go on with the work."])
    if dispatched.get("mode") == "teams":
        return Result(task_id=task_id, lines=[f"Teachback stored on #{task_id}; the lead reviews it.",
                                              f"Next: repact-wait {task_id}"])
    return Result(task_id=task_id, lines=[f"Teachback stored on #{task_id}; the lead reviews it.",
                                          f"Stop here. End your turn with the one line `TEACHBACK SUBMITTED "
                                          f"#{task_id}`. The lead resumes you."])


# -- the HANDOFF ------------------------------------------------------------------------------

def _path_word(item: str):
    match = re.match(r"\s*(\S+)", item)
    if not match:
        return None
    word = _TAIL.sub("", match.group(1).rstrip(":,;").strip("`'\""))
    if not word or "://" in word or not ("/" in word or PurePosixPath(word).suffix):
        return None
    return match.group(1), word


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def repair_produced(produced, worktree: str) -> tuple:
    """(items, problems): a string becomes one item; each item's leading path is made absolute
    against the worktree and must resolve inside it."""
    items = [produced] if isinstance(produced, str) else produced
    if not isinstance(items, list) or not items or not all(isinstance(item, str) for item in items):
        return produced, ["handoff.produced must be a non-empty list of strings, one per file"]
    root, out, problems = os.path.realpath(worktree), [], []
    for index, item in enumerate(items):
        found = _path_word(item)
        if found is None:
            out.append(item)
            continue
        raw, word = found
        target = os.path.realpath(os.path.join(root, os.path.expanduser(word)))
        if not _inside(target, root):
            problems.append(f"handoff.produced[{index}]: {word} resolves outside the worktree {root}")
            continue
        out.append(item.replace(raw, raw.replace(word, target, 1), 1))
    return out, problems


def _carried(task: dict, dispatched: dict) -> list:
    refs = list(dispatched.get("refs") or []) + RD.metadata_refs(records.metadata(task))
    return list(dict.fromkeys(refs + list((dispatched.get("acceptance_pin") or {}).keys())))


def check_handoff(task: dict, dispatched: dict, given) -> tuple:
    """(the repaired HANDOFF, problems)."""
    if not isinstance(given, dict):
        return given, ["the HANDOFF must be a JSON object"]
    problems = _size_problem(given, HANDOFF_MAX, "the HANDOFF")
    if problems:
        return given, problems
    given, _repairs = normalise_handoff(given)
    handoff = {key: value for key, value in given.items() if key != "rev"}
    problems = _key_problems(handoff, HANDOFF_CANONICAL_FIELDS + ("amendments", "refs", "evidence"), "handoff")
    problems += [f"handoff.{key} is missing" for key in REQUIRED_KEYS if key not in handoff]
    for key in LIST_KEYS:
        handoff.setdefault(key, [])
    if "produced" in handoff:
        handoff["produced"], found = repair_produced(handoff["produced"], dispatched["worktree"])
        problems += found
    problems += [f"handoff.uncertainty: {text[:80]!r} opens with none of fixed:, known-limit:, "
                 "not-applicable:" for text in EV.undisposed(handoff)]
    problems += [f"handoff.amendments has no disposition for {key} ({text})"
                 for key, text in AM.missing_dispositions(AM.amendments(records.metadata(task)), handoff)]
    carried = _carried(task, dispatched)
    given_refs = {**RD.dispositions(handoff), **RD.ac_dispositions(handoff)}
    problems += [f"handoff.refs has no disposition for {ref}" for ref in carried if ref not in given_refs]
    problems += [f"handoff.refs: {line}" for line in RD.malformed(handoff)]
    return handoff, problems + _placeholders(handoff, "handoff")


def handoff(ctx: records.Context, task_id: str, given, relayed: bool = False) -> Result:
    task = ctx.task(task_id)
    dispatched = _open(task, ctx.caller)
    meta = records.metadata(task)
    if not ((meta.get("teachback") or {}).get("accepted")):
        raise OpError("ops.wrong_state", [f"the teachback on #{task_id} is not accepted yet; run "
                                          f"repact submit {task_id} --teachback <file> first"])
    repaired, problems = check_handoff(task, dispatched, given)
    if problems:
        raise OpError("ops.invalid_input", problems)
    revision = meta.get("revision_number") if type(meta.get("revision_number")) is int else 0
    args = {"handoff": repaired, "revision": revision}
    op_id = records.op_id_of("submit_handoff", task_id, records.digest(args))

    def mutate(current: dict) -> None:
        prior = current["metadata"].get("handoff")
        rev = AM.handoff_rev(prior) or 0
        current["metadata"]["handoff"] = dict(repaired, rev=rev + 1, submitted_at=records.now(),
                                              relayed=bool(relayed))

    records.execute(ctx, "submit_handoff", task_id, records.digest(args), mutate, [
        ("workflow", lambda: sides.workflow(ctx, task_id, dispatched.get("worktree"))),
    ])
    stored = ctx.task(task_id)
    rev = AM.handoff_rev(records.metadata(stored).get("handoff"))
    preview = EV.verdict(stored, ctx.team, records.metadata(stored)["handoff"], dispatched.get("worktree"),
                         ctx.session_id)["problems"] + ac_problems(stored, records.metadata(stored)["handoff"])
    head = f"HANDOFF stored on #{task_id}: rev {rev} ({op_id})."
    if not preview:
        return Result(task_id=task_id, lines=[head, "accept will pass the checks it can see now. End your turn."])
    shown = [f"  {line[:200]}" for line in preview[:PREVIEW_SHOWN]]
    more = [f"  and {len(preview) - PREVIEW_SHOWN} more"] if len(preview) > PREVIEW_SHOWN else []
    return Result(task_id=task_id, exit_code=3, lines=[head, "accept will refuse:", *shown, *more,
                                                       f"Fix them, then run repact submit {task_id} again."][:6])
