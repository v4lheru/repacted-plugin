# pact-plugin/tests/test_hooks_json.py
"""
Tests for hooks.json structural validation.

Tests cover:
1. Valid JSON structure
2. All hook types are recognized Claude Code hook events
3. Async flags only on non-critical hooks
4. All referenced Python scripts exist on disk
5. TeammateIdle hook entry exists (new in SDK optimization)
6. SessionEnd is async (new in SDK optimization)
7. Matcher patterns use valid pipe syntax
8. SubagentStart registration selects every spawnable PACT agent type
"""
import json
from pathlib import Path

import pytest
from helpers import effective_hooks_config  # noqa: E402

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"

# Valid Claude Code hook event types
VALID_HOOK_EVENTS = {
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "PreToolUse",
    "PostToolUse",
    # Fires instead of PostToolUse when a tool call fails (a Bash command's
    # non-zero exit); the run ledger needs it so a failing test run is recorded.
    "PostToolUseFailure",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "TaskCompleted",
    "TeammateIdle",
}

# Hooks that MUST be synchronous (blocking) — they affect tool decisions
MUST_BE_SYNC = {
    # (#979: team_guard.py removed from the Agent PreToolUse bind — the
    # create-before-dispatch model is obsolete; dispatch_gate ⑧ is the
    # residual fail-closed backstop.)
    "worktree_guard.py",  # Blocks edits outside worktree
    "stop_background_gate.py",  # Refuses a turn end over unacknowledged background work
    "git_commit_check.py",  # Checks git commit conventions
    "wait_filler_gate.py",  # Denies bare true/sleep filler commands
    "track_files.py",     # Tracks file edits (PostToolUse, non-async)
    "precompact_state_reminder.py",  # Emits state snapshot before compaction
    "postcompact_archive.py",  # Archives compact_summary to disk for session_init + secretary
    # The user-layer hooks emit additionalContext, which an async hook's
    # discarded stdout would silently lose.
    "run_ledger.py",  # Records test-run counts; nudges toward tcount when none parse
    # EVENTS (m8a) and the phase D hooks it registers. A deny, and additionalContext,
    # are lost in an async hook; the journal emitter is sync so its event lands before
    # the next tool call, which the repeat suppression against a later block relies on.
    "process_table_guard.py",  # Denies process listings that print command lines
    "env_guard.py",  # Keeps .env-class file contents out of Read, Grep and Bash
    "egress_guard.py",  # Denies a credential in MCP, WebFetch, Artifact or network-client input
    "write_checks.py",  # Returns a rule-breaking commit
    "amendment_relay.py",  # Relays new amend_<n> keys to a busy teammate
    "output_secret_scan.py",  # Replaces a secret value in Bash output before the model sees it
}

# Hooks that SHOULD be async (non-blocking, fire-and-forget)
SHOULD_BE_ASYNC = {
    "session_end.py",     # Fire-and-forget cleanup
    "file_tracker.py",    # Advisory tracking only
}


@pytest.fixture
def hooks_config():
    """Load and parse hooks.json."""
    return effective_hooks_config()


class TestHooksJsonStructure:
    """Validate hooks.json is well-formed."""

    def test_valid_json(self):
        """hooks.json must parse as valid JSON."""
        content = HOOKS_JSON.read_text(encoding="utf-8")
        config = json.loads(content)
        assert "hooks" in config


class TestReferencedScriptsExist:
    """Verify all Python scripts referenced in hooks.json exist."""

    def test_all_python_scripts_exist(self, hooks_config):
        """Every python3 command should reference an existing .py file."""
        missing = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "python3" in cmd and ".py" in cmd:
                        # Extract filename from command like:
                        # python3 "${CLAUDE_PLUGIN_ROOT}/hooks/teammate_idle.py"
                        parts = cmd.split("/hooks/")
                        if len(parts) == 2:
                            script_name = parts[1].split(".py", 1)[0] + ".py"  # drop argv
                            script_path = HOOKS_DIR / script_name
                            if not script_path.exists():
                                missing.append(f"{event_type}: {script_name}")

        assert missing == [], f"Referenced scripts not found: {missing}"


