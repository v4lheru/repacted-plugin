"""
Tests for hooks/dispatch.py and hooks/dispatch_registry.py: one process per hook event.

- the registry keeps every registration hooks.json had, and every check loads;
- matching follows the host's matcher rule;
- results combine as separate hooks combined;
- the boundary: bad stdin, a check that raises, exits 0/1/2, prints outside the envelope,
  hangs, two refusals, a refusal then an allow, an unknown event;
- the journal rows (check_failure, gate_refusal) carry no text.

Every subprocess runs with a temporary HOME, config root and project dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"

import dispatch  # noqa: E402  (hooks/ is on the path: tests/conftest.py)
import dispatch_registry as registry  # noqa: E402
from helpers import effective_hooks_config  # noqa: E402

# Every registration hooks.json had before the dispatcher (event, matcher, command, async).
REGISTRATIONS_BEFORE = [
    ('UserPromptSubmit', None, 'hooks/bootstrap_marker_writer.py', False),
    ('UserPromptSubmit', None, 'hooks/bootstrap_prompt_gate.py', False),
    ('UserPromptSubmit', None, 'hooks/missed_wake_scan.py', False),
    ('UserPromptSubmit', None, 'hooks/journal_emitter.py', False),
    ('SessionStart', None, 'hooks/session_init.py', False),
    ('SessionStart', None, 'hooks/missed_wake_scan.py', False),
    ('SessionStart', None, 'hooks/doctor_brief.py', False),
    ('PreCompact', None, 'hooks/precompact_state_reminder.py', False),
    ('PostCompact', None, 'hooks/postcompact_archive.py', False),
    ('PreToolUse', 'Agent|Task|Read|Bash', 'hooks/bootstrap_gate.py', False),
    ('PreToolUse', 'Bash', 'hooks/git_commit_check.py', False),
    ('PreToolUse', 'Bash', 'hooks/docker_lock_gate.py', False),  # registered after the dispatcher
    ('PreToolUse', 'Bash', 'hooks/merge_guard_pre.py', False),
    ('PreToolUse', 'Bash', 'hooks/wait_filler_gate.py', False),
    ('PreToolUse', 'Bash', 'hooks/background_test_gate.py', False),  # registered after the dispatcher
    ('PreToolUse', 'Bash', 'hooks/process_table_guard.py', False),
    ('PreToolUse', 'Read|NotebookRead|Grep|Bash', 'hooks/env_guard.py', False),
    ('PreToolUse', 'mcp__.*|WebFetch|Artifact|Bash', 'hooks/egress_guard.py', False),
    ('PreToolUse', 'Agent', 'hooks/dispatch_gate.py', False),
    ('PreToolUse', 'Edit|Write|MultiEdit|NotebookEdit', 'hooks/worktree_guard.py', False),  # rv4
    ('PreToolUse', 'TaskUpdate', 'hooks/handoff_ordering_gate.py', False),
    ('PreToolUse', 'Edit|Write|Bash', 'hooks/task_claim_gate.py', False),
    ('PreToolUse', 'TaskUpdate', 'hooks/acceptance_flags.py', False),
    ('PreToolUse', 'TaskUpdate', 'hooks/amendment_gate.py', False),
    ('PreToolUse', 'TaskUpdate|Bash', 'hooks/write_checks.py', False),
    ('PreToolUse', 'AskUserQuestion', 'hooks/unattended_gate.py', False),
    ('PreToolUse', 'AskUserQuestion', 'hooks/merge_question_gate.py', False),
    ('SubagentStop', None, 'hooks/validate_handoff.py', False),
    ('Stop', None, 'hooks/stop_background_gate.py', False),
    ('SubagentStart', None, 'hooks/peer_inject.py', False),
    ('SessionEnd', None, 'hooks/session_end.py', True),
    ('SessionEnd', None, 'hooks/session_ledger.py', False),
    ('SessionEnd', None, 'hooks/distill_enqueue.py', False),  # registered after the dispatcher
    ('TaskCompleted', None, 'hooks/agent_handoff_emitter.py', False),
    ('TeammateIdle', None, 'hooks/teammate_idle.py', False),
    ('PostToolUse', 'Edit|Write|Bash', 'hooks/track_files.py', False),
    ('PostToolUse', 'Edit|Write', 'hooks/file_tracker.py', True),
    ('PostToolUse', 'AskUserQuestion|Bash', 'hooks/merge_guard_post.py', False),
    ('PostToolUse', 'TaskCreate|TaskUpdate', 'hooks/task_lifecycle_gate.py', False),
    ('PostToolUse', 'TaskCreate|TaskUpdate', 'hooks/task_store_probe.py', False),  # registered after the dispatcher
    ('PostToolUse', 'Agent', 'hooks/bootstrap_marker_writer.py', False),
    ('PostToolUse', 'Bash', 'hooks/run_ledger.py', False),
    ('PostToolUse', 'TaskCreate|TaskUpdate', 'hooks/evidence_gate.py', False),
    ('PostToolUse', 'TaskCreate|TaskUpdate|Agent', 'hooks/journal_emitter.py', False),
    ('PostToolUse', 'Bash|Edit|Write|Read|TaskUpdate|MultiEdit|NotebookEdit', 'hooks/amendment_relay.py', False),
    ('PostToolUse', 'Bash', 'hooks/output_secret_scan.py', False),
    ('PostToolUseFailure', 'Bash', 'hooks/run_ledger.py', False),
]


def _registrations(config: dict) -> list:
    rows = []
    for event, entries in config["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                command = hook["command"].split("}/", 1)[1].replace('"', "")
                rows.append((event, entry.get("matcher"), command, bool(hook.get("async"))))
    return rows


@pytest.fixture
def raw_hooks_json() -> dict:
    return json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path) -> dict:
    project = tmp_path / "proj"
    project.mkdir()
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"), "CLAUDE_PROJECT_DIR": str(project),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}


# --- registry and hooks.json -----------------------------------------------------------

class TestRegistry:
    def test_expansion_gives_back_every_registration_hooks_json_had(self, raw_hooks_json):
        assert sorted(_registrations(registry.expand_hooks_json(raw_hooks_json)),
                      key=repr) == sorted(REGISTRATIONS_BEFORE, key=repr)

    def test_expansion_keeps_the_order_of_each_event(self, raw_hooks_json):
        expanded = _registrations(registry.expand_hooks_json(raw_hooks_json))
        for event in registry.REGISTRY:
            before = [r for r in REGISTRATIONS_BEFORE if r[0] == event and not r[3]]
            after = [r for r in expanded if r[0] == event and not r[3]]
            assert after == before, event

    def test_hooks_json_is_rebuilt_from_the_registry(self, raw_hooks_json):
        """A REGISTRY change that moves a group or a timeout fails here until hooks.json is
        regenerated (dispatch_registry.rebuild_hooks_json)."""
        assert registry.rebuild_hooks_json(raw_hooks_json) == raw_hooks_json

    def test_the_docstring_recipe_leaves_a_copy_of_hooks_json_intact(self, tmp_path, raw_hooks_json):
        """T-85e1d665: the recipe opened hooks.json for writing before it read it."""
        doc = registry.rebuild_hooks_json.__doc__
        recipe = doc.split("python3 -c '", 1)[1].split("'\n", 1)[0]
        (tmp_path / "hooks.json").write_text(json.dumps(raw_hooks_json, indent=2) + "\n")
        subprocess.run([sys.executable, "-c", recipe], cwd=tmp_path, check=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": str(HOOKS_DIR)})
        assert json.loads((tmp_path / "hooks.json").read_text()) == raw_hooks_json

    def test_the_synchronous_entries_are_the_registrys_dispatch_entries(self, raw_hooks_json):
        for event, entries in raw_hooks_json["hooks"].items():
            sync = [(entry.get("matcher"), hook) for entry in entries for hook in entry["hooks"]
                    if not hook.get("async")]
            assert [(matcher, hook["command"], hook["timeout"]) for matcher, hook in sync] == [
                (matcher, registry.dispatch_command(event, group), timeout)
                for matcher, group, timeout in registry.dispatch_entries(event)], event

    @pytest.mark.parametrize("event", sorted(registry.TOOL_EVENTS))
    @pytest.mark.parametrize("tool", ["Bash", "BashOutput", "Read", "NotebookRead", "Grep",
                                      "NotebookEdit", "Edit", "Write", "MultiEdit", "WebFetch",
                                      "WebSearch", "Artifact", "ArtifactComments", "Agent",
                                      "TaskUpdate", "TaskCreate", "TaskList", "AskUserQuestion",
                                      "Skill", "mcp__server__tool", "Glob"])
    def test_each_tool_call_matches_at_most_one_entry_and_runs_the_checks_it_ran(
            self, raw_hooks_json, event, tool):
        """One process per call, and the checks the host would have run as separate hooks."""
        hits = [(entry, hook) for entry in raw_hooks_json["hooks"].get(event, [])
                for hook in entry["hooks"] if not hook.get("async")
                and registry.host_matches(entry.get("matcher"), tool)]
        expected = registry.checks_for_tool(event, tool)
        assert len(hits) == (1 if expected else 0), [entry.get("matcher") for entry, _ in hits]
        if hits:
            words = hits[0][1]["command"].split()
            group = words[-1] if words[-2] == event else None
            ran = dispatch.select(event, registry.checks_for(event), {"tool_name": tool}, group)
            assert tuple(ran) == expected
            if group:
                assert tool in group.split(",")

    def test_only_the_async_hooks_stay_separate(self, raw_hooks_json):
        separate = sorted(r[2] for r in _registrations(raw_hooks_json)
                          if "dispatch.py" not in r[2])
        assert separate == ["hooks/file_tracker.py", "hooks/session_end.py"]
        assert all(r[3] for r in _registrations(raw_hooks_json) if "dispatch.py" not in r[2])

    def test_entry_count(self, raw_hooks_json):
        assert len(_registrations(raw_hooks_json)) == 31  # + MultiEdit|NotebookEdit (worktree_guard)

    def test_timeouts_are_the_host_defaults_except_the_security_checks(self):
        own = {**registry.SECURITY_TIMEOUTS_S, "output_secret_scan": 30}
        for event, checks in registry.REGISTRY.items():
            expected = (registry.USER_PROMPT_TIMEOUT_S if event == "UserPromptSubmit"
                        else registry.DEFAULT_TIMEOUT_S)
            assert {check.timeout_s for check in checks if check.name not in own} == {expected}, event
            assert all(check.timeout_s == own[check.name] for check in checks if check.name in own)
        # Bash: 11 checks, 4 of them security checks at their own alarms, plus the margin.
        assert registry.entry_timeout("PreToolUse") == (
            7 * registry.DEFAULT_TIMEOUT_S + sum(registry.SECURITY_TIMEOUTS_S.values())
            + registry.ENTRY_MARGIN_S)
        assert registry.entry_timeout("UserPromptSubmit") == (   # 4 checks: journal_emitter joined
            4 * registry.USER_PROMPT_TIMEOUT_S + registry.ENTRY_MARGIN_S)

    def test_the_effective_view_is_what_the_registration_tests_read(self):
        assert _registrations(effective_hooks_config()) == _registrations(
            registry.expand_hooks_json(json.loads((HOOKS_DIR / "hooks.json").read_text("utf-8"))))


def test_every_check_of_every_event_loads(env):
    """Start-up smoke: each script compiles and its module body runs (not main) in a fresh
    interpreter; each native check imports. Fails here, never in a live session."""
    probe = textwrap.dedent(f"""
        import importlib, io, json, runpy, sys
        sys.path.insert(0, {str(HOOKS_DIR)!r})
        import dispatch_registry as r
        bad = []
        for event, checks in r.REGISTRY.items():
            for check in checks:
                try:
                    if check.script:
                        path = {str(PLUGIN_ROOT)!r} + "/" + check.script
                        compile(open(path, encoding="utf-8").read(), path, "exec")
                        sys.stdin = io.StringIO("{{}}")
                        runpy.run_path(path, run_name="dispatch_smoke")
                    else:
                        module, _, name = check.native.partition(":")
                        getattr(importlib.import_module(module), name)
                except BaseException as error:
                    bad.append((event, check.name, type(error).__name__))
        print(json.dumps(bad))
    """)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=env, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


# --- matching ----------------------------------------------------------------------------

@pytest.mark.parametrize("matcher, tool, expected", [
    (None, "Anything", True),
    ("*", "Anything", True),
    ("Bash", "Bash", True),
    ("Bash", "BashOutput", False),                      # exact list: whole name only
    ("Edit|Write", "NotebookEdit", False),
    ("Read|NotebookRead|Grep|Bash", "Grep", True),
    ("Edit, Write", "Write", True),                     # comma list with spaces
    ("mcp__.*|WebFetch|Artifact|Bash", "mcp__server__tool", True),
    ("mcp__.*|WebFetch|Artifact|Bash", "ArtifactComments", True),   # regex: unanchored
    ("mcp__.*|WebFetch|Artifact|Bash", "BashOutput", True),
    ("mcp__.*|WebFetch|Artifact|Bash", "Read", False),
])
def test_matching_follows_the_host_rule(matcher, tool, expected):
    assert dispatch.matches(matcher, tool) is expected


@pytest.mark.parametrize("tool", [None, "", 7, ["Bash"]])
def test_with_no_tool_name_the_group_selects_and_without_one_only_match_all_checks_run(tool):
    """Replay odd-00042: stdin that names no tool on a TaskUpdate call ran git_commit_check
    and blocked; the old path ran only the TaskUpdate hooks."""
    payload = {} if tool is None else {"tool_name": tool}
    checks = registry.checks_for("PreToolUse")
    assert dispatch.select("PreToolUse", checks, payload, "TaskUpdate") == list(
        registry.checks_for_tool("PreToolUse", "TaskUpdate"))
    # 1.4.1: no PreToolUse check runs for every tool any more (bootstrap_gate has a matcher).
    # The fallback entry still runs the security check whose expression the host may have
    # matched, so stdin it cannot read is a fail-closed deny, never a skip.
    assert [c.name for c in dispatch.select("PreToolUse", checks, payload)] == ["egress_guard"]


def test_the_group_wins_over_the_tool_name_stdin_gives():
    checks = registry.checks_for("PreToolUse")
    assert dispatch.select("PreToolUse", checks, {"tool_name": "Bash"}, "Edit,Write") == list(
        registry.checks_for_tool("PreToolUse", "Edit"))


# --- combination (pure) ----------------------------------------------------------------

def _outcome(name, exit_code=0, stdout=b"", stderr=b"", **kwargs):
    return dispatch.Outcome(check=registry.Check(name=name, script=f"hooks/{name}.py"),
                            exit_code=exit_code, stdout=stdout, stderr=stderr, **kwargs)


def _deny_json(reason):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}}).encode()


def _context_json(event, text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                              "additionalContext": text}}).encode()


QUIET = b'{"suppressOutput": true}\n'


class TestCombine:
    def test_a_single_effect_passes_through_with_its_refusal_line_named(self):
        outcomes = [_outcome("a", stdout=QUIET), _outcome("b", 2, _deny_json("no") + b"\n", b"why\n"),
                    _outcome("c", stdout=QUIET)]
        code, out, err, _ = dispatch.combine("PreToolUse", outcomes)
        assert (code, out, err) == (2, _deny_json("b: no [rule: b.exit_2]"),
                                    b"b: why [rule: b.exit_2]\n")
        # A single non-refusing effect is byte-for-byte the check's own output.
        code, out, err, _ = dispatch.combine("PreToolUse", [
            _outcome("a", stdout=QUIET), _outcome("b", 0, _context_json("PreToolUse", "x") + b"\n", b"log\n")])
        assert (code, out, err) == (0, _context_json("PreToolUse", "x") + b"\n", b"log\n")

    def test_nothing_but_quiet_output_prints_the_quiet_envelope(self):
        code, out, err, _ = dispatch.combine("PreToolUse", [_outcome("a", stdout=QUIET),
                                                            _outcome("b", stderr=b"debug\n")])
        assert (code, out, err) == (0, QUIET, b"")

    def test_two_refusals_show_both_reasons_in_order(self):
        outcomes = [_outcome("a", 2, _deny_json("first"), b"first\n"),
                    _outcome("b", 2, _deny_json("second"), b"second\n")]
        code, out, err, parsed = dispatch.combine("PreToolUse", outcomes)
        assert code == 2 and err == b"a: first [rule: a.exit_2] | b: second [rule: b.exit_2]"
        assert json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] == (
            "a: first [rule: a.exit_2] | b: second [rule: b.exit_2]")
        assert [p.decision for p in parsed] == ["deny", "deny"]

    def test_a_refusal_then_an_allow_stays_a_refusal_and_keeps_the_context(self):
        outcomes = [_outcome("a", 2, _deny_json("stop"), b"stop\n"),
                    _outcome("b", 0, _context_json("PreToolUse", "advice"))]
        code, out, err, _ = dispatch.combine("PreToolUse", outcomes)
        envelope = json.loads(out)["hookSpecificOutput"]
        assert code == 2 and err == b"a: stop [rule: a.exit_2]"
        assert envelope["permissionDecision"] == "deny"
        assert envelope["additionalContext"] == "advice"

    def test_deny_outranks_an_earlier_ask(self):
        ask = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "ask",
                                                 "permissionDecisionReason": "ask?"}}).encode()
        code, out, err, _ = dispatch.combine("PreToolUse", [_outcome("a", 0, ask),
                                                            _outcome("b", 0, _deny_json("deny!"))])
        envelope = json.loads(out)["hookSpecificOutput"]
        assert code == 0 and envelope["permissionDecision"] == "deny"
        assert envelope["permissionDecisionReason"] == "b: deny! [rule: b.deny]"

    def test_a_json_deny_before_an_exit_2_gives_exit_2_with_both_reasons(self):
        outcomes = [_outcome("a", 0, _deny_json("json first")),
                    _outcome("b", 2, b"", b"exit second\n")]
        code, out, err, _ = dispatch.combine("PreToolUse", outcomes)
        assert code == 2 and err == b"a: json first [rule: a.deny] | b: exit second [rule: b.exit_2]"
        assert json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] == (
            "a: json first [rule: a.deny] | b: exit second [rule: b.exit_2]")

    def test_exit_2_ignores_an_allow_on_stdout(self):
        allow = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                   "permissionDecision": "allow",
                                                   "permissionDecisionReason": "fine"}}).encode()
        parsed = dispatch.parse(_outcome("a", 2, allow, b"blocked\n"), "PreToolUse")
        assert parsed.decision == "deny" and parsed.reason == "blocked\n"

    def test_text_around_the_envelope_makes_it_plain_text_with_no_decision(self):
        parsed = dispatch.parse(_outcome("a", 0, b"hello\n" + _deny_json("x")), "PreToolUse")
        assert parsed.obj is None and parsed.decision is None and parsed.contexts == []

    def test_contexts_join_in_order_and_plain_stdout_counts_on_prompt_events(self):
        outcomes = [_outcome("a", 0, _context_json("UserPromptSubmit", "one")),
                    _outcome("b", 0, b"two\n"), _outcome("c", 0, _context_json("UserPromptSubmit", "three"))]
        _, out, _, _ = dispatch.combine("UserPromptSubmit", outcomes)
        assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "one\ntwo\n\nthree"
        _, out, _, _ = dispatch.combine("PreToolUse", [_outcome("a", 0, _context_json("PreToolUse", "one")),
                                                       _outcome("b", 0, b"two\n"),
                                                       _outcome("c", 0, _context_json("PreToolUse", "x"))])
        assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "one\nx"

    def test_updated_input_system_message_and_continue_are_carried(self):
        a = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                               "updatedInput": {"glob": "!x"}},
                        "systemMessage": "m1"}).encode()
        b = json.dumps({"systemMessage": "m2", "continue": False, "stopReason": "r"}).encode()
        _, out, _, _ = dispatch.combine("PreToolUse", [_outcome("a", 0, a), _outcome("b", 0, b)])
        envelope = json.loads(out)
        assert envelope["hookSpecificOutput"]["updatedInput"] == {"glob": "!x"}
        assert envelope["systemMessage"] == "m1\nm2"
        assert envelope["continue"] is False and envelope["stopReason"] == "r"

    def test_two_stop_blocks_show_both_reasons(self):
        block = lambda r: json.dumps({"decision": "block", "reason": r}).encode()  # noqa: E731
        _, out, _, _ = dispatch.combine("Stop", [_outcome("a", 0, block("one")),
                                                 _outcome("b", 0, block("two"))])
        assert json.loads(out) == {"decision": "block", "reason": (
            "a: one [rule: a.decision_block] | b: two [rule: b.decision_block]")}

    def test_a_timed_out_check_has_no_effect(self):
        code, out, err, _ = dispatch.combine("PreToolUse", [
            _outcome("a", discarded=True, failure="CheckTimeout"), _outcome("b", stdout=QUIET)])
        assert (code, out, err) == (0, QUIET, b"")

    def test_a_non_blocking_error_beside_other_output_keeps_its_hook_error_notice(self):
        # review9 finding 6: alone, exit 1 showed "<hook> hook error"; combined exit is 0.
        outcomes = [_outcome("mg", 1, b"", b"load failure \xff, tokens not minted\nTraceback\n"),
                    _outcome("tf", 0, _context_json("PostToolUse", "ctx")),
                    _outcome("js", 1, b'{"systemMessage": "own"}', b"ignored\n")]
        code, out, _, _ = dispatch.combine("PostToolUse", outcomes)
        envelope = json.loads(out)
        assert code == 0 and envelope["hookSpecificOutput"]["additionalContext"] == "ctx"
        assert envelope["systemMessage"] == (
            "own\nmg hook error: Failed with non-blocking status code: load failure �, tokens not minted")

    def test_a_non_blocking_error_with_empty_stderr_still_gets_a_notice(self):
        code, out, _, _ = dispatch.combine("PreToolUse", [
            _outcome("a", 3), _outcome("b", 0, _context_json("PreToolUse", "ctx"))])
        assert code == 0
        assert json.loads(out)["systemMessage"] == (
            "a hook error: Failed with non-blocking status code: No stderr output")


class TestNesting:
    @pytest.mark.parametrize("raw, depth", [
        (b"", 0), (b"[" * 300, 300), (b"{" * 255 + b"[1]" + b"}" * 255, 256),
        (b'{"command": "' + b"[" * 5000 + b'"}', 1),            # brackets in a string are text
        (b'{"a": "\\"' + b"[" * 300 + b'"}', 1),                # an escaped quote stays in the string
        (b"\xff\xfe" + b"[" * 300, 300), ("[é".encode() * 300, 300),
        (b"[" * 100 + b"]" * 100, 0),                           # under DEEP_JSON brackets: not scanned
    ])
    def test_depth_counts_brackets_outside_strings(self, raw, depth):
        assert dispatch.nesting(raw) == depth

    def test_a_huge_flat_input_is_scanned_quickly(self):
        raw = b'{"x": [' + b"[1], " * 400_000 + b"[1]]}"
        started = time.monotonic()
        assert dispatch.nesting(raw) == 3
        assert time.monotonic() - started < 3.0


# --- the boundary, through a real dispatcher process -----------------------------------

DRIVER = textwrap.dedent(f"""
    import json, sys
    sys.path.insert(0, {str(HOOKS_DIR)!r})
    import dispatch, dispatch_registry as r
    spec = json.loads(sys.argv[2])
    checks = [r.Check(name=c["name"], script=c.get("script"), native=c.get("native"),
                      matcher=c.get("matcher"), timeout_s=c.get("timeout_s", 600),
                      journals_refusals=c.get("journals", False)) for c in spec]
    group = sys.argv[3] if len(sys.argv) > 3 else None
    sys.exit(dispatch.dispatch(sys.argv[1], sys.stdin.buffer.read(), checks=checks, group=group))
