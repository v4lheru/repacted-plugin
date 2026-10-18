#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/doctor_brief.py
Summary: SessionStart hook: one line from repact-doctor's CACHED result, or nothing when green.
Used by: hooks.json SessionStart (its own entry; session_init.py is not involved).
Design: the design notes section D5 DOCTOR.

It never runs a check itself: it reads ${CLAUDE_PLUGIN_DATA}/doctor.json and nothing else, so it
adds no measurable latency to a session start. When that cache is missing or older than
REFRESH_AFTER, it starts `repact-doctor --quick --quiet` detached, niced and in its own session,
and returns at once without waiting for it: the NEXT session start reads the fresh result. (An
inline --quick run takes seconds, far past the latency pin, so it stays detached.)

A cache older than STALE_AFTER is not shown as current: the line says how old it is, names the
checks that were not green without their (drifted) counts, and drops the user-action list. At any
age, a listed task whose task file now says completed or deleted is left out of that list.

Output: nothing (suppressOutput) on a compaction restart or in a teammate's session (agent_type
present and not the orchestrator). The doctor's line is absent when every check is green or
skipped and when the cache is missing. Each line goes on one channel (1.4.1): the doctor's line
as the systemMessage the user sees, e.g.
  doctor: 1 red (secrets: credential values in 2 place(s)); 2 user actions open: #81 ALERT ... · #82 ...
and the tasks line, which the lead reads, as additionalContext.

The cache is one per install, and the doctor reads the repo of the directory it ran in. Its
repo-bound lines (a backlog's USER-OWNED actions, the release position and gates, the team
policy) are shown only when the cache's repo is the main checkout of this session's cwd, so one
project's backlog never reaches another's session. Open user-action task items are the user's, whatever the repo.
Fail-open: any error prints suppressOutput and exits 0. Stdlib only, no plugin imports (a cold
import of the shared module costs more than this whole hook).

D10 tasks line (the design notes section 7): at most one more line,
  tasks: next T-1a2b3c4d "Merge D3 before D5" (+4 ready) · active 3 · blocked 2
from the task log of the session's repo (found from the event's cwd by stat only). When the log's
(size, mtime_ns) equals the stamp in .repacted/cache/summary.json, the cached line. Otherwise, at
TASKS_INLINE_MAX bytes or less, an inline replay through hooks/shared/project_tasks.py loaded BY
PATH. Above that, nothing, and a detached `repact-task summary --refresh` fills the cache for the
next start. It never writes, and it follows the same silences as the doctor line.

Scratch prune: when the pruner's stamp (${CLAUDE_PLUGIN_DATA}/scratch-prune.json) is missing or a
day old, it starts `repact-scratch-prune --auto` detached and niced, as it starts the doctor's
refresh, skipping this session's folder. When a run freed 1 GB or more it leaves a report, and the
next lead start shows one systemMessage line, e.g.
  scratch: freed 12.4 GB of build output from 3 stale session folder(s) (repact-scratch-prune)
The report is taken by an atomic rename, so one session shows it, once. REPACT_SCRATCH_PRUNE_OFF
stops the run; REPACT_DOCTOR_NO_REFRESH stops it with the doctor's refresh.

Task store (docs/architecture/state-ownership.md): on a resume only, one more systemMessage line
when the session's team has a task directory holding zero task files while its journal recorded
tasks, e.g.
  task store: no files for session-ab12cd34 (journal recorded 7 tasks)
Silent when the two agree; `repact-doctor` makes the same comparison for every team.

"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path

