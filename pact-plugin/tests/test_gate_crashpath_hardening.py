"""
Crash/error-render path hardening coverage for the two gate hooks
(task_lifecycle_gate.py = tlg, bootstrap_gate.py = bg).

Sibling file to test_task_lifecycle_gate.py / test_bootstrap_gate.py /
test_task_lifecycle_gate_degraded.py. It exercises four pre-existing,
non-reachable defense-in-depth items that the primary suites prove "do
not regress" but do NOT exercise on their new crash-path branches:

  item 1 — bg deny render routed through the bounded/sanitizing
           _bounded_error_text (T1).
  item 2 — every stdin read in both hooks capped at _STDIN_READ_MAX so an
           over-cap frame truncates into the already-handled
           JSONDecodeError disposition instead of an unbounded slurp (T2).
  item 3 — the 5 post-floor stderr diagnostic prints wrapped so a
           BrokenPipeError on the debug channel cannot propagate past
           sys.exit and flip the intended exit code (T3).
  item 4 — _bounded_error_text reordered to truncate-FIRST so the
           isprintable sanitize join runs on a <=cap slice (MemoryError-
           safe), kept byte-identical across both hooks (T4a + T4b).

NON-VACUITY DISCIPLINE (central to this file): every test targeting a
defense-in-depth guard carries an in-line proof that it would FAIL if the
guard were reverted/neutered — otherwise a transparent guard makes the
test pass trivially. The proofs are in-process (a guard-stripped stand-in
or a precondition-fired counter) so no source mutation is needed; the
per-test vacuity evidence is recorded in the HANDOFF.

These are non-reachable paths: the emitters fire only on a broken install
or a latent gate-logic bug, so the tests invoke the emitter/helper
DIRECTLY rather than through a natural runtime path. For item 4's
MemoryError safety we assert OBSERVABLE consequences (correct truncation,
unchanged exact strings, twin byte-identity) — never a multi-GB
allocation.
"""

import io
import json
import sys

import pytest

import bootstrap_gate as bg  # noqa: E402

# Session-setup + frame helpers reused from the bg suite (the established
# single-source idiom, mirroring test_task_lifecycle_gate_degraded.py's reuse
# of the same module). Used by the bg primary-read cap test so an uncapped
# revert produces a behaviorally-distinct deny rather than a symbol error.
from tests.test_bootstrap_gate import (  # noqa: E402
    _make_input,
    _setup_pact_session,
)


# =============================================================================
# Helpers
# =============================================================================


class _BrokenStderr:
    """A stderr stand-in whose write/flush raise BrokenPipeError, counting
    how many times a write was actually attempted.

    The counter is the non-vacuity precondition for the T3 tests: if
    ``write_calls == 0`` the diagnostic print never ran, so a green exit
    code proves nothing about the guard. Each T3 test asserts the writer
    actually raised (``write_calls >= 1``) AND the intended exit code was
    still reached.
    """

    def __init__(self):
        self.write_calls = 0

    def write(self, *a, **k):
        self.write_calls += 1
        raise BrokenPipeError("simulated closed stderr pipe")

    def flush(self, *a, **k):
        raise BrokenPipeError("simulated closed stderr pipe")

    def isatty(self):
        return False


def _drive_with_broken_stderr(fn, *args):
    """Call ``fn(*args)`` with sys.stderr swapped for a _BrokenStderr and
    sys.stdout captured. Returns (exit_code, stderr_write_calls, stdout_text,
    escaped_exc_or_None).

    ``exit_code`` is the SystemExit code, or the sentinel "NO-EXIT" if the
    callee returned without exiting, or the escaped exception's type name if
    one propagated. Restores sys.stderr/sys.stdout in a finally so a raise
    never leaks the swap into sibling tests (worker-pollution guard).
    """
    bs = _BrokenStderr()
    buf = io.StringIO()
    old_err, old_out = sys.stderr, sys.stdout
    sys.stderr = bs
    sys.stdout = buf
    code = "NO-EXIT"
    escaped = None
    try:
        try:
            fn(*args)
        except SystemExit as e:
            code = e.code
        except BaseException as e:  # noqa: BLE001 — test must observe an escape
            escaped = type(e).__name__
    finally:
        sys.stderr, sys.stdout = old_err, old_out
    return code, bs.write_calls, buf.getvalue(), escaped


# =============================================================================
# T1 — item 1: bg deny render routed through _bounded_error_text
#
# The deny path (_emit_load_failure_deny) historically interpolated
# f"{type(error).__name__}: {error}" raw into permissionDecisionReason; item
# 1 routes it through _bounded_error_text so a long or non-printable message
# is bounded + sanitized. The degraded path already had this coverage
# (test_bootstrap_gate.py:test_degraded_warning_bounds_error_text); T1 is the
# deny-path mirror.
# =============================================================================


