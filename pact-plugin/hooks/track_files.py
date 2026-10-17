#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/track_files.py
Summary: PostToolUse hook. It records the files that an Edit or a Write
         modified, and a teammate's background Bash launch.
Used by: pact-plugin/hooks/hooks.json, PostToolUse, matcher `Edit|Write|Bash`.
         The registration lives in that file and not in settings.json.

Extracts file paths from Edit and Write tool usage and records them
for the memory system's graph network. The `Bash` leg serves the background
launch record.

Input: JSON from stdin with tool_name, tool_input, tool_response
Output: None (writes to tracking file for later memory association)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_session_id
from shared.paths import get_claude_config_dir
from shared import state_file

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Directory for tracking data. Accessor (B1) — resolves $CLAUDE_CONFIG_DIR at
# CALL time via the shared resolver, so a non-default config dir is honored and
# the import-time freeze (a #924-class inert trap) is avoided.
def get_tracking_dir() -> Path:
    return get_claude_config_dir() / "pact-memory" / "session-tracking"


def ensure_tracking_dir():
    """Ensure the tracking directory exists."""
    get_tracking_dir().mkdir(parents=True, exist_ok=True)


def get_session_tracking_file() -> Path:
    """Get the tracking file for the current session.

    Requires pact_context.init() to have been called so get_session_id()
    reads from the correct session-scoped context file.
    """
    session_id = get_session_id() or "unknown"
    return get_tracking_dir() / f"{session_id}.json"


def _tracking_root(tracking_file: Path) -> Path:
    """The directory the session tracking file must stay under: its own.

    The file sits directly in the tracking directory, so in production this IS
    `get_tracking_dir()`, and containment for a file directly in its root
    compares that directory with itself.
    """
    return tracking_file.parent


def load_tracked_files() -> dict:
    """Load existing tracked files for this session.

    Reads without a lock: writers swap in a complete file, so a reader sees
    the whole previous file or the whole new one.
    """
    default = {"files": [], "session_id": get_session_id() or "unknown"}
    tracking_file = get_session_tracking_file()
    try:
        content = state_file.read_text(tracking_file, _tracking_root(tracking_file))
    except OSError:
        return default
    try:
        return json.loads(content) if content.strip() else default
    except json.JSONDecodeError:
        return default


def save_tracked_files(data: dict):
    """Save tracked files for this session.

    Replaces the file under its state-file lock: the new content is written to
    a temp file and swapped in, so a failure part-way leaves the previous file
    intact.
    """
    tracking_file = get_session_tracking_file()
    try:
        state_file.write_text(
            tracking_file, json.dumps(data, indent=2), _tracking_root(tracking_file)
        )
    except OSError as e:
        print(f"Warning: Could not save tracking data: {e}", file=sys.stderr)


def extract_file_path(tool_input: dict) -> str:
    """Extract file path from tool input."""
    # Both Edit and Write use file_path parameter
    return tool_input.get("file_path", "")


def _update_data(data: dict, file_path: str, tool_name: str) -> dict:
    """Update tracking data with a file entry (pure function, no I/O).

    Separated from I/O so that the read-modify-write cycle can be done
    under a single lock.
    """
    existing_paths = [f["path"] for f in data["files"]]
    if file_path in existing_paths:
        for f in data["files"]:
            if f["path"] == file_path:
                f["last_modified"] = datetime.now(timezone.utc).isoformat()
                f["tool"] = tool_name
                break
    else:
        data["files"].append({
            "path": file_path,
            "tool": tool_name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_modified": datetime.now(timezone.utc).isoformat(),
        })
    return data


def track_file(file_path: str, tool_name: str):
    """Add a file to the tracking list.

    Uses a single exclusive lock for the entire read-modify-write cycle
    to prevent TOCTOU race conditions between concurrent hook invocations.
    """
    if not file_path:
        return

    tracking_file = get_session_tracking_file()
    session_id = get_session_id() or "unknown"

    def _apply(content: str):
        try:
            data = json.loads(content) if content.strip() else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            data = {"files": [], "session_id": session_id}
        data = _update_data(data, file_path, tool_name)
        return json.dumps(data, indent=2), True, None

    try:
        state_file.locked_update(tracking_file, _apply, _tracking_root(tracking_file))
    except OSError as e:
        print(f"Warning: Could not track file: {e}", file=sys.stderr)


def main():
    """Main entry point for the PostToolUse hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # ANOTHER JOB: record a teammate's background Bash launch — a frame
        # carrying the harness `run_in_background` flag, or a command ending
        # in a bare `&`. All of the logic lives in shared.background_work;
        # this host holds only the gate and the call. Its own try/except, so a
        # fault there cannot take down the other jobs in this hook — file
        # tracking is this hook's contract and must survive anything that
        # happens in the registry write.
        #
        # The `Bash` test gates the IMPORT as well as the call, so Edit and
        # Write calls never load the module. Every Bash call does load it,
        # background or not; the launch test runs inside the call.
        if tool_name == "Bash":
            try:
                from shared.background_work import record_background_launch

                record_background_launch(input_data)
            except Exception:
                pass

        # Only track Edit and Write tools
        if tool_name not in ("Edit", "Write"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Extract and track the file path
        file_path = extract_file_path(tool_input)
        if file_path:
            track_file(file_path, tool_name)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Don't block on errors
        print(f"Hook warning (track_files): {e}", file=sys.stderr)
        print(hook_error_json("track_files", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
