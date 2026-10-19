"""
Location: pact-plugin/hooks/shared/ops/brief.py
Summary: The coder's brief and its two skeleton files, written by
`repact dispatch`: `<session dir>/briefs/<id>.md` (everything the coder needs, so it never hunts
for a reference), and the drafts `<id>.teachback.json` and `<id>.handoff.json` (every key the
submit checks, as placeholders the coder replaces). The drafts are the one thing the coder writes,
so they live in the per-user state dir, ${XDG_STATE_HOME:-~/.local/state}/repacted/drafts/<session>/:
never under <config>/pact-sessions/, which settings.task_state keeps from every direct write, and
never in a worktree, where they would be committed. The brief's `repact` lines
and its tcount name this plugin's own copies, so they never resolve on PATH to another install.
Used by: shared/ops/dispatch.py (write), shared/ops/submit.py (PLACEHOLDER test), shared/ops/receipt.py
    and bin/_repact_ops.py (paths), hooks/subagent_stop.py and hooks/dispatch_gate.py (BRIEF_PROMPT).

The HANDOFF skeleton and the brief's HANDOFF guide are rendered from
hooks/shared/protocol_descriptor.py into the marked region below (edit the descriptor, then run
`python3 dev/protocol/render.py --write`; never edit inside the region), so the brief
says exactly what submit checks. The brief is under 2 KB plus the lead's mission. A placeholder
is a whole string that opens with "<" and closes with ">": submit refuses a HANDOFF or teachback
that still holds one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from shared import plugin_paths
from shared.teachback_schema import (
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_SUBKEYS,
)

from .evidence import brief_line

BRIEFS = "briefs"
DRAFTS = ("repacted", "drafts")     # under the user's state dir; one folder per session
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# The Agent prompt dispatch prints; subagent_stop finds the task by it, dispatch_gate rule (9)
# exempts it (the mission is in the brief file).
BRIEF_PROMPT = re.compile(r"Read (?P<path>.+/briefs/(?P<id>[A-Za-z0-9_-]{1,64})\.md) and do what it says\.")
PROFILE = Path(".repacted", "profile").with_suffix(".md")   # written by bin/repact-profile
PROFILE_MAX_LINES = 12
PROFILE_MAX_CHARS = 900


# <!-- protocol:brief-handoff -->
HANDOFF_SKELETON = {
    "produced": ["<absolute path in your worktree>: <what changed>"],
    "decisions": [],
    "reasoning_chain": "",
    "uncertainty": [],
    "integration": "<what the lead or the next unit must know to use this>",
    "open_questions": [],
    "amendments": {},
}

HANDOFF_GUIDE = (
    "- produced: one item per file, its path first (made absolute; it must be in your worktree).",
    "- integration: required: what the lead or the next unit must know to use this.",
    "- uncertainty: each item opens with fixed:, known-limit: or not-applicable:.",
    "- amendments: one entry per amend_<n> on the task (applied:, declined:, disputed: or ack).",
    "- refs: one entry per listed ref or AC (done:, partial:, deferred:, dropped:).",
    "- Replace every <...> placeholder. Do not set rev: the command assigns it.",
)
# <!-- /protocol -->


def is_placeholder(value) -> bool:
    return isinstance(value, str) and value.startswith("<") and value.endswith(">")


def drafts_dir(session_dir: Path) -> Path:
    """The session's draft folder: ${XDG_STATE_HOME:-~/.local/state}/repacted/drafts/<session id>,
    keyed by the session dir's name (a hash of its path when the name is not a plain id), so the
    lead's dispatch and the coder's submit agree whatever session the coder runs in."""
    state = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(state):
        state = str(Path.home() / ".local" / "state")
    name = Path(session_dir).name
    if not SAFE_NAME.fullmatch(name):
        name = hashlib.sha256(str(session_dir).encode("utf-8", "replace")).hexdigest()[:16]
    return Path(state).joinpath(*DRAFTS, name)


def paths(session_dir: Path, task_id: str) -> dict:
    drafts = drafts_dir(session_dir)
    return {"brief": Path(session_dir) / BRIEFS / f"{task_id}.md",
            "teachback": drafts / f"{task_id}.teachback.json", "handoff": drafts / f"{task_id}.handoff.json"}


def launcher() -> str:
    """This plugin's `repact`, quoted for the shell: never a PATH lookup."""
    return shlex.quote(str(plugin_paths.plugin_root() / "bin" / "repact"))


