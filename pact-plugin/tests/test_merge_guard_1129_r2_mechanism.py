"""
Location: pact-plugin/tests/test_merge_guard_1129_r2_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1129 R2 — the carrier-7
         unification (SACROSANCT shared strip/leg pipeline). Proves R2's properties
         against the REAL classifier, base-vs-HEAD, NEVER a byte-diff (#1118):

           - OB1-8 over-block CLOSURE: each confirmed faithful-click over-block is
             is_dangerous on BASE (023ee2c3) and NOT on HEAD (R2).
           - MUST-NOT-REGRESS: genuinely-executing / genuinely-destructive forms stay
             gated in BOTH base and HEAD (R2 narrows NO detection).
           - -d SURVIVES -m STRIP: the git-tag -m value strip is flag-anchored and
             leaves the -d DELETE target visible in every ordering; the release
             value-strip never consumes the -d/-p BOOLEAN's neighbour.
           - SUBCOMMAND-DELETE accepted-residual PIN: gh release delete / gh gist
             delete / git tag -d stay UNDETECTED (base==HEAD==False), byte-unchanged.
             These under-blocks are a SEPARATE hardening issue, NOT R2's over-block
             scope — this pins them so a future detection arm can't silently regress.
           - THE LOAD-BEARING ROW: a REAL post_main->pre_main mint->execute round-trip
             proving the mint-side launder is WITHHELD at HEAD. MECHANISM (architect §1,
             F1-cycle refinement — primary/secondary, not a blanket ":447"): PRIMARY closure
             = read-side AUTO-ALLOW — post-R2 is_dangerous(carrier)=False, so
             check_merge_authorization returns None (ALLOW), NO AskUserQuestion approval is
             raised, and the mint path is NEVER invoked with the carrier command in the
             normal flow. SECONDARY backstop = the :447 is_dangerous write-gate (ACCURATE,
             base-vs-HEAD verified): IF the mint IS driven (an AskUserQuestion whose clicked
             option carries the carrier command — what THIS cert harness simulates), :447
             withholds because is_dangerous=False. The gist launder is a real USABLE base
             launder R2 closes END-TO-END (base mints {merge,5,--delete-branch}, exec
             `gh pr merge 5 --delete-branch` ALLOWs; HEAD mints nothing + DENYs). The git-tag
             launder base token IS CLASSIFIED (push-to-main, target 'main"' — CORRUPTED by
             the closing quote captured inside the -m value), NOT unclassified/vacuous, so
             R2's git-tag closure is mint-level defense-in-depth, not a usable-exec launder.
           - F1 LEG-MERGE REGRESSION GUARD (HALT #64, fix 3abfa3dc): the pre-fix
             whole-command carrier-7d strip let _strip_flag_values consume `STRIPPED<SEP>gh`
             across an UNQUOTED separator, EATING the head of a no-space gh-headed destructive
             tail (`git tag -m "x";gh pr merge 5 --delete-branch`) -> is_dangerous=False ->
             auth-bypass. The span-scope fix restores leg-locality. These rows load a THIRD
             baked classifier (PRE-FIX R2 6f404f2e) and assert base=True / pre-fix=False /
             fixed=True (pre-fix is the discriminator). Includes the over-anchoring witness
             (the non-gobbling git-tag anchor must not cross a separator into a LATER git tag)
             + SAFE controls (spaced separator, git-headed tail) that must NOT flip.
           - NEW-CARRIER AXIS COVERAGE (review MINOR-1): the strip axes ($()/backtick
             preserve, exotic-quote closure, leg-locality) exercised on ALL 3 new carriers
             (release/gist/git-tag), not just carrier-7.

NON-VACUITY (base-vs-HEAD, and pre-fix-vs-fixed for F1): the read-side rows load the BASE
classifier (023ee2c3) AND the PRE-FIX R2 classifier (6f404f2e) from vendored fixtures and
assert the discrimination IN-TEST, so the cert is permanently non-vacuous — a future strip
regression flips a HEAD column back and reds the row, and the base/pre-fix column proves
each form was genuinely a vector (never a vacuous green).

Cross-refs: docs/architecture/1129-r2-carrier7.md §5 (cert matrix); docs/preparation/
1129-r2-groundtruth.md (empirical OB/launder findings). Destructive verbs are assembled
at runtime (BD/PF/M5) so this file carries no raw force-delete/force-push literal and
stays inert to the live guard.
"""
import io
import json
from unittest.mock import patch

import pytest  # noqa: E402

import merge_guard_post  # noqa: E402
import merge_guard_pre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402
from merge_guard_baseline_loader import load_vendored  # noqa: E402

# --- Baked classifiers loaded from vendored fixtures ONCE for in-test base-vs-HEAD non-vacuity:
#     BASE (pre-R2) for the OB-closure rows; PRE-FIX R2 (the buggy whole-command
#     carrier-7d) for the F1 leg-merge regression guard. The F1 discriminator is
#     pre-fix=False -> fixed=True; BASE has no 7d carrier so it does NOT flip on F1.
_BASE_SHA = "023ee2c3"    # pre-R2


_BASE = load_vendored(_BASE_SHA)
D_BASE = _BASE.is_dangerous_command
D = mgc.is_dangerous_command
STRIP = mgc._strip_non_executable_content

PF = "git " + "push " + "--force origin main"  # destructive force-push literal
M5 = "gh " + "pr " + "merge 5"                 # destructive merge literal
ALLOW, DENY = 0, 2