CACHE_NAME = "doctor.json"
REFRESH_AFTER = datetime.timedelta(hours=1)    # a --quick refresh costs ~5 s of niced, detached CPU
STALE_AFTER = datetime.timedelta(hours=12)     # older than this, the line says so and lists no actions
CLOSED_STATUSES = ("completed", "deleted")     # equal to shared/user_actions' (pinned by a test)
REPO_CHECKS = ("release", "gates", "policy")   # read from the doctor's repo, so shown only in it
TASK_ITEM = re.compile(r"^#(?P<id>[^\s/]+) .*\((?P<team>[^()/]+)\)$")  # _doctor._alert_tasks' item shape
NO_REFRESH_ENV = "REPACT_DOCTOR_NO_REFRESH"
QUIET_SOURCES = {"compact"}
LEAD_AGENT = "pact-orchestrator"  # every LEAD_AGENT_TYPES spelling ends in it (pinned by a test)
MAX_ITEMS = 3
SUPPRESS = json.dumps({"suppressOutput": True})
DOCTOR = Path(__file__).resolve().parents[1] / "bin" / "repact-doctor"
TASK_CLI = Path(__file__).resolve().parents[1] / "bin" / "repact-task"
TASK_READER = Path(__file__).resolve().parent / "shared" / "project_tasks.py"
TASK_LOG = Path(".repacted") / "tasks.jsonl"                 # equal to project_tasks' (pinned by a test)
TASK_CACHE = Path(".repacted") / "cache" / "summary.json"
# Largest log replayed inline. MEASURED against the 25 ms pin (test_doctor_brief_tasks): the reader's
# load is ~11 ms, and replay grows with size; above this the line waits for the detached refresh.
TASKS_INLINE_MAX = 512 * 1024                    # the design's §7 cap; a busy repo's log passes 64 KB in days
SCRATCH_PRUNE = Path(__file__).resolve().parents[1] / "bin" / "repact-scratch-prune"
SCRATCH_STATE = "scratch-prune.json"           # equal to _scratch_prune's (pinned by a test)
SCRATCH_REPORT = "scratch-prune-report.json"   # equal to _scratch_prune's (pinned by a test)
SCRATCH_EVERY = datetime.timedelta(hours=24)
SCRATCH_REPORT_MIN = 1 << 30
SCRATCH_OFF_ENV = "REPACT_SCRATCH_PRUNE_OFF"
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,120}")   # a session id or team name fit for a path
JOURNAL_TASK_ROWS = ("agent_dispatch", "task_metadata_snapshot")  # equal to bin/_doctor's


def cache_file() -> Path:
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:  # the plugin harness sets it for every plugin hook: the normal, cheap path
        return Path(data).expanduser() / CACHE_NAME
    # Outside the harness only: the shared package costs ~27 ms to import, so it stays lazy.
    from shared.plugin_paths import default_data_dir
    return default_data_dir() / CACHE_NAME


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def age(cache: dict, now: datetime.datetime) -> datetime.timedelta | None:
    """How old the cache is, or None when it carries no readable timestamp."""
    try:
        return now - datetime.datetime.fromisoformat(cache["generated"])
    except (KeyError, TypeError, ValueError):
        return None


def is_stale(cache: dict, now: datetime.datetime) -> bool:
    cache_age = age(cache, now)
    return cache_age is None or cache_age > REFRESH_AFTER


def tasks_dir() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if config:  # the cheap path; otherwise the shared resolver, lazily, as in cache_file()
        return Path(config).expanduser() / "tasks"
    from shared.paths import get_claude_config_dir
    return get_claude_config_dir() / "tasks"


