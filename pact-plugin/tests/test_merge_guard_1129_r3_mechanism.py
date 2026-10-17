"""
Location: pact-plugin/tests/test_merge_guard_1129_r3_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1129 R3 — scoping the two
         routing flags (_has_pipe_to_shell, _has_process_substitution_to_shell) to
         the EXECUTED SURFACE so a routing token inside quoted carrier DATA / a
         heredoc BODY no longer disables the carrier strips and re-exposes co-residing
         destructive text — PLUS the R3-fix remediation of the arm-B anchor under-block
         the R3 view first introduced (found by the independent security review pass).

         Proven against the REAL classifier, up to FOUR baked columns, NEVER a byte-diff
         (#1118):
           - BASE   = 51e6c5a5 (pre-R3): the flags compute over the raw command.
           - R3HEAD = 72bacaf8 (R3 shipped, carries the arm-B regression): the flags
             compute over the pure space-mask view. For the OUTPUT-SIDE procsub rows
             this column IS the 'anchor-restore reverted' state, so R-1..R-4 R3HEAD=False
             is the permanent non-vacuity discriminator for the arm-B anchor restore.
           - FIX1   = b313ecaa (R3-fix1: arm-B anchor restored for a SPACE separator ONLY):
             the 'fix2 reverted' state. Non-space bash blanks (tab / blank-run-ending-in-tab)
             are UNDER-BLOCKED here, so FIX1=False on those rows is the permanent fix2
             non-vacuity discriminator.
           - PATCH  = live 7561d32d (R3 + fix1 anchor restore + fix2 whitespace widening):
             piped stays on the space-mask view; procsub reads _procsub_anchor_view whose
             anchor walk-left now skips any UNMASKED BASH BLANK (space/tab), not just space.

         Separator classification (by SHELL EXECUTION SEMANTICS, not by arm-B \\s+ match):
           a quoted-writer procsub `echo "<d>" | <writer><SEP>>(bash)` executes a fanout
           ONLY when <SEP> is a run of bash BLANKS (space/tab). fix2 catches ALL such blanks
           (FIX-blanks, must-be-True). Non-blank whitespace {NL,CR,FF,VT} is non-executing
           (\\n = command separator; \\r/\\f/\\v glue the writer into a nonexistent command),
           and zero-separator adjacency is a pre-existing arm-B \\s+ gap — both DIFFERENTIAL-
           False, asserted PATCH=False (NOT must-be-True; demanding True is the trap toward
           the over-broad \\s fix that re-introduces over-blocks).

         Row classes (executed-surface scoping + the R3-fix remediation):
           - FIX (carrier-DATA over-block CLOSURE): base True -> R3HEAD False -> PATCH
             False. Pipe/procsub/input-procsub token inside a quoted carrier value or a
             heredoc body across carriers 1/3/5/7/7b/7c/7d/8/9. The base=True column
             proves each was a genuine faithful-click over-block (never a vacuous green).
           - REGRESSION-FIX (R-1..R-4): base True -> R3HEAD False -> PATCH True.
             A genuinely-EXECUTING output-side procsub whose FIFO-writer name is quoted
             (`echo "<d>" | "tee" >(bash)`, class "tee"/'tee'/"dd"/"cat"). base catches
             (the closing `"` anchors arm B); R3's space-mask blanks the anchor ->
             UNDER-BLOCK (the R3HEAD=False discriminator == the confirmed auth regression);
             the R3-fix restores the anchor -> PATCH re-catches. The R3HEAD=False column
             is the load-bearing proof that the anchor-restore is non-vacuous.
           - PRESERVE (executing routing stays caught): base True -> PATCH True — real
             `| sh`/`| bash`/`| xargs bash`, $()/backtick/eval/bash -c, heredoc opener
             tails, input-side `bash <(..)`, honest `tee >(bash)`/arm-A `> >(bash)`, and
             carrier value + EXECUTING tail (C18/C19). Plus P-1 (B3 survives:
             quoted `>(bash)` DATA stays False at PATCH) and P-2 (the incidental
             `>("ba"sh)` under-block R3's mask closed must NOT regress).
           - DIFFERENTIAL (pre-existing under-blocks / correct negatives): False==False
             ==False. #1146 `| sudo sh`, quoted shell name `| "sh"`, `| sudo sh` in body,
             content-gated (token, no danger), non-shell `<(` in body, #1133 bare heredoc,
             stderr `2> >(bash)` exclusion; PLUS the separator DIFFERENTIALs above
             (non-executing {NL,CR,FF,VT} + zero-sep adjacency). Asserted False (NOT
             must-stay-True) — asserting True on a pre-existing under-block or a
             non-executing form would wrongly implicate R3/fix.
           - BONUS-CLOSURE (document, don't gate): `echo "<d>" | "tee" >("ba"sh)`
             base False -> PATCH True — genuinely-executing under-block the R3-fix closes;
             MORE correct than base, not an over-block.

NON-VACUITY (permanent, in-suite): the base(51e6c5a5), R3HEAD(72bacaf8) AND FIX1(b313ecaa)
classifiers are loaded from vendored fixtures and asserted IN-TEST, so the cert can never be
vacuously green — a FIX row's base=True proves the vector existed; a REGRESSION-FIX row's
R3HEAD=False proves the arm-B under-block was real and the anchor-restore is load-bearing; a
FIX-blank row's FIX1=False (on non-space blanks) proves fix2's whitespace widening is
independently load-bearing; a future regression flips a PATCH column and reds the row. Three
real-source-revert legs (1: relocation, 2: heredoc-excision, 3: the fix2 one-line-predicate
revert `view[k]==excised[k]` -> `view[k]==" "`, which flips the non-space-blank rows back to
False while SPACE stays True) are documented in the HANDOFF with cardinality — they exercise
the LIVE 7561d32d source.

This cert covers both the R3 executed-surface scoping and the R3-fix arm-B/whitespace
remediation; each row class below carries its own base/R3HEAD/FIX1/PATCH expectation inline,
so a reader needs no external design doc. Destructive verbs are assembled at runtime
(PF/BD/M9/SH) so this file carries no raw force-push/force-delete/merge literal and stays
inert to the live guard; probe forms are never run as shell.
"""

