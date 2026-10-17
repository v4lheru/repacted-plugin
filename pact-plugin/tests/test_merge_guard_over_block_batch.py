"""
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Batch-level TEST-phase certification for the merge-guard over-block batch
(#1064 lease-push fold + presence-bind, #1077 httpie un-gating, #1078 cross-leg
flag-leak union-arm anchoring).

These are the TEST-phase ADDITIONS on top of the per-lane verification suites —
they cover the composition surface the single-lane tests do not:
  * faithful lease spellings across flag PLACEMENT (not just the bare/=value
    spellings the lane suite pinned) — every one must gate AND mint;
  * the #1064 x #1069 composition — a faithful lease approval must still
    AUTHORIZE the SAME command carrying a benign continuation leg;
  * the #1078 cure across the full separator/placement matrix (extends the
    lane suite's &&/;/| rows);
  * the under-block guard — first-leg danger + a benign continuation must STILL
    gate (the anchoring narrows to the first leg, it must not stop gating a
    first-leg destructive op just because a benign leg follows);
  * cross-lane interaction rows (lease x lane3, lease x httpie).

Every assertion here is the SAFE direction: faithful single-command clicks
mint/authorize; benign compounds run free; first-leg-destructive forms stay
gated. No test here pins an accepted residual as contract — pinning would
cement it: the intra-lease value-variation residual and the =false negation
corner are documented via the lane suites' existing tripwire pins ONLY. The
mint-vs-read bound_flags surface asymmetry (#1083 — the mint scanned the full
option text while the read side isolated the single destructive leg, so a
privileged-flag literal in a benign continuation leg DENIED that faithful
compound) was subsequently FIXED in-batch: the mint scan is leg-bounded and the
read bind gained a two-tier fallback, with canaries in
test_merge_guard_auth_symmetry.py::TestLegBoundedMintWindow.

Sibling per-lane suites (do not duplicate):
  * test_merge_guard.py::TestLeaseToDefaultGateAndMint (Lane 1 envelope)
  * test_merge_guard_privileged_flags.py::TestLeaseNegationResidualTripwire (=false)
  * test_merge_guard_op_recognition_completeness.py::TestCrossLegFlagLeakOverBlockGone,
    ::TestHttpieMembershipCompleteness, ::TestAcceptedRecognitionLimitationPins
"""

import io
import json
from unittest.mock import patch

import pytest

from shared.merge_guard_common import is_dangerous_command as D  # noqa: E402
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)


# ---------------------------------------------------------------------------
# Real mint -> execute round-trip helpers (envelope layer), mirroring the
# TestLeaseToDefaultGateAndMint idiom: mint a SINGLE-command approval, then run
# a (possibly continuation-carrying) command through the real pre hook.
# ---------------------------------------------------------------------------

def _mint_single(cmd: str, tmp_path) -> int:
    """Drive the REAL post hook with an approval whose clicked option embeds a
    SINGLE `cmd`; return the count of tokens minted."""
    from merge_guard_post import main as post_main

    envelope = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Proceed?",
            "options": [
                {"label": "Yes", "description": f"Run `{cmd}`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {"Proceed?": "Yes"}},
        "session_id": "batch-test-session",
    })
    with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
         patch("sys.stdin", io.StringIO(envelope)):
        with pytest.raises(SystemExit) as exc_info:
            post_main()
    assert exc_info.value.code == 0
    return len(list(tmp_path.glob("merge-authorized-*")))


def _execute(cmd: str, tmp_path) -> int:
    """Run `cmd` through the REAL pre hook main(); return its exit code
    (0 = ALLOW, 2 = DENY)."""
    from merge_guard_pre import main as pre_main

    envelope = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": "batch-test-session",
    })
    with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
         patch("sys.stdin", io.StringIO(envelope)), \
         patch("sys.stdout", io.StringIO()):
        with pytest.raises(SystemExit) as exc_info:
            pre_main()
    return exc_info.value.code


# ===========================================================================
# Lane 1 (#1064) — faithful lease spellings across flag placement
# ===========================================================================


class TestLeaseBenignContinuationComposition:
    """#1064 x #1069: a faithful lease approval (SINGLE command) must AUTHORIZE the
    same command carrying a BENIGN continuation leg — the read side isolates the
    single destructive leg, so appending `&& echo ...` / a redirect must not break
    the authorization the fold un-blocked. The lane envelope suite only exercises
    the byte-identical case; this is the composition with the benign-continuation
    guarantee."""

    LEASE = "git push --force-with-lease origin main"

    @pytest.mark.parametrize(
        "executed",
        [
            "git push --force-with-lease origin main && echo pushed",
            "git push --force-with-lease origin main ; echo pushed",
            "git push --force-with-lease origin main > push.log",
            "git push --force-with-lease origin main && git status",
        ],
    )
    def test_lease_approval_authorizes_with_benign_continuation(self, executed, tmp_path):
        assert _mint_single(self.LEASE, tmp_path) == 1
        assert _execute(executed, tmp_path) == 0, (
            f"benign continuation broke the faithful lease authorization: {executed!r}"
        )

    def test_lease_approval_authorizes_byte_identical_control(self, tmp_path):
        """Non-vacuity control: the bare byte-identical execution authorizes, so a
        DENY in the continuation rows above would isolate to the continuation, not a
        broken token."""
        assert _mint_single(self.LEASE, tmp_path) == 1
        assert _execute(self.LEASE, tmp_path) == 0


