"""hooks/shared/context_ledger.py: per-agent context attribution and the advisory tiers.

The session's counter file, <session dir>/context-cost.json, keeps the three tables the
dispatcher always kept (`checks`, `events`, `frames`: bytes the hooks put in front of the
model) and, since the refactor core lane, one `agents.<name>` row per acting member:

    calls             PreToolUse frames seen for this actor
    in_bytes          sum of len(json.dumps(tool_input))                (PreToolUse)
    out_bytes         sum of len(json.dumps(tool_response | error)), capped per frame
    injected          hook context + systemMessage bytes on this actor's frames
    transcript_bytes  os.stat size of the frame's transcript, the latest reading
    est_context       BASE + in_bytes + out_bytes + injected  (a lower bound on the next call)
    est_spend         sum over calls of est_context at that call  (the re-read integral)
    tier              "" | "advise" | "stop" | "hard"  (the highest tier already announced)

BASE is the session's own measured session_init (and, for a teammate, peer_inject) bytes
per run, read from the same file. SIZES ONLY: no tool text, no reason, no transcript
content is stored, and the transcript is never opened (tests/contract/test_transcript_boundary.py
pins that; os.stat is the whole reading).

Three lines per actor, in bytes of est_context: `advise`, `stop` and `hard` (stop x 1.25).
Between stop and hard is the finish-what-you-wrote band: no new work, test and fix what is
written, then HANDOFF; at hard, HANDOFF now. The budget is per task (`task_budget`), read
from the actor's in_progress task file (highest id of several) in the team's task directory:

    default   advise 300 KB, stop 500 KB   (`LINES`; profile.json `context_lines` overrides
              these two; the lead, an unknown actor and anything unreadable stay here)
    large     advise 500 KB, stop 800 KB   (metadata.variety.scope >= 3 or .risk >= 4)
    explicit  stop = metadata.context_budget_kb clamped to 300..1000 KB, advise 60% of it;
              wins over the stamp and the profile

Each line is advisory and spoken once per (actor, tier): the lead hears all three as a
systemMessage on its next PreToolUse frame; the actor hears `stop` (the band) and `hard` as
additionalContext on its own next PreToolUse; `session` (sum of est_spend) is one lead
line. Nothing is denied.

Used by hooks/dispatch.py (`record` after every frame's output, `advise` just before it),
bin/repact-cost (prints the table) and the SessionEnd `context_budget` journal row that
`record` returns for dispatch to append.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any

from . import state_file

COST_FILE = "context-cost.json"
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
OUT_CAP = 1_000_000          # bytes of one tool_response counted at most
LINES = {"advise": 300_000, "stop": 500_000, "session": 50_000_000}
LARGE_LINES = {"advise": 500_000, "stop": 800_000}
LARGE_SCOPE, LARGE_RISK = 3, 4    # variety.scope >= 3 or variety.risk >= 4 is a large task
BUDGET_KB_RANGE = (300, 1000)     # metadata.context_budget_kb is clamped to this
ADVISE_SHARE = 0.6                # advise line of an explicit budget, as a share of stop
HARD_FACTOR = 1.25                # hard line = stop x this; the band lies between them
TIER_RANK = {"": 0, "advise": 1, "stop": 2, "hard": 3}
TOP_N = 3                    # agents named in the session line and the journal row
_CONTEXT_BYTES = ("context", "system")


# --- sizes ---------------------------------------------------------------------------

def _size(text: Any) -> int:
    return len(text.encode("utf-8", "replace")) if isinstance(text, str) else 0


def _json_size(value: Any) -> int:
    """Bytes of `value` as JSON; 0 for None or anything json cannot serialise."""
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8", "replace"))
    except (TypeError, ValueError):
        return 0


def run_cost(parsed: list) -> dict:
    """{check name: (context bytes, system bytes)} for the checks of one run that put text
    in front of the model; {} on an exit 2, where the host reads only stderr."""
    if any(item.outcome.exit_code == 2 for item in parsed):
        return {}
    sizes = {}
    for item in parsed:
        context = sum(_size(text) for text in item.contexts)
        system = _size((item.obj or {}).get("systemMessage"))
        if context or system:
            sizes[item.outcome.check.name] = (context, system)
    return sizes


def transcript_size(payload: dict) -> int:
    """The frame's transcript size by os.stat alone; 0 when absent or unreadable. The
    file is never opened: the boundary that applies to agents applies to hooks."""
    for key in ("agent_transcript_path", "transcript_path"):
        path = payload.get(key)
        if isinstance(path, str) and path:
            try:
                return os.stat(path).st_size
            except OSError:
                continue
    return 0


def frame_sizes(event: str, payload: dict) -> dict:
    """The sizes one tool frame adds to its actor: in (PreToolUse), out (PostToolUse or
    the failure's error, capped at OUT_CAP) and the transcript reading."""
    sizes = {"in": 0, "out": 0, "transcript": transcript_size(payload)}
    if event == "PreToolUse":
        sizes["in"] = _json_size(payload.get("tool_input"))
    elif event == "PostToolUse":
        sizes["out"] = min(_json_size(payload.get("tool_response")), OUT_CAP)
    elif event == "PostToolUseFailure":
        sizes["out"] = min(_json_size(payload.get("error")), OUT_CAP)
    return sizes


def actor_name(payload: dict) -> str:
    """The resolved actor's name, "" when the frame names nobody; quiet, since the
    resolver's context-file warning would otherwise reach the host's stderr on every
    frame of a session that has no PACT context."""
    try:
        from .actor import resolve_actor

        with contextlib.redirect_stderr(io.StringIO()):
            return resolve_actor(payload).name
    except Exception:  # noqa: BLE001 - an unreadable frame still counts
        return ""


def actor_team(payload: dict) -> str:
    """The resolved actor's team, "" when unknown; quiet like `actor_name`."""
    try:
        from .actor import resolve_actor

        with contextlib.redirect_stderr(io.StringIO()):
            return resolve_actor(payload).team
    except Exception:  # noqa: BLE001 - no team means the default budget
        return ""


def actor_key(payload: dict, frame: str) -> str:
    """The agents-table key: the resolved actor's name, else the frame kind ("lead") or
    "unknown" for a teammate frame that names nobody."""
    return actor_name(payload) or ("lead" if frame == "lead" else "unknown")


def lines() -> dict:
    """LINES, with any `context_lines` ints the per-user profile.json sets."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base).expanduser() / "repacted" / "profile.json"
    try:
        profile = json.loads(state_file.read_text(path, path.parent))
        override = profile.get("context_lines") if isinstance(profile, dict) else None
    except (OSError, ValueError):
        override = None
    chosen = dict(LINES)
    for key, value in (override if isinstance(override, dict) else {}).items():
        if key in chosen and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            chosen[key] = value
    return chosen


# --- the per-task budget -------------------------------------------------------------

def _number(value: Any) -> int | float | None:
    """`value` when it is an int or a finite float, else None (a bool is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if isinstance(value, int) or math.isfinite(value) else None


def hard_line(limits: dict) -> int:
    """The hard line of `limits`: stop x HARD_FACTOR."""
    return int(limits["stop"] * HARD_FACTOR)


def _stamp(metadata: dict) -> dict:
    """The variety stamp: the one `repact dispatch` writes (metadata.dispatch.variety), else
    the hand-written metadata.variety, else {}."""
    dispatch = metadata.get("dispatch")
    for stamp in ((dispatch.get("variety") if isinstance(dispatch, dict) else None),
                  metadata.get("variety")):
        if isinstance(stamp, dict):
            return stamp
    return {}


def _score(value: Any) -> int | float | None:
    """A dimension's score: a bare number, or the `score` of a {score, rationale} entry."""
    return _number(value.get("score")) if isinstance(value, dict) else _number(value)


def task_budget(task: Any, limits: dict) -> dict:
    """`limits` with the advise and stop lines of `task` (a parsed task file): the explicit
    metadata.context_budget_kb first, else the large lines for a large variety stamp, else
    `limits` unchanged. A malformed task, stamp or number is the default."""
    metadata = task.get("metadata") if isinstance(task, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    explicit = _number(metadata.get("context_budget_kb"))
    if explicit is not None:
        low, high = BUDGET_KB_RANGE
        stop = int(min(max(explicit, low), high) * 1000)
        return {**limits, "advise": int(stop * ADVISE_SHARE), "stop": stop}
    variety = _stamp(metadata)
    scope, risk = _score(variety.get("scope")), _score(variety.get("risk"))
    if (scope is not None and scope >= LARGE_SCOPE) or (risk is not None and risk >= LARGE_RISK):
        return {**limits, **LARGE_LINES}
    return dict(limits)


def _task_number(task: dict) -> tuple:
    """Sort key of a task id: numeric ids in order, any other id below them."""
    task_id = str(task.get("id", ""))
    return (1, int(task_id), "") if task_id.isascii() and task_id.isdigit() else (0, 0, task_id)


def current_task(team: str, agent: str) -> dict | None:
    """The in_progress task `agent` owns in the team's task directory, the highest id of
    several; None when there is none or the store cannot be read."""
    if not (team and agent):
        return None
    try:
        from .task_store import TaskStore

        owned = [task for task in TaskStore().iter_tasks(team)
                 if task.get("owner") == agent and task.get("status") == "in_progress"]
        return max(owned, key=_task_number) if owned else None
    except Exception:  # noqa: BLE001 - an unreadable store is the default budget
        return None


def agent_limits(payload: dict, frame: str, agent: str, limits: dict) -> dict:
    """The lines of this frame's actor: `limits` for the lead, an unnamed actor or an
    actor with no task; else its task's budget. Never raises."""
    if frame == "lead" or agent in ("", "lead", "unknown"):
        return limits
    try:
        return task_budget(current_task(actor_team(payload), agent), limits)
    except Exception:  # noqa: BLE001
        return limits


# --- the counter file ----------------------------------------------------------------

def _load(text: str) -> dict:
    try:
        counters = json.loads(text) if text.strip() else {}
    except ValueError:
        counters = {}
    return counters if isinstance(counters, dict) else {}


def _group(counters: dict, table: str) -> dict:
    group = counters.get(table) if isinstance(counters.get(table), dict) else {}
    counters[table] = group
    return group


def _row(group: dict, key: str) -> dict:
    row = group.get(key) if isinstance(group.get(key), dict) else {}
    group[key] = row
    return row


def _int(row: dict, key: str) -> int:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def add_cost(text: str, event: str, frame: str, sizes: dict) -> tuple:
    """state_file.locked_update's apply for the hook-injection tables alone: the counter
    file with one run added. A file that is not a JSON object starts again from zero."""
    counters = _load(text)
    _add_injection(counters, event, frame, sizes)
    counters["v"] = 1
    return json.dumps(counters, sort_keys=True), True, None


def _add_injection(counters: dict, event: str, frame: str, sizes: dict) -> None:
    def bump(table: str, key: str, context: int, system: int) -> None:
        row = _row(_group(counters, table), key)
        row.update(runs=_int(row, "runs") + 1, context=_int(row, "context") + context,
                   system=_int(row, "system") + system)

    for name, (context, system) in sizes.items():
        bump("checks", name, context, system)
    bump("events", event, sum(c for c, _ in sizes.values()), sum(s for _, s in sizes.values()))
    bump("frames", frame, sum(c for c, _ in sizes.values()), sum(s for _, s in sizes.values()))


def base_bytes(counters: dict, frame: str) -> int:
    """The session's measured start-of-context bytes per run: session_init for every
    frame, plus peer_inject for a teammate's."""
    checks = counters.get("checks") if isinstance(counters.get("checks"), dict) else {}
    names = ("session_init",) + (("peer_inject",) if frame != "lead" else ())
    total = 0
    for name in names:
        row = checks.get(name) if isinstance(checks.get(name), dict) else {}
        runs = _int(row, "runs")
        if runs:
            total += sum(_int(row, key) for key in _CONTEXT_BYTES) // runs
    return total


def _kb(size: int) -> str:
    return f"{size / 1000:.0f} KB"


def _lead_line(tier: str, name: str, row: dict) -> str:
    size, calls = _kb(_int(row, "est_context")), _int(row, "calls")
    if tier == "hard":
        return f"context: {name} is past the hard line (~{size} after {calls} calls); expect its HANDOFF"
    if tier == "stop":
        return (f"context: {name} is past the stop line (~{size} after {calls} calls); it may "
                "test and fix what it wrote, then expect its HANDOFF")
    advice = ("consider a fresh session after this feature" if name == "lead"
              else "consider replacing it after its next HANDOFF")
    return f"context: {name} ~{size} after {calls} calls; {advice}"


def _session_line(agents: dict, spend: int) -> str:
    top = sorted(agents.items(), key=lambda item: -_int(item[1], "est_spend"))[:TOP_N]
    named = ", ".join(f"{name} {_int(row, 'est_spend') / 1e6:.0f}" for name, row in top)
    return (f"session spend ~{spend / 1e6:.0f} MB-calls across {len(agents)} agents; "
            f"top: {named}")


def _lowest_line(limits: dict) -> int:
    """The lowest advise line any budget can give: the default's or the smallest explicit."""
    return min(limits["advise"], int(BUDGET_KB_RANGE[0] * 1000 * ADVISE_SHARE))


def _tier_of(est_context: int, limits: dict) -> str:
    if est_context >= hard_line(limits):
        return "hard"
    if est_context >= limits["stop"]:
        return "stop"
    return "advise" if est_context >= limits["advise"] else ""


def _add_frame(counters: dict, event: str, frame: str, agent: str, sizes: dict,
               injected: int, limits: dict, budget: Any = None) -> None:
    """One tool frame on the actor's row, and the announcements it earns (once each).
    `budget`, when given, is called for the actor's own lines once its context reaches
    the lowest line any budget can set; under that no task file is read."""
    agents = _group(counters, "agents")
    row = _row(agents, agent)
    row["in_bytes"] = _int(row, "in_bytes") + sizes.get("in", 0)
    row["out_bytes"] = _int(row, "out_bytes") + sizes.get("out", 0)
    row["injected"] = _int(row, "injected") + injected
    if sizes.get("transcript"):
        row["transcript_bytes"] = sizes["transcript"]
    row["est_context"] = (base_bytes(counters, frame) + row["in_bytes"] + row["out_bytes"]
                          + row["injected"])
    if event == "PreToolUse":
        row["calls"] = _int(row, "calls") + 1
        row["est_spend"] = _int(row, "est_spend") + row["est_context"]
    told = row.get("tier") if row.get("tier") in TIER_RANK else ""
    row["tier"] = told
    own = limits
    if budget is not None and told != "hard" and row["est_context"] >= _lowest_line(limits):
        own = budget()
    tier = _tier_of(row["est_context"], own)
    if TIER_RANK[tier] > TIER_RANK[told]:
        row["tier"] = tier
        counters.setdefault("pending", []).append(_lead_line(tier, agent, row))
        if tier in ("stop", "hard"):
            row["stop_pending"] = tier
            row["hard_line"] = hard_line(own)
    spend = sum(_int(r, "est_spend") for r in agents.values() if isinstance(r, dict))
    if spend >= limits["session"] and not counters.get("session_told"):
        counters["session_told"] = True
        counters.setdefault("pending", []).append(_session_line(agents, spend))


def budget_row(counters: dict) -> dict | None:
    """The SessionEnd `context_budget` journal row: agent count, total spend and the top
    spenders by name; None when no agent row exists."""
    agents = counters.get("agents") if isinstance(counters.get("agents"), dict) else {}
    rows = {name: row for name, row in agents.items() if isinstance(row, dict)}
    if not rows:
        return None
    top = sorted(rows.items(), key=lambda item: -_int(item[1], "est_spend"))[:TOP_N]
    return {"agents": len(rows), "spend": sum(_int(row, "est_spend") for row in rows.values()),
            "top": [{"agent": name, "spend": _int(row, "est_spend"),
                     "est_context": _int(row, "est_context")} for name, row in top]}


def add_run(text: str, event: str, frame: str, sizes: dict, agent: str, frame_bytes: dict,
            limits: dict, budget: Any = None) -> tuple:
    """state_file.locked_update's apply for one dispatch: the injection tables when a
    check injected, the actor's row on a tool event (`budget`: see `_add_frame`). Returns
    (text, changed, rows), rows being the journal rows the caller appends (the SessionEnd
    budget row)."""
    counters = _load(text)
    changed = False
    if sizes:
        _add_injection(counters, event, frame, sizes)
        changed = True
    injected = sum(context + system for context, system in sizes.values())
    if agent and event in TOOL_EVENTS:
        _add_frame(counters, event, frame, agent, frame_bytes, injected, limits, budget)
        changed = True
    rows = []
    if event == "SessionEnd":
        budget = budget_row(counters)
        if budget is not None:
            rows.append(("context_budget", budget))
    counters["v"] = 1
    return json.dumps(counters, sort_keys=True), changed, rows


def _ledger_path(payload: dict) -> Path | None:
    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not (isinstance(session_id, str) and session_id and project_dir):
        return None
    from .pact_context import reconstruct_session_dir

    session_dir = reconstruct_session_dir(project_dir, session_id)
    if not session_dir or not os.path.isdir(session_dir):
        return None
    return Path(session_dir) / COST_FILE


def record(event: str, payload: dict, parsed: list, frame: str) -> list:
    """Add one dispatch to the counter file; the journal rows to append. Never raises:
    a count that cannot be written changes no decision."""
    try:
        sizes = run_cost(parsed)
        path = _ledger_path(payload)
        if path is None or not (sizes or event in TOOL_EVENTS or event == "SessionEnd"):
            return []
        agent = actor_key(payload, frame) if event in TOOL_EVENTS else ""
        frame_bytes = frame_sizes(event, payload) if agent else {}
        limits = lines()
        return state_file.locked_update(
            path, lambda text: add_run(text, event, frame, sizes, agent, frame_bytes, limits,
                                       lambda: agent_limits(payload, frame, agent, limits)),
            path.parent) or []
    except Exception:  # noqa: BLE001
        return []


# --- the advisory, spoken once -------------------------------------------------------

BAND_TEXT = ("your context is past the stop line (~{size}): start no new work. You may run "
             "the tests for what you have already written and fix what they show; then write "
             "your HANDOFF with a `remaining:` list. Past the hard line (~{hard}) you stop.")
# The lead's own band: its limit is on this context, not on the job, and only the user can
# run the checkpoint that carries the work into a fresh one.
LEAD_BAND_TEXT = ("your context is past the stop line (~{size}): do no new work in THIS context. "
                  "Finish the step in hand, record each new order from the user as a task and "
                  "give it to a fresh agent, and tell the user once to type "
                  "/repacted:pause checkpoint then /compact. Past the hard line (~{hard}) delegate everything.")
HARD_TEXT = ("your context is past the hard line (~{size}); write your HANDOFF now and end "
             "the turn")


def _actor_line(row: dict, lead: bool = False) -> str:
    """The actor's own line for the tier it owes: the hard text at the hard line, the band
    text at the stop line (a row written before the band existed says True: the band). The
    lead's band has its own wording; a teammate's is unchanged."""
    size = _kb(_int(row, "est_context"))
    if row.get("stop_pending") == "hard":
        return HARD_TEXT.format(size=size)
    band = LEAD_BAND_TEXT if lead else BAND_TEXT
    return band.format(size=size, hard=_kb(_int(row, "hard_line") or hard_line(LINES)))


def consume(text: str, agent: str, frame: str) -> tuple:
    """state_file.locked_update's apply that takes the lines owed to this frame: the
    pending lead lines on a lead frame, the actor's own band or hard line on its frame.
    Returns (text, changed, (system lines, context lines))."""
    counters = _load(text)
    system: list = []
    context: list = []
    if frame == "lead" and isinstance(counters.get("pending"), list) and counters["pending"]:
        system = [line for line in counters["pending"] if isinstance(line, str)]
        counters["pending"] = []
    agents = counters.get("agents") if isinstance(counters.get("agents"), dict) else {}
    row = agents.get(agent) if isinstance(agents.get(agent), dict) else None
    if row is not None and row.get("stop_pending"):
        context.append(_actor_line(row, lead=frame == "lead"))
        row["stop_pending"] = False
    changed = bool(system or context)
    return (json.dumps(counters, sort_keys=True) if changed else text), changed, (system, context)


def _owed(text: str, agent: str, frame: str) -> bool:
    """Whether a lock is worth taking: a cheap unlocked reading of what consume would find."""
    counters = _load(text)
    if frame == "lead" and counters.get("pending"):
        return True
    agents = counters.get("agents") if isinstance(counters.get("agents"), dict) else {}
    row = agents.get(agent)
    return isinstance(row, dict) and bool(row.get("stop_pending"))


def inject(stdout: bytes, event: str, system: list, context: list) -> bytes:
    """The frame's envelope with the advisory lines added: systemMessage for the lead's
    lines, hookSpecificOutput.additionalContext for the actor's. Other keys stay."""
    if not (system or context):
        return stdout
    try:
        envelope = json.loads(stdout.decode("utf-8")) if stdout.strip() else {}
    except ValueError:
        return stdout
    if not isinstance(envelope, dict):
        return stdout
    if system:
        envelope["systemMessage"] = "\n".join(
            ([envelope["systemMessage"]] if envelope.get("systemMessage") else []) + system)
    if context:
        hso = envelope.get("hookSpecificOutput")
        hso = hso if isinstance(hso, dict) else {}
        hso.setdefault("hookEventName", event)
        hso["additionalContext"] = "\n".join(
            ([hso["additionalContext"]] if hso.get("additionalContext") else []) + context)
        envelope["hookSpecificOutput"] = hso
    return json.dumps(envelope).encode("utf-8")


def advise(event: str, payload: dict, exit_code: int, stdout: bytes, frame: str) -> bytes:
    """stdout with any advisory owed to this PreToolUse frame; unchanged on a refusal (the
    host reads only stderr), on other events, or when nothing is owed. Never raises."""
    if exit_code != 0 or event != "PreToolUse":
        return stdout
    try:
        path = _ledger_path(payload)
        if path is None:
            return stdout
        try:
            text = state_file.read_text(path, path.parent)
        except OSError:
            return stdout
        agent = actor_key(payload, frame)
        if not _owed(text, agent, frame):
            return stdout
        system, context = state_file.locked_update(
            path, lambda current: consume(current, agent, frame), path.parent)
        return inject(stdout, event, system, context)
    except Exception:  # noqa: BLE001 - advice that cannot be read is not given
        return stdout
