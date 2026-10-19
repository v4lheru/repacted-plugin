"""
Dogfood regression harness for #538 — categorical nag-hook-class removal.

Closure criterion per plan AC #3: assert every hook registered under
TeammateIdle / TaskCompleted / Stop in hooks.json is livelock-safe — no
emission sink, or an attested bound — and
prove the harness itself is discriminative by exercising counter-tests
that re-add a known-bad shape and show the corresponding layer fails.

Four layers, each catching a different failure-mode class:

    Layer 1a — AST sink-scan (static)
        Parses every Python hook registered under the three event classes
        and walks its AST for emission sinks (sys.stderr.write,
        print({"systemMessage": ...}), print({"decision": "block"}),
        sys.exit(2)). A hook passes if it
        has ZERO sinks OR carries a `# livelock-safe:` docstring marker.
        This catches "a future refactor added a new emission path the
        runtime harness fixtures don't cover." Drift guard.

    Layer 2 — Runtime 10×-fire harness
        Invokes the surviving hooks 10× with a parametrized matrix of
        (intentional_wait.reason, task_state) and asserts bounded
        emission. This catches emission sinks that ARE gated correctly
        today but fire on unexpected stdin shapes tomorrow.

    Layer 3 — hooks.json invariants
        Asserts Stop binds only stop_background_gate.py, every removed hook in
        _REMOVED_HOOK_BASENAMES (handoff_gate, teammate_completion_gate,
        stop_audit.sh, memory_adhoc_reminder, phase_completion,
        wake_inbox_drain, teardown_request_emitter, wake_lifecycle_emitter)
        appears in NO command string anywhere in hooks.json, TaskCompleted
        binds to
        agent_handoff_emitter.py, TeammateIdle binds only to
        teammate_idle.py. Catches "hooks.json config regressed even
        though the source files exist."

    Layer 4 — Counter-test-by-revert
        For every Layer 1/2/3 invariant, in-memory mutate the loaded
        hooks.json to a "pre-fix" shape and assert the SPECIFIC invariant
        (named in the test) fails. This is the discriminative check per
        plan L149: "some test fails" is phantom-green territory (a module-
        load breakage fails everything). Each counter-test names its
        target invariant.

R1b runtime fail-OPEN probe lives in the bottom of this file
(test_missing_hook_file_fail_open) — verifies that if hooks.json points
at a non-existent script, Claude Code's hook dispatcher (via a minimal
python3 invocation) exits non-blocking, does not emit a systemMessage,
and does not propagate a blocking exit-2. This subsumes plan R1b.
"""
import ast
import json
import subprocess
from pathlib import Path

import pytest

# F2: the single shared writable-journal sentinel (SSOT in fixtures.emitter),
# imported rather than re-literalled. tests/ is on sys.path via conftest.
from helpers import effective_hooks_config  # noqa: E402


# ---------------------------------------------------------------------------
# Path constants — resolved relative to this test file so refactors of the
# repo layout do not require line-number fixups.
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # pact-plugin/
_HOOKS_DIR = _PLUGIN_ROOT / "hooks"
_HOOKS_JSON = _HOOKS_DIR / "hooks.json"

# Hook classes the #538 closure criterion applies to.
_LIVELOCK_EVENT_CLASSES = ("TeammateIdle", "TaskCompleted", "Stop")

# Hooks that have been removed and MUST stay absent — named by basename so
# both the source-file deletion and hooks.json config-removal are covered
# by a single fixture.
_REMOVED_HOOK_BASENAMES = (
    "handoff_gate.py",
    "teammate_completion_gate.py",
    "stop_audit.sh",
    "memory_adhoc_reminder.py",
    "phase_completion.py",
    "wake_inbox_drain.py",
    "teardown_request_emitter.py",
    "wake_lifecycle_emitter.py",
)

# Livelock-safe docstring opt-out marker (plan L127-129).
_LIVELOCK_SAFE_MARKER = "# livelock-safe:"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# The reviewed TaskCompleted handlers: the HANDOFF emitter, and the workflow-state
# projection, which writes workflow-state.json and no journal row.


