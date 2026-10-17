"""
Location: pact-plugin/tests/test_merge_guard_1140_carrier5_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: BIDIRECTIONAL certification for #1140 — carrier-5 (`git commit -m/--message`
         message-value strip) migrated from two `-m`-only inline `re.sub` arms to the
         shared span-bounded quote-balanced machinery (`_strip_flag_values` /
         `_VALUE_TOKEN` / `_keep_carrier_value`), a verb-only clone of carrier-7d
         (`git tag -m`). Certifies the fix against the REAL `is_dangerous_command`,
         base-vs-HEAD, NEVER a byte-diff / additive-lines argument (the #1118 trap:
         a +N/-0 change to the shared strip/leg-split pipeline opened BOTH an over-block
         and an under-block, invisible to a byte-diff — only behavioral data-flow review
         against the real classifier catches it).

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: blocking a faithful `git commit` click is wrong
             by definition. The five residuals below were each a real over-block at base.
           - UNDER-block = security hole the fix MUST NOT open. Leg-locality is the
             load-bearing invariant: the value-strip span stops at the first UNQUOTED
             ;/&&/|/newline, so an executing destructive tail stays OUTSIDE the stripped
             span and is caught.

         WHAT IS CERTIFIED (design §5 matrix, 26 vectors):
           - OVER-BLOCK CLOSURE (5): multi `-m`, ANSI-C $'...', adjacent-concat,
             `--message` single, `git -C <path> commit` — each is_dangerous on BASE
             (over-blocked) and NOT on HEAD (faithful click freed).
           - FAITHFUL NEVER-BLOCKED (5): benign / prose-bearing single `-m`/`--message`/
             `-C commit` forms stay False (incl. the issue #1140 body's exact repro,
             a single-`-m` dq form that was already handled by the old arm-1).
           - UNDER-BLOCK RETENTION (8): &&/; tails (spaced + unspaced), piped body,
             $()-body, >(bash) procsub, and the u8 adversarial later-`git commit` canary
             — each stays gated (True) on both base and HEAD.
           - WIDENED-PREFIX UNDER-BLOCK (3): the global-flag prefix (OQ1) surface —
             `-C "$()"`, `-C … && tail`, `-C … ; tail` — stays gated.
           - EXISTING-TEST-PIN COMPATIBILITY (4): the fix leaves 4 pre-existing
             behavioral pins unchanged (design §5.6).

NON-VACUITY (base-vs-HEAD, in-test + permanent): the OVER-BLOCK and UNDER-BLOCK rows load
the PRE-FIX parent classifier (f6e3639a = 2d7fcd07^, the `-m`-only inline arms) via
vendored fixtures and assert the discrimination IN-TEST (base=True), so a future strip
regression that re-breaks carrier-5 flips a HEAD column back and reds the row, and the
base column proves each form was a genuine vector (never a vacuous green). This baked-
classifier discriminator is crash-atomic (NO working-tree mutation — the fix is COMMITTED,
so a `git checkout HEAD` would restore the FIXED baseline; this avoids that trap entirely)
and mirrors test_merge_guard_1129_r2_cert.py.

THE GOBBLING NEGATIVE CONTROL (the load-bearing non-gobbling-word-class proof, design §5.5):
`TestU8AdversarialAndGobblingControl` builds a MUTANT of the live source with carrier-5's
non-gobbling `[^;&|\\n\\s]` word class swapped for a `\\S` gobbler and proves the u8
adversarial canary flips True (caught) -> False (UNDER-BLOCK OPENED) under the mutant. This
is the empirical proof (not a static argument) that the `[^;&|\\n\\s]` word class is
load-bearing — a `\\S+` gobbler crosses two `;` separators to re-anchor on a LATER
`git commit`, pulls the intermediate `;gh pr merge 5;` into ONE span, and `_strip_flag_values`
arm-3's `_VALUE_TOKEN` (which does NOT stop at ;&|) then eats `STRIPPED;gh` -> the `gh` head
is deleted -> the merge is unrecognized -> auto-ALLOW.

Cross-refs: docs/architecture/1140-carrier5.md §5 (cert matrix) + §5.5 (gobbling control);
docs/preparation/1140-carrier5.md Appendix A (global-flag-prefix under-block mapping).
Destructive verbs are assembled at runtime (BD/BDR/PF/M5) so this file carries no raw
force-delete / force-push / merge literal and stays inert to the live guard.
"""
import types
from pathlib import Path