class TestAsyncFlags:
    """Verify async flags are correctly set on hooks."""

    def _get_hook_async_status(self, hooks_config) -> dict:
        """Map script_name -> [(event_type, async flag), ...], one pair per registration.

        A script registered under several events keeps every registration, so a
        flag dropped from any one of them is visible (a last-registration-wins map
        hid the second registration of a two-event hook).
        """
        result = {}
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "/hooks/" in cmd:
                        parts = cmd.split("/hooks/")
                        if len(parts) == 2:
                            script_name = parts[1].strip('"').strip("'")
                            is_async = hook.get("async", False)
                            result.setdefault(script_name, []).append((event_type, is_async))
        return result

    def test_critical_hooks_are_synchronous(self, hooks_config):
        """Hooks that affect tool decisions MUST be synchronous, in every registration."""
        status = self._get_hook_async_status(hooks_config)
        for script in MUST_BE_SYNC:
            for event_type, is_async in status.get(script, []):
                assert is_async is not True, (
                    f"{script} ({event_type}) must be synchronous (no async:true) — "
                    "it affects tool decisions"
                )


class TestUserLayerHookRegistrations:
    """The user layer's five entries (repact-user-layer.md section 6.2), merged
    into the existing event arrays by the UL-integrate milestone, and the
    task-file backup added after it."""

    EXPECTED = [
        ("PostToolUse", "Bash", "run_ledger.py"),
        ("PostToolUseFailure", "Bash", "run_ledger.py"),
    ]

    @staticmethod
    def _registrations(hooks_config, script):
        return [
            (event, entry.get("matcher"))
            for event, entries in hooks_config["hooks"].items()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("command", "").endswith(f'/hooks/{script}"')
        ]

    @pytest.mark.parametrize("event, matcher, script", list(list(EXPECTED)[:1])[:1],
                             ids=list([f"{e}-{s}" for e, _, s in EXPECTED])[:1])
    def test_each_entry_is_registered_exactly_once(self, hooks_config, event, matcher, script):
        found = self._registrations(hooks_config, script)
        assert found.count((event, matcher)) == 1, (
            f"{script} must be registered once under {event} with matcher "
            f"{matcher!r}; found {found}"
        )


class TestEnvGuardRegistration:
    """ENV-GUARD (task 211): one synchronous PreToolUse entry covering every tool that can
    read a file's contents into the transcript. A deny and a Grep rewrite are both lost
    in an async hook."""

    def test_registered_once_for_the_four_reading_tools_and_synchronous(self, hooks_config):
        found = [
            (event, entry.get("matcher"), hook.get("async", False))
            for event, entries in hooks_config["hooks"].items()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("command", "").endswith('/hooks/env_guard.py"')
        ]
        assert found == [("PreToolUse", "Read|NotebookRead|Grep|Bash", False)]


class TestEgressGuardRegistration:
    """the egress guard (task 239): one synchronous PreToolUse entry covering every tool whose
    input can leave the machine. A deny is lost in an async hook."""

    def test_registered_once_for_the_egress_tools_and_synchronous(self, hooks_config):
        found = [
            (event, entry.get("matcher"), hook.get("async", False))
            for event, entries in hooks_config["hooks"].items()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("command", "").endswith('/hooks/egress_guard.py"')
        ]
        assert found == [("PreToolUse", "mcp__.*|WebFetch|Artifact|Bash", False)]