class TestDenyRenderBounding:

    def test_deny_render_truncates_over_cap_message(self, capsys):
        """A >cap deny message is truncated with the explicit marker, the
        over-cap payload never appears in full in permissionDecisionReason,
        and the deliberate exit-2 (blocking) path is preserved.

        NON-VACUITY: if item 1 were reverted (raw
        f"{type(error).__name__}: {error}" render), the full 1000-char
        payload WOULD appear in the reason and "...[truncated]" would NOT —
        the two assertions below invert. The full text still reaches the
        separate stderr render (debug channel), so its presence there is not
        evidence of a bounding failure.
        """
        payload = "X" * 1000
        with pytest.raises(SystemExit) as exc:
            bg._emit_load_failure_deny("module imports", RuntimeError(payload))
        assert exc.value.code == 2, "deny must stay exit-2 (blocking)"
        out = json.loads(capsys.readouterr().out.strip())
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "...[truncated]" in reason, (
            "deny render must route through the bounding helper (item 1); a "
            f"raw render would omit the truncation marker: {reason!r}"
        )
        assert "X" * (bg._ERROR_TEXT_MAX + 1) not in reason, (
            "the over-cap payload must not appear in full in the deny reason"
        )
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_render_sanitizes_non_printable_chars(self, capsys):
        """Control/escape characters in the deny message are sanitized to
        spaces before reaching the context-bound permissionDecisionReason.

        NON-VACUITY: a raw render (item 1 reverted) would interpolate the
        \\x07/\\x1b bytes verbatim into the reason — the two "not in"
        assertions below would then fail. Exit-2 is preserved either way, so
        the sanitize assertions, not the exit code, carry the signal.
        """
        payload = "boom\x07\x1b[31mINJECT\ntail"
        with pytest.raises(SystemExit) as exc:
            bg._emit_load_failure_deny("runtime", RuntimeError(payload))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        out = json.loads(captured.out.strip())
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "\x07" not in reason and "\x1b" not in reason, (
            "control/escape chars must be sanitized out of the deny reason "
            f"(item 1 bounding helper): {reason!r}"
        )
        # The type name still survives the sanitize (printable) — parity with
        # the degraded path's bounded render.
        assert "RuntimeError" in reason
        # Full text (incl. the control bytes) still goes to the stderr debug
        # channel — the call site's separate full-text render, intentionally
        # NOT sanitized — so the bounding above is confirmed to be the deny
        # reason's behavior specifically, not a global strip.
        assert "\x07" in captured.err, (
            "full unsanitized text must still reach the stderr debug channel"
        )


# =============================================================================
# T2 — item 2: every stdin read capped at _STDIN_READ_MAX
#
# Three sites: tlg main() primary read, bg main() primary read, bg degraded
# import-stage read (_read_stdin_tool_name). An over-cap frame truncates
# mid-JSON -> JSONDecodeError -> the read site's EXISTING except (fail-open
# suppress on the two primaries; fail-closed deny on the degraded read). The
# over-cap frame is otherwise VALID JSON, so the failure is the cap, not
# malformed input — each test proves this by confirming an uncapped read of
# the SAME frame parses successfully (the reverted-item-2 behavior).
# Mirrors test_task_lifecycle_gate_degraded.py:402 (the late-read cap).
# =============================================================================


def _over_cap_frame(cap, base=None):
    """A syntactically-valid JSON frame padded just past ``cap`` so a
    sys.stdin.read(cap) truncates it mid-string -> JSONDecodeError, while an
    uncapped json.load(sys.stdin) of the same text parses cleanly."""
    frame = dict(base or {})
    # Pad past the cap; the pad lands inside a string literal, so a cap-length
    # prefix is invalid JSON (unterminated string), but the FULL text is valid.
    frame["pad"] = "a" * (cap + 1024)
    return json.dumps(frame)


def _run_main_with_stdin(mod, stdin_text):
    """Drive ``mod.main()`` with stdin_text on sys.stdin; capture stdout.
    Returns (exit_code, stdout_text). Restores stdin/stdout in finally."""
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout = io.StringIO()
    code = "NO-EXIT"
    try:
        try:
            mod.main()
        except SystemExit as e:
            code = e.code
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return code, out