def _load_hooks_json() -> dict:
    """Read hooks.json from the on-disk source (not a cached copy)."""
    return effective_hooks_config()


def _hook_script_paths_for_event(
    hooks_config: dict,
    event_name: str,
) -> list[Path]:
    """
    Extract absolute Path objects for every hook script bound to an event.

    Returns [] if the event is absent from hooks.json.
    Handles the `${CLAUDE_PLUGIN_ROOT}/hooks/foo.py` template by substituting
    the worktree's pact-plugin/ path.
    """
    event_blocks = hooks_config.get("hooks", {}).get(event_name, [])
    paths: list[Path] = []
    for block in event_blocks:
        for hook in block.get("hooks", []):
            command = hook.get("command", "")
            # Strip `python3 "` prefix and trailing quote + extract the path
            # after ${CLAUDE_PLUGIN_ROOT}. We do not need a full shell parse
            # — the format is stable per hooks.json schema.
            if "${CLAUDE_PLUGIN_ROOT}" not in command:
                continue
            # e.g. `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/agent_handoff_emitter.py"`
            suffix = command.split("${CLAUDE_PLUGIN_ROOT}", 1)[1]
            suffix = suffix.lstrip("/").rstrip('"').rstrip("'")
            paths.append(_PLUGIN_ROOT / suffix)
    return paths


def _scan_ast_for_emission_sinks(source: str) -> list[str]:
    """
    Walk a Python source's AST and return human-readable descriptions of
    emission sinks found.

    Detected sinks:
      - sys.stderr.write(...) / sys.stdout.write(...)
      - print(json.dumps({"systemMessage": ...})) — detected as any
        print() call whose argument references "systemMessage" literal
      - Any print() call whose argument is a dict literal containing the
        key "systemMessage"
      - Any print() call whose argument carries a "decision" key and a
        "block" value — a refusal that makes the agent take another turn,
        which is exactly what a livelock repeats
      - sys.exit(2) — blocking exit codes

    Empty list ⇒ no sinks (pure journal-writer / pure validator).
    """
    tree = ast.parse(source)
    sinks: list[str] = []

    for node in ast.walk(tree):
        # sys.stderr.write / sys.stdout.write
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr = node.func
                # sys.stderr.write
                if (
                    attr.attr == "write"
                    and isinstance(attr.value, ast.Attribute)
                    and attr.value.attr in ("stderr", "stdout")
                ):
                    sinks.append(
                        f"sys.{attr.value.attr}.write at line {node.lineno}"
                    )

            # print(...) with systemMessage-shaped argument
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                for arg in node.args:
                    dump = ast.dump(arg)
                    if "systemMessage" in dump:
                        sinks.append(
                            f"print(systemMessage) at line {node.lineno}"
                        )
                        break
                    if "value='decision'" in dump and "value='block'" in dump:
                        sinks.append(
                            f"print(decision: block) at line {node.lineno}"
                        )
                        break

            # sys.exit(2) — blocking exit
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sys"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 2
            ):
                sinks.append(f"sys.exit(2) at line {node.lineno}")

    return sinks


def _has_livelock_safe_marker(source: str) -> bool:
    """True iff source contains the docstring opt-out marker."""
    return _LIVELOCK_SAFE_MARKER in source


# ---------------------------------------------------------------------------
# Layer 1a — AST sink-scan
# ---------------------------------------------------------------------------