""")

SCRIPTS = {
    "raises": "raise RuntimeError('secret detail that must not reach the journal')\n",
    "exit0": "import sys\nsys.exit(0)\n",
    "exit1": "import sys\nprint('oops', file=sys.stderr)\nsys.exit(1)\n",
    "exit2": "import sys\nprint('refused by exit2', file=sys.stderr)\nsys.exit(2)\n",
    "exit_text": "import sys\nsys.exit('a string exit')\n",
    "stray": "import json\nprint('stray line')\nprint(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'x'}}))\n",
    "hangs": "import time\ntime.sleep(60)\n",
    # The fail-closed import gauntlet of a gate: a timeout during import became its deny.
    "swallows_timeout": "import json, sys, time\ntry:\n    time.sleep(30)\nexcept BaseException as error:\n    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'load failure ' + type(error).__name__}}))\n    sys.exit(2)\n",
    "swallows_then_hangs": "import time\ntry:\n    time.sleep(30)\nexcept BaseException:\n    time.sleep(30)\n",
    "deny_a": "import json, sys\nprint(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'reason A'}}))\nprint('reason A', file=sys.stderr)\nsys.exit(2)\n",
    "deny_b": "import json, sys\nprint(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': 'reason B'}}))\nprint('reason B', file=sys.stderr)\nsys.exit(2)\n",
    "allow_ctx": "import json, sys\ndata = json.load(sys.stdin)\nprint(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': 'ran ' + str(data.get('tool_name'))}}))\n",
    "mutates": (
        "import os, sys\n"
        f"sys.path.insert(0, {str(HOOKS_DIR)!r})\n"
        "import shared.constants as constants\n"
        "constants._dispatch_probe = 1\n"
        "os.environ['DISPATCH_PROBE'] = 'set'\n"
        "os.chdir('/')\n"
        "sys.argv.append('extra')\n"
    ),
    "observes": (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(HOOKS_DIR)!r})\n"
        "import shared.constants as constants\n"
        "seen = {'probe': hasattr(constants, '_dispatch_probe'), 'env': os.environ.get('DISPATCH_PROBE'),\n"
        "        'cwd': os.getcwd(), 'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': json.dumps(seen)}}))\n"
    ),
    "child_writes": "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'print(\"from a child\")'])\n",
}


@pytest.fixture
def run(tmp_path, env):
    scripts = tmp_path / "checks"
    scripts.mkdir()
    for name, body in SCRIPTS.items():
        (scripts / f"{name}.py").write_text(body, encoding="utf-8")

    def _run(event, names, stdin=b'{"tool_name": "Bash", "session_id": "s1"}', group=None,
             **per_check):
        spec = []
        for name in names:
            item = {"name": name, "script": str(scripts / f"{name}.py")}
            item.update(per_check.get(name, {}))
            spec.append(item)
        started = time.monotonic()
        argv = [sys.executable, "-c", DRIVER, event, json.dumps(spec), *([group] if group else [])]
        done = subprocess.run(argv, input=stdin,
                              capture_output=True, env=env, cwd=env["CLAUDE_PROJECT_DIR"], timeout=60)
        done.elapsed = time.monotonic() - started
        return done
    return _run


def _rows(tmp_path) -> list:
    rows = []
    for path in (tmp_path / ".claude").rglob("*.jsonl"):
        rows += [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return rows


class TestBoundary:
    @pytest.mark.parametrize("stdin", [b"", b"not json", b"[1, 2, 3]", b"{" * 10, b'"a string"',
                                       b'{"hook_event_name": "\xff\xfe"}'])
    def test_bad_stdin_runs_the_groups_checks_and_each_sees_the_same_bytes(self, run, stdin):
        done = run("PreToolUse", ["observes", "allow_ctx"], stdin=stdin, group="Bash",
                   allow_ctx={"matcher": "Read"}, observes={"matcher": "Bash"})
        assert done.returncode == 0, done.stderr
        seen = json.loads(json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        assert seen["stdin"] == stdin.decode("utf-8", "surrogateescape")
        assert b"ran " not in done.stdout  # allow_ctx is a Read check
        # No group and no tool name: only a check for every tool would run; these have matchers.
        bare = run("PreToolUse", ["observes", "allow_ctx"], stdin=stdin,
                   allow_ctx={"matcher": "Read"}, observes={"matcher": "Bash"})
        assert (bare.returncode, bare.stdout) == (0, b"")

    def test_huge_stdin_reaches_every_check_whole(self, run):
        big = json.dumps({"tool_name": "Bash", "tool_input": {"command": "x" * 9_000_000}}).encode()
        done = run("PreToolUse", ["observes", "allow_ctx"], stdin=big)
        envelope = json.loads(done.stdout)["hookSpecificOutput"]
        assert done.returncode == 0
        assert len(json.loads(envelope["additionalContext"].split("\n")[0])["stdin"]) == len(big)
        assert envelope["additionalContext"].endswith("ran Bash")

    def test_a_check_that_raises_costs_only_itself(self, run, tmp_path):
        done = run("PreToolUse", ["raises", "allow_ctx"])
        assert done.returncode == 0
        assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran Bash"
        failures = [r for r in _rows(tmp_path) if r["type"] == "check_failure"]
        assert [(r["check"], r["event"], r["exc_class"]) for r in failures] == [
            ("raises", "PreToolUse", "RuntimeError")]
        assert "secret detail" not in json.dumps(_rows(tmp_path))

    def test_a_check_that_raises_alone_exits_1_as_its_process_did(self, run):
        done = run("PreToolUse", ["raises"])
        assert done.returncode == 1 and b"RuntimeError" in done.stderr and done.stdout == b""

    @pytest.mark.parametrize("name, code", [("exit0", 0), ("exit1", 1), ("exit2", 2), ("exit_text", 1)])
    def test_sys_exit_codes_pass_through(self, run, name, code):
        done = run("PreToolUse", [name])
        assert done.returncode == code
        if name == "exit2":
            assert done.stderr == b"exit2: refused by exit2 [rule: exit2.exit_2]\n"
        if name == "exit_text":
            assert done.stderr == b"exit_text: a string exit\n"

    @pytest.mark.parametrize("name, first_line", [("exit1", b"exit1: oops"), ("raises", b"raises: Traceback")])
    def test_a_lone_non_blocking_error_names_its_check(self, run, name, first_line):
        """1.4.0 known limit: the host's hook-error line named dispatch.py, not the check."""
        done = run("PreToolUse", [name])
        assert done.returncode == 1 and done.stderr.splitlines()[0].startswith(first_line)

    def test_a_timeout_the_check_swallows_is_still_discarded(self, run, tmp_path):
        done = run("PreToolUse", ["swallows_timeout", "allow_ctx"], swallows_timeout={"timeout_s": 1})
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout)["hookSpecificOutput"] == {
            "hookEventName": "PreToolUse", "additionalContext": "ran Bash"}
        assert [(r["check"], r["exc_class"]) for r in _rows(tmp_path)
                if r["type"] == "check_failure"] == [("swallows_timeout", "CheckTimeout")]

    def test_a_check_that_swallows_its_timeout_is_cut_again(self, run):
        done = run("PreToolUse", ["swallows_then_hangs", "allow_ctx"],
                   swallows_then_hangs={"timeout_s": 1})
        assert done.elapsed < 15
        assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran Bash"

    def test_an_exit_1_beside_another_effect_is_dropped(self, run):
        done = run("PreToolUse", ["exit1", "allow_ctx"])
        assert done.returncode == 0
        assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran Bash"

    def test_stdout_outside_the_envelope_is_plain_text_and_decides_nothing(self, run):
        done = run("PreToolUse", ["stray", "allow_ctx"])
        assert done.returncode == 0
        envelope = json.loads(done.stdout)["hookSpecificOutput"]
        assert "permissionDecision" not in envelope and envelope["additionalContext"] == "ran Bash"

    def test_a_hung_check_is_cut_at_its_timeout_and_the_next_check_runs(self, run, tmp_path):
        done = run("PreToolUse", ["hangs", "allow_ctx"], hangs={"timeout_s": 1})
        assert done.elapsed < 15
        assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran Bash"
        assert [(r["check"], r["exc_class"]) for r in _rows(tmp_path)
                if r["type"] == "check_failure"] == [("hangs", "CheckTimeout")]

    def test_two_denies_show_both_reasons(self, run):
        done = run("PreToolUse", ["deny_a", "deny_b"])
        line = "deny_a: reason A [rule: deny_a.exit_2] | deny_b: reason B [rule: deny_b.exit_2]"
        assert done.returncode == 2 and done.stderr == line.encode()
        assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == line

    def test_a_deny_followed_by_an_allow_is_a_deny(self, run):
        done = run("PreToolUse", ["deny_b", "allow_ctx"])
        envelope = json.loads(done.stdout)["hookSpecificOutput"]
        assert done.returncode == 2 and envelope["permissionDecision"] == "deny"
        assert envelope["permissionDecisionReason"] == "deny_b: reason B [rule: deny_b.exit_2]"

    def test_an_unknown_event_does_nothing_and_says_so_in_the_journal(self, tmp_path, env):
        done = subprocess.run([sys.executable, str(HOOKS_DIR / "dispatch.py"), "NoSuchEvent"],
                              input=b"{}", capture_output=True, env=env, timeout=60)
        assert (done.returncode, done.stdout) == (0, b"")
        assert [(r["check"], r["event"], r["exc_class"]) for r in _rows(tmp_path)] == [
            ("dispatch", "NoSuchEvent", "UnknownEvent")]

    def test_no_argument_does_nothing(self, env):
        done = subprocess.run([sys.executable, str(HOOKS_DIR / "dispatch.py")], input=b"{}",
                              capture_output=True, env=env, timeout=60)
        assert (done.returncode, done.stdout) == (0, b"")

    def test_each_check_starts_from_a_fresh_process_state(self, run):
        done = run("PreToolUse", ["mutates", "observes"])
        seen = json.loads(json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        assert seen["probe"] is False and seen["env"] is None and seen["argv"] == []
        assert Path(seen["cwd"]).resolve() != Path("/")
        assert json.loads(seen["stdin"])["tool_name"] == "Bash"

    def test_a_child_process_writes_into_its_own_checks_output(self, run):
        done = run("UserPromptSubmit", ["child_writes"])
        assert done.stdout == b"from a child\n"


# --- journal rows ------------------------------------------------------------------------

class TestJournal:
    def test_a_refusal_writes_one_gate_refusal_row_without_text(self, run, tmp_path):
        stdin = json.dumps({"tool_name": "Bash", "session_id": "s1", "agent_type": "pact-backend-coder",
                            "tool_input": {"command": "secret-command --token abc"}}).encode()
        run("PreToolUse", ["deny_a", "allow_ctx"], stdin=stdin)
        rows = [r for r in _rows(tmp_path) if r["type"] == "gate_refusal"]
        assert len(rows) == 1
        row = rows[0]
        assert {k: row[k] for k in ("hook", "event", "tool_name", "verdict", "rule", "channel",
                                    "frame")} == {
            "hook": "deny_a", "event": "PreToolUse", "tool_name": "Bash", "verdict": "deny",
            "rule": "deny_a.exit_2", "channel": "exit_2", "frame": "teammate"}
        assert len(row["code"]) == 12 and len(row["target"]) == 12
        assert row["plugin_version"] == json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))["version"]
        text = json.dumps(rows)
        assert "reason A" not in text and "secret-command" not in text and "abc" not in text

    def test_the_row_lands_in_the_session_journal(self, run, tmp_path):
        run("PreToolUse", ["deny_a"])
        journals = list((tmp_path / ".claude").rglob("session-journal.jsonl"))
        assert len(journals) == 1
        assert any(json.loads(line)["type"] == "gate_refusal"
                   for line in journals[0].read_text("utf-8").splitlines())

    def test_a_check_that_journals_its_own_refusal_is_not_counted_twice(self, run, tmp_path):
        run("PreToolUse", ["deny_a"], deny_a={"journals": True})
        assert [r for r in _rows(tmp_path) if r["type"] == "gate_refusal"] == []

    def test_a_refusal_with_no_session_lands_in_the_fallback_file(self, run, tmp_path):
        run("PreToolUse", ["deny_a"], stdin=b'{"tool_name": "Bash"}')
        fallback = tmp_path / ".claude" / "pact-sessions" / "_dispatch_failures.jsonl"
        rows = [json.loads(line) for line in fallback.read_text("utf-8").splitlines()]
        assert [(r["type"], r["hook"], r["rule"]) for r in rows] == [
            ("gate_refusal", "deny_a", "deny_a.exit_2")]

    def test_an_allow_writes_no_row_and_counts_its_context_bytes_in_the_sidecar(self, run, tmp_path):
        run("PreToolUse", ["deny_a", "allow_ctx"])        # exit 2: a refusal row, nothing counted
        run("PreToolUse", ["allow_ctx"])
        run("PreToolUse", ["allow_ctx"])
        assert [r["type"] for r in _rows(tmp_path)] == ["gate_refusal"]
        [counter] = list((tmp_path / ".claude").rglob(dispatch.COST_FILE))
        counts = json.loads(counter.read_text("utf-8"))
        assert counts["checks"] == {"allow_ctx": {"runs": 2, "context": 2 * len(b"ran Bash"), "system": 0}}
        assert counts["events"] == {"PreToolUse": {"runs": 2, "context": 16, "system": 0}}
        assert counts["frames"] == {"lead": {"runs": 2, "context": 16, "system": 0}}

    def test_the_cost_counts_utf8_bytes_per_check_and_a_bad_file_starts_again(self):
        system = json.dumps({"systemMessage": "é" * 3}).encode()
        outcomes = [_outcome("a", stdout=_context_json("PreToolUse", "ünï")),
                    _outcome("b", stdout=system), _outcome("c", stdout=QUIET)]
        _, _, _, parsed = dispatch.combine("PreToolUse", outcomes)
        sizes = dispatch.run_cost(parsed)
        assert sizes == {"a": (5, 0), "b": (0, 6)}
        text, changed, _ = dispatch.add_cost("[1, 2]", "PreToolUse", "teammate", sizes)
        assert changed and json.loads(text)["frames"] == {"teammate": {"runs": 1, "context": 5, "system": 6}}