class TestStdinCaps:

    # A pact-* work TaskCreate missing variety metadata. If the over-cap
    # frame were PARSED (item 2 reverted), this shape fires the
    # variety_missing_on_dispatch_task advisory (output gains
    # additionalContext) — a distinct observable from the bare suppress the
    # capped read produces. Used by the tlg primary-read test so the
    # disposition discriminates capped-vs-uncapped, not merely "exit 0".
    # NOTE: the variety is PRESENT-but-malformed (missing novelty_rationale),
    # which is the R4 arm RETAINED after the #865 surgical split — a bare-absent
    # variety no longer fires R4 (its enforcement moved to the wiring-write
    # gate), so the advisory-bearing frame must carry a malformed-present stamp.
    _ADVISORY_BEARING_BASE = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {
                "variety": {
                    "novelty": 3,
                    # novelty_rationale intentionally omitted → malformed-present
                    "scope": 3, "scope_rationale": "x",
                    "uncertainty": 3, "uncertainty_rationale": "x",
                    "risk": 3, "risk_rationale": "x",
                    "total": 12,
                },
            },
        },
        "tool_response": {},
        "session_id": "s",
    }


    def test_bg_main_over_cap_frame_fails_open_no_hang(
        self, monkeypatch, tmp_path,
    ):
        """bg main()'s primary read is capped identically: an over-cap frame
        -> JSONDecodeError -> _SUPPRESS_OUTPUT at exit 0 (fail-open; the
        input side is the harness's domain).

        NON-VACUITY (discriminating): the frame is a blocked Edit on a lead +
        no-marker session (a real session is set up so the gate actually
        evaluates). Under the capped read it truncates -> bare suppress at
        exit 0. Under an UNCAPPED read (item 2 reverted) the SAME frame parses
        and reaches the blocking deny at exit 2 (Edit before bootstrap) — a
        different exit code AND a deny JSON, so the `== suppress` + `code == 0`
        assertions FAIL under revert. The in-bounds control proves the Edit
        genuinely denies when parsed.
        """
        cap = bg._STDIN_READ_MAX
        base = _make_input(tool_name="Agent")  # lead frame, blocked tool (Agent only since 1.4.1)
        text = _over_cap_frame(cap, base)
        assert json.loads(text)["tool_name"] == "Agent"

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        code, out = _run_main_with_stdin(bg, text)
        assert code == 0, (
            "over-cap frame must truncate to the fail-open suppress branch; "
            "an uncapped read would parse the Edit and deny at exit 2"
        )
        assert json.loads(out.strip()) == json.loads(bg._SUPPRESS_OUTPUT)

        # In-bounds control: the SAME Edit frame WITHOUT the pad parses and
        # DOES deny at exit 2 — proving the over-cap suppress is the cap's
        # doing, not an always-suppress gate.
        code2, out2 = _run_main_with_stdin(bg, json.dumps(base))
        assert code2 == 2, "in-bounds blocked Agent must deny at exit 2"
        assert json.loads(out2.strip())["hookSpecificOutput"][
            "permissionDecision"
        ] == "deny"

    def test_bg_degraded_read_over_cap_frame_fails_closed(self):
        """bg's degraded import-stage read (_read_stdin_tool_name) is capped:
        an over-cap frame truncates -> the except returns None -> the caller
        treats None as a fail-CLOSED deny (an unverifiable frame cannot be
        confirmed read-only). This is the crispest non-vacuity discriminator
        in T2.

        NON-VACUITY: with item 2 reverted (uncapped json.load), the SAME
        valid frame parses and the function returns the real tool_name
        ('Read') instead of None — an observable flip. Both branches are
        asserted: the over-cap read -> None, and a sanity in-bounds read ->
        the real name (so a function that ALWAYS returned None would also
        fail).
        """
        cap = bg._STDIN_READ_MAX
        text = _over_cap_frame(cap, {"tool_name": "Read"})
        # Reverted-item-2 control: the full frame is valid and names a tool.
        assert json.loads(text)["tool_name"] == "Read"

        old_in = sys.stdin
        sys.stdin = io.StringIO(text)
        try:
            result = bg._read_stdin_tool_name()
        finally:
            sys.stdin = old_in
        assert result is None, (
            "over-cap frame must truncate to a JSONDecodeError -> None "
            "(fail-closed deny); an uncapped read would return 'Read'"
        )

        # Sanity (guards an always-None regression): an in-bounds frame still
        # returns the real tool name.
        old_in = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"tool_name": "Read"}))
        try:
            assert bg._read_stdin_tool_name() == "Read"
        finally:
            sys.stdin = old_in




