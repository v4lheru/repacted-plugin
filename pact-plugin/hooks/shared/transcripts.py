"""Shared transcript reader for repact-status, repact-report and the session ledger.

Location: pact-plugin/hooks/shared/transcripts.py (stdlib and shared/ only; import it as
`shared.transcripts` with hooks/ on sys.path). The design is section D3 of
the design notes; the report reproduces that doc's §1 tables with the definitions of the measurement scripts
behind them (#99), stated per function below.

PRIVACY. Transcripts hold user content. Nothing here returns message text: only counts,
ages, tool names, task ids, teammate names and the `description` of a Bash, Agent or Task
call (the one-line summary an agent writes for it; every other tool shows its name only),
which goes through the secret scrubber before it is returned. Text is compared internally (to match a lead message to its
delivery) and never copied into a result.

Layout (measured): <config>/projects/<slug>/<session>.jsonl is the lead; beside it,
<session>/subagents/agent-a<name>-<16 hex>.jsonl is teammate <name>, and
agent-a<16 hex>.jsonl is an Agent-tool subagent or fork.

USAGE IS COUNTED ONCE PER MESSAGE ID. A streamed message is written as several entries
that repeat its usage; counting entries overcounts output tokens by about 2.7x
(MEASURED on the idle-only wakes: 66,780 vs 24,195).
"""
from __future__ import annotations

import collections
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(HOOKS_DIR) not in sys.path:      # loadable by file path too
    sys.path.insert(0, str(HOOKS_DIR))
from shared import hook_latency, plugin_paths, scrub  # noqa: E402
from shared import amendments as amendment_rules  # noqa: E402
from shared import transcript_activity as activity_rules  # noqa: E402

TEAMMATE_FILE = re.compile(r"^agent-a(?P<name>.+)-[0-9a-f]{16}\.jsonl$")
SUBAGENT_FILE = re.compile(r"^agent-a[0-9a-f]{16}\.jsonl$")
TEAMMATE_MESSAGE = re.compile(r"<teammate-message([^>]*)>(.*?)</teammate-message>", re.S)
TEAMMATE_ID = re.compile(r'teammate_id="([^"]*)"')
TASK_ID_TAG = re.compile(r"<task-id>([^<]+)</task-id>")
PSGUARD_PREFIX = "Process listings that print command lines"
UNFLAGGED_PREFIX = "UNFLAGGED BACKGROUND WORK"
DESCRIPTION_MAX = 80
# Tools whose `description` is the agent's one-line summary of the call. For every other tool
# (TaskCreate, TaskUpdate, Write, MCP tools, ...) that field is content, often a task body,
# so status prints the tool's NAME only.
DESCRIBED_TOOLS = frozenset({"Bash", "Agent", "Task"})


parse_ts = activity_rules.parse_ts


# --------------------------------------------------------------------------- locating

def slug(path: os.PathLike | str) -> str:
    """The platform's project dir name: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def find_lead_transcript(transcript: Optional[str] = None, session: Optional[str] = None,
                         project_dir: Optional[str] = None) -> Optional[Path]:
    """An explicit path; else <session>.jsonl under any project; else the newest session of
    the project at `project_dir` (default: the cwd)."""
    if transcript:
        path = Path(transcript).expanduser()
        return path if path.is_file() else None
    root = plugin_paths.claude_config_dir() / "projects"
    if session:
        hits = sorted(root.glob(f"*/{session}.jsonl"))
        return hits[0] if hits else None
    folder = root / slug(Path(project_dir or os.getcwd()).resolve())
    sessions = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return sessions[-1] if sessions else None


def session_files(lead: Path) -> list[dict]:
    """[{path, role, name}] for the lead and every transcript under its subagents dir."""
    files = [{"path": lead, "role": "lead", "name": "lead"}]
    folder = activity_rules.subagents_dir(lead)
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        if SUBAGENT_FILE.match(path.name):
            files.append({"path": path, "role": "subagent", "name": path.stem})
            continue
        match = TEAMMATE_FILE.match(path.name)
        role = "teammate" if match else "subagent"
        files.append({"path": path, "role": role, "name": match.group("name") if match else path.stem})
    return files


def read_meta(path: Path) -> dict:
    try:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def team_name(lead: Path) -> Optional[str]:
    """The team the session's teammates belong to (the most common meta teamName)."""
    names = collections.Counter(
        read_meta(f["path"]).get("teamName") for f in session_files(lead) if f["role"] == "teammate")
    names.pop(None, None)
    return names.most_common(1)[0][0] if names else None