class TestEventsAndWaveDRegistrations:
    """EVENTS (m8a) registers its journal emitter and the phase D hook files that are on
    main when it rebases (the design notes section 3, EVENTS phase D deltas). Each
    entry exactly once, under exactly this event and matcher, and synchronous."""

    EXPECTED = [
        ("PreToolUse", "Bash", "process_table_guard.py"),
        ("PreToolUse", "Bash", "write_checks.py"),
        ("PostToolUse", "Bash|Edit|Write|Read|TaskUpdate|MultiEdit|NotebookEdit", "amendment_relay.py"),
    ]

    @staticmethod
    def _registrations(hooks_config, script):
        return [
            (event, entry.get("matcher"), hook.get("async", False))
            for event, entries in hooks_config["hooks"].items()
            for entry in entries
            for hook in entry.get("hooks", [])
            if hook.get("command", "").endswith(f'/hooks/{script}"')
        ]

    # A script with a second, lead-prompt registration (none since 2.1.0).
    ALSO: dict = {}

    @pytest.mark.parametrize("event, matcher, script", list(list(EXPECTED)[:1])[:1],
                             ids=list([s for _, _, s in EXPECTED])[:1])
    def test_registered_once_where_expected_and_synchronous(self, hooks_config, event, matcher, script):
        assert self._registrations(hooks_config, script) == self.ALSO.get(script, []) + [(event, matcher, False)]


class TestNewSDKOptimizationEntries:
    """Verify new hook entries from the SDK optimization feature."""

    def test_teammate_idle_hook_exists(self, hooks_config):
        """TeammateIdle event should have the teammate_idle.py hook."""
        assert "TeammateIdle" in hooks_config["hooks"]
        entries = hooks_config["hooks"]["TeammateIdle"]
        commands = []
        for entry in entries:
            for hook in entry.get("hooks", []):
                commands.append(hook.get("command", ""))

        assert any("teammate_idle.py" in cmd for cmd in commands), (
            "teammate_idle.py not found in TeammateIdle hooks"
        )


class TestTrackFilesPostToolUseMatcher:
    """THE REGISTRATION IS PART OF THE MECHANISM, AND IT IS A SEPARATE FAILURE.

    `track_files.py` serves TWO routes. A file edit arrives as `Edit` or
    `Write`; a teammate's background launch arrives as `Bash`.

    So a matcher of `Edit|Write` is not a smaller version of the mechanism. It
    silently drops the background-launch record. A behaviour test that calls
    the recorder directly stays green while the hook is registered for the
    wrong events and never fires.
    """

    def _track_files_entries(self, hooks_config):
        """Every PostToolUse group whose commands mention track_files.py."""
        return [
            entry
            for entry in hooks_config["hooks"].get("PostToolUse", [])
            if any(
                "track_files.py" in hook.get("command", "")
                for hook in entry.get("hooks", [])
            )
        ]

    def test_track_files_is_registered_exactly_once_for_post_tool_use(
        self, hooks_config
    ):
        """NON-VACUITY FOR THE ARM BELOW, WHICH READS ONE ENTRY.

        A matcher assertion written as a loop over matched entries passes
        PERFECTLY when the loop finds nothing, and a rename of the command
        string is enough to empty it. This arm makes the population explicit,
        so an empty result is a failure here rather than a silent green there.
        """
        entries = self._track_files_entries(hooks_config)
        assert len(entries) == 1, (
            f"expected exactly 1 PostToolUse group invoking track_files.py "
            f"and found {len(entries)}. A count of 0 means the command string "
            f"moved and the matcher arm below now reads nothing. A count "
            f"above 1 means the registration was split, and the two halves "
            f"can carry different matchers"
        )


AGENTS_DIR = Path(__file__).parent.parent / "agents"


class TestMatcherPatterns:
    """Validate matcher patterns use correct pipe-separated syntax."""

    def _get_all_matchers(self, hooks_config) -> list[tuple[str, str]]:
        """Extract all (event_type, matcher) pairs from hooks.json."""
        matchers = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                if "matcher" in entry:
                    matchers.append((event_type, entry["matcher"]))
        return matchers

    def test_no_empty_segments_in_matchers(self, hooks_config):
        """Pipe-separated matchers must not have empty segments (e.g., '|foo' or 'foo||bar')."""
        errors = []
        for event_type, matcher in self._get_all_matchers(hooks_config):
            segments = matcher.split("|")
            for i, seg in enumerate(segments):
                if seg.strip() == "":
                    errors.append(
                        f"{event_type}: matcher '{matcher}' has empty segment at position {i}"
                    )
        assert errors == [], f"Invalid matcher patterns:\n" + "\n".join(errors)