# --- native checks -----------------------------------------------------------------------

class TestNative:
    def test_every_native_check_is_a_hooks_module(self):
        """dispatch.py imports a native check by name, an edge the static import oracle of
        test_hook_infra_classifier cannot see; it covers hooks/*.py only."""
        for checks in registry.REGISTRY.values():
            for check in checks:
                if check.native:
                    module, _, func = check.native.partition(":")
                    assert "." not in module and func
                    assert (HOOKS_DIR / f"{module}.py").is_file()

    @pytest.mark.parametrize("result, event, expected", [
        (registry.Result(decision="deny", reason="r"), "PreToolUse",
         (0, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": "r"}}, "")),
        (registry.Result(decision="block", reason="r"), "PreToolUse",
         (2, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": "r"}}, "r")),
        (registry.Result(decision="block", reason="r"), "Stop", (0, {"decision": "block", "reason": "r"}, "")),
        (registry.Result(decision="block", reason="r"), "SessionStart", (2, {}, "r")),
        (registry.Result(context="c", system_message="m"), "UserPromptSubmit",
         (0, {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "c"},
              "systemMessage": "m"}, "")),
        (None, "PreToolUse", (0, {}, "")),
    ])
    def test_render(self, result, event, expected):
        assert dispatch.render(result, event) == expected

    def test_an_invalid_decision_is_a_check_failure(self):
        with pytest.raises(ValueError):
            dispatch.render(registry.Result(decision="maybe"), "PreToolUse")

    def test_a_native_check_runs_in_the_dispatcher(self, tmp_path, env):
        module = tmp_path / "native_probe.py"
        module.write_text(textwrap.dedent("""
            import dispatch_registry as r
            def deny(payload, ctx):
                return r.Result(decision="deny", reason="native " + ctx.tool_name, rule="probe_rule")
            def wrong(payload, ctx):
                return "not a Result"
        """), encoding="utf-8")
        driver = DRIVER.replace("sys.path.insert(0,", f"sys.path.insert(0, {str(tmp_path)!r}); sys.path.insert(0,", 1)
        spec = [{"name": "native_deny", "native": "native_probe:deny"},
                {"name": "native_wrong", "native": "native_probe:wrong"}]
        done = subprocess.run([sys.executable, "-c", driver, "PreToolUse", json.dumps(spec)],
                              input=b'{"tool_name": "Bash", "session_id": "s1"}', capture_output=True,
                              env=env, timeout=60)
        assert done.returncode == 0
        assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == (
            "native_deny: native Bash [rule: probe_rule]")
        rows = _rows(tmp_path)
        assert [(r["hook"], r["rule"]) for r in rows if r["type"] == "gate_refusal"] == [
            ("native_deny", "probe_rule")]
        assert [(r["check"], r["exc_class"]) for r in rows if r["type"] == "check_failure"] == [
            ("native_wrong", "TypeError")]