# --------------------------------------------------------------------------- reading

def iter_entries(path: Path, until: Optional[datetime] = None) -> Iterator[dict]:
    """Each parsed entry; with `until`, only entries stamped at or before it."""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if until is not None:
                ts = parse_ts(entry.get("timestamp"))
                if ts is None or ts > until:
                    continue
            yield entry


def message(entry: dict) -> dict:
    value = entry.get("message")
    return value if isinstance(value, dict) else {}


def blocks(entry: dict) -> list:
    content = message(entry).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def input_side(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def usage(entries: Iterable[dict]) -> dict:
    """API calls and tokens, one call per assistant message id that carries usage."""
    seen: set = set()
    out = {"calls": 0, "input": 0, "cache_read": 0, "cache_creation": 0, "output": 0,
           "input_side": 0, "first_ctx": None, "last_ctx": None, "max_ctx": 0}
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = message(entry)
        mid = msg.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        use = msg.get("usage") or {}
        if not use:
            continue
        out["calls"] += 1
        out["input"] += int(use.get("input_tokens") or 0)
        out["cache_read"] += int(use.get("cache_read_input_tokens") or 0)
        out["cache_creation"] += int(use.get("cache_creation_input_tokens") or 0)
        out["output"] += int(use.get("output_tokens") or 0)
        ctx = input_side(use)
        out["input_side"] += ctx
        if out["first_ctx"] is None:
            out["first_ctx"] = ctx
        out["last_ctx"] = ctx
        out["max_ctx"] = max(out["max_ctx"], ctx)
    return out


def clean_description(value) -> Optional[str]:
    """A tool call's description, one line, capped, with secret values redacted."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    if len(text) > DESCRIPTION_MAX:
        text = text[: DESCRIPTION_MAX - 1] + "…"
    return scrub.scrub_text(text)


def teammate_state(entries: list, now: Optional[datetime] = None) -> dict:
    """busy/idle, the last tool call (name, fg/bg, description, time), outstanding background
    jobs and the last call's context, from one transcript's entries in order."""
    act = activity_rules.activity(entries)
    now = now or datetime.now(timezone.utc)
    last_tool = None
    launched: set = set()
    finished: set = set()
    for entry in entries:
        kind = entry.get("type")
        if kind == "assistant":
            for block in blocks(entry):
                if block.get("type") == "tool_use":
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    last_tool = {
                        "name": block.get("name"),
                        "background": bool(tool_input.get("run_in_background")),
                        "description": (clean_description(tool_input.get("description"))
                                        if block.get("name") in DESCRIBED_TOOLS else None),
                        "ts": parse_ts(entry.get("timestamp")),
                    }
        elif kind == "user":
            result = entry.get("toolUseResult")
            if isinstance(result, dict) and isinstance(result.get("backgroundTaskId"), str):
                launched.add(result["backgroundTaskId"])
            content = message(entry).get("content")
            if isinstance(content, str) and "<task-notification>" in content:
                finished.update(TASK_ID_TAG.findall(content))
        elif kind == "attachment":
            attachment = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
            if attachment.get("commandMode") == "task-notification":
                finished.update(TASK_ID_TAG.findall(json.dumps(attachment.get("prompt"))))
    return {
        "busy": activity_rules.is_active(act, now),   # the alarm's predicate, not a second one
        "last_ts": act["last_ts"],
        "last_tool": last_tool,
        "background_outstanding": len(launched - finished),
        "ctx": usage(entries)["last_ctx"],
    }


def _tool_result_only(entry: dict) -> bool:
    return bool(blocks(entry)) and all(b.get("type") == "tool_result" for b in blocks(entry))


def age(then: Optional[datetime], now: datetime) -> str:
    if then is None:
        return "?"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


# --------------------------------------------------------------------------- tasks

def team_tasks(team: Optional[str]) -> list[dict]:
    if not team or "/" in team:
        return []
    return plugin_paths.task_store().list(team)


def unrelayed_amendments(task: dict, relayed: dict) -> int:
    keys = set(amendment_rules.amendments(task.get("metadata") or {}))
    return len(keys - set(relayed.get(str(task.get("id"))) or []))


def relay_stamp(session_id: str, team: str, name: str) -> dict:
    """The amendment relay's record of what it already relayed to this teammate ({} if none)."""
    root = plugin_paths.claude_config_dir() / "pact-sessions"
    for path in root.glob(f"*/{session_id}/amendment-relay/{team}--{name}.json"):
        try:
            stamp = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        relayed = stamp.get("relayed") if isinstance(stamp, dict) else None
        return relayed if isinstance(relayed, dict) else {}
    return {}


# --------------------------------------------------------------------------- status

def status_lines(lead: Path, team: Optional[str] = None, now: Optional[datetime] = None,
                 until: Optional[datetime] = None) -> list[str]:
    """One line per team member that has a transcript (the lead excluded)."""
    now = until or now or datetime.now(timezone.utc)
    team = team or team_name(lead)
    tasks = team_tasks(team)
    members = _member_names(team) or sorted(
        f["name"] for f in session_files(lead) if f["role"] == "teammate")
    transcripts = {f["name"]: f["path"] for f in session_files(lead) if f["role"] == "teammate"}
    lines = []
    for name in members:
        path = transcripts.get(name)
        if path is None:
            continue
        entries = list(iter_entries(path, until))
        if not entries:
            continue          # not spawned yet at `until`
        state = teammate_state(entries, now)
        owned = [t for t in tasks if t.get("owner") == name and t.get("status") == "in_progress"]
        relayed = relay_stamp(lead.stem, team, name) if team else {}
        amend = sum(unrelayed_amendments(t, relayed) for t in owned)
        parts = [name, "busy" if state["busy"] else "idle"]
        tool = state["last_tool"]
        if tool:
            label = str(tool["name"] or "?")
            if tool["name"] == "Bash":
                label += " bg" if tool["background"] else " fg"
            if tool["description"]:
                label += f' "{tool["description"]}"'
            parts.append(f"{label} {age(tool['ts'], now)}")
        if state["ctx"]:
            parts.append(f"ctx {round(state['ctx'] / 1000)}k")
        if owned:
            parts.append(" ".join(f"#{t.get('id')}" for t in owned))
        if state["background_outstanding"]:
            parts.append(f"bg:{state['background_outstanding']}")
        if amend:
            parts.append(f"amend:{amend}")
        lines.append("  ".join(parts))
    return lines


def _member_names(team: Optional[str]) -> list[str]:
    if not team or "/" in team:
        return []
    try:
        config = json.loads((plugin_paths.claude_config_dir() / "teams" / team / "config.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    members = config.get("members") if isinstance(config, dict) else None
    return [m["name"] for m in members or [] if isinstance(m, dict) and isinstance(m.get("name"), str)]


# --------------------------------------------------------------------------- report

def spend_by_role(files: list[dict], until: Optional[datetime]) -> dict:
    """§1.5: transcripts, API calls, input-side and output tokens per role, deduped by message id."""
    roles: dict = {}
    for f in files:
        entries = list(iter_entries(f["path"], until))
        if not entries:
            continue          # a transcript that did not exist yet at `until`
        use = usage(entries)
        row = roles.setdefault(f["role"], {"transcripts": 0, "calls": 0, "input_side": 0, "output": 0})
        row["transcripts"] += 1
        row["calls"] += use["calls"]
        row["input_side"] += use["input_side"]
        row["output"] += use["output"]
    total = sum(r["input_side"] for r in roles.values()) or 1
    for row in roles.values():
        row["share"] = round(row["input_side"] / total, 3)
    return roles


def _teammate_messages(entry: dict) -> list[tuple[str, str, str]]:
    """[(sender, kind, body)] for a lead user entry carrying <teammate-message> blocks."""
    content = message(entry).get("content")
    if entry.get("type") != "user" or not isinstance(content, str) or "<teammate-message" not in content:
        return []
    out = []
    for match in TEAMMATE_MESSAGE.finditer(content):
        sender = TEAMMATE_ID.search(match.group(1))
        body = match.group(2).strip()
        kind = "text"
        if body.startswith("{"):
            try:
                kind = "json:" + str(json.loads(body).get("type", "?"))
            except ValueError:
                kind = "badjson"
        out.append((sender.group(1) if sender else "?", kind, body))
    return out


def wakes(lead_entries: list) -> dict:
    """§1.1 with the definitions of wd_inbound*.py (#99): an idle-only wake is a lead user entry
    whose teammate messages are all idle notifications; its cost is every lead assistant
    message after it up to the next user entry that is not a tool result (calls and tokens
    deduped by message id; tool calls counted over entries); a double wake is an idle-only
    wake from a teammate whose text message the lead had already answered (an assistant
    entry in between) at most 120 s earlier."""
    items = [(i, parse_ts(e.get("timestamp")), _teammate_messages(e)) for i, e in enumerate(lead_entries)]
    items = [(i, t, m) for i, t, m in items if m]
    idle_only = [(i, t, m) for i, t, m in items if all(k == "json:idle_notification" for _, k, _ in m)]
    calls = output = cache_read = no_tool = 0
    for i, _, _ in idle_only:
        seen: set = set()
        tools = 0
        for entry in lead_entries[i + 1:]:
            if entry.get("type") == "user":
                if _tool_result_only(entry):
                    continue
                break
            if entry.get("type") != "assistant":
                continue
            tools += sum(1 for b in blocks(entry) if b.get("type") == "tool_use")
            mid = message(entry).get("id")
            if mid in seen:
                continue
            seen.add(mid)
            use = message(entry).get("usage") or {}
            calls += 1
            cache_read += int(use.get("cache_read_input_tokens") or 0)
            output += int(use.get("output_tokens") or 0)
        no_tool += tools == 0
    last_text: dict = {}
    double = 0
    gaps = []
    for i, t, m in items:
        if all(k == "json:idle_notification" for _, k, _ in m):
            hit = False
            for sender, _, _ in m:
                if sender in last_text:
                    j, tt = last_text[sender]
                    gap = (t - tt).total_seconds() if t and tt else None
                    answered = any(lead_entries[x].get("type") == "assistant" for x in range(j + 1, i))
                    if gap is not None and gap <= 120 and answered:
                        hit = True
                        gaps.append(gap)
            double += hit
        for sender, kind, _ in m:
            if kind == "text":
                last_text[sender] = (i, t)
    idle_notes = sum(1 for _, _, m in items for _, k, _ in m if k == "json:idle_notification")
    text_notes = sum(1 for _, _, m in items for _, k, _ in m if k == "text")
    return {
        "teammate_entries": len(items), "idle_notifications": idle_notes, "text_messages": text_notes,
        "idle_only_wakes": len(idle_only), "idle_only_calls": calls,
        "idle_only_cache_read": cache_read, "idle_only_output": output,
        "idle_only_no_tool": no_tool, "double_wakes": double,
        "double_wake_median_gap_s": round(statistics.median(gaps)) if gaps else None,
    }


def delivery(lead_entries: list, files: list[dict], until: Optional[datetime]) -> dict:
    """§1.2 with wd_delay.py's definitions (#99): each lead SendMessage over 60 chars is found in
    its recipient's transcript by a 50-char slice from its middle; a delay over 60 s is late."""
    sends = collections.defaultdict(list)
    for entry in lead_entries:
        if entry.get("type") != "assistant":
            continue
        for block in blocks(entry):
            if block.get("type") == "tool_use" and block.get("name") == "SendMessage":
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                text = tool_input.get("message")
                if isinstance(text, str) and len(text) > 60:
                    sends[tool_input.get("to")].append((parse_ts(entry.get("timestamp")), text))
    teammates = {f["name"]: f["path"] for f in files if f["role"] == "teammate"}
    located = []
    total = 0
    for who, items in sends.items():
        path = teammates.get(who)
        if path is None:
            continue
        received = [(parse_ts(e.get("timestamp")), message(e).get("content"))
                    for e in iter_entries(path, until)
                    if e.get("type") == "user" and isinstance(message(e).get("content"), str)
                    and 'teammate_id="team-lead"' in message(e)["content"]]
        for sent, text in items:
            total += 1
            probe = text[len(text) // 2: len(text) // 2 + 50]
            hit = next((t for t, c in received if probe in c), None)
            if hit is not None and sent is not None:
                located.append((hit - sent).total_seconds())
    late = [d for d in located if d > 60]
    return {"messages": total, "located": len(located), "late": len(late),
            "late_median_min": round(statistics.median(late) / 60, 1) if late else None}


def rejections(tasks: list[dict]) -> dict:
    """Tasks carrying a handoff_rejection, split by whether an amend_<n> key the HANDOFF
    does not dispose of was on the task (the machine-readable half of §1.2's causes)."""
    with_amend = other = 0
    for task in tasks:
        meta = task.get("metadata") or {}
        if not meta.get("handoff_rejection"):
            continue
        handoff = meta.get("handoff") if isinstance(meta.get("handoff"), dict) else {}
        missing = amendment_rules.missing_dispositions(amendment_rules.amendments(meta), handoff)
        if missing:
            with_amend += 1
        else:
            other += 1
    return {"rejected_tasks": with_amend + other, "with_unaddressed_amend": with_amend, "other": other}


def amendment_gate(since: Optional[datetime], until: Optional[datetime]) -> dict:
    """D2's gate log (checks/shadow.jsonl, kind amendment_gate) inside the session's window."""
    counts = collections.Counter()
    try:
        text = (plugin_paths.data_subdir("checks", create=False) / "shadow.jsonl").read_text(encoding="utf-8")
    except OSError:
        return {"would_deny": 0, "rejection_rev_mismatch": 0}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "amendment_gate":
            continue
        ts = parse_ts(event.get("at") or event.get("ts") or event.get("timestamp"))
        if ts is None or (since and ts < since) or (until and ts > until):
            continue
        if event.get("event") == "would_deny":
            counts["would_deny"] += 1
        if event.get("rejection_rev_ok") is False:
            counts["rejection_rev_mismatch"] += 1
    return {"would_deny": counts["would_deny"], "rejection_rev_mismatch": counts["rejection_rev_mismatch"]}


def _jsonl(path: Path) -> Iterator[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


CHECK_USES = ("handoff_evidence", "amendment_gate", "commit_rule")


def gate_usage(team: Optional[str], since: Optional[datetime], until: Optional[datetime]) -> dict:
    """The write-time checks' outcomes for one session, per check (kind write_gate in
    checks/shadow.jsonl, D30): items `returned` to their sender, `repeats` that passed on the
    return-once rail, `notes` (items noted) and `noted` (note events: writes that got a note).
    A line counts when it carries this team (a commit line, team _commits, when it falls inside
    the session's window)."""
    def in_window(event: dict) -> bool:
        ts = parse_ts(event.get("ts"))
        return ts is not None and (since is None or ts >= since) and (until is None or ts <= until)

    def belongs(event: dict) -> bool:
        owner = event.get("team")
        if owner:
            ts = parse_ts(event.get("ts"))
            return owner == team and (until is None or ts is None or ts <= until)
        return in_window(event)

    uses: dict = {}
    for event in _jsonl(plugin_paths.data_subdir("checks", create=False) / "shadow.jsonl"):
        if event.get("kind") != "write_gate":
            continue
        owner = event.get("team")
        mine = (owner == team and belongs(event)) or (owner == "_commits" and in_window(event))
        column = {"return": "returned", "repeat_pass": "repeats", "note": "notes"}.get(event.get("event"))
        if mine and column:
            row = uses.setdefault(str(event.get("use") or "gate"),
                                  {"returned": 0, "repeats": 0, "notes": 0, "noted": 0})
            row[column] += len(event.get("keys") or [])
            if column == "notes":
                row["noted"] += 1
    total = {key: sum(r[key] for r in uses.values()) for key in ("returned", "repeats", "notes", "noted")}
    rank = {use: index for index, use in enumerate(CHECK_USES)}
    ordered = {use: uses[use] for use in sorted(uses, key=lambda u: (rank.get(u, len(rank)), u))}
    return {"total": total, "uses": ordered}


COST_FILE = "context-cost.json"          # hooks/dispatch.py's counter file in the session dir


def session_dirs(session: str, every: bool = False) -> list[Path]:
    """The session's dir (pact-sessions/<slug>/<session>); with `every`, every session dir
    of the same project."""
    hits = sorted(path for path in (plugin_paths.claude_config_dir() / "pact-sessions").glob(f"*/{session}")
                  if path.is_dir())
    if not hits or not every:
        return hits[:1]
    return sorted(path for path in hits[0].parent.iterdir() if path.is_dir())


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def context_cost(dirs: Iterable[Path], until: Optional[datetime] = None) -> dict:
    """Bytes the hooks put in front of the model (each session's context-cost.json, from
    hooks/dispatch.py) and bytes of each stored teachback_submit and handoff (metadata_write
    journal rows, which journals from before 2.1.0 carry), per session, per check, per frame and per
    writing agent. `until` filters the journal rows only: the counter file has no times."""
    sessions: dict = {}
    starts: dict = {}
    checks: dict = collections.defaultdict(lambda: {"context": 0, "system": 0, "runs": 0})
    frames: collections.Counter = collections.Counter()
    agents: dict = collections.defaultdict(lambda: {"teachback_submit": 0, "handoff": 0, "writes": 0})
    for folder in dirs:
        row = {"runs": 0, "context": 0, "system": 0, "writes": 0, "written": 0}
        try:
            counters = json.loads((folder / COST_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            counters = {}
        counters = counters if isinstance(counters, dict) else {}
        # The last session start's byte ledger (hooks/shared/parts_ledger.py): source, context,
        # system and the bytes per named part.
        start = counters.get("session_init")
        if isinstance(start, dict) and isinstance(start.get("parts"), dict):
            parts = {str(k): _count(v) for k, v in start["parts"].items()}
            starts[folder.name] = {"source": str(start.get("source") or "?"), "context": _count(start.get("context")),
                                   "system": _count(start.get("system")), "parts": parts}
        for table in ("checks", "events", "frames"):
            group = counters.get(table) if isinstance(counters.get(table), dict) else {}
            for name, sizes in group.items():
                if not isinstance(sizes, dict):
                    continue
                context, system, runs = (_count(sizes.get(key)) for key in ("context", "system", "runs"))
                if table == "checks":
                    checks[name]["context"] += context
                    checks[name]["system"] += system
                    checks[name]["runs"] += runs
                elif table == "events":
                    row["runs"] += runs
                    row["context"] += context
                    row["system"] += system
                else:
                    frames[name] += context + system
        try:
            lines = (folder / "session-journal.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            if '"metadata_write"' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("type") != "metadata_write":
                continue
            ts = parse_ts(event.get("ts"))
            size, key = _count(event.get("bytes")), event.get("key")
            if (until and ts is not None and ts > until) or key not in ("teachback_submit", "handoff"):
                continue
            agent = agents[str(event.get("agent") or "?")]
            agent[key] += size
            agent["writes"] += 1
            row["writes"] += 1
            row["written"] += size
        sessions[folder.name] = row
    total = {key: sum(r[key] for r in sessions.values())
             for key in ("runs", "context", "system", "writes", "written")}
    top = sorted(checks.items(), key=lambda kv: (-(kv[1]["context"] + kv[1]["system"]), kv[0]))[:5]
    return {"total": total, "sessions": sessions, "top_checks": dict(top), "frames": dict(frames),
            "agents": dict(agents), "session_start": starts}


def transcript_signals(files: list[dict], until: Optional[datetime]) -> dict:
    """process-table guard denials, task-list reminders and UNFLAGGED alarms, counted across the session."""
    psguard: set = set()
    reminders = unflagged = 0
    for f in files:
        for entry in iter_entries(f["path"], until):
            kind = entry.get("type")
            if kind == "user":
                for block in blocks(entry):
                    if block.get("type") == "tool_result" and PSGUARD_PREFIX in json.dumps(block.get("content")):
                        psguard.add(block.get("tool_use_id"))
            elif kind == "attachment":
                attachment = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
                if attachment.get("type") == "task_reminder" and f["role"] != "subagent":
                    reminders += 1
                if (f["role"] == "lead" and attachment.get("type") == "hook_additional_context"
                        and UNFLAGGED_PREFIX in json.dumps(attachment)):
                    unflagged += 1
    return {"psguard_denials": len(psguard), "task_reminders": reminders, "unflagged_alarms": unflagged}


def build_report(lead: Path, until: Optional[datetime] = None, team: Optional[str] = None,
                 cost_all: bool = False) -> dict:
    files = session_files(lead)
    lead_entries = list(iter_entries(lead, until))
    stamps = [parse_ts(e.get("timestamp")) for e in lead_entries]
    stamps = [s for s in stamps if s is not None]
    team = team or team_name(lead)
    return {
        "session": lead.stem,
        "team": team,
        "until": until.isoformat() if until else None,
        "window": [min(stamps).isoformat(), max(stamps).isoformat()] if stamps else None,
        "spend": spend_by_role(files, until),
        "wakes": wakes(lead_entries),
        "delivery": delivery(lead_entries, files, until),
        "rejections": rejections(team_tasks(team)),
        "amendment_gate": amendment_gate(min(stamps) if stamps else None, until or (max(stamps) if stamps else None)),
        "gates": gate_usage(team, min(stamps) if stamps else None, until or (max(stamps) if stamps else None)),
        "signals": transcript_signals(files, until),
        "context_cost": context_cost(session_dirs(lead.stem, every=cost_all), until),
        "hook_latency": hook_latency.summarise(session_dirs(lead.stem, every=cost_all)),
    }


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} KB"


def render_cost(cost: dict) -> list[str]:
    """The context-cost lines: totals, the top 5 checks, writes by agent, and per session
    when the report covers several."""
    t = cost["total"]
    frames = ", ".join(f"{frame} {_kb(size)}" for frame, size in sorted(cost["frames"].items()))
    lines = [f"context cost         {t['runs']} runs injected {_kb(t['context'])} context, "
             f"{_kb(t['system'])} system" + (f" ({frames})" if frames else "")]
    for name, r in cost["top_checks"].items():
        lines.append(f"  {name:<28}{_kb(r['context'] + r['system']):>12}{r['runs']:>6} runs")
    agents = sorted(cost["agents"].items(),
                    key=lambda kv: (-(kv[1]["teachback_submit"] + kv[1]["handoff"]), kv[0]))[:5]
    lines.append(f"teachback/handoff    {t['writes']} writes, {_kb(t['written'])}"
                 + ("; " + ", ".join(f"{name} {_kb(r['teachback_submit'] + r['handoff'])}"
                                     for name, r in agents) if agents else ""))
    for session, start in list(cost.get("session_start", {}).items())[:3]:
        top_parts = sorted(start["parts"].items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        lines.append(f"session start        {start['source']} {_kb(start['context'])} context, "
                     f"{_kb(start['system'])} system"
                     + (f" ({session[:8]})" if len(cost["session_start"]) > 1 else "")
                     + (": " + ", ".join(f"{name} {_kb(size)}" for name, size in top_parts) if top_parts else ""))
    if len(cost["sessions"]) > 1:
        ranked = sorted(cost["sessions"].items(),
                        key=lambda kv: (-(kv[1]["context"] + kv[1]["system"] + kv[1]["written"]), kv[0]))
        for session, r in ranked[:5]:
            lines.append(f"  session {session[:8]:<19}{_kb(r['context'] + r['system']):>12} injected"
                         f"{_kb(r['written']):>12} written")
    return lines


def render_report(report: dict) -> list[str]:
    """About 20 lines: the §1 tables for one session."""
    lines = [f"session {report['session']}  team {report['team'] or '?'}"
             + (f"  until {report['until']}" if report.get("until") else "")]
    spend = report["spend"]
    total_in = sum(r["input_side"] for r in spend.values())
    total_out = sum(r["output"] for r in spend.values())
    lines.append("spend by role        transcripts   calls   input-side   share   output")
    for role in ("lead", "teammate", "subagent"):
        row = spend.get(role)
        if row:
            lines.append(f"  {role:<18}{row['transcripts']:>13}{row['calls']:>8}"
                         f"{row['input_side'] / 1e6:>12.1f}M{row['share'] * 100:>7.1f}%{row['output']:>9}")
    lines.append(f"  {'total':<18}{sum(r['transcripts'] for r in spend.values()):>13}"
                 f"{sum(r['calls'] for r in spend.values()):>8}{total_in / 1e6:>12.1f}M{'':>8}{total_out:>9}")
    w = report["wakes"]
    lines.append(f"lead wakes           {w['idle_only_wakes']} idle-only of {w['teammate_entries']} teammate entries; "
                 f"{w['idle_only_calls']} calls, {w['idle_only_output']} output tokens; "
                 f"{w['idle_only_no_tool']} made no tool call")
    lines.append(f"double wakes         {w['double_wakes']} (median gap {w['double_wake_median_gap_s']} s)")
    d = report["delivery"]
    lines.append(f"lead->teammate       {d['located']}/{d['messages']} located; {d['late']} arrived >60 s late"
                 + (f", median {d['late_median_min']} min" if d["late_median_min"] is not None else ""))
    r = report["rejections"]
    lines.append(f"rejections           {r['rejected_tasks']} tasks: {r['with_unaddressed_amend']} with an "
                 f"unaddressed amendment, {r['other']} other")
    g = report["amendment_gate"]
    lines.append(f"amendment gate       {g['would_deny']} would-deny, {g['rejection_rev_mismatch']} rejection rev mismatch")
    s = report["signals"]
    lines.append(f"psguard denials      {s['psguard_denials']}")
    lines.append(f"task reminders       {s['task_reminders']} (lead + teammates)")
    lines.append(f"unflagged alarms     {s['unflagged_alarms']} in the lead's context")
    if (report.get("context_cost") or {}).get("sessions"):
        lines += render_cost(report["context_cost"])
    lines += hook_latency.render(report.get("hook_latency") or {})
    checks = report.get("gates") or {"total": {"returned": 0, "repeats": 0, "notes": 0, "noted": 0}, "uses": {}}
    t = checks["total"]
    lines.append(f"write checks         {t['returned']} returned, {t['repeats']} repeats passed, {t['notes']} notes, {t['noted']} noted")
    for use, r in checks["uses"].items():
        lines.append(f"  {use:<22}{r['returned']:>4} returned {r['repeats']:>3} repeats {r['notes']:>4} notes {r['noted']:>3} noted")
    return lines
