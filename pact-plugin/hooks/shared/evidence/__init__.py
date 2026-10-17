"""Shared HANDOFF run-evidence and write-time check logic (stdlib only).

Location: pact-plugin/hooks/shared/evidence/__init__.py (work unit unit 11; moved from
bin/_evidence.py in the refactor layering pass). A facade: the code lives in five sibling
modules and this one re-exports every name they define, so callers keep
`from shared import evidence`. A test that patches a name patches it on the module that looks
it up.
  claims  what an item claims: count and test-run wording, gated fields, per-claim checks
  ledger  check modes, task files and team resolution, the run ledger, the check state
  files   cited result files and runner logs, files edited after a run
  notes   note, flag and acceptance text
  check   item_evidence, evaluate_handoff and handoff_gate
Used by: bin/handoff-evidence (the manual CLI), hooks/write_checks.py (PreToolUse
TaskUpdate|Bash), hooks/evidence_gate.py (PostToolUse TaskUpdate, the stored line),
hooks/amendment_gate.py and hooks/acceptance_flags.py (PreToolUse TaskUpdate). One module, so
the CLI replays exactly what the hooks decide. No check here asks a model.

Order of the check, per item:
  1. Stop check: mode off, no gated field, no item -> no verdict.
     Gated fields = EVERY string or list field of metadata.handoff except the named
     exclusions (open_questions, uncertainty: speculative by construction). The canonical
     fields go first, reasoning_chain last. (TEST finding T-GATE, task #77.)
  2. Run facts: counts claimed in the item are matched against the run
     ledger (unit 0 schema, read via read_runs: a line it cannot read is skipped, and the verdict
     says how many under ledger_skipped). The runs that count are this
     task's: the runs the item cites (a log path or run id, item_evidence) and its runs in
     scope, never another agent's or task's (run_standing):
       MATCH      a cited or in-scope run has the claimed counts
       MISMATCH   runs are recorded, none of the cited or in-scope ones has the claimed counts
                  (an in-scope run counts while no later run of its command replaced it), or
                  every cited result file was read and none has them
       NO-FILE    the item cites an absolute result-file path that does not exist
       UNDECIDED  no run has the counts and the item cites something the check cannot read (a
                  directory inside the scratchpad, a log there with no count, a file over the cap):
                  a note for the lead, never returned; a count only an older run has stays a
                  MISMATCH whatever the item cites. Also a count no run has that is reported
                  as red by design, as an earlier count in front of a final one the latest
                  run has, or as larger than a clean package-script run (item_evidence)
       EXIT       a clean claim (nothing failed) whose counts only runs that exited non-zero
                  have, or only cited result files that hold failures or errors
       NO-RUN     the ledger exists but holds no run in scope, or a cited run is not in it,
                  or its only counted runs ran in another tree (a scratch copy)
       NO-LEDGER  there is no ledger at all: said as such, never a flag by itself
       NO-COUNT   the item reports a test run but states no parseable count (the D7 trap:
                  "rtk pytest ... exited 0")
       n/a        the item claims no count and reports no test run
  3. Flag: a parsed count that is MISMATCH, EXIT, UNREADABLE, NO-FILE or NO-RUN, or a cited run
     the ledger lacks. A flag is a note, never a return (handoff_gate).

Check log events (checks/shadow.jsonl under the plugin data dir): kind write_gate (the
write-time checks' returns and notes) and amendment_gate (hooks/amendment_gate.py).
"""
from __future__ import annotations

import os  # noqa: F401 - callers reach os through this module

