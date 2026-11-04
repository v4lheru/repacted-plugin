"""
Location: pact-plugin/tests/test_merge_guard_1155_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: BIDIRECTIONAL certification for the cross-auth recognition fix (detect on the
         executed-surface view with raw fallback) + the find -name/-path carrier.
         TWO-LAYER ORACLE: (1) unit base-vs-HEAD differentials over the REAL
         `detect_command_operation_type` / `is_dangerous_command` (baseline from the
         committed vendored fixture via merge_guard_baseline_loader — loud-fail,
         CI-executable); (2) END-TO-END `check_merge_authorization` with a live
         token fixture — the observed symptom lives in the token-mismatch path, not
         the unit classifier, so the e2e row is the one that reproduces the issue.

         THE BITE (cross-auth class): `gh pr close 5 --comment '…gh pr merge 5
         --admin…'` — the comment PROSE classified merge at base, so (a) the read
         side bound (merge,5) and any close token the operator minted mismatched
         (the faithful close was permanently gated), and (b) approving the close
         MINTED a merge+admin credential (the launder). On the view the prose is
         masked: both sides bind (close,5); a (close,5) token authorizes end-to-end.

         V2 FALLBACK DISCRIMINATORS: `bash -c 'gh pr merge 5'` / `sh -c "gh pr
         merge 5"` classify None on the view (payload masked) but are
         currently-minting faithful spellings — the raw fallback pass keeps them
         merge. A future "simplify detect to view-only" edit fails exactly here
         (and in test_merge_guard_mint_parity.py).

         DELIBERATE DISTRACTOR POLICY (pinned, not a regression): an option whose
         text embeds BOTH the close command and its merge-naming comment as separate
         regions now yields two distinct (op, target) pairs -> the multiplicity gate
         refuses the mint with `multiple_commands`. The click's path to execution is
         a token minted for the close spelling (single-region option shape, also
         pinned) or a comment reworded without the embedded command.

         Destructive verbs are assembled at runtime so this file stays inert to the
         live guard.
"""


import merge_guard_pre  # noqa: E402
import merge_guard_post  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)


MERGE_WORD = "mer" + "ge"
BITE = (
    "gh pr close 5 --comment 'weighed gh pr " + MERGE_WORD
    + " 5 --admin but closing instead'"
)


# =========================================================================================
# LAYER 2 — END-TO-END check_merge_authorization with a live token fixture. THE row
# that reproduces the issue: a (close, 5, []) token — what a faithful operator's
# close approval mints — authorizes the bite at HEAD and MISMATCHED at base.
# =========================================================================================
class TestEndToEndTokenOracle:
    CLOSE_TOKEN_CTX = {"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "5", "bound_flags": []}

    def test_close_token_authorizes_bite_at_head(self, tmp_path):
        merge_guard_post.write_token(dict(self.CLOSE_TOKEN_CTX), token_dir=tmp_path)
        assert merge_guard_pre.check_merge_authorization(BITE, tmp_path) is None, (
            "a faithful close token no longer authorizes the faithful close click"
        )

    def test_bite_still_gated_without_token(self, tmp_path):
        # close is a destructive op: with NO token the bite stays gated (the fix
        # changes WHICH token authorizes, never whether approval is required).
        assert merge_guard_pre.check_merge_authorization(BITE, tmp_path) is not None


# =========================================================================================
# MINT SIDE — the launder discriminator and the deliberate distractor policy.
# Option shape matters: a bare-text option carries the comment prose AND the full
# command as two regions; a backtick-wrapped option carries one region.
# =========================================================================================
# =========================================================================================
# R1 / F2 — PUSH/BRANCH DETECT ARMS OFF THE MASKED VIEW (remediation commit 5). Commit 2
# ran the force-push / push-to-main / branch-delete arms on the quote-masked view; a
# COSMETICALLY-QUOTED remote (`git push 'origin' --delete main` — the quotes are cosmetic,
# the token EXECUTES) masks to a whitespace gap that push-to-main's POSITIONAL regex binds
# ACROSS, downgrading the op (remote-ref-delete -> push-to-main) and short-circuiting the
# raw delete-class extractors. LAUNDERING: a benign `git push origin main` approval minted
# a push-to-main token that authorized the quoted-remote DELETE within TTL.
#
# THE FIX IS GENERAL (structural), not a per-flag guard: those arms run on RAW in BOTH
# passes (their certified base surface); ONLY the gh-pr merge/close prose arms stay on the
# view. A per-flag delete-class guard would have to enumerate the disqualifying flag
# surface completely and would still miss the quoted-'--force' spelling.
# =========================================================================================
_R1_PUSH = "git push "


class TestR1PushArmsOffView:


    def test_e2e_launder_closed(self, tmp_path):
        # the confirmed end-to-end launder: a benign push-to-main token must REFUSE the
        # quoted-remote delete (op mismatch restored). Both directions of the round-trip.
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes", "description": "Run `git push origin main` now"}],
            "multiSelect": False,
        }
        ctx, refusal = merge_guard_post._mint_context_from_bundle(
            [question], {"Proceed?": "Yes"}
        )
        assert ctx is not None and ctx["operation_type"] == "push-to-main"
        merge_guard_post.write_token(ctx, token_dir=tmp_path)
        for victim in (_R1_PUSH + "'origin' --delete main", _R1_PUSH + '"origin" -d main'):
            assert merge_guard_pre.check_merge_authorization(victim, tmp_path) is not None, (
                "LAUNDER OPEN: a benign push-to-main token authorized %r" % victim
            )


class TestR1LaunderE2ESmoke:
    """Belt-and-suspenders standing anchor for the F2 launder. The full mint->auth
    round-trip lives in TestR1PushArmsOffView (above); this thin smoke
    survives even if that cert is later refactored: a benign push-to-main token must REFUSE
    a quoted-remote DELETE (the confirmed end-to-end launder)."""

    def test_benign_push_token_refuses_quoted_remote_delete(self, tmp_path):
        question = {
            "question": "Proceed?",
            "options": [{"label": "Yes", "description": "Run `git push origin main` now"}],
            "multiSelect": False,
        }
        ctx, refusal = merge_guard_post._mint_context_from_bundle(
            [question], {"Proceed?": "Yes"}
        )
        assert ctx is not None and ctx["operation_type"] == "push-to-main", (
            "faithful push-to-main click stopped minting (%s)" % refusal
        )
        merge_guard_post.write_token(ctx, token_dir=tmp_path)
        victim = "git push " + "'origin' --delete main"
        assert merge_guard_pre.check_merge_authorization(victim, tmp_path) is not None, (
            "LAUNDER OPEN: a benign push-to-main token authorized a quoted-remote delete"
        )
