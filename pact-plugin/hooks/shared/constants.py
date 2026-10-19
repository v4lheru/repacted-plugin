"""
Location: pact-plugin/hooks/shared/constants.py
Summary: Canonical constants shared across PACT hooks and tests.
Used by: postcompact_archive.py (get_compact_summary_path),
         session_init.py (get_compact_summary_path).
"""

from __future__ import annotations

from pathlib import Path

from .paths import get_claude_config_dir

# Canonical list of all PACT specialist agents in lifecycle order.
# This is the single source of truth for agent enumeration.
# Keep in sync with: CLAUDE.md agent roster, task_utils.py agent_prefixes,
# refresh/patterns.py PACT_AGENT_PATTERN.
PACT_AGENTS = [
    "pact-preparer",
    "pact-architect",
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-test-engineer",
    "pact-security-engineer",
    "pact-qa-engineer",
    "pact-auditor",
    "pact-secretary",
]

# Filename of the compact summary. ONE spelling, shared by the session-scoped
# location ({session_dir}/compact-summary.txt, resolved by
# pact_context.resolve_compact_summary_path) and the root singleton below, so
# neither leg can drift from the name the secretary states in prose.
COMPACT_SUMMARY_NAME = "compact-summary.txt"


# Canonical ROOT path for the compact summary. After #1504 this is the
# DEGRADATION + LEGACY-DRAIN destination, not the primary write target:
# postcompact_archive writes the session-scoped path whenever the frame is
# identifiable and degrades HERE when it is not (missing session_id or
# CLAUDE_PROJECT_DIR), and pre-upgrade bytes start here. session_init drains
# whatever it finds at this path on every non-compact SessionStart.
#
# SINGLE-USE, AND ARCHIVED RATHER THAN DELETED. The file must LEAVE this path
# once processed, because a copy left here is processed again by the next
# briefing in the same session. It does NOT follow that the bytes may go:
# whoever clears it destroys the only copy unless the clear is a MOVE. Both
# clearers move it — the secretary, when its session-scoped read falls back to
# this path, into its session directory; and session_init, into the clearing
# session's directory when it can identify one, else into the orphan slot
# below.
#
# Also referenced in: pact-plugin/agents/pact-secretary.md (the named fallback
# read) and pact-plugin/hooks/session_init.py (the stale-summary archive, and
# the resume-time pointer's degraded branch).
# Accessor (B1) — resolves $CLAUDE_CONFIG_DIR at CALL time (no import-time freeze).
def get_compact_summary_path() -> Path:
    return get_claude_config_dir() / "pact-sessions" / COMPACT_SUMMARY_NAME


# Filename prefix for an ARCHIVED compact summary. One definition, so the hook
# that writes an archive cannot drift from the convention the secretary states
# in prose.
COMPACT_SUMMARY_ARCHIVE_PREFIX = "compact-summary-"

# Fixed-name slot for a stale summary that cannot be attributed to a session.
# A FIXED name, never a timestamped one: this lives in the shared sessions root,
# where timestamped files would accumulate without bound. One slot makes that
# growth UNREPRESENTABLE rather than merely policed.
#
# ROLE (narrowed by #1504): the catch basin of the ROOT drain — the destination
# when the CLEARING session is itself unidentified. Since the writer now scopes
# its file to the session that produced it, that means degraded writes and
# pre-upgrade legacy bytes only.
#
# THE TRADE, so nobody discovers it by losing something: a second orphan
# OVERWRITES the first. That is a strict improvement on the previous behaviour,
# which lost the summary every time; and the newer copy is the more likely to be
# wanted, since an older orphan is from a session nobody came back for.
COMPACT_SUMMARY_ORPHAN_NAME = "compact-summary.orphan.txt"

# The capped read beside compact-summary.txt (summary_head.py): pointers rebuilt
# from disk, then the summary cut at a line boundary. The lead's post-compaction
# directive names this file, not the 22-44 KB summary. Its name holds
# "compact-summary", so bootstrap_gate settles staged summaries before a read of
# it, and it matches neither the archive prefix nor any staging prefix.
COMPACT_SUMMARY_HEAD_NAME = "compact-summary.head.txt"
COMPACT_SUMMARY_HEAD_MAX_BYTES = 8192

# In the compact directive (session_init) it follows the role marker line and
# precedes the bootstrap instruction, so a teammate reads what it is before it
# reads the instruction the clause tells it to set aside. In the missed-wake
# surface on a compaction (missed_wake_scan) it leads. An in-process teammate's
# compaction arrives lead-shaped and receives both, and its system prompt
# survives the compaction, so the sentence keys on that. It lives here rather
# than in compaction_owner so that a broken settle module cannot disable the scan.
COMPACTION_TEAMMATE_CLAUSE = (
    "If your system prompt makes you a teammate who reports to a team lead, "
    "this message is not for you: ignore it and continue your task."
)


# Subject prefixes that indicate synthetic / system-level tasks (phase
# markers and algedonic signal tasks) as opposed to real feature work.
# Used by session_state._derive_feature_from_journal and
# _read_feature_subject_from_disk to reject system tasks from the
# feature-subject derivation path.
#
# NOTE: This is distinct from `phase_prefixes` in task_utils.py
# (`PREPARE:`, `ARCHITECT:`, `CODE:`, `TEST:`, `Review:`) — those are
# phase-marker-task-subject prefixes, a narrower set used by
# find_feature_task / find_current_phase. The two tuples have
# different semantics and should not be unified.
SYSTEM_TASK_PREFIXES = ("Phase:", "BLOCKER:", "ALERT:", "HALT:")


# Bootstrap gate marker: the session-scoped file whose presence signals that
# Skill("repacted:bootstrap") has been invoked and the tool gate can self-disable.
# Used by bootstrap_gate.py, bootstrap_prompt_gate.py, bootstrap_marker_writer.py and
# session_init_resume.py. Also referenced (as a string literal) in commands/bootstrap.md.
BOOTSTRAP_MARKER_NAME = "bootstrap-complete"


# Top-level event-field value marking a `variety_assessed` journal event as a
# PER-DISPATCH mirror (written by the dispatcher's command prose at the Task-B
# stamp site) rather than a feature-level assessment. ABSENT means
# feature-level, so every legacy event is feature-level by construction and
# the position-based consumers (session_state._derive_feature_from_journal /
# _derive_variety_from_journal latest-ts feature-level selection, and the
# wrap-up retrospective's arc start) exclude this value without any
# migration. Readers compare the TOP-LEVEL event field; the variety DICT
# nested one level down carries a dimension also named "scope" (one of the
# four variety dimensions), and the two never interact.
VARIETY_ASSESSED_DISPATCH_SCOPE = "dispatch"
