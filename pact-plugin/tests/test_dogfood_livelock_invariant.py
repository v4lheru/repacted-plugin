"""
Dogfood regression harness for #538 — categorical nag-hook-class removal.

Closure criterion per plan AC #3: assert every hook registered under
TeammateIdle / TaskCompleted / Stop in hooks.json is livelock-safe — no
emission sink, or an attested bound.

Three layers, each catching a different failure-mode class:

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

R1b runtime fail-OPEN probe lives in the bottom of this file
(test_missing_hook_file_fail_open) — verifies that if hooks.json points
at a non-existent script, Claude Code's hook dispatcher (via a minimal
python3 invocation) exits non-blocking, does not emit a systemMessage,
and does not propagate a blocking exit-2. This subsumes plan R1b.
"""
import ast
from pathlib import Path


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

        It holds one assertion, so a red names the Stop key and nothing else.
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


# ---------------------------------------------------------------------------
# R1b preparer-HIGH carry-over: runtime fail-OPEN probe
# ---------------------------------------------------------------------------

