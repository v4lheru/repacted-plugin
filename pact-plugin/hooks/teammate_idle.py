#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_idle.py
Summary: TeammateIdle hook: the Layer 2 unflagged-background advisory. A
         teammate that idles three consecutive times on an in_progress task
         while background work it launched has no covering intentional_wait
         is told, once, to collect the result or flag the wait.
         Also the missed-wake line (missed_wake_line: a stored HANDOFF or
         teachback that has waited on the lead past the staleness threshold;
         the lead's own scan, missed_wake_scan.py, runs only on a prompt). It
         is a systemMessage: shown on the user's screen, never in the lead's
         model. An idle lead makes no tool call, so the user who reads it
         prompts the lead and the scan then gives the lead the full alarm.
         And the unread-key re-prompt (unread_lead_keys): a teammate
         that idles while a task it owns and has in_progress holds an amend_<n>
         or "<resource>_grant" key it was never handed is sent back to work
         with those keys (exit 2: the host gives stderr to the teammate and it
         keeps working). A lead message sent mid-turn can go unread; the task
         file cannot.
Used by: hooks.json TeammateIdle hook

# livelock-safe: emits once, at the third consecutive unflagged idle, and not
# again for the same task. Exits deterministically on every code path. Does
# NOT consume intentional_wait. The missed-wake line is emitted once per
# (task, wait since) (said_once, <session_dir>/idle-said-once.json); a state
# that cannot be recorded is not emitted. The unread-key re-prompt is the one exit 2: it
# fires once per key, recorded in the relay stamp BEFORE it is emitted, and a
# stamp that cannot be written emits nothing, so it cannot hold a teammate in
# a loop.

Input: JSON from stdin with teammate_name, team_name
Output: JSON with systemMessage (the advisory, the missed-wake line) or
        suppressOutput.
        Exit 2 with the unread keys on stderr for the re-prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation-only. This file has `from __future__ import annotations`, so
    # the type below is a string at runtime and this import never executes --
    # which is what keeps a per-session hook from paying for a type.
    from datetime import datetime

import json
import os
import sys
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.background_work import frame_team_and_name
from shared.session_state import _sanitize_member_name
from shared.payload_digest import stored_teachback
from shared.task_utils import iter_team_task_jsons


# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

IDLE_PREAMBLE = "[System idle notification — no response needed] "

def find_teammate_task(
    tasks: list[dict],
    teammate_name: str,
) -> dict | None:
    """
    Find the most recent task owned by this teammate.

    Looks for tasks with owner matching teammate_name. Returns the
    in_progress task if one exists, otherwise the most recently completed one.

    Args:
        tasks: The team's tasks, as main() reads them
        teammate_name: Name of the idle teammate

    Returns:
        Task dict, or None if no task found for this teammate
    """
    in_progress = None
    completed = None

    for task in tasks:
        owner = task.get("owner", "")
        if owner != teammate_name:
            continue

        status = task.get("status", "")
        if status == "in_progress":
            in_progress = task
        elif status == "completed":
            # Keep the highest-ID completed task (most recent)
            # Task IDs are numeric strings — compare as int to avoid
            # lexicographic errors (e.g., "3" > "20" in string comparison)
            try:
                task_id_num = int(task.get("id", "0"))
                completed_id_num = int(completed.get("id", "0")) if completed else -1
            except (ValueError, TypeError):
                task_id_num = 0
                completed_id_num = -1 if completed is None else 0
            if completed is None or task_id_num > completed_id_num:
                completed = task

    return in_progress or completed


MISSED_WAKE_REASON = "awaiting_lead_completion"     # missed_wake_scan._MISSED_WAKE_REASON
SAID_ONCE_FILENAME = "idle-said-once.json"
SAID_ONCE_MAX_BYTES = 256 * 1024    # a larger state file is not read: nothing is said


def said_once(session_dir: str, key: str, value: str) -> bool:
    """True the first time `value` is recorded under `key` in the session's
    idle-said-once.json, False when it is what the key already holds or the state cannot be
    recorded: no session dir, a state file that is a symlink, no regular file or over
    SAID_ONCE_MAX_BYTES, a failed write. The bound that keeps the missed-wake line from
    repeating on every idle (the livelock class this hook must stay out of): fail closed,
    an unrecorded line is not emitted. The file is disk state: its values are compared,
    never printed, and text that does not parse to an object records nothing (False)."""
    import stat

    from shared import state_file

    def _apply(text: str) -> tuple[str, bool, bool]:
        try:
            said = json.loads(text) if text.strip() else {}
        except ValueError:
            return text, False, False
        if not isinstance(said, dict) or said.get(key) == value:
            return text, False, False
        said[key] = value
        return json.dumps(said), True, True

    try:
        if not session_dir or not os.path.isdir(session_dir):
            return False
        path = Path(session_dir) / SAID_ONCE_FILENAME
        try:
            found = os.lstat(path)
        except FileNotFoundError:
            found = None
        # locked_update never follows a symlink either; this refuses it before the lock
        # sidecar is opened, and refuses a file too large to be this hook's own.
        if found is not None and (not stat.S_ISREG(found.st_mode) or found.st_size > SAID_ONCE_MAX_BYTES):
            return False
        return state_file.locked_update(path, _apply, Path(session_dir)) is True
    except (OSError, TypeError, ValueError):
        return False


def missed_wake_line(tasks: list, session_dir: str, now: datetime | None = None) -> str | None:
    """One line naming each task that holds a well-formed awaiting_lead_completion wait past
    the staleness threshold (the predicate of missed_wake_scan.find_stale_missed_wakes) and
    was not named before for that wait (said_once on the task and its `since`), or None.
    Says what the task has stored, so the lead knows what to accept, reject or wake.
    Shown on the user's screen only; nothing is written under teams/."""
    from shared.intentional_wait import validate_wait, wait_stale

    found = []
    for task in tasks:
        metadata = task.get("metadata") if isinstance(task, dict) else None
        wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
        if task.get("status") != "in_progress" or not validate_wait(wait) \
                or wait.get("reason") != MISSED_WAKE_REASON or not wait_stale(wait, _now=now):
            continue
        task_id = _sanitize_member_name(str(task.get("id") or ""))
        if not task_id or not said_once(session_dir, f"wake:{task_id}", str(wait.get("since") or "")):
            continue
        stored = ("HANDOFF stored" if isinstance(metadata.get("handoff"), dict)
                  else "teachback stored" if isinstance(stored_teachback(metadata), dict)
                  else "nothing stored")
        found.append(f"#{task_id} ({_sanitize_member_name(str(task.get('owner') or '')) or 'unknown'}, {stored})")
    if not found:
        return None
    return ("Missed wake: " + ", ".join(found) + " waited past the threshold on awaiting_lead_completion "
            "with no acceptance, rejection or wake on disk. Lead: accept or reject it on the task, then send the wake.")


UNFLAGGED_ADVISORY = (
    "You launched background work and have no flagged wait; it may already "
    "have finished. Either collect "
    "the result now, or SET metadata.intentional_wait on the task, naming what "
    "you are waiting for. validate_wait accepts a free-form reason."
)


def _clear_unflagged_idle(teammate_name: str, team_name: str) -> None:
    from shared.background_work import update_unflagged_idle_counts

    def _drop(counts: dict) -> dict:
        counts.pop(teammate_name, None)
        return counts

    update_unflagged_idle_counts(_drop, team_name)


def check_unflagged_background(
    tasks: list, teammate_name: str, team_name: str, now: datetime | None = None
) -> str | None:
    """Layer 2 — advise once at three consecutive unflagged idles.

    The counter lives in `unflagged_background_idle.json` (background_work).
    """
    from shared.background_work import (
        UNFLAGGED_IDLE_THRESHOLD,
        discharge_acknowledged_for_owner,
        stamp_idled_at,
        teammate_is_separate_process,
        unflagged_fire,
        update_unflagged_idle_counts,
    )

    # DISCHARGE BEFORE TESTING, AND BEFORE THE STATUS GATE. A teammate that
    # flagged a wait covering its launch has demonstrably associated the two,
    # so the record has done its job and must not outlive the acknowledgment.
    #
    # It covers EVERY task this teammate owns, whatever its status, in ONE
    # registry update. A teammate owning no in_progress task still has records
    # (anchored on its most recently completed task) and still idles, so a
    # discharge placed behind the status gate below would never retire them.
    # And a record lists the tasks held at LAUNCH, which need not include the
    # task resolved here: a teammate holding two tasks may flag the other one,
    # and a consultant may have completed a newer task since. A record is
    # dropped only when a task it lists is owned here and that task's wait
    # covers its launch, so a teammate with no covering wait drops nothing.
    #
    # WHAT THIS BUYS, STATED AT ITS ACTUAL SIZE: an ACCURATE CITATION, not the
    # removal of a false alarm. Without it, a teammate that flagged, collected
    # the result and cleared the wait keeps a live record, and a LATER
    # unflagged idle draws an advisory naming a job that finished hours ago.
    # The advice is still correct at that moment — the counter below only
    # reaches its threshold on three CONSECUTIVE idles with no valid wait, so
    # the teammate really is idling unflagged — but the reason given is stale.
    # An agent that checks, finds the job long done, and concludes the alarm
    # is unreliable is the cost this prevents.
    #
    # DO NOT RESTATE THIS AS "the alarm would otherwise fire at teammates who
    # behaved correctly". That is FALSE and was checked: the counter is
    # cleared on every tick where the fire predicate is false, so a teammate
    # that flags never accumulates toward the threshold, and one that keeps
    # working does not tick at all.
    discharge_acknowledged_for_owner(tasks, teammate_name, team_name=team_name, now=now)

    # A separate-process (tmux) teammate is woken by its own background
    # completion and collects the result on that turn, so the record outlives
    # the job and an idle-count advisory would name work it already has. The
    # discharge above still runs for it; only the advisory is skipped.
    if teammate_is_separate_process(team_name, teammate_name):
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    task = find_teammate_task(tasks, teammate_name)
    if not task or task.get("status") != "in_progress":
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    fire, _wait_class, record = unflagged_fire(
        task, team_name=team_name, tasks=tasks, now=now
    )
    if not fire or record is None:
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    task_id = str(task.get("id") or "")
    if task_id:
        stamp_idled_at(task_id, team_name=team_name, now=now)

    result = {"emit": False}

    def _bump(counts: dict) -> dict:
        entry = counts.get(teammate_name, {})
        if isinstance(entry, int):
            entry = {"count": entry, "task_id": ""}
        if not isinstance(entry, dict):
            entry = {}
        last_task_id = entry.get("task_id", "")
        if last_task_id and last_task_id != task_id:
            entry = {"count": 0, "task_id": task_id}
        # TOTAL COERCION. A hand-edited or corrupted counter file can carry a
        # non-numeric `count`, and a bare int() raises ValueError from inside
        # the atomic update. Treating an unreadable count as 0 restarts the
        # threshold rather than crashing the tick — the advisory fires later
        # than it might have, which is the safe direction for an alarm.
        try:
            current = int(entry.get("count", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        # Emit once at N == threshold; later same-task ticks must not re-emit.
        if last_task_id == task_id and current >= UNFLAGGED_IDLE_THRESHOLD:
            return counts
        entry["count"] = current + 1
        entry["task_id"] = task_id
        counts[teammate_name] = entry
        result["emit"] = entry["count"] == UNFLAGGED_IDLE_THRESHOLD
        return counts

    update_unflagged_idle_counts(_bump, team_name)
    return UNFLAGGED_ADVISORY if result["emit"] else None


UNREAD_KEYS_SHOWN = 10     # the keys one re-prompt prints; the rest are counted
UNREAD_KEYS_PREAMBLE = (
    "Do not idle yet. Your task file holds keys from the lead that were never handed to "
    "you; a message about them may not have reached you. Act on each one now:"
)


def unread_lead_keys(
    tasks: list, teammate_name: str, team_name: str, session_dir: str
) -> str | None:
    """The re-prompt for the teammate, or None.

    A key is unread when the teammate owns the task, has it in_progress, the relay stamp
    (amendment_relay.py) does not list the key and the stored HANDOFF does not dispose of
    it. The keys are written to the stamp before the text is returned, so each is handed
    over once by this hook or by the relay, never by both; a stamp that cannot be written
    raises OSError and the caller emits nothing.
    """
    import amendment_relay as relay_hook
    from shared import amendments as rules

    if not session_dir or not os.path.isdir(session_dir):
        return None
    if not relay_hook._path_part(teammate_name) or not relay_hook._path_part(team_name):
        return None
    stamp_path = os.path.join(
        session_dir, relay_hook.STAMP_DIRNAME, f"{team_name}--{teammate_name}.json")
    stamp = relay_hook.read_stamp(stamp_path)
    relayed = stamp.get("relayed") if isinstance(stamp.get("relayed"), dict) else {}
    lines: list[str] = []
    for task in tasks:
        if task.get("owner") != teammate_name or task.get("status") != "in_progress":
            continue
        metadata = task.get("metadata")
        task_id = "".join(ch for ch in str(task.get("id") or "") if ch.isalnum() or ch in "_-")
        if not isinstance(metadata, dict) or not task_id:
            continue
        listed = relayed.get(task_id)
        already = {key for key in listed if isinstance(key, str)} if isinstance(listed, list) else set()
        handoff = metadata.get("handoff")
        disposed = handoff.get("amendments") if isinstance(handoff, dict) else None
        if isinstance(disposed, dict):
            already |= {key for key in disposed if isinstance(key, str)}
        new_lines, seen = rules.unread_lines(metadata, task_id, already)
        if not new_lines:
            continue
        lines.extend(new_lines)
        relayed[task_id] = sorted(seen)
    if not lines:
        return None
    stamp["relayed"] = relayed
    relay_hook.write_stamp(stamp_path, stamp)
    if len(lines) > UNREAD_KEYS_SHOWN:
        lines = lines[:UNREAD_KEYS_SHOWN] + [
            f"{len(lines) - UNREAD_KEYS_SHOWN} more keys are not shown: read your task file."]
    return "\n".join([UNREAD_KEYS_PREAMBLE] + lines)


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        team_name, _name = frame_team_and_name(input_data)
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        teammate_name = input_data.get("teammate_name", "")
        if not teammate_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tasks = list(iter_team_task_jsons(team_name))
        if not tasks:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        messages = []
        session_dir = pact_context.get_session_dir()
        # The unread-key re-prompt. Its own try/except: an error, or a stamp that
        # cannot record the keys, re-prompts nobody.
        reprompt = None
        try:
            reprompt = unread_lead_keys(tasks, teammate_name, team_name, session_dir)
        except Exception:
            reprompt = None
        # The missed-wake line, on screen: the user can prompt an idle lead. Not taken on
        # a re-prompt, whose exit 2 shows no systemMessage: the line keeps for the next idle.
        try:
            wake_line = None if reprompt else missed_wake_line(tasks, session_dir)
            if wake_line:
                messages.append(wake_line)
        except Exception:
            pass
        # Own try/except: a failed advisory leaves the idle silent.
        try:
            unflagged_msg = check_unflagged_background(
                tasks, teammate_name, team_name
            )
            if unflagged_msg:
                messages.append(unflagged_msg)
        except Exception:
            pass

        if reprompt:
            # Exit 2: the host hands stderr to the teammate, which keeps working.
            print(reprompt, file=sys.stderr)
            sys.exit(2)

        if messages:
            output = {"systemMessage": IDLE_PREAMBLE + " | ".join(messages)}
            print(json.dumps(output))
        else:
            print(_SUPPRESS_OUTPUT)

        sys.exit(0)

    except Exception as e:
        # Don't block on errors — just warn and exit cleanly
        print(f"Hook warning (teammate_idle): {e}", file=sys.stderr)
        print(hook_error_json("teammate_idle", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