import pytest  # noqa: E402

import shared.merge_guard_common as mgc  # noqa: E402
import shell.merge.strip as _strip  # noqa: E402  # carrier 5's home since the carve

D = mgc.is_dangerous_command
STRIP = mgc._strip_non_executable_content


# --- Destructive verbs assembled at runtime — this file carries no raw literal.
BD = "git " + "branch " + "-D victim"          # destructive branch-delete (prose target)
M5 = "gh " + "pr " + "merge 5 --delete-branch"  # destructive merge


def _load_gobbling_mutant():
    """Load the LIVE strip module (shell/merge/strip.py) with carrier-5's non-gobbling
    `[^;&|\\n\\s]` word class swapped for a `\\S` gobbler — the design's REJECTED variant
    (§5.5). Used to prove the word class is load-bearing: the mutant re-opens the u8
    leg-merge under-block. The needle includes the `commit\\b` anchor so ONLY carrier-5's
    span is mutated (carrier-7d's identical `tag` span is left intact). Asserts the needle
    exists so a future source drift fails LOUDLY here rather than silently no-op'ing the
    negative control into a vacuous pass. The caller patches the mutant's strip function
    into the live binding (mgc setattr propagates to every package reader)."""
    src = Path(_strip.__file__).read_text()
    needle = r'r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}commit\b"'
    repl = r'r"\bgit\s+(?:\S+\s+){0,%d}commit\b"'
    assert needle in src, "carrier-5 non-gobbling span literal not found — source drifted; " \
                          "the gobbling negative control would be vacuous"
    mutant_src = src.replace(needle, repl)
    assert mutant_src != src
    mod = types.ModuleType("merge_guard_strip_1140_gobble")
    mod.__file__ = _strip.__file__
    exec(compile(mutant_src, mod.__file__, "exec"), mod.__dict__)
    return mod


def _carrier5_span_source():
    """The carrier-5 CODE block — the `_git_commit_span = (...)` assignment + its `re.sub`
    call, scoped from the assignment to the `# 6.` marker. Deliberately EXCLUDES the
    preceding explanatory comment, which NAMES the rejected `_GIT_PREFIX` / `(?:\\S+\\s+)`
    gobbler to document why it is not used — the drift-detector below asserts on executable
    code, not prose, so the comment's mention of the rejected form does not trip it."""
    src = Path(_strip.__file__).read_text()
    start = src.index("_git_commit_span = (")
    end = src.index("# 6. ", start)
    return src[start:end]


# ===========================================================================
# THE TWO HIGHEST-VALUE PINS (architect-flagged): u8 adversarial + gobbling negative control.
# ===========================================================================
class TestU8AdversarialAndGobblingControl:

    _U8 = 'git commit -m "x";%s;git commit -m "y"' % M5

    def test_u8_stays_caught_at_head(self):
        # A later `git commit` must NOT let the non-gobbling prefix cross the two `;` to
        # re-anchor and swallow the middle `gh pr merge 5 --delete-branch` — it stays visible.
        assert D(self._U8) is True, "u8: the middle merge must stay caught at HEAD: %r" % self._U8
        # Mechanism witness: the middle merge survives on the stripped surface.
        assert M5 in STRIP(self._U8), \
            "u8: the middle merge must remain on the stripped surface: %r" % STRIP(self._U8)

    def test_gobbling_mutant_opens_the_underblock(self, monkeypatch):
        # LOAD-BEARING NEGATIVE CONTROL (design §5.5): swap carrier-5's non-gobbling
        # `[^;&|\n\s]` word class for a `\S` gobbler and prove u8 flips True -> False.
        # The gobbler crosses both `;` to reach the later `git commit`, pulls `;gh pr merge 5;`
        # into ONE span, and arm-3's `_VALUE_TOKEN` eats `STRIPPED;gh` — deleting the `gh`
        # head so the merge is unrecognized -> auto-ALLOW. This is the EMPIRICAL proof (not a
        # static argument) that the non-gobbling word class is load-bearing.
        mutant = _load_gobbling_mutant()
        assert D(self._U8) is True, "sanity: real classifier catches u8"
        monkeypatch.setattr(mgc, "_strip_non_executable_content", mutant._strip_non_executable_content)
        assert D(self._U8) is False, \
            "the gobbling mutant MUST re-open the u8 under-block (else the negative control is vacuous)"
        # The gh head is eaten on the mutant's stripped surface (gone), present on the real one.
        assert M5 not in mutant._strip_non_executable_content(self._U8), \
            "the gobbling mutant must EAT the gh head (the under-block mechanism)"

    def test_source_uses_nongobbling_word_class(self):
        # SOURCE-LEVEL DRIFT-DETECTOR — DESIGN-INTENT, deliberately anchored to the exact
        # word class, NOT brittle: this pins that carrier-5's global-flag prefix uses the
        # non-gobbling `[^;&|\n\s]` word class and NOT a `\S+` gobbler and NOT `_GIT_PREFIX`
        # (whose `(?:\S+\s+){0,N}` gobbler spans separators). Per the behavioral proof above,
        # a gobbling prefix re-opens a leg-merge UNDER-BLOCK (auth bypass). A future reader
        # tempted to "de-brittle" this by loosening the word class to `\S+` or reusing
        # `_GIT_PREFIX` would silently ship that security hole — this assertion is the
        # tripwire that stops it. Do NOT relax it.
        block = _carrier5_span_source()
        assert r"[^;&|\n\s]" in block, \
            "carrier-5 prefix must use the non-gobbling [^;&|\\n\\s] word class"
        assert r"(?:\S+\s+){0," not in block, \
            "carrier-5 prefix must NOT use a \\S+ gobbler (re-opens the leg-merge under-block)"
        assert "_GIT_PREFIX" not in block, \
            "carrier-5 prefix must NOT reuse _GIT_PREFIX (its \\S+ gobbler spans separators)"