def is_closed_task(item: str, tasks: Path) -> bool:
    """True when `item` names a task ("#45 subject (team)") whose task file is now closed. Any
    other item (a USER-OWNED backlog line), or a file that cannot be read, stays listed."""
    match = TASK_ITEM.match(item)
    if not match:
        return False
    try:
        task = json.loads((tasks / match["team"] / f"{match['id']}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(task, dict) and task.get("status") in CLOSED_STATUSES


def start_refresh(cwd: Path | None = None) -> None:
    """Fire and forget; the doctor's own run lock stops a pile-up of concurrent refreshes. It
    runs in the session's repo, so the cache it writes is that repo's."""
    if os.environ.get(NO_REFRESH_ENV) or not DOCTOR.is_file():
        return
    _spawn([str(DOCTOR), "--quick", "--quiet"], cwd=cwd)


def _spawn(command: list, cwd: Path | None = None) -> None:
    import subprocess  # only on a refresh path: it costs more than the rest of the hook

    nice = ["/usr/bin/nice", "-n", "19"] if Path("/usr/bin/nice").exists() else []
    subprocess.Popen([*nice, sys.executable, *command], cwd=cwd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     start_new_session=True)


def start_scratch_prune(data_dir: Path, session_id) -> None:
    """Fire and forget, at most once per SCRATCH_EVERY; the pruner's own lock and stamp stop a
    pile-up of concurrent starts."""
    if os.environ.get(NO_REFRESH_ENV) or os.environ.get(SCRATCH_OFF_ENV) or not SCRATCH_PRUNE.is_file():
        return
    try:
        stamped = datetime.datetime.fromtimestamp((data_dir / SCRATCH_STATE).stat().st_mtime,
                                                  datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) - stamped < SCRATCH_EVERY:
            return
    except OSError:
        pass
    command = [str(SCRATCH_PRUNE), "--auto", "--state-dir", str(data_dir)]
    if isinstance(session_id, str) and session_id.strip():
        command += ["--skip-session", session_id.strip()]
    _spawn(command)


def scratch_line(data_dir: Path) -> str:
    """The pruner's report line, once: the rename takes the report so no other start shows it."""
    taken = data_dir / f"{SCRATCH_REPORT}.shown"
    try:
        os.replace(data_dir / SCRATCH_REPORT, taken)
    except OSError:
        return ""
    report = load(taken)
    freed, sessions = report.get("freed"), report.get("sessions")
    if not isinstance(freed, int) or isinstance(freed, bool) or freed < SCRATCH_REPORT_MIN \
            or not isinstance(sessions, int):
        return ""
    return (f"scratch: freed {freed / 2**30:.1f} GB of build output from {sessions} stale session "
            "folder(s) (repact-scratch-prune)")


def main_checkout(start) -> Path | None:
    """The main checkout holding `start`: project_tasks.find_main_checkout, copied here because
    loading that module costs ~11 ms (the two are pinned equal by a test)."""
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    if here.is_file():
        here = here.parent
    for directory in (here, *here.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return directory
        if dot_git.is_file():
            try:
                line = dot_git.read_text(encoding="utf-8").strip()
                gitdir = (directory / line.split(":", 1)[1].strip()).resolve() if line.startswith("gitdir:") else None
            except OSError:
                return None
            if gitdir is None:
                return None
            commondir = gitdir / "commondir"
            if not commondir.is_file():
                return directory
            try:
                common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
            except OSError:
                return None
            return common.parent if common.name == ".git" else None
        if (directory / "HEAD").is_file() and (directory / "objects").is_dir() and (directory / "refs").is_dir():
            return None
    return None


def tasks_line(event: dict) -> str:
    """The tasks line for the session's repo, or ''. Reads only; never writes."""
    cwd = event.get("cwd")
    main = main_checkout(cwd) if isinstance(cwd, str) and cwd else None
    if main is None:
        return ""
    try:
        stat = (main / TASK_LOG).stat()
    except OSError:
        return ""
    cache = load(main / TASK_CACHE)
    if cache.get("stamp") == [stat.st_size, stat.st_mtime_ns]:
        line = cache.get("line")
        return line if isinstance(line, str) else ""
    if stat.st_size > TASKS_INLINE_MAX:
        if not os.environ.get(NO_REFRESH_ENV) and TASK_CLI.is_file():
            _spawn([str(TASK_CLI), "summary", "--refresh"], cwd=main)   # fills the cache for the next start
        return ""
    import importlib.util

    # The cache-miss path only: the stdlib-only reader, loaded by path (allowlisted in
    # test_hook_infra_classifier beside the other two by-path helpers).
    spec = importlib.util.spec_from_file_location("repacted_project_tasks_brief", TASK_READER)
    reader = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reader  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(reader)
    return reader.summary_line(reader.load(main))


def task_store_line(event: dict) -> str:
    """On a resume, one line when the session's team has a task directory with zero task files
    while its journal recorded tasks (the host stopped writing, or wiped, the store); '' when
    they agree or there is nothing to compare. The team comes from the session's own
    pact-session-context.json; the journal is read only on the mismatch path, so a healthy
    resume costs a directory listing and nothing more."""
    if event.get("source") != "resume":
        return ""
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not SAFE_ID.fullmatch(session_id):
        return ""
    config = tasks_dir().parent
    team, journal = "", None
    for session_dir in config.glob(f"pact-sessions/*/{session_id}"):
        name = load(session_dir / "pact-session-context.json").get("team_name")
        if isinstance(name, str) and SAFE_ID.fullmatch(name):
            team, journal = name, session_dir / "session-journal.jsonl"
    if not team or journal is None:
        return ""
    directory = tasks_dir() / team
    if not directory.is_dir() or any(p.is_file() for p in directory.glob("*.json")):
        return ""
    needles = tuple(f'"{kind}"' for kind in JOURNAL_TASK_ROWS)
    recorded = set()
    try:
        with journal.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not any(needle in line for needle in needles):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("type") in JOURNAL_TASK_ROWS:
                    recorded.add(str(row.get("task_id", "")))
    except OSError:
        return ""
    return f"task store: no files for {team} (journal recorded {len(recorded)} tasks)" if recorded else ""


def is_teammate(event: dict) -> bool:
    """A specialist's session: agent_type (the field shared.pact_context.is_lead reads) is present
    and is not the orchestrator. Absent means a plain or lead session, which does get the line."""
    agent_type = event.get("agent_type")
    return isinstance(agent_type, str) and bool(agent_type.strip()) \
        and agent_type.strip().rsplit(":", 1)[-1] != LEAD_AGENT


def _group(checks: list, state: str) -> str:
    hits = [c for c in checks if c.get("state") == state and c.get("id") != "actions"]
    if not hits:
        return ""
    return f"{len(hits)} {state} (" + "; ".join(f"{c.get('name')}: {c.get('summary')}" for c in hits) + ")"


def render(cache: dict, now: datetime.datetime | None = None, tasks: Path | None = None,
           repo: Path | None = None) -> str:
    """The one line, or '' when nothing needs the user's attention. `repo` is the main checkout
    of the session's cwd; the repo-bound lines show only when the cache was made there."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    same_repo = repo is not None and cache.get("repo") == str(repo)
    checks = [c for c in cache.get("checks", []) if isinstance(c, dict)
              and (same_repo or c.get("id") not in REPO_CHECKS)]
    generated = str(cache.get("generated", "?"))[:16]
    cache_age = age(cache, now)
    if cache_age is None or cache_age > STALE_AFTER:
        names = [str(c.get("name")) for c in checks if c.get("state") in ("red", "amber") and c.get("id") != "actions"]
        if not names:
            return ""
        hours = "unknown age" if cache_age is None else f"{int(cache_age.total_seconds() // 3600)}h old"
        return (f"doctor: STALE result ({hours}, from {generated}), refreshing in the background; "
                f"not green then: {', '.join(names)}. Run repact-doctor for the current state.")
    parts = [p for p in (_group(checks, "red"), _group(checks, "amber")) if p]
    actions = next((c for c in checks if c.get("id") == "actions" and c.get("state") in ("red", "amber")), None)
    if actions:
        tasks = tasks or tasks_dir()
        items = [str(i) for i in actions.get("detail", [])
                 if not str(i).startswith("... and ") and not is_closed_task(str(i), tasks)
                 and (same_repo or TASK_ITEM.match(str(i)))]
        if items:
            shown = " · ".join(items[:MAX_ITEMS]) + (" · ..." if len(items) > MAX_ITEMS else "")
            parts.append(f"{len(items)} user action(s) open: {shown}")
    if not parts:
        return ""
    return f"doctor: {'; '.join(parts)} (as of {generated}; run repact-doctor)"


def main() -> None:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    if isinstance(event, dict) and event.get("source") in QUIET_SOURCES:
        print(SUPPRESS)
        return
    cache_path = cache_file()
    cache = load(cache_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    cwd = event.get("cwd") if isinstance(event, dict) else None
    repo = main_checkout(cwd) if isinstance(cwd, str) and cwd else None
    if is_stale(cache, now):
        start_refresh(repo)  # a teammate's start may refresh too; only the line is the lead's
    try:
        start_scratch_prune(cache_path.parent, event.get("session_id") if isinstance(event, dict) else None)
    except Exception:  # the prune is a side job: it never costs the doctor's line
        pass
    if isinstance(event, dict) and is_teammate(event):
        print(SUPPRESS)
        return
    doctor = render(cache, now, repo=repo)
    try:
        scratch = scratch_line(cache_path.parent)
    except Exception:
        scratch = ""
    try:
        store = task_store_line(event) if isinstance(event, dict) else ""
    except Exception:  # the comparison fails open; it never costs the doctor's line
        store = ""
    doctor = "\n".join(line for line in (doctor, scratch, store) if line)
    lines = []
    try:
        lines.append(tasks_line(event) if isinstance(event, dict) else "")
    except Exception:  # the tasks line fails open on its own, and never costs the doctor's line
        pass
    context = "\n".join(part for part in lines if part)
    if not doctor and not context:
        print(SUPPRESS)
        return
    out: dict = {}
    if context:
        out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": context}
    if doctor:
        out["systemMessage"] = doctor
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: a broken brief must never block or break a session start
        print(SUPPRESS)
    sys.exit(0)
