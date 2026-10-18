"""The state-ownership check behind bin/repact-doctor (check 11), the user-profile check and
`--clear task-store`.

Used by bin/_doctor.py (run_checks) and bin/repact-doctor. Reads the table in
docs/architecture/state-ownership.md: every owning store's writer must exist in the plugin
(red when one is gone), a store with no files yet is noted, and a team whose task files
vanished under a standing teams/<team>/config.json while its lead journal recorded tasks is
amber. check_profile is the advisory line for the optional per-user profile; check_distill the
line for the distill session queue; check_packs the line for skill-pack recall
fragments. Split from _doctor.py to keep that module under its byte ceiling.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from _doctor_result import Result, _capped  # noqa: E402
from _repact_common import plugin_data_dir, profile, profile_path, task_store  # noqa: E402

# The profile is optional (no file: a neutral line). When one exists it must name its owner
# (D8a: a missing identity exempts nothing in the scrub); the KB key is optional, since the KB
# stays off without one (paths.kb, else the legacy paths.vault).
PROFILE_KEYS = ("identity.email",)
KB_KEYS = ("paths.kb", "paths.vault")
PROFILE_EXAMPLE = "templates/profile.example.json"


STATE_DOC = Path("docs") / "architecture" / "state-ownership.md"
STATE_BLOCK = re.compile(r"```json state-ownership\n(.*?)\n```", re.S)
STATE_DOC_ENV = "REPACT_DOCTOR_STATE_DOC"       # tests only: the table to read instead
PROBE_MARKER = "task-store-probe.json"           # equal to shared/task_store's (pinned by a test)
JOURNAL_TASK_ROWS = ("agent_dispatch", "task_metadata_snapshot")
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _state_doc(ctx) -> Optional[Path]:
    """The ownership page: the plugin's repo first (docs/ sits above repacted/pact-plugin),
    then the plugin itself (should the page be relocated into it)."""
    override = ctx.env.get(STATE_DOC_ENV)
    if override:
        return Path(override)
    for base in (ctx.plugin_root.parents[1], ctx.plugin_root):
        if (base / STATE_DOC).is_file():
            return base / STATE_DOC
    return None


def _state_table(path: Path) -> List[dict]:
    match = STATE_BLOCK.search(path.read_text(encoding="utf-8"))
    rows = json.loads(match.group(1)) if match else []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _store_present(ctx, row: dict) -> Optional[bool]:
    """Whether the store holds anything yet; None when its root is not here (no repo)."""
    roots = {"config": ctx.config_root, "data": plugin_data_dir(), "repo": ctx.repo_root,
             "session": ctx.config_root / "pact-sessions"}
    base = roots.get(str(row.get("root")))
    pattern = str(row.get("glob") or "")
    if base is None or not pattern:
        return None
    if row.get("root") == "session":
        pattern = "*/*/" + pattern
    return any(True for _ in base.glob(pattern))


def _journal_task_count(journal: Path) -> int:
    """Distinct task ids the journal's dispatch and snapshot rows name."""
    needles = tuple(f'"{kind}"' for kind in JOURNAL_TASK_ROWS)
    ids = set()
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
                    ids.add(str(row.get("task_id", "")))
    except OSError:
        return 0
    return len(ids)


def wiped_teams(ctx) -> List[str]:
    """Teams whose config.json stands, whose task directory exists with zero task files, and
    whose lead session journal recorded tasks: the host stopped writing, or wiped, the store.
    A team whose task directory is gone is a finished team the host cleaned up (silent)."""
    store = task_store(ctx.config_root)
    found = []
    for config in sorted((ctx.config_root / "teams").glob("*/config.json")):
        team = config.parent.name
        directory = store.team_dir(team)
        if directory is None or not directory.is_dir() or store.files(team):
            continue
        try:
            lead = json.loads(config.read_text(encoding="utf-8")).get("leadSessionId")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(lead, str) or not SAFE_ID.fullmatch(lead):
            continue
        recorded = sum(_journal_task_count(journal) for journal
                       in (ctx.config_root / "pact-sessions").glob(f"*/{lead}/session-journal.jsonl"))
        if recorded:
            found.append(f"task store: no files for {team} (journal recorded {recorded} tasks)")
    return found