# ===========================================================================
# EXISTING-TEST-PIN COMPATIBILITY (design §5.6) — the fix leaves these unchanged.
# ===========================================================================
class TestExistingPinCompatibility:


    def test_strip_surface_pin_gh_pr_merge_removed(self):
        # test_merge_guard.py:2064 pins that a single-`-m` message containing `gh pr merge`
        # prose is stripped to an inert bareword (the value never survives). Byte-identical
        # to HEAD's old single-`-m` output (`git commit -m STRIPPED`).
        result = STRIP('git commit -m "gh pr merge 42"')
        assert "gh pr merge" not in result, "the -m value must be stripped: %r" % result


# ===========================================================================
# STRIP-SURFACE MECHANISM PINS — document HOW each closure/retention happens (the exact
# post-strip surface), so a regression that changes the mechanism (not just the boolean)
# is visible. These ride on the real _strip_non_executable_content.
# ===========================================================================
class TestStripSurfaceMechanism:

    def test_multi_m_both_values_stripped(self):
        # Both -m values become inert -> no destructive prose survives.
        s = STRIP('git commit -m "subj" -m "body: run %s to clean up"' % BD)
        assert s == "git commit -m STRIPPED -m STRIPPED", s

    def test_ansi_c_value_consumed_atomically(self):
        s = STRIP("git commit -m $'fix: describe %s in prose'" % BD)
        assert s == "git commit -m STRIPPED", s

    def test_adjacent_concat_fully_stripped(self):
        s = STRIP('git commit -m "prefix ""%s"' % BD)
        assert s == "git commit -m STRIPPED", s

    def test_dashC_commit_prefix_matched(self):
        s = STRIP('git -C /some/repo commit -m "fix: describe %s in prose"' % BD)
        assert s == "git -C /some/repo commit -m STRIPPED", s

    def test_leg_locality_semicolon_tail_survives(self):
        # u4: the span stops at the first unquoted `;`, so the executing merge tail survives.
        s = STRIP('git commit -m "x";%s' % M5)
        assert s == "git commit -m STRIPPED;%s" % M5, s

    def test_widened_prefix_dashC_cmdsub_value_preserved(self):
        # w1: the $() in the -C value rides in the span PREFIX and is NEVER stripped.
        s = STRIP('git -C "$(%s)" commit -m "msg"' % M5)
        assert s == 'git -C "$(%s)" commit -m STRIPPED' % M5, s


