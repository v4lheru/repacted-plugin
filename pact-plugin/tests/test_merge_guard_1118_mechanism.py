"""
Location: pact-plugin/tests/test_merge_guard_1118_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: Bidirectional base-vs-HEAD-vs-PATCH re-cert matrix for the #1118 QUOTE-SAFE re-model
         (design §7). The shipped carrier 9 (space-only, commit 38f76965) had a quote-unsafe
         value strip whose dangling `"` merged legs and defeated per-leg isolation in BOTH
         directions (SEC-1 over-block / SEC-2 under-block), plus an incomplete cure (RED-1:
         `=`/attached flag spellings) and a shared carrier-8 flaw. The PATCH (commit 6d71a816,
         current HEAD) replaces the non-quote-aware `[^\\s'"]\\S*` matcher with a shared
         quote-BALANCED `_VALUE_TOKEN` consumed via `_strip_flag_values` by BOTH carriers 8 & 9,
         and a form-aware `_selector_flagsep` (space / =-long / =-short / attached-short).

         WHY THIS SUITE EXISTS (design §2.4): the original 44-test matrix was SPACE-FORM-ONLY and
         proved `+N/-0` additive SOURCE, not additive BEHAVIOR — it gave ZERO coverage for the
         embedded-quote leg-merge (SEC-1/SEC-2), the attached-spelling over-block (RED-1), the
         carrier-8 twin, or the leg-count mechanism. That coverage gap is why the regression
         shipped. This suite locks the whole class permanently, asserting a base-vs-HEAD-vs-PATCH
         DIFFERENTIAL (not a bare pass) so each cure row is non-vacuous by construction.

         base  = c5e9b324 (no carrier 9)         — the correct pre-carrier-9 behavior
         HEAD  = 38f76965 (buggy space-only)     — the regressed behavior the PATCH fixes
         PATCH = 6d71a816 (quote-safe re-model)  — the live/shipped module (current HEAD)

         The base/HEAD modules load from vendored fixtures (tests/fixtures/merge_guard_baseline/)
         through load_vendored, under __package__='shared', pinned by git blob id. A missing or
         drifted fixture fails the file; no row skips.
Used by: pytest (merge-guard suite).

Dangerous substrings are assembled at runtime (M / GR / PR_MERGE) so this file carries no raw
`gh pr merge` / `git/refs` literal — mirrors the coder + cert files' probe-harness convention.
"""
from typing import Any


import shared.merge_guard_common as PATCH  # noqa: E402  (live module == current HEAD == the PATCH)
from merge_guard_baseline_loader import load_vendored  # noqa: E402

# Dangerous substrings assembled at runtime — never a raw literal in the source.
M = "mer" + "ge"                 # merge
GR = "git" + "/refs"             # git/refs

_BASE_SHA = "c5e9b324"   # pre-carrier-9 baseline (permanent merged commit)
_HEAD_SHA = "38f76965"   # buggy space-only original carrier 9 (permanent merged commit)

# Annotated Any: a loaded module has no static type.
_BASE: Any = load_vendored(_BASE_SHA)
_HEAD: Any = load_vendored(_HEAD_SHA)


class TestOverMatchAnchorNegatives:
    def test_mid_token_dash_q_in_positional_not_stripped(self):
        # `(?<!\S)` anchors the selector flag at a token boundary, so a `-q` embedded inside a
        # positional (`some-q-...`) is NOT read as the --jq short flag: the token survives verbatim
        # in the stripped surface (proving the strip did not mis-consume the following chars).
        cmd = f"gh api repos/o/r/some-q-{M}able -f x=y"
        stripped = PATCH._strip_non_executable_content(cmd)
        assert f"some-q-{M}able" in stripped


# ======================================================================================
# §7.8 Leg-count regression guard — the mechanism-level signature of the leg-merge bug
# ======================================================================================
class TestLegCountRegressionGuard:
    SEC1 = f'gh api repos/o/r/pulls/5/{M} -X GET -q p"q r" && gh api "x/y" -X POST -f a=b'

    def test_patch_splits_two_legs(self):
        # Absolute PATCH mechanism guard: the dangling-quote compound splits into 2 legs (the
        # quote-safe strip never merges them). This is the mechanism the regression corrupted.
        assert len(PATCH._split_into_legs(self.SEC1)) == 2

    def test_leg_count_differential(self):
        # The regression signature: base=2 legs (correct), HEAD=1 leg (merged — the bug), PATCH=2
        # (restored). Pins that the mechanism cannot silently regress to the merged state.
        assert len(_BASE._split_into_legs(self.SEC1)) == 2
        assert len(_HEAD._split_into_legs(self.SEC1)) == 1
        assert len(PATCH._split_into_legs(self.SEC1)) == 2


# ======================================================================================
# §7.10 Structural group-index guards — pin the shared helper's capturing-group contract
# ======================================================================================
class TestStructuralGroupIndexGuards:
    def test_value_token_is_non_capturing(self):
        # The shared _VALUE_TOKEN primitive MUST stay non-capturing (0 groups): keep_fn reads
        # m.group(1) = the flag+separator, and the sq arm backrefs \1 = the flag+separator. A stray
        # capturing group in _VALUE_TOKEN would shift those indices and silently mis-strip.
        import re
        assert re.compile(PATCH._VALUE_TOKEN).groups == 0

    def test_strip_flag_values_group1_contract(self):
        # Behavioral pin of the group-index contract via the importable shared helper: with a
        # ONE-group flag_sep_regex, both the dq/unquoted keep_fn (m.group(1)) and the sq arm (\1)
        # must resolve to the flag+separator. A group-count drift would raise or mis-render here.
        def _keep(m):
            return m.group(1) + "'STRIPPED'"
        # single-quoted value -> sq arm's \1 backref
        assert PATCH._strip_flag_values("-q '.secret'", r"(-q\s+)", _keep) == "-q 'STRIPPED'"
        # unquoted VALUE-TOKEN -> keep_fn's m.group(1)
        assert PATCH._strip_flag_values("-q .secret", r"(-q\s+)", _keep) == "-q 'STRIPPED'"

    def test_sq_flag_token_round_trip_through_real_pipeline(self):
        # Through the REAL _strip_non_executable_content (exercising the function-local
        # _selector_flagsep): a held write's -q selector keeps its flag+separator and its value
        # becomes the balanced 'STRIPPED' placeholder (no dangling quote).
        cmd = f"gh api -X DELETE repos/o/r/{GR}/heads/x -q '.{M}able'"
        stripped = PATCH._strip_non_executable_content(cmd)
        assert "-q " in stripped
        assert "'STRIPPED'" in stripped
        assert f".{M}able" not in stripped