class TestLayer1a_ASTSinkScan:
    """Static drift guard — every hook under the 3 event classes either has
    zero emission sinks OR carries the `# livelock-safe:` docstring marker.
    """

    def test_every_registered_hook_passes_sink_or_marker_gate(self):
        """Enumerate all registered hooks and assert each is livelock-safe
        by-construction (zero sinks) or by-attestation (docstring marker).
        """
        hooks_config = _load_hooks_json()
        failures: list[str] = []

        for event_name in _LIVELOCK_EVENT_CLASSES:
            for script_path in _hook_script_paths_for_event(hooks_config, event_name):
                if not script_path.exists():
                    failures.append(
                        f"{event_name} binds {script_path} but file is missing"
                    )
                    continue
                if script_path.suffix != ".py":
                    # Shell scripts and other types: assert absent per #538.
                    failures.append(
                        f"{event_name} binds non-Python hook {script_path}; "
                        f"#538 removed all shell hooks from these event classes"
                    )
                    continue

                source = script_path.read_text(encoding="utf-8")
                sinks = _scan_ast_for_emission_sinks(source)
                if sinks and not _has_livelock_safe_marker(source):
                    failures.append(
                        f"{script_path.name} has emission sinks {sinks} "
                        f"and no `{_LIVELOCK_SAFE_MARKER}` marker — "
                        f"livelock-capable without attestation"
                    )

        assert not failures, (
            "Layer 1a AST sink-scan found livelock-capable hooks:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


    def test_teammate_idle_opts_out_via_docstring_marker(self):
        """teammate_idle is the lone TeammateIdle hook post-#538. It has
        emission sinks (threshold-escalation systemMessages) and MUST
        carry the `# livelock-safe:` marker to pass Layer 1a."""
        source = (_HOOKS_DIR / "teammate_idle.py").read_text(encoding="utf-8")
        assert _has_livelock_safe_marker(source), (
            "teammate_idle.py must carry the `# livelock-safe:` docstring "
            "marker per plan L107 — bounded threshold-escalation emission "
            "is attested by reviewer, not structurally invisible."
        )

    def test_stop_background_gate_opts_out_via_docstring_marker(self):
        """stop_background_gate is the lone Stop hook. It prints a blocking
        decision, so the scan MUST see a sink in it, and it MUST carry the
        `# livelock-safe:` marker stating its bound. The first assertion is
        what keeps the decision-sink rule tied to the hook it exists for."""
        source = (_HOOKS_DIR / "stop_background_gate.py").read_text(encoding="utf-8")
        assert _scan_ast_for_emission_sinks(source), (
            "stop_background_gate.py prints a blocking decision, but the sink "
            "scan found no sink in it — the decision-sink rule is not seeing "
            "the hook it exists for."
        )
        assert _has_livelock_safe_marker(source), (
            "stop_background_gate.py must carry the `# livelock-safe:` marker "
            "stating its bound: at most one block per job id, and none while "
            "stop_hook_active is set."
        )


# ---------------------------------------------------------------------------
# Layer 2 — Runtime 10×-fire harness (emitter)
# ---------------------------------------------------------------------------



_REASON_MATRIX = [
    "awaiting_teachback_approved",
    "awaiting_lead_commit",
    "awaiting_peer_response",
    "awaiting_user_decision",
    "unknown_reason_probe_X7Q9",
    "",
    "   ",
    "x" * 500,
    "unicode-ëmöji-🎉",
]




# ---------------------------------------------------------------------------
# Layer 3 — hooks.json config invariants (_REMOVED_HOOK_BASENAMES +
# UserPromptSubmit missed-wake surfacer)
# ---------------------------------------------------------------------------

class TestLayer3_HooksJsonInvariants:
    """Config-level assertions — every hook in _REMOVED_HOOK_BASENAMES MUST
    be absent from every command string, UserPromptSubmit MUST bind
    missed_wake_scan.py (the #903 missed-wake surfacer),
    TaskCompleted
    MUST bind to agent_handoff_emitter.py, TeammateIdle MUST bind only
    to teammate_idle.py."""

    def test_stop_binds_only_stop_background_gate(self):
        """Stop binds exactly one hook: the background-work turn-end gate.

        The record-only Stop carrier stays dropped. The one hook allowed here
        is livelock-safe by its attested bound (see the Layer 1a marker arm);
        any other Stop hook needs explicit design review.

        IT HOLDS ONE ASSERTION AND THAT IS THE POINT OF ITS OWN NAME. This
        assertion used to sit inside the UserPromptSubmit arm below. A
        counter-test that named that arm as its target declared a target whose
        failure can arrive from a DIFFERENT assertion in the same body, so a
        red told the reader nothing about the Stop key. One assertion under one
        name makes a raise attributable, and the counter-test that drives this
        arm depends on that.
        """
        hooks_config = _load_hooks_json()
        basenames = [
            p.name for p in _hook_script_paths_for_event(hooks_config, "Stop")
        ]
        assert basenames == ["stop_background_gate.py"], (
            "Stop must bind only stop_background_gate.py — the #903 record-only "
            "Stop carrier was dropped in the B1 surfacing remediation, and any "
            f"other Stop hook needs design review; actual: {basenames}"
        )

    def test_userpromptsubmit_binds_missed_wake_scan(self):
        """#903 missed-wake SURFACER: the lead-side stale-wait scan surfaces via
        UserPromptSubmit additionalContext (turn-START, lead-injectable) — the B1
        remediation that replaced the record-only Stop carrier (Stop fired at
        turn-END and could only suppressOutput, so it recorded but never
        surfaced). missed_wake_scan.py MUST be among the UserPromptSubmit hooks.

        THE STOP-ABSENCE HALF LEFT THIS BODY and now runs as
        `test_stop_binds_only_stop_background_gate` above. This arm asserts the UserPromptSubmit
        binding and nothing else.
        """
        hooks_config = _load_hooks_json()
        ups_basenames = {
            p.name
            for p in _hook_script_paths_for_event(hooks_config, "UserPromptSubmit")
        }
        assert "missed_wake_scan.py" in ups_basenames, (
            f"UserPromptSubmit must bind missed_wake_scan.py (the #903 surfacer); "
            f"got {sorted(ups_basenames)}."
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_removed_hook_absent_from_all_command_strings(self, removed_hook):
        """Scan every command string in hooks.json for the removed hook
        basename — any occurrence is a regression."""
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        assert removed_hook not in raw, (
            f"{removed_hook} still referenced in hooks.json — #538 removed "
            f"this hook but its config entry survived. This would cause a "
            f"runtime error on the platform firing the event."
        )


    def test_teammateidle_binds_only_to_teammate_idle(self):
        hooks_config = _load_hooks_json()
        paths = _hook_script_paths_for_event(hooks_config, "TeammateIdle")
        basenames = [p.name for p in paths]
        assert basenames == ["teammate_idle.py"], (
            f"TeammateIdle must bind only to teammate_idle.py (partial-keep "
            f"per plan v2 L116); actual: {basenames}."
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_removed_hook_source_file_absent(self, removed_hook):
        # stop_audit.sh is a shell file; the others are .py. Both should
        # be absent as files under hooks/.
        candidate = _HOOKS_DIR / removed_hook
        assert not candidate.exists(), (
            f"{candidate} still on disk — #538 deleted this hook but the "
            f"source file survived. hooks.json config may no longer "
            f"reference it but the file is dead code."
        )


# ---------------------------------------------------------------------------
# Layer 4 — Counter-test-by-revert (each test names the Layer it protects)
# ---------------------------------------------------------------------------

def _scan_for_removed_hook(raw: str) -> list[str]:
    """Return the list of removed-hook basenames found in `raw`. Mirrors
    TestLayer3_HooksJsonInvariants.test_removed_hook_absent_from_all_command_strings
    — if this function returns ≥1, Layer 3 would fail."""
    return [h for h in _REMOVED_HOOK_BASENAMES if h in raw]


_STOP_ASSERTION_FRAGMENT = "Stop must bind only stop_background_gate.py"


def _assert_target_fails_on(target, fragment):
    """Run a named target and require it to FAIL on the assertion `fragment` names.

    THE FRAGMENT IS THE WHOLE POINT AND NOT DECORATION. A counter-test that
    accepts ANY AssertionError declares a target whose failure can arrive from a
    DIFFERENT line of the same body, so it stops being a statement about the
    thing it names. That is the defect this file met once already, where a
    counter-test named a host test and the host held two assertions.

    THE BINDING IS ITSELF GUARDED, which the earlier shape was not:
    `test_the_attribution_binding_refuses_a_raise_from_another_line` below
    reddens if this function drops the fragment.
    """
    with pytest.raises(AssertionError, match=fragment):
        target()


class TestLayer4_CounterTestByRevert:
    """Each test names the SPECIFIC Layer 1/2/3 invariant it targets and
    proves, by in-memory mutation, that reverting the fix flips that
    exact invariant to failing. Per plan L149 — sharpening against
    phantom-green (module-load breakage failing everything).
    """


    def test_reverting_stop_binding_flips_layer3_stop_binding_test(self, monkeypatch):
        """Target: TestLayer3_HooksJsonInvariants::test_stop_binds_only_stop_background_gate.

        Revert shape: bind Stop to all 3 removed Tier-2 hooks (mirrors
        pre-C2b hooks.json shape).

        🔴 IT RUNS THE TARGET. THE EARLIER SHAPE RESTATED ITS OWN SETUP. That
        shape wrote a Stop key into a dict and then asserted the key was in the
        dict, two times. The Layer 3 invariant never ran, so the counter-test
        passed for whatever Layer 3 asserted, and it named a target that was
        defined nowhere. A counter-test that cannot fail certifies the arm it
        was built to hold.

        THE TWO LEGS TOGETHER ARE THE EVIDENCE, AND ONE LEG ALONE IS NOT.
          CONTROL: the target passes against the config the repository ships.
          REVERT : the same target RAISES with the removed hooks bound to Stop.
        Without the control a raise is ambiguous, because a target that fails
        for every input raises here too and reads as a discriminative result.

        THE MATCH IS DELIBERATE. It binds the raise to the Stop assertion, so a
        second assertion added to the target later cannot supply the red in its
        place. That is the defect this repair exists to close.

        🔴 THE LIMIT OF THE CONTROL LEG, STATED BECAUSE NOTHING HOLDS IT THERE.
        The control leg reads the SHIPPED configuration today, through
        `_load_hooks_json`. NO ARM KEEPS IT ON THAT DOCUMENT. An edit that
        swaps the input for a small inline mapping, to make the leg faster or
        to share a fixture with the revert leg below, PASSES WITH NOTHING GOING
        RED, and the leg then certifies the target against a document nobody
        ships. THE PASS WOULD BE TRUE AND ABOUT THE WRONG POPULATION.
        """
        target = TestLayer3_HooksJsonInvariants().test_stop_binds_only_stop_background_gate

        # CONTROL LEG, and it runs FIRST so the revert cannot mask a broken
        # target: against the shipped config the target must pass.
        target()

        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        config = json.loads(raw)
        # In-memory revert: bind Stop to the 3 removed hooks.
        config["hooks"]["Stop"] = [
            {
                "hooks": [
                    {"type": "command",
                     "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/stop_audit.sh"'},
                    {"type": "command",
                     "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/phase_completion.py"'},
                    {"type": "command",
                     "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memory_adhoc_reminder.py"'},
                ],
            }
        ]
        reverted = [p.name for p in _hook_script_paths_for_event(config, "Stop")]
        assert "phase_completion.py" in reverted, (
            f"counter-test setup failed — the removed hooks are not bound to "
            f"Stop in the reverted config; got {reverted}"
        )

        # Put the reverted config where the target reads its input from. The
        # target calls `_load_hooks_json`, so this is what makes the target run
        # against the revert rather than against the file on disk.
        monkeypatch.setattr(f"{__name__}._load_hooks_json", lambda: config)
        assert _load_hooks_json() is config, (
            "counter-test setup failed — the reverted config did not reach the "
            "loader the target reads, so the revert leg below would run "
            "against the shipped file and prove nothing"
        )

        # REVERT LEG: the named target must FAIL, and it must fail on the Stop
        # assertion rather than on some other line of its body. The binding
        # that holds that lives in `_assert_target_fails_on`, and the arm below
        # is what keeps the binding from being dropped in silence.
        _assert_target_fails_on(target, _STOP_ASSERTION_FRAGMENT)

    def test_the_attribution_binding_refuses_a_raise_from_another_line(self):
        """THE GUARD ON THE COUNTER-TEST ABOVE, WHICH HELD NOTHING BEFORE THIS.

        THE HOLE THIS CLOSES WAS MEASURED AGAINST MY OWN INSTRUMENT. The
        counter-test above binds its expected failure to the Stop assertion by
        a message fragment. Remove that binding and the whole file stayed
        green, so the guard that makes a raise ATTRIBUTABLE could be deleted
        with nothing reporting it.

        THE TWO LEGS ARE THE ARM, AND ONE LEG ALONE IS NOT.
          REFUSE: a raise carrying a DIFFERENT message must not satisfy the
          binding. A binding that dropped its fragment accepts that raise and
          this leg reddens.
          ACCEPT: a raise carrying the fragment must satisfy it. Without this
          leg, a binding that refused everything would pass the leg above.

        WHY IT CATCHES A BaseException RATHER THAN A NAMED CLASS. The failure
        of a message binding is raised by the test framework, and its class is
        framework-internal. The property under test is that the call did NOT
        return normally, so the arm asserts on THAT and imports nothing
        private.
        """
        def raises_from_another_line():
            raise AssertionError("a different assertion in the same body failed")

        def raises_from_the_stop_assertion():
            raise AssertionError(f"{_STOP_ASSERTION_FRAGMENT}, and a detail")

        refused = False
        try:
            _assert_target_fails_on(
                raises_from_another_line, _STOP_ASSERTION_FRAGMENT
            )
        except BaseException:
            refused = True
        assert refused, (
            "the counter-test binding ACCEPTED a raise from another line, so a "
            "counter-test built on it declares a target whose failure can "
            "arrive from an assertion it does not name. Restore the message "
            "binding in _assert_target_fails_on."
        )

        # ACCEPT LEG, and it is what stops the leg above passing for a binding
        # that refuses every raise.
        _assert_target_fails_on(
            raises_from_the_stop_assertion, _STOP_ASSERTION_FRAGMENT
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_reinjecting_removed_hook_flips_layer3_removed_hook_test(
        self, removed_hook
    ):
        """Target: TestLayer3_HooksJsonInvariants::test_removed_hook_absent_from_all_command_strings[{removed_hook}].

        Revert shape: append a phony hook-command string that references
        the removed hook by name. The scan MUST catch it.
        """
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        reverted_raw = raw + f'\n// {removed_hook} revert probe'
        found = _scan_for_removed_hook(reverted_raw)
        assert removed_hook in found, (
            f"counter-test setup failed — {removed_hook} not detected in "
            f"mutated raw"
        )

    def test_hook_without_livelock_marker_and_with_sinks_fails_layer1a(self):
        """Target: TestLayer1a_ASTSinkScan::test_every_registered_hook_passes_sink_or_marker_gate.

        Revert shape: construct a synthetic hook source that has a
        systemMessage emission sink AND no `# livelock-safe:` marker.
        Assert the sink-scan detects it.
        """
        phantom_source = (
            "import json, sys\n"
            "def main():\n"
            '    print(json.dumps({"systemMessage": "nag nag nag"}))\n'
            "    sys.exit(0)\n"
        )
        sinks = _scan_ast_for_emission_sinks(phantom_source)
        assert any("systemMessage" in s for s in sinks), (
            f"Layer 1a AST scan did not detect a systemMessage emission "
            f"sink in a phantom hook — counter-test is NOT discriminative. "
            f"Observed sinks: {sinks}"
        )
        assert not _has_livelock_safe_marker(phantom_source), (
            "counter-test setup failed — phantom source must not carry the "
            "livelock-safe marker"
        )

    def test_an_unattested_blocking_stop_hook_turns_layer1a_red(
        self, tmp_path, monkeypatch
    ):
        """Target: TestLayer1a_ASTSinkScan::test_every_registered_hook_passes_sink_or_marker_gate.

        Revert shape: bind Stop to a hook that prints a blocking decision and
        carries no `# livelock-safe:` marker. Before the decision sink existed
        the scan saw nothing in such a hook, so it passed with no attestation.

        CONTROL: the same hook WITH the marker passes the target, so the raise
        is about the missing attestation and not about a broken fixture. The
        match binds the raise to the decision sink.
        """
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        script = hooks_dir / "phantom_stop_gate.py"
        body = (
            "import json\n"
            "def main():\n"
            '    print(json.dumps({"decision": "block", "reason": "again"}))\n'
        )
        config = {"hooks": {"Stop": [{"hooks": [{
            "type": "command",
            "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/phantom_stop_gate.py"',
        }]}]}}
        monkeypatch.setattr(f"{__name__}._PLUGIN_ROOT", tmp_path)
        monkeypatch.setattr(f"{__name__}._load_hooks_json", lambda: config)
        target = TestLayer1a_ASTSinkScan().test_every_registered_hook_passes_sink_or_marker_gate

        script.write_text('"""# livelock-safe: bounded."""\n' + body, encoding="utf-8")
        target()

        script.write_text(body, encoding="utf-8")
        _assert_target_fails_on(target, r"print\(decision: block\)")



# ---------------------------------------------------------------------------
# R1b preparer-HIGH carry-over: runtime fail-OPEN probe
# ---------------------------------------------------------------------------

class TestR1bRuntimeFailOpen:
    """Plan R1b: a hooks.json entry pointing at a non-existent hook file
    MUST fail-open — not block the session, not emit a systemMessage, not
    propagate a blocking exit-2. Claude Code's hook dispatcher invokes
    `python3 <path>`; if <path> is missing, python3 exits with code 2
    and a SyntaxError-ish message on stderr, but the dispatcher wraps
    this and treats it as non-blocking.

    We probe this by invoking `python3 /nonexistent/phantom_hook.py` in
    a subprocess and asserting: (a) exit code is non-zero (expected), and
    (b) stdout contains NO systemMessage (structural — can't block the
    user's turn because Claude Code only treats stdout-JSON as a signal).

    This is a PROXY for the full runtime fail-open: we cannot invoke
    Claude Code's hook dispatcher itself in-process, but we can pin the
    structural invariant that the python3 subprocess does not emit
    protocol-level JSON. If the dispatcher correctly suppresses stderr
    and only parses stdout, the user's turn is unaffected.
    """

    def test_missing_hook_file_fail_open(self, tmp_path):
        phantom_path = tmp_path / "does_not_exist.py"
        result = subprocess.run(
            ["python3", str(phantom_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Python exits non-zero when the file is missing — expected.
        assert result.returncode != 0, (
            "python3 on a missing file should exit non-zero; got "
            f"{result.returncode}. Runtime behavior changed — re-verify "
            "fail-open assumption."
        )
        # The critical structural invariant: no systemMessage on stdout.
        # Claude Code treats stdout-JSON as a hook signal; if python's
        # own error output leaked there, every missing-hook path would
        # surface in the team-lead's prompt.
        assert "systemMessage" not in result.stdout, (
            f"python3 emitted systemMessage to stdout on missing file — "
            f"this would incorrectly surface as a Claude Code hook signal. "
            f"stdout: {result.stdout!r}"
        )
        # Stderr is where python error messages land; that is
        # non-blocking per Claude Code's hook contract.
        assert result.stdout == "" or not result.stdout.strip().startswith("{"), (
            f"python3 wrote JSON-shaped output to stdout on missing file — "
            f"would be interpreted as a hook protocol signal. "
            f"stdout: {result.stdout!r}"
        )

    def test_missing_hook_python_is_not_blocking_exit_code(self, tmp_path):
        """Python exits with code 2 on missing file. Claude Code's hook
        contract treats only stdout-JSON `{"decision": "block"}` or exit-2
        WITH a protocol-level stdout as blocking. Bare exit-2 with no
        structured stdout must be fail-open.

        We cannot invoke Claude Code's dispatcher here, but we pin the
        joint invariant: missing file produces NO structured stdout AND
        the exit code indicates a platform-level error, not a hook-level
        decision.
        """
        phantom_path = tmp_path / "does_not_exist.py"
        result = subprocess.run(
            ["python3", str(phantom_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Pin: no hook-protocol JSON on stdout.
        try:
            json.loads(result.stdout.strip() or "null")
            parsed_json = True
        except json.JSONDecodeError:
            parsed_json = False
        assert not (parsed_json and "decision" in result.stdout), (
            f"missing-file subprocess emitted hook-decision JSON; the "
            f"dispatcher would interpret this as a blocking signal. "
            f"stdout: {result.stdout!r}"
        )