# ===========================================================================
# ===  #1176 REMEDIATION — FIX-R (bash-faithful body) + C4 (widened anchor)  ===
# The 26 vectors above certified the FLAWED first fix (2d7fcd07). An independent review found
# a BLOCKING under-block that fix missed: an escaped-quote/ANSI-C message + an executing
# destructive tail + a TRAILING QUOTED LEG — a cross-product the first matrix never combined
# (a naive `'[^']*'` body paired a `'` across the separator and swallowed the executing leg).
# FIX-R (b6418727) made the shared verb-message body bash-faithful and closed it (commit AND
# tag); C4 (f7f370a3) widened the value-strip anchor and closed the bundled/attached `-m`
# OVER-block (commit AND tag). The classes below certify the FINAL committed state (HEAD =
# f7f370a3) bidirectionally, each asserting the SHARPEST per-fix discriminator (see the
# two-baseline note by the baked-classifier loaders). OVER-BLOCK is the USER'S TOP PRIORITY
# (a faithful click blocked is wrong by definition) — its closures come first + most thoroughly.
# ===========================================================================


# --- C4 ANCHOR char-class DRIFT-DETECTOR (design-intent, deliberately NOT brittle).
class TestC4AnchorDriftDetector:
    def test_anchor_uses_m_excluding_class(self):
        # DESIGN-INTENT DRIFT-DETECTOR — the char class `[a-ln-zA-Z]` in the bundled-flag SHORT ARM
        # `-[a-ln-zA-Z]*m` deliberately EXCLUDES lowercase `m`. That exclusion is LOAD-BEARING:
        # it makes the cluster match stop at the message `m` with NO backtracking, so the pattern is
        # UNBOUNDED (closes even the absurd -aa..am cluster — zero residual over-block) AND PROVABLY
        # LINEAR. A greedy `-[A-Za-z]*m` / `-[a-zA-Z]*m` (INCLUDING m) backtracks to "find" the m ->
        # O(n^2) = a ReDoS over-block-BY-TIMEOUT; a bounded `-[a-ln-zA-Z]{0,N}m` would leave an
        # absurd-cluster residual over-block. A future reader must NOT "simplify"/bound this class.
        #
        # REALIGNED for the fold (F1): the positive check pins the m-EXCLUDING SHORT ARM
        # `-[a-ln-zA-Z]*m` — the load-bearing linear/unbounded invariant — NOT the full anchor string.
        # F1 legitimately rewrote each carrier's LONG arm (commit -> the prefix-of-message
        # `--m(?:e(?:s...)?)?`; tag -> the bounded `--mes(?:s...)?`), leaving the short arm UNCHANGED,
        # so the OLD full-string pin `((?:--message|-[a-ln-zA-Z]*m)\s*)` now appears 0x and its
        # positive assertion was a STALE incidental over-coupling. This detector owns the short-arm
        # class ONLY; the long arm's abbreviation-closure + --merged/--no-merged over-strip-safety is
        # guarded BEHAVIORALLY by the F1 vector classes (TestF1CommitAbbreviationClosure /
        # TestF1TagAbbreviationClosure / TestF1AbbreviationControls), not by a brittle full-string pin.
        # Re-pinning the whole (now per-verb-divergent) anchor here would re-introduce exactly the
        # over-coupling this comment warns against — do NOT. The short arm appears at BOTH the commit
        # AND tag carriers (>= 2; F2's _MSG_FLAG_ANCHOR adds a 3rd, but this detector deliberately does
        # NOT couple to F2's carrier count — >= 2, not >= 3).
        short_arm = r"-[a-ln-zA-Z]*m"
        src = Path(_strip.__file__).read_text()      # both carriers live in strip.py
        assert src.count(short_arm) >= 2, \
            "the m-excluding SHORT ARM must appear at BOTH the commit AND tag carriers; found %d" % src.count(short_arm)
        assert r"-[A-Za-z]*m" not in src and r"-[a-zA-Z]*m" not in src, \
            "the anchor must NOT use an m-INCLUDING class (backtracking -> ReDoS/over-block-by-timeout)"
        assert r"-[a-ln-zA-Z]{0," not in src, \
            "the anchor cluster must be UNBOUNDED (a bounded {0,N} leaves an absurd-cluster residual over-block)"