# ===========================================================================
# Lane 3 (#1078) — cross-leg leak cure across the full separator/placement matrix
# ===========================================================================


# ===========================================================================
# Cross-lane composition
# ===========================================================================


# ===========================================================================
# Lane 2 (#1077) — adversarial httpie spellings (extends the membership suite)
# ===========================================================================


# ===========================================================================
# Leg-isolation completion re-cert (#1082 Fix B + #1083 Fix A two-tier window
# + #1087 close per-leg conversion). TEST-phase EXTENSION of the coder's
# TestLegBoundedMintWindow canaries. The close-ambiguity laundering residual
# (approve `close N && close M && echo --delete-branch` → a [--delete-branch]
# token that authorizes a real branch-delete) — the close literal arm's
# cross-leg `(?=.*--delete-branch)` lookahead, which the #1082 root Fix B fixed
# for force-push arms but not for close — is now FIXED by the #1087 per-leg
# close conversion (_CLOSE_LITERAL_ARMS matched per-leg at the read floor: the
# ambiguous form is is_dangerous=False → mints nothing → cannot launder). It is
# no longer issue-deferred; it IS pinned here now, in
# TestAmbiguousApprovalByteIdenticalIsSafeDirection.
# ===========================================================================

def _mint_ctx(desc: str, tmp_path, question="Proceed?", label="Yes"):
    """Drive the REAL post hook with an option whose description = `desc`
    (verbatim — lets a test control the exact wrapper/whitespace shape). Returns
    (n_tokens, first_token_context_or_None)."""
    from merge_guard_post import main as post_main

    envelope = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": question,
            "options": [
                {"label": label, "description": desc},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {question: label}},
        "session_id": "batch-test-session",
    })
    with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
         patch("sys.stdin", io.StringIO(envelope)):
        with pytest.raises(SystemExit) as exc_info:
            post_main()
    assert exc_info.value.code == 0
    toks = list(tmp_path.glob("merge-authorized-*"))
    ctx = json.loads(toks[0].read_text())["context"] if toks else None
    return len(toks), ctx


def _authorize(cmd: str, tmp_path):
    """Read side over the real token seam: None = ALLOW, str = DENY reason."""
    from merge_guard_pre import check_merge_authorization
    return check_merge_authorization(cmd, token_dir=tmp_path)


class TestMintWindowAnchorFaithfulShapes:
    """#1083 CARDINAL re-cert: the mint window's `find(the_command)` fallback is
    an accepted fail-toward-over-block ONLY IF no FAITHFUL single-command click
    triggers a find-miss that drops a flag the command carries. The #1042
    truncation-recovery form (region parser truncates at the quoted arg, so
    `--admin` sits AFTER the region) is the vulnerable shape — it binds correctly
    only if the window recovers it from the command's own leg. Driven through the
    REAL mint seam across wrapper/whitespace/prose shapes."""

    TRUNC = 'gh pr merge 42 --subject "polish the docs" --admin'

    @pytest.mark.parametrize(
        "desc",
        [
            "On approval run: `gh pr merge 42 --subject \"polish the docs\" --admin`",
            "On approval run: `gh pr merge 42 --subject \"polish the docs\" --admin` now",
            "gh pr merge 42 --subject \"polish the docs\" --admin",
            "   `gh pr merge 42 --subject \"polish the docs\" --admin`   ",
            "Please run `gh pr merge 42 --subject \"polish the docs\" --admin` to finish",
            "Run\t`gh pr merge 42 --subject \"polish the docs\" --admin`\tthanks",
        ],
        ids=["backtick", "backtick-trailing", "bare", "ws-padded", "prose-both", "tab-prose"],
    )
    def test_faithful_shape_binds_truncation_recovery_flag(self, desc, tmp_path):
        """No faithful shape triggers a find-miss under-bind: the truncation-
        recovery `--admin` binds, and the byte-identical execution AUTHORIZES
        (an under-bind would mint [] → the faithful re-exec would DENY)."""
        n, ctx = _mint_ctx(desc, tmp_path)
        assert n == 1
        assert ctx["bound_flags"] == ["--admin"], (
            f"faithful shape under-bound the truncation-recovery flag: {desc!r}"
        )
        assert _authorize(self.TRUNC, tmp_path) is None

    def test_curly_brace_wrapper_is_non_faithful_safe_over_block(self, tmp_path):
        """DOCUMENTED BOUNDARY (not a faithful-click over-block): curly BRACES are
        NOT a stripped command wrapper (only backticks + smart-quotes are, per
        _FLAG_SCAN_WRAPPER_TABLE), so `{... --admin}` leaves the `}` glued to the
        flag → the window binds []. This fails in the SAFE over-block direction
        (mint [] → set mismatch → DENY), never authorizing more than approved —
        so it is a tolerated non-faithful shape, not a cardinal violation. Pinned
        as the boundary of the faithful-shape guarantee above."""
        n, ctx = _mint_ctx(f"On approval run: {{{self.TRUNC}}}", tmp_path)
        assert n == 1
        assert ctx["bound_flags"] == []  # safe over-block: no --admin bound
        # over-block direction only: the faithful execution DENIES (never authorizes)
        assert _authorize(self.TRUNC, tmp_path) is not None