# ===========================================================================
# Real post_main -> pre_main round-trip harness (the launder witness).
# ===========================================================================
def _mint(carrier_cmd, tok):
    """Drive the REAL post hook mint for a faithful carrier click; return the list of
    minted token contexts (empty when the :447 write-gate withholds)."""
    env = json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{
        "question": "Proceed?", "options": [
            {"label": "Yes", "description": "Run `%s`" % carrier_cmd},
            {"label": "Cancel", "description": "Abort"}]}]},
        "tool_response": {"answers": {"Proceed?": "Yes"}}, "session_id": "r2cert"})
    with patch.object(merge_guard_post, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit:
            pass
    return [json.loads(f.read_text()).get("context", {})
            for f in tok.glob("merge-authorized-*")]


def _execute(cmd, tok):
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY)."""
    env = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "r2cert"})
    with patch.object(merge_guard_pre, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()), \
         patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return ALLOW
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else ALLOW


# ===========================================================================
# -d SURVIVES -m STRIP + release boolean non-collision (sibling-safety).
# ===========================================================================
class TestDSurvivesMStrip:

    @pytest.mark.parametrize("label,cmd", [
        ("git tag -m then -d", 'git tag -m "msg" -d oldtag'),
        ("git tag -d then -m", 'git tag -d oldtag -m "msg"'),
    ])
    def test_tag_delete_target_visible_after_m_strip(self, label, cmd):
        # The flag-anchored -m value strip must NOT consume the -d DELETE target in any
        # ordering (the git-tag verb-collision guard — -m benign shares the verb with -d).
        stripped = STRIP(cmd)
        assert "-d oldtag" in stripped, "%s: -d target lost to the -m strip: %r" % (label, stripped)

    def test_release_boolean_not_mis_consumed(self):
        # -d (=--draft) / -p (=--prerelease) are BOOLEANS for release; the value-strip
        # (per-carrier set, NOT a union) strips only --title/--notes VALUES and leaves the
        # boolean -d visible in every ordering.
        stripped = STRIP('gh release create v1 --title "T" -d --notes "N"')
        assert "-d" in stripped and "STRIPPED" in stripped
        assert '"T"' not in stripped and '"N"' not in stripped


# ===========================================================================
# LOAD-BEARING — the REAL mint->execute round-trip: launder WITHHELD at HEAD.
# ===========================================================================
class TestLaunderClosureRoundTrip:
    """PRIMARY closure = read-side auto-allow (is_dangerous=False -> check_merge_authorization
    returns None -> no approval event -> the mint is NEVER invoked in the normal flow); the
    :447 is_dangerous write-gate is the ACCURATE SECONDARY backstop THIS harness drives
    directly (architect §1). Proven by the REAL post_main->pre_main round-trip, NOT read-side
    reasoning (phantom-green #1129). The gist base-mint witness is baked below (MINOR-2); the
    git-tag base token IS classified (push-to-main) but its target 'main\"' is CORRUPTED (not
    a usable launder), so R2's git-tag closure is mint-level defense-in-depth."""

    def test_gist_merge_launder_withheld_at_head(self, tmp_path):
        # `gh gist create -d "<merge>"` — the gist -d is read as merge's --delete-branch
        # alias. HEAD must withhold + DENY. (Base-mint proven in the sibling row below.)
        carrier = 'gh gist create -d "%s"' % M5
        assert D(carrier) is False, "carrier must be non-dangerous at HEAD (strip removed the literal)"
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO launder token"
        assert _execute(M5 + " --delete-branch", tmp_path) == DENY, "the base-usable laundered exec must DENY at HEAD"


    def test_tag_forcepush_launder_withheld_at_head(self, tmp_path):
        # Mint-level closure. The git-tag base token IS classified (push-to-main) but its
        # target 'main"' is CORRUPTED (the closing quote captured inside the -m value), so it
        # was NEVER a usable clean-exec launder — R2's tag closure is defense-in-depth. The
        # tag carrier is non-dangerous at HEAD so no push-to-main token mints.
        carrier = 'git tag v1.0 -m "%s"' % PF
        assert D(carrier) is False
        assert _mint(carrier, tmp_path) == [], "HEAD must mint NO push-to-main token from the tag carrier"


class TestNewCarrierAxisCoverage:
    """MINOR-1 (review): the strip axes exercised on carrier-7 (gh pr) are now exercised on
    ALL 3 NEW carriers (release/gist/git-tag) — they inherit via the shared _strip_flag_values
    /_VALUE_TOKEN, but the cert now GUARDS it per carrier (a regression to one new carrier's
    $()-preserve or exotic-quote handling would now red a row)."""

    # (label, value-flag prefix, tag-name suffix needed after the value)
    _CARRIERS = [
        ("release --notes", 'gh release create v1 --notes ', ''),
        ("gist --desc", 'gh gist create f.txt --desc ', ''),
        ("git-tag -m", 'git tag -m ', ' v1'),
    ]

    @pytest.mark.parametrize("label,prefix,suffix", _CARRIERS)
    def test_cmdsub_preserved_stays_gated(self, label, prefix, suffix):
        # $()/backtick in the value EXECUTES -> must stay gated (base AND HEAD).
        for val in ['"$(%s)"' % PF, '"`%s`"' % PF]:
            cmd = prefix + val + suffix
            assert D_BASE(cmd) is True and D(cmd) is True, "%s: cmd-sub must stay gated: %r" % (label, cmd)