# --- ReDoS LINEARITY pins (ReDoS is an over-block BY TIMEOUT).
#
# BOUNDS ARE SIZED AGAINST THE FAILURE THEY DETECT, NOT AGAINST TYPICAL RUNTIME.
# The regression these pins exist to catch is catastrophic backtracking, which is
# ORDERS OF MAGNITUDE — a non-terminal carrier span measured 60,662 ms where the
# terminal one takes 8 ms. So the bound only has to sit far above the honest
# runtime and far below a real blowup; every millisecond of the gap between those
# two is free, and narrowing it buys nothing while costing flakiness.
#
# MEASURED (this module, moderately loaded): the slowest single call is ~600 ms.
# The previous bounds were 1000 ms and 2000 ms — a margin of under 2x — and a
# preceding comment claimed the runtime was "single-digit ms even at n=8000",
# which is false and is what made those bounds look generous. Under a loaded
# suite they failed at 1006.5 ms against 1000 ms, and 2040.6 ms against 2000 ms:
# both under 3% over, i.e. pure load, not regression.
_LINEAR_BOUND_MS = 8000.0        # ~13x the slowest honest call
_LINEAR_BOUND_WIDE_MS = 12000.0  # for the heavier echo/printf cases

# Re-sample only when a sample looks bad. Timing noise is ONE-SIDED — load can
# only make a run slower — so the MINIMUM is the robust estimator and the mean is
# not. Sampling three times unconditionally would triple this module's runtime;
# re-sampling only above this threshold keeps the fast path at one sample and
# pays the cost solely where a spike would otherwise produce a false red.
_RESAMPLE_ABOVE_MS = 1500.0
_RESAMPLE_TIMES = 3


def _elapsed_ms(cmd):
    """Milliseconds for one classification, robust to a transient load spike."""
    import time

    def _once():
        start = time.perf_counter()
        D(cmd)
        return (time.perf_counter() - start) * 1000.0

    best = _once()
    if best > _RESAMPLE_ABOVE_MS:
        for _ in range(_RESAMPLE_TIMES - 1):
            best = min(best, _once())
    return best


class TestReDoSLinearity:
    # A hang blocks a faithful click -> ReDoS is an over-block. Both the C4 anchor cluster and
    # the FIX-R body must be linear. The bound is sized against the REGRESSION (orders of
    # magnitude), not against typical runtime -- see the _elapsed_ms header for the measured
    # numbers. An earlier version of this comment claimed single-digit ms at n=8000; the real
    # figure is ~450ms, and that false premise is what made a sub-2x bound look generous.
    @pytest.mark.parametrize("n", [2000, 8000])
    def test_c4_anchor_cluster_linear(self, n):
        # A huge VALID bundled cluster AND a pathological NO-`m` cluster (the input that forces a
        # greedy m-including variant to backtrack the whole run). C4 handles both in linear time.
        assert _elapsed_ms('git commit -%sm "run %s"' % ("a" * n, BD)) < _LINEAR_BOUND_MS, "valid cluster n=%d" % n
        assert _elapsed_ms('git commit -%sx "run %s"' % ("a" * n, BD)) < _LINEAR_BOUND_MS, "no-m cluster n=%d" % n

    @pytest.mark.parametrize("n", [2000, 8000])
    def test_fixr_body_linear(self, n):
        # Pathological quote/escape runs that stress the multi-arm bash-faithful body's ambiguity.
        assert _elapsed_ms('git commit -m ' + '"' * n) < _LINEAR_BOUND_MS, "unclosed-dq run n=%d" % n
        assert _elapsed_ms("git commit -m " + "$'" * n) < _LINEAR_BOUND_MS, "ansi-c open run n=%d" % n
        assert _elapsed_ms('git commit -m ' + '\\' * n) < _LINEAR_BOUND_MS, "backslash run n=%d" % n


# ===========================================================================
# ===  #1176 FOLD-ALL-4 — four re-review over-blocks closed on the message  ===
# ===  value-strip surface (F1 abbreviated --message, F2 sibling verbs,     ===
# ===  F3+F4 span-scoped substitution preserve). NON-VACUITY vs D_PREFOLD    ===
# ===  (3972bb5f = 6a3a86b7^): EVERY over-block-closure flips True(pre-fold) ===
# ===  -> False(HEAD); true-positives / under-block canaries / fail-safe     ===
# ===  residuals hold True at BOTH. Polarities were EMPIRICALLY ground-      ===
# ===  truthed against the real classifier before codification (the --m     ===
# ===  boundary control is a False==False vector reclassified OUT of the     ===
# ===  closure set — a closure that does not flip would be vacuous).         ===
# ===========================================================================