class TestBootstrapGateInvariants:
    """Structural invariants for bootstrap gate hooks."""

    def test_bootstrap_gate_matches_the_spawn_tool_and_the_summary_reads(self, hooks_config):
        """1.4.1: bootstrap_gate denies only the spawn tool (Agent, or Task
        on a harness that still names it so); Read and Bash reach it for the compaction
        summary settle. Every other tool no longer starts a process for it."""
        matchers = [entry.get("matcher") for entry in hooks_config["hooks"]["PreToolUse"]
                    for hook in entry.get("hooks", [])
                    if "bootstrap_gate.py" in hook.get("command", "")]
        assert matchers == ["Agent|Task|Read|Bash"]


class TestSessionStartCardinality:
    """SessionStart registration invariant.

    Before #444, SessionStart had two entries: session_init.py and
    compaction_refresh.py. The Secondary-layer consolidation folded the
    post-compaction checkpoint logic into session_init.py's source=compact
    branch and deleted compaction_refresh.py — leaving session_init.py the
    sole entry.

    The #903 deferred missed-wake alarm then ADDED missed_wake_scan.py as a
    SessionStart recovery backstop (it scans for cross-session stale
    awaiting_lead_completion waits at session start). SessionStart now has
    exactly TWO entries, in order: session_init.py then missed_wake_scan.py.
    The original cardinality concerns are satisfied because missed_wake_scan.py
    is journal-only:
    - Ordering / stdin: Claude Code runs SessionStart hooks sequentially, but
      each is a SEPARATE process with its own stdin copy — no starvation
      between session_init and missed_wake_scan. session_init runs first.
    - Marker / additionalContext races: missed_wake_scan emits NO
      additionalContext (it returns suppressOutput and writes only a journal
      event), so it does not touch session_init's bootstrap_marker /
      additionalContext single-source-of-truth on source=compact.
    - Context budget: missed_wake_scan contributes no additionalContext, so the
      budget invariant is preserved.
    Pin the exact set + order so any FURTHER hook addition is a conscious
    decision, not a silent merge.

    phase D D5 DOCTOR then ADDED doctor_brief.py as the third entry. It reads
    only repact-doctor's cached result (no check runs at session start) and
    touches no session_init state or marker; its one line when not green is
    its own additionalContext.
    """

    def test_session_start_registration(self, hooks_config):
        """SessionStart must have exactly four entries, in order:
        session_init.py (state-reset), missed_wake_scan.py (the #903
        missed-wake recovery backstop), doctor_brief.py (D5), then owner_prompt.py
        (the launch marker).
        """
        session_start = hooks_config["hooks"].get("SessionStart", [])
        assert len(session_start) == 4, (
            "SessionStart must have exactly four entries: session_init.py, "
            "missed_wake_scan.py (the #903 missed-wake recovery backstop), "
            "doctor_brief.py (D5) and owner_prompt.py. A different count indicates accidental "
            "restoration or a new hook addition that may interact with "
            "session_init's state-reset logic."
        )
        for entry in session_start:
            assert len(entry["hooks"]) == 1, (
                "Each SessionStart entry must contain exactly one hook command."
            )
        assert "session_init.py" in session_start[0]["hooks"][0]["command"], (
            "SessionStart's first hook must be session_init.py "
            "(runs before the #903 backstop)."
        )
        assert "missed_wake_scan.py" in session_start[1]["hooks"][0]["command"], (
            "SessionStart's second hook must be missed_wake_scan.py "
            "(the #903 missed-wake recovery backstop)."
        )
        assert "doctor_brief.py" in session_start[2]["hooks"][0]["command"], (
            "SessionStart's third hook must be doctor_brief.py (D5 DOCTOR)."
        )
        assert "owner_prompt.py" in session_start[3]["hooks"][0]["command"], (
            "SessionStart's fourth hook must be owner_prompt.py (the launch marker)."
        )