def _profile_value(data: dict, dotted: str) -> Optional[str]:
    section, key = dotted.split(".")
    block = data.get(section)
    value = block.get(key) if isinstance(block, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def check_profile(ctx) -> "Result":
    """Advisory: no profile is a valid install (a neutral green line); a profile that exists
    names its owner's identity, so an upgrade from a build that carried it as a literal never
    fails silently. The KB key is reported, never required."""
    path = profile_path()
    if not path.exists():
        return Result("profile", "user profile", "green",
                      f"none (optional: copy {PROFILE_EXAMPLE} to {path})")
    data = profile()
    missing = [dotted for dotted in PROFILE_KEYS if _profile_value(data, dotted) is None]
    if missing:
        return Result("profile", "user profile", "amber", f"{path} lacks {', '.join(missing)}",
                      [f"set {', '.join(PROFILE_KEYS)} (and identity.slack_id, identity.name) in "
                       f"{path}: identity.* is your own and is what the scrub exempts; paths.kb "
                       "(optional) is your knowledge folder, and the KB stays off without it"])
    kb_key = next((dotted for dotted in KB_KEYS if _profile_value(data, dotted)), None)
    if kb_key is None:
        return Result("profile", "user profile", "green", f"identity set in {path}; KB off (no paths.kb)")
    return Result("profile", "user profile", "green", f"identity and {kb_key} set in {path}")


def check_distill(ctx) -> "Result":
    """Advisory: whether transcript distill is on and how many ended sessions wait in the
    queue; amber only when the SessionEnd enqueue swallowed an error."""
    from shared import distill_queue as queue

    distill = profile().get("distill")
    on = isinstance(distill, dict) and distill.get("transcripts") is True
    count, errors = queue.queued_count(), queue.error_count()
    summary = (f"transcript distill {'on' if on else 'off'}; {count} ended session(s) queued"
               + ("; run repact-distill run" if count else ""))
    if errors:
        return Result("distill", "distill queue", "amber", f"{summary}; {errors} enqueue error(s)",
                      [f"see {queue.ERRORS_FILE} in {queue.queue_dir(create=False)} (the last "
                       "error type); delete it once read"])
    return Result("distill", "distill queue", "green", summary)


def _printable(text: str) -> str:
    """A report line fit for the terminal: control and format characters become '?'."""
    return "".join(ch if ch.isprintable() else "?" for ch in text)


def check_packs(ctx) -> "Result":
    """The recall.d fragments of installed skill packs (_recall/packs.py): green (optional,
    like the profile) with no pack, amber when a fragment was skipped or a trigger name is claimed twice (never resolved
    silently: the claim is dropped), else green with the count of added triggers."""
    from _recall import packs, skills

    report = packs.merge(skills.load_triggers(), ctx.config_root, os.getcwd())[1]
    if not (report.packs or report.lines()):
        return Result("packs", "skill packs", "green",
                      "no skill pack installed (optional: reference/recall-packs.md)")
    names = ", ".join(report.packs) or "none loaded"
    if report.lines():
        return Result("packs", "skill packs", "amber",
                      f"{len(report.conflicts)} conflict(s), {len(report.problems)} skipped ({names})",
                      _capped([_printable(line) for line in report.lines()]
                              + ["fragment format: reference/recall-packs.md"]))
    return Result("packs", "skill packs", "green", f"{report.triggers} recall trigger(s) from {names}")


def check_state(ctx) -> "Result":
    """Every owning store in the table has its writer in the plugin (a missing writer is red: a
    store nothing can write), a store with no files yet is noted (green), and a team whose task
    files vanished under a standing config.json while its journal recorded tasks is amber."""
    doc = _state_doc(ctx)
    if doc is None or not doc.is_file():
        return Result("state", "state ownership", "skip", "no state-ownership table shipped here")
    try:
        rows = _state_table(doc)
    except (OSError, ValueError) as err:
        return Result("state", "state ownership", "red", f"{doc}: the table cannot be read ({err})")
    if not rows:
        return Result("state", "state ownership", "red", f"{doc}: no state-ownership block")
    missing, empty, present = [], [], 0
    for row in rows:
        writers = [w for w in row.get("writers", []) if isinstance(w, str) and w not in ("host", "user")]
        missing += [f"{row['id']}: writer {w} is not in the plugin" for w in writers
                    if not (ctx.plugin_root / w).exists()]
        state = _store_present(ctx, row)
        if state is True:
            present += 1
        elif state is False:
            empty.append(f"{row['id']}: nothing written yet ({row.get('root')}/{row.get('glob')})")
    wiped = wiped_teams(ctx)
    detail = missing + wiped + empty
    if missing:
        return Result("state", "state ownership", "red",
                      f"{len(missing)} store(s) without their writer", _capped(detail))
    if wiped:
        return Result("state", "state ownership", "amber",
                      f"{len(wiped)} team(s) with a wiped task store", _capped(detail))
    return Result("state", "state ownership", "green",
                  f"{len(rows)} stores on record, {present} present", _capped(empty))


def clear_task_store_marker(ctx, session_id: str) -> List[Path]:
    """Remove the probe's HALT marker of one session (`repact-doctor --clear task-store`)."""
    if not SAFE_ID.fullmatch(session_id or ""):
        return []
    removed = []
    for marker in (ctx.config_root / "pact-sessions").glob(f"*/{session_id}/{PROBE_MARKER}"):
        try:
            marker.unlink()
        except OSError:
            continue
        removed.append(marker)
    return removed