# --- FOLD STRIP-SURFACE MECHANISM PINS — document HOW each fold closure/retention happens.
class TestFoldStripSurfaceMechanism:
    def test_f1_abbrev_value_stripped(self):
        # F1: the long arm matches the whole abbreviation, so the value strips to the inert bareword.
        assert STRIP('git commit --me "run %s later"' % BD) == "git commit --me STRIPPED"
        assert STRIP('git commit --messag "run %s later"' % BD) == "git commit --messag STRIPPED"

    def test_dashm_boundary_stripped_at_head(self):
        # The `--m` boundary control: the long arm matches --m and strips the value at HEAD too -> False.
        assert STRIP('git commit --m "run %s later"' % BD) == "git commit --m STRIPPED"

    def test_tag_merged_cosmetic_strip(self):
        # Lead pin: the UNCHANGED short arm cosmetically strips --merged -> --mSTRIPPED, but the
        # positional survives and the command stays a non-dangerous read (False). Documented, harmless.
        assert STRIP('git tag --merged mainbranch') == "git tag --mSTRIPPED mainbranch"
        assert STRIP('git tag --no-merged mainbranch') == "git tag --no-mSTRIPPED mainbranch"

    def test_f2_save_positional_stripped(self):
        assert STRIP('git stash save "run %s later"' % BD) == "git stash save STRIPPED"
        assert STRIP('git stash save -u "run %s later"' % BD) == "git stash save -u STRIPPED"

    def test_f2_mergebase_not_mishandled(self):
        # `git merge-base` must survive intact (no message flag for the carrier to anchor on).
        assert STRIP('git merge-base main feat') == "git merge-base main feat"

    def test_f3_span_scoped_strip(self):
        # F3: inert literal -> STRIPPED, the benign span preserved VERBATIM.
        assert STRIP('git commit -m "use \\"$(date)\\" then %s"' % BD) == 'git commit -m "STRIPPED$(date)STRIPPED"'

    def test_f3_residual_whole_value_preserved(self):
        # Fail-safe residual: the whole value is preserved (danger survives -> stays caught), == pre-fold.
        s = STRIP('git commit -m "fix $(basename \\"$d\\") then %s"' % BD)
        assert BD in s, "the fail-safe residual must preserve the whole value (danger stays visible): %r" % s


# --- FOLD ReDoS LINEARITY pins (ReDoS is an over-block BY TIMEOUT). The F3 scanners + the F2 save
#     flag-run must be linear; the measured cost is ~50 ms at n=16000 (bounds are generous vs that).
class TestFoldReDoSLinearity:
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_f3_scanner_linear(self, n):
        assert _elapsed_ms('git commit -m "' + "$(" * n + '"') < _LINEAR_BOUND_MS, "unterminated $( n=%d" % n
        assert _elapsed_ms('git commit -m "$(' + "(" * n + ")" * n + ')"') < _LINEAR_BOUND_MS, "nested parens n=%d" % n
        assert _elapsed_ms('git commit -m "' + "'" * n + '$(date)"') < _LINEAR_BOUND_MS, "apostrophe-run n=%d" % n
        assert _elapsed_ms('git commit -m "' + "$" * n + '"') < _LINEAR_BOUND_MS, "dollar-run n=%d" % n

    @pytest.mark.parametrize("n", [4000, 16000])
    def test_f2_save_flag_run_linear(self, n):
        # The stash-save positional anchor `(save(?:\s+-[-\w]+)*\s+)` is a deterministic per-flag loop.
        assert _elapsed_ms('git stash save ' + "-a " * n + '"msg"') < _LINEAR_BOUND_MS, "save flag-run n=%d" % n


# ===========================================================================
# ===  #1176 CARRIER-4 EQUALS-FORM — remediation cycle 3. _strip_var_dq now  ===
# ===  routes a non-expanded, non-config NAME="...$()..." value through the   ===
# ===  SAME span scanner the message carriers use (_var_is_expanded FIRST +   ===
# ===  a `.`-guard keeping git -c section.key= config injections at status    ===
# ===  quo). NON-VACUITY vs D_PREFIX_C4 (bf7c8786 = 00195c1a^): every         ===
# ===  equals-form closure flips True(pre-fix) -> False(HEAD). True-positives, ===
# ===  the `-c` status-quo pins, and expanded/eval hold True at BOTH; the      ===
# ===  no-$() literals / sq forms hold False at BOTH. Polarities EMPIRICALLY   ===
# ===  ground-truthed (bf7c8786 AND 3972bb5f) before codification.            ===
# ===========================================================================