def tcount_command() -> str:
    """This plugin's `tcount`, quoted for the shell: its runs go to the ledger accept reads."""
    return shlex.quote(str(plugin_paths.plugin_root() / "bin" / "tcount"))


def submit_command(task_id: str, name: str, team: str, session_dir: Path) -> str:
    return (f"{launcher()} submit {task_id} --as {shlex.quote(name)} --team {team} "
            f"--session-dir {shlex.quote(str(session_dir))}")


def teachback_skeleton(total: int) -> dict:
    skeleton = {
        "understanding": "<the brief in your own words: what you build, constraints, interfaces>",
        "most_likely_wrong": "<the part of your plan most likely to be wrong>",
        "least_confident_item": "<one assumption for the lead to confirm>",
        "first_action": "<your first concrete step>",
        "variety_acknowledgment": {"rationale_articulates_this_dispatch": "<yes|no|concern>",
                                   "concern": "<empty when yes, else why>"},
    }
    if total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN:
        skeleton["reasoning_reconstruction"] = {key: f"<{key.replace('_', ' ')}>"
                                                for key in TEACHBACK_REQUIRED_SUBKEYS}
    return skeleton


def handoff_skeleton(carried: list) -> dict:
    skeleton = copy.deepcopy(HANDOFF_SKELETON)
    skeleton["refs"] = {ref: "<done: evidence | partial: what is left | deferred: why | dropped: why>"
                        for ref in carried}
    return skeleton


def _profile(repo_root: str) -> list:
    try:
        text = (Path(repo_root) / PROFILE).read_text(encoding="utf-8")[:PROFILE_MAX_CHARS]
    except (OSError, ValueError):
        return []
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return lines[:PROFILE_MAX_LINES]


def render(task_id: str, dispatch: dict, mission: str, team: str, session_dir: Path,
           repo_root: str) -> str:
    where = paths(session_dir, task_id)
    submit = submit_command(task_id, dispatch["name"], team, session_dir)
    acs = dispatch.get("acceptance_pin") or {}
    lines = [f"# Task #{task_id}: brief for {dispatch['name']}", "", "## Mission", mission.strip(), ""]
    profile = _profile(repo_root)
    if profile:
        lines += ["## How this repo works", *profile, ""]
    lines += ["## Where", f"Work only in {dispatch['worktree']} (branch {dispatch['branch']}).", ""]
    if acs:
        lines += ["## Acceptance criteria (pinned commands)",
                  *[f"- {ac}: `{command}`" for ac, command in acs.items()], ""]
    lines += ["## Tests", brief_line(tcount_command()), "", "## Steps",
              f"1. Teachback: fill {where['teachback']}, then run `{submit} --teachback "
              f"{shlex.quote(str(where['teachback']))}` and do what it prints"
              + (" (it stops you until the lead accepts)." if dispatch["teachback"] == "two_phase"
                 else " (accepted automatically when your answer is yes)."),
              "2. Do the work.",
              f"3. Hand in: fill {where['handoff']}, then run `{submit}`. It repairs what it can, "
              "lists every problem in one message, and says what accept will check. Fix and rerun.",
              "", "## HANDOFF", *HANDOFF_GUIDE]
    return "\n".join(lines) + "\n"


def write(task_id: str, dispatch: dict, mission: str, team: str, session_dir: Path,
          repo_root: str) -> Path:
    """Write the brief and both skeletons (overwriting: a rerun writes the same bytes)."""
    where = paths(session_dir, task_id)
    where["brief"].parent.mkdir(parents=True, exist_ok=True)
    where["teachback"].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    total = int((dispatch.get("variety") or {}).get("total") or 0)
    carried = list(dispatch.get("refs") or []) + list((dispatch.get("acceptance_pin") or {}).keys())
    where["brief"].write_text(render(task_id, dispatch, mission, team, session_dir, repo_root),
                              encoding="utf-8")
    where["teachback"].write_text(json.dumps(teachback_skeleton(total), indent=2) + "\n", encoding="utf-8")
    where["handoff"].write_text(json.dumps(handoff_skeleton(carried), indent=2) + "\n", encoding="utf-8")
    return where["brief"]