from shared.clock import utc_now_iso  # noqa: F401
from shared.ledger import parse_ledger_lines  # noqa: F401
from shared.plugin_paths import checks_dir, claude_config_dir, ledger_path, task_store  # noqa: F401
from shared.scrub import scrub_text  # noqa: F401
from shared.evidence.claims import (  # noqa: F401
    BARE_RATIO, CLAUSE_SPLIT, COUNTABLE, COUNT_PATTERNS, DEFAULT_EXCLUDED_FIELDS,
    DEFAULT_PRIORITY_FIELDS, DISPOSITION, DISPOSITIONS, ENUMERATED, EXIT_BEFORE, EXIT_CLAUSE_SPLIT,
    EXIT_CODE, FOREIGN_DIGITS, GATE_WORDING, ITEM_MIN_CHARS, KEYED_CHAIN, KEYED_PATTERNS,
    LABEL_END, LAST_FIELDS, NAMED_TOOL, NON_TEST_TOOL, NO_UNCERTAINTY, NUM, OPENS, RUN_WORDING,
    SENTENCE_END, SEVERITY, TEST_CONTEXT, TEST_RUN, TOOL_WORD, UNREADABLE_COUNT, VERIFY_WORDS,
    _COUNT_KEY, _COUNT_SHAPES, _FOREIGN, _NAMED_TOOL, _NOT_A_KEY, _NOT_A_SPACED_KEY, _SENTENCE,
    _TOOL_CHECK, _TOOL_WORD, _WORDS, _clause_counts, _count, _flatten, _has_counts, _nonzero,
    _runner_label, check_claim, claim_key, claim_readings, claimed_counts, counts_sig, every_count,
    exit_only_clause, failed_exits, field_policy, flag_item, gated_fields, handoff_items,
    reports_test_run, run_check, uncertainty_items, unparsed_only, unreadable_count,
    without_exit_codes,
)
from shared.evidence.ledger import (  # noqa: F401
    CHECKS_FILE, COUNT_KEYS, DEFAULT_MODES, FALLBACK_WINDOW_SECONDS, MODES, PATH_SEGMENT, RUN_PIDS, RUN_STEM,
    SPEAKING_MODES, _after_anchor, _command_key, _floor, _inside, _record_time, _run_end, _run_key,
    _same_dir, _session_row, _tree_key, _write_gate_state, cited_runs, cited_total, claim_time,
    claimant_session, gate_state_path, lead_session_id, linked_worktree, load_constants, load_task,
    newest_runs, own_worktrees, project_root, read_gate_state, read_runs, recorded_elsewhere,
    recorded_run_ids, resolve_team, return_once, run_standing, runs_in_scope, session_journal,
    shadow_log, superseded, task_from_journal, task_path, team_from_session, use_mode, written_at,
)
from shared.evidence.files import (  # noqa: F401
    AMBIGUOUS, CHANGED_BYTES_MAX, CHANGED_FILES_MAX, COUNTERS, COUNTERS_BODY, COUNTERS_TAG_MAX,
    COUNTER_ATTR, COUNTER_KEYS, EDIT_SLACK_SECONDS, GIT_STATUS_SECONDS, LOG_FILE_MAX,
    NEAR_DIRS_MAX, NOT_A_SOURCE, NO_AGE_LIMIT, NO_LOG_COUNT, NO_SCRATCHPAD, OVER_CAP, PATH_TOKEN,
    PATH_TOKENS_MAX, PLAIN_COUNT, RED_FILE, RESULT_FILES_MAX, RESULT_FILE_MAX, RESULT_FILE_MAX_AGE,
    ResultFiles, SESSION_ID, TASK_LOG, TOO_OLD, _cannot_change_a_test, _clock, _printable, _rooted,
    changed_files, edited_after_run, git_root, over_cap, parse_counters, read_counters,
    read_log_counts, read_result_bytes, result_files, session_dirs, session_scratchpads,
)
from shared.evidence.notes import (  # noqa: F401
    CHECKS_TAIL, DEFAULT_REASON, HANDOFF_ADVISORY, HANDOFF_USES, MAX_FLAG_LINES, NOTE_TEXT_CHARS,
    REASONS, UNPARSED_REASON, UNPARSED_SHOWN, _cap, _exited, _flag, _mismatch, _named, _on_disk,
    _standing, _undecided, _unread, acceptance_message, evidence_note, flag_message,
    gate_note_lines, handoff_flag_lines, set_gate_notes, uncertainty_gate, uncertainty_note,
    unparsed_line,
)
from shared.evidence.check import (  # noqa: F401
    EXCERPT_CHARS, NOT_RUN, evaluate_handoff, handoff_gate, item_evidence,
)