class TestC4StripSurfaceMechanism:
    # Document HOW each disposition happens on the real strip surface (so a mechanism regression is
    # visible, not just a boolean flip).
    def test_equals_span_scoped(self):
        assert STRIP('git commit --message="as of $(date): note %s"' % BD) \
            == 'git commit --message="STRIPPED$(date)STRIPPED"'

    def test_general_foo_span_scoped(self):
        assert STRIP('FOO="$(date) note %s"' % BD) == 'FOO="$(date)STRIPPED"'

    def test_nosub_literal_stripped_whole(self):
        assert STRIP('FOO="just note %s"' % BD) == "FOO=STRIPPED"

    def test_dot_guard_preserves_config_value_whole(self):
        # The `.`-guard preserves the WHOLE -c config value (danger stays visible -> caught == base).
        s = STRIP('git -c user.name="$(date) %s" commit' % BD)
        assert BD in s, "the `.`-guard must preserve the whole -c value (status quo): %r" % s
        assert s == 'git -c user.name="$(date) %s" commit' % BD

    def test_nondot_flag_is_span_scoped_not_guarded(self):
        # The `.`-guard must NOT fire on a `--flag=` name (preceded by `-`, not `.`) -> span-scoped.
        # Leading $(date) span preserved, trailing inert literal -> STRIPPED (no leading literal here).
        assert STRIP('git commit --message="$(date) %s"' % BD) == 'git commit --message="$(date)STRIPPED"'

    def test_c_no_sub_config_stripped_11_gap(self):
        # #11 gap unchanged: a -c config value with NO $() is stripped by carrier-4's no-$() arm.
        assert STRIP('git -c core.pager="%s" commit' % BD) == "git -c core.pager=STRIPPED commit"


class TestC4ReDoSLinearity:
    # The reused _preserve_substitution_spans scanner is O(n) and the `.`-guard is O(1); a light
    # linearity pin on the equals-form var-assignment path (measured ~73 ms at n=16000).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_equals_form_path_linear(self, n):
        assert _elapsed_ms('FOO="' + "$(" * n + '"') < _LINEAR_BOUND_MS, "FOO= unterminated $( n=%d" % n
        assert _elapsed_ms('FOO="$(' + "(" * n + ")" * n + ')"') < _LINEAR_BOUND_MS, "FOO= nested parens n=%d" % n
        assert _elapsed_ms('FOO="' + "'" * n + '$(date)"') < _LINEAR_BOUND_MS, "FOO= apostrophe-run n=%d" % n


# ===========================================================================
# ===  #1176 F-C1 4-CARRIER CURE — remediation cycle 4 (the FINAL           ===
# ===  coarse-substitution-preserve closure). The last 4 prose carriers      ===
# ===  (C3 echo/printf, C6 here-string, C8 HTTP-body -d/--data, C9 gh-api     ===
# ===  -q/--jq) now span-scope has-substitution values via                   ===
# ===  _preserve_substitution_spans (NATIVE DQ output). NON-VACUITY vs        ===
# ===  D_PREFIX_FC1 (a62703f1 = 3f25c8ea^): every closure flips True(pre-fix) ===
# ===  -> False(HEAD); every per-carrier TRUE-POSITIVE holds True==base (the  ===
# ===  load-bearing native-dq under-block check — a TP->False would be a      ===
# ===  security under-block); controls hold False==False. The echo/printf     ===
# ===  MULTI-ARG anchor gap is pinned as a PRE-EXISTING out-of-scope boundary ===
# ===  (True==base, over-blocks even with NO substitution). EMPIRICALLY       ===
# ===  ground-truthed at a62703f1 vs HEAD before codification.               ===
# ===========================================================================

_FC1_URL = "http://example.com/api"