# =============================================================================
# T3 — item 3: the 5 post-floor stderr diagnostic prints are guarded so a
# BrokenPipeError on the (best-effort) debug channel cannot propagate past
# sys.exit and flip the intended exit code.
#
# 5 sites:
#   bg:119  _emit_load_failure_deny       -> deny floor printed, then exit 2
#   bg:289  _emit_degraded_warning        -> defer/ask floor printed, exit 0
#   tlg:263 _emit_load_failure_advisory   -> advisory floor printed, exit 0
#   tlg:187 _emit_gate_health_event       -> journal-skip branch print
#   tlg:201 _emit_gate_health_event       -> the except-handler's OWN print
#                                            (the genuinely-escaping site —
#                                            NOT inside the enclosing guard)
#
# The three emitter sites are driven directly. The two gate_health sites are
# reached through _emit_load_failure_advisory's "bonus LAST" call (the
# emitter has no sys.exit of its own — the exit code it protects belongs to
# its caller). Every test asserts the BrokenStderr writer ACTUALLY raised
# (write_calls >= 1) — without that precondition a green exit proves nothing.
# =============================================================================


class TestStderrBrokenPipeGuards:

    def test_bg_deny_stderr_brokenpipe_preserves_exit_2(self):
        """T3-4 (bg:119). A BrokenPipeError on the deny-path stderr
        diagnostic must NOT flip the deliberate blocking exit 2 — for
        PreToolUse a nonzero-non-2 exit is non-blocking (fail-OPEN), so a
        leaked BrokenPipeError would silently let the denied tool PROCEED.

        NON-VACUITY: the writer raised (write_calls >= 1) AND exit stayed 2
        AND the deny floor printed first. With the guard removed the raised
        BrokenPipeError would propagate before sys.exit(2), so exit would be
        the sentinel "NO-EXIT"/an escaped exc — the assertion inverts.
        """
        code, writes, out, escaped = _drive_with_broken_stderr(
            bg._emit_load_failure_deny, "runtime", RuntimeError("boom")
        )
        assert writes >= 1, "stderr writer must have been exercised (precondition)"
        assert escaped is None, f"a raise escaped the guard: {escaped}"
        assert code == 2, "deny must still reach blocking exit 2"
        floor = json.loads(out.strip())
        assert floor["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_bg_degraded_stderr_brokenpipe_preserves_exit_0(self):
        """T3-5 (bg:289). A BrokenPipeError on the degraded-warning stderr
        diagnostic must NOT flip exit 0 — stdout JSON is honored only on
        exit 0, so a leak would retroactively VOID the defer/ask decision.

        NON-VACUITY: writer raised AND exit stayed 0 AND the defer floor
        printed. Guard removed -> propagated raise before sys.exit(0).
        """
        code, writes, out, escaped = _drive_with_broken_stderr(
            bg._emit_degraded_warning, "runtime", RuntimeError("boom"), "Read"
        )
        assert writes >= 1, "stderr writer must have been exercised (precondition)"
        assert escaped is None, f"a raise escaped the guard: {escaped}"
        assert code == 0, "degraded defer/ask must still reach exit 0"
        floor = json.loads(out.strip())
        assert floor["hookSpecificOutput"]["permissionDecision"] == "defer"





# =============================================================================
# T4a — item 4: truncate-FIRST reorder is output-identical to the old
# sanitize-then-truncate order.
#
# The reorder captures `truncated` from the pre-slice length, slices to cap,
# runs the isprintable sanitize on the <=cap slice (so the O(n) join never
# touches a multi-GB tail -> MemoryError-safe), then appends the marker.
# Because the sanitize is a position-preserving per-char map, S(r)[:cap] ==
# S(r[:cap]) -> identical observable output. We assert the exact bytes for
# both an in-bounds and an out-of-bounds input. MemoryError-safety itself is
# asserted via OBSERVABLE consequences (correct truncation + the structural
# guarantee that the join runs on a bounded slice), never a multi-GB
# allocation.
# =============================================================================




# =============================================================================
# T4b — THE LOAD-BEARING ADDITION: cross-file source-equality drift test.
#
# Converts the "_bounded_error_text must stay byte-identical across the two
# hooks" CONVENTION (today enforced only by reviewer discipline — no CI test
# exists) into a CI-enforced INVARIANT. Compares FULL source including the
# docstring (inspect.getsource directly, NOT a logic-only body strip),
# because the issue's constraint is literally "keep _bounded_error_text
# byte-identical" and the two docstrings ARE currently identical. Mirrors
# test_working_memory_twin_drift.py:TestFileLockTwinCopyDrift (which strips docstrings only
# because ITS twins live in differently-commented modules — not the case
# here).
# =============================================================================