import pytest  # noqa: E402

import shared.merge_guard_common as mgc  # noqa: E402


# Destructive literals assembled at runtime — this file carries no raw literal.
PF = "git " + "push " + "--force origin main"    # force-push
SH = "s" + "h"                                    # shell name, kept un-obvious


class TestR3ViewBuilderInvariants:
    """The two internal invariants the anchor-restore offset math relies on, pinned
    directly on the shared _excise_and_mask helper so a future refactor that breaks
    either reds a row instead of silently mis-aligning the anchor walk-left."""

    def test_excision_precedes_mask_order_is_load_bearing(self):
        # ORDER INVARIANT: _excise_and_mask must excise heredoc bodies BEFORE masking
        # quotes. Witness: a NAKED heredoc body carrying a stray unbalanced quote, then a
        # quoted carrier value. Excise-first removes the body, keeping the quote mask synced
        # over the trailing carrier; mask-first would let the stray body quote desync the
        # mask. Pin: the live view equals excise-then-mask, and DIFFERS from mask-then-excise
        # on this witness — so excision-before-mask is provably load-bearing (a swapped order
        # reds both asserts).
        cmd = 'cat <<EOF\n" | %s\nEOF\ngh pr comment 1 --body "%s | %s"' % (SH, PF, SH)
        excised, view = mgc._excise_and_mask(cmd)
        assert "HEREDOC_BODY_EXCISED" in excised, "the naked heredoc body must be excised: %r" % cmd
        assert view == mgc._mask_shell_quotes(
            mgc._excise_heredoc_bodies_for_routing_scan(cmd)), \
            "live view must equal excise-then-mask: %r" % cmd
        assert view != mgc._excise_heredoc_bodies_for_routing_scan(
            mgc._mask_shell_quotes(cmd)), \
            "mask-then-excise must differ -> excision-before-mask is load-bearing: %r" % cmd

    @pytest.mark.parametrize("s", [
        'gh pr edit 1 --body "%s | %s"' % (PF, SH),
        'echo "%s" | "tee" >(bash)' % PF,
        "gh pr comment 1 --body '%s | %s'" % (PF, SH),
        'cat <<EOF\n%s | %s\nEOF' % (PF, SH),
        'gh pr edit 1 --body "unbalanced %s' % PF,        # unbalanced quote (fail-open path)
        'echo "%s" | tee >("ba"sh)' % PF,                  # nested quote in procsub
        '',
        'plain command no quotes',
    ])
    def test_mask_is_same_length_and_view_excised_parity(self, s):
        # SAME-LENGTH INVARIANT: _procsub_anchor_view walks left comparing view[k] to
        # excised[k] by offset, sound only if _mask_shell_quotes preserves length
        # (space-fill, never add/drop chars). Pin length parity on the masker and on the
        # (excised, view) pair the shared _excise_and_mask returns.
        assert len(mgc._mask_shell_quotes(s)) == len(s), \
            "_mask_shell_quotes must be same-length (offset alignment): %r" % s
        excised, view = mgc._excise_and_mask(s)
        assert len(view) == len(excised), \
            "view/excised length parity (offset alignment): %r" % s