class TestFC1StripSurfaceMechanism:
    # Document the native-dq span-scope surface (literal -> STRIPPED, span preserved VERBATIM) per
    # carrier, so a mechanism regression is visible.
    def test_echo_span_scoped(self):
        assert STRIP('echo "as of $(date): note %s"' % BD) == 'echo "STRIPPED$(date)STRIPPED"'

    def test_echo_truepositive_span_preserved(self):
        assert STRIP('echo "$(%s)"' % BD) == 'echo "$(%s)"' % BD

    def test_herestring_span_scoped(self):
        assert STRIP('cat <<< "as of $(date): note %s"' % BD) == 'cat <<<"STRIPPED$(date)STRIPPED"'

    def test_curl_data_native_dq_span_scoped(self):
        # C8: the has-$() output is DOUBLE-quoted (native dq), NOT the single-quoted 'STRIPPED' the
        # no-$() path emits — this keeps the preserved span executing (the under-block-safety hinge).
        assert STRIP('curl -d "as of $(date): note %s" %s' % (BD, _FC1_URL)) \
            == 'curl -d "STRIPPED$(date)STRIPPED" %s' % _FC1_URL

    def test_curl_data_truepositive_span_preserved(self):
        assert STRIP('curl -d "$(%s)" %s' % (BD, _FC1_URL)) == 'curl -d "$(%s)" %s' % (BD, _FC1_URL)

    def test_gh_selector_native_dq_span_scoped(self):
        assert STRIP('gh api repos/o/r -q "as of $(date): %s"' % BD) \
            == 'gh api repos/o/r -q "STRIPPED$(date)STRIPPED"'


class TestFC1ReDoSLinearity:
    # The reused _preserve_substitution_spans scanner is O(n) across all 4 carriers; a light linearity
    # pin (measured ~46 ms at n=16000).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_carrier_scanner_linear(self, n):
        assert _elapsed_ms('echo "' + "$(" * n + '"') < _LINEAR_BOUND_MS, "echo unterminated $( n=%d" % n
        assert _elapsed_ms('curl -d "$(' + "(" * n + ")" * n + ')" ' + _FC1_URL) < _LINEAR_BOUND_MS, "curl -d nested parens n=%d" % n


# ===========================================================================
# ===  #1176 ECHO/PRINTF MULTI-ARG CARVE-OUT — remediation cycle 5b. The     ===
# ===  carrier-3 first-arg-only strip is replaced by a span+per-arg loop that ===
# ===  strips EVERY positional echo/printf arg (bounded to echo/printf, which ===
# ===  never execute their args). NON-VACUITY vs D_PREFIX_ECHO (a542e21b =    ===
# ===  e2145b44^): every 2nd+ arg over-block flips True(pre-carve)->False;     ===
# ===  under-block guards (piped/procsub/tail/inline-malicious) hold True;     ===
# ===  the still-open C5/C7/C8 positional class stays pinned True==both        ===
# ===  (deferred audit). EMPIRICALLY ground-truthed at a542e21b vs HEAD.       ===
# ===========================================================================


class TestEchoPrintfCarveoutStripSurface:
    # Document the span+per-arg-loop surface: EVERY quoted arg -> STRIPPED (dq span-scoped, sq
    # bareword); the verb stays intact; leg-locality keeps a tail outside; a $() span is preserved.
    def test_all_args_stripped(self):
        assert STRIP('echo "first arg" "note %s"' % BD) == "echo STRIPPED STRIPPED"

    def test_mixed_quote_args_stripped(self):
        assert STRIP('echo "a" \'note %s\'' % BD) == "echo STRIPPED STRIPPED"

    def test_printf_fmt_and_data_stripped(self):
        assert STRIP('printf "%%s\\n" "note $(date) %s"' % BD) == 'printf STRIPPED "STRIPPED$(date)STRIPPED"'

    def test_leg_locality_tail_preserved(self):
        assert STRIP('echo "x" && %s' % BD) == "echo STRIPPED && %s" % BD

    def test_piped_whole_preserved(self):
        # The outer piped-to-shell skip preserves the whole command (danger stays visible -> caught).
        assert STRIP('echo "%s" | bash' % BD) == 'echo "%s" | bash' % BD

    def test_inline_second_arg_substitution_preserved(self):
        assert STRIP('echo "a" "$(%s)"' % BD) == 'echo STRIPPED "$(%s)"' % BD


class TestEchoPrintfCarveoutReDoS:
    # The span + per-arg loop is O(total length); a many-args input and a nested-paren input both stay
    # linear (measured ~142 ms at 16000 args, ~50 ms nested).
    @pytest.mark.parametrize("n", [4000, 16000])
    def test_carveout_linear(self, n):
        assert _elapsed_ms('echo ' + '"a" ' * n + '"%s"' % BD) < _LINEAR_BOUND_WIDE_MS, "echo many-args n=%d" % n
        assert _elapsed_ms('echo "$(' + "(" * n + ")" * n + ')"') < _LINEAR_BOUND_WIDE_MS, "echo nested parens n=%d" % n