class TestTwoTierNoUnderBindRideAlong:
    """#1083 no-under-bind for the ops whose target is FIRST-LEG-ANCHORED
    (push-to-main/lease, force-push) — extends the coder's op-leg-flag canaries,
    which cover close/merge (targets are match-anywhere so they mint the compound
    directly). Push/force-push CANNOT mint a benign-PREFIX compound directly (no
    extractable target from a non-first leg → no_command), so the faithful flow
    is: approve the SINGLE destructive command, then execute it under a benign
    prefix/continuation — the read side isolates the destructive leg and
    AUTHORIZES. This is the no-gated-but-unmintable-dead-end proof for those
    ops."""

    @pytest.mark.parametrize(
        "approve,execute,flags",
        [
            ("git push --force-with-lease origin main",
             "cd . && git push --force-with-lease origin main", ["--force-with-lease"]),
            ("git push --force-with-lease origin main",
             "git push --force-with-lease origin main && echo done", ["--force-with-lease"]),
            ("git push --no-verify origin main --force",
             "cd . && git push --no-verify origin main --force", ["--no-verify"]),
            ("git push origin main",
             "cd . && git push origin main", []),
        ],
        ids=["lease-cd-prefix", "lease-continuation", "forcepush-noverify-cd", "plain-cd-prefix"],
    )
    def test_single_approval_rides_benign_prefix_or_continuation(
            self, approve, execute, flags, tmp_path):
        assert _mint_single(approve, tmp_path) == 1
        # the compound IS gated on read (so a token is genuinely consulted) ...
        assert D(execute) is True
        # ... and the single-command token authorizes it via read-leg-isolation.
        assert _authorize(execute, tmp_path) is None, (
            f"benign-prefix/continuation compound over-blocked despite a faithful "
            f"single-command approval: approve={approve!r} execute={execute!r}"
        )


class TestAmbiguousApprovalByteIdenticalIsSafeDirection:
    """#1087 laundering closed at the source. Pre-fix, the ambiguous multi-close
    `gh pr close 42 && gh pr close 43 && echo --delete-branch` was is_dangerous=
    True (the close arm's cross-leg `(?=.*--delete-branch)` lookahead), so
    approving it MINTED a token — and as an APPROVAL SOURCE that token authorized
    an escalated same-target single `gh pr close 42 --delete-branch` (a real
    branch delete). Post-fix the close arms match PER-LEG: no leg has `gh pr
    close` and `--delete-branch` together, so the ambiguous form is
    is_dangerous=False → mints NOTHING → cannot launder in any direction. This
    class pins the approval-SOURCE direction the #1083 residual left unpinned (it
    previously tested the form only as an execution target); it is now a fixed
    channel, not a deferred residual."""

    AMBIG = "gh pr close 42 && gh pr close 43 && echo --delete-branch"

    def test_ambiguous_approval_mints_no_token(self, tmp_path):
        """The approval mints NOTHING (n == 0 — was n == 1 pre-#1087). FIX-MERGE f3
        (amend_1 a): each bare close now gates, so the ambiguous multi-close is a
        compound of two destructive legs: the mint refuses it and the byte-identical
        re-execution is REFUSED as a compound (before f3 it ran free as not-dangerous)."""
        n, _ctx = _mint_ctx(f"On approval run: `{self.AMBIG}`", tmp_path, question="Close?", label="Yes")
        assert n == 0
        assert _authorize(self.AMBIG, tmp_path) is not None

    def test_ambiguous_approval_source_does_not_authorize_escalated_single(self, tmp_path):
        """THE laundering-closed proof (closes the #1087 O9 gap): approving the
        ambiguous multi-close mints no token, so the ESCALATED single
        `gh pr close 42 --delete-branch` (a real, irreversible branch delete)
        REFUSES for lack of any authorization. Pre-fix this AUTHORIZED (T1)."""
        n, _ctx = _mint_ctx(f"On approval run: `{self.AMBIG}`", tmp_path, question="Close?", label="Yes")
        assert n == 0
        assert _authorize("gh pr close 42 --delete-branch", tmp_path) is not None

    def test_ambiguous_approval_source_does_not_authorize_different_target(self, tmp_path):
        """Target-axis guard, now trivial: with no token minted, an execution
        against a DIFFERENT pr target also REFUSES."""
        n, _ctx = _mint_ctx(f"On approval run: `{self.AMBIG}`", tmp_path, question="Close?", label="Yes")
        assert n == 0
        assert _authorize("gh pr close 99 --delete-branch", tmp_path) is not None
