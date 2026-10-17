"""
Tests for merge_guard_pre._GH_PR_NUMBER_RE — PR-number extraction.

Pins the regex behavior: `_GH_FLAG_TOKENS` restricts BOTH flag-walks (between
`gh` and `pr`, AND between subcommand and PR number) to flag-shaped tokens
only (`-x`, `--long`, optionally `--flag value`). The `(?![\\w-])` negative-
lookahead on the (\\d+) capture rejects suffix matches inside longer
alphanumeric-or-hyphenated tokens.

Each TRUE-GAIN test below cites the exact OLD-vs-NEW behavioral delta — these
are the cases that were broken on main (regex captured a non-PR digit token
from heredoc body / 2>&1 redirect / trailing positional / branch-name suffix)
and now correctly extract the PR number or return None.

Two previously-xfail-strict cases are now FIXED in this file:

  1. "heredoc body containing a fully-formed `gh pr merge <N>` substring":
     `_GH_PR_NUMBER_RE` uses the tight `_GH_FLAG_TOKENS` form for BOTH the
     pre-subcommand AND post-subcommand flag walks (rather than reusing the
     broad `_GH_GLOBAL_FLAGS` for the pre-subcommand walk). This eliminates
     the re-anchor-at-second-occurrence authorization-bypass class.

  2. "branch-name suffix `7352-tests`": Python `\\b` IS a word boundary at
     digit-to-hyphen (word char `2` to non-word char `-`). The earlier spec
     assumed `\\b` would reject this; that assumption was wrong about Python
     regex semantics. The fix replaces the trailing `\\b` with `(?![\\w-])`
     (negative lookahead) — strictly stronger: rejects any continuation that
     is a word char OR a hyphen.

Both `test_heredoc_body_with_embedded_gh_pr_merge` and
`test_branch_name_with_digit_prefix_suffix_match` are now regular passing
tests pinning the fixes; the new `test_authorization_mismatch_attack` test
pins the end-to-end attack shape that the heredoc-side fix prevents.
"""



from merge_guard_pre import _GH_PR_NUMBER_RE  # noqa: E402


def _capture(command: str):
    """Helper: run the regex and return the captured PR number, or None."""
    m = _GH_PR_NUMBER_RE.search(command)
    return m.group(1) if m else None


# =============================================================================
# TRUE GAINS — cases broken on main, fixed by the regex tightening
# =============================================================================
# Each test in this class documents the OLD-vs-NEW behavioral delta. On main,
# the regex used the broad `_GH_GLOBAL_FLAGS` for the post-subcommand walk,
# which greedily consumed tokens past the PR positional and captured the LAST
# digit reachable. After tightening to `_GH_FLAG_TOKENS`, each of these cases
# now captures the real positional PR number.
#
# Selection criterion: a test belongs in TrueGains iff source-only revert of
# the regex tightening commit causes the test to FAIL. Cases that pass on
# both pre-fix and post-fix source belong in NoRegressionCanaries (sibling
# class below) instead.
#
# Counter-test cardinality: source-only revert of the regex tightening
# (`git checkout <pre-fix-sha> -- pact-plugin/hooks/merge_guard_pre.py`)
# causes ALL 4 tests in this class to FAIL with the regex capturing the
# wrong digit. Empirically verified during PR #697 review.

class TestGH_PR_NumberRE_TrueGains:
    """Cases broken on main, fixed by the regex tightening.

    Each test fails on source-only revert of the fix commit. This is the
    discriminating criterion: TRUE GAINS cause failures when the fix is
    reverted. Compare with `TestGH_PR_NumberRE_NoRegressionCanaries`
    (sibling class) where the cases pass on both pre-fix and post-fix
    source — those are no-op protection canaries, not behavioral deltas.
    """

    def test_subject_with_version_digits_captures_pr(self):
        # OLD captured "7352" (last digit in subject string).
        # NEW correctly captures the PR positional "663".
        cmd = 'gh pr merge 663 --squash --subject "v4.1.7 release notes ships 7352 tests"'
        assert _capture(cmd) == "663"

    def test_body_with_version_digits_captures_pr(self):
        # OLD captured "7352" (last digit in body content).
        # NEW correctly captures "663".
        cmd = (
            'gh pr merge 663 --squash --subject foo '
            '--body "v4.1.7 ships 7352 tests passing"'
        )
        assert _capture(cmd) == "663"

    def test_stderr_redirect_captures_pr(self):
        # OLD captured "2" (the "2" of "2>&1" redirect).
        # NEW correctly captures "663".
        cmd = "gh pr merge 663 --squash 2>&1"
        assert _capture(cmd) == "663"

    def test_trailing_positional_text_with_digits(self):
        # OLD captured "7352" from trailing positional text.
        # NEW correctly captures "663".
        cmd = "gh pr merge 663 with 7352 tests passing"
        assert _capture(cmd) == "663"


# =============================================================================
# NO-REGRESSION CANARIES — cases unchanged by the fix; pinned as protection
# =============================================================================
# These tests pass on BOTH pre-fix and post-fix source. They do not demonstrate
# behavioral gain from the fix; instead they pin currently-correct behavior so
# a future regex change cannot silently regress these working cases.
#
# Selection criterion: a test belongs in NoRegressionCanaries iff source-only
# revert of the regex tightening commit leaves the test PASSING. The fix is
# orthogonal to these inputs.
#
# Counter-test cardinality: source-only revert of the regex tightening leaves
# ALL 5 tests in this class PASSING. Empirically verified during PR #697
# review. If a future regression causes any of these to fail under the
# current source, the canary fires loudly.

class TestGH_PR_NumberRE_NoRegressionCanaries:
    """Cases unchanged by the fix — pinned as future-regression tripwires.

    Distinct from `TestGH_PR_NumberRE_TrueGains` (sibling class above)
    where source-only revert of the fix causes failure: these cases pass
    regardless of whether the fix is in place. Their value is forward-
    looking — a future "cleanup" or refactor of the regex that broke any
    of these inputs would be caught by the canary firing.
    """

    def test_body_file_with_versioned_path(self):
        # Empirical probe: OLD captured "663" here (the path's slashes/
        # hyphens are non-word and `\S+\s+` requires whitespace separation,
        # so the broad walk consumes the path and backtracks to the
        # positional). Pinned as a no-op canary so a future regex change
        # cannot regress this case to capturing "7352".
        cmd = "gh pr merge 663 --body-file /tmp/release-notes-v4.1.7-7352.md --squash"
        assert _capture(cmd) == "663"

    def test_simple_squash_unaffected(self):
        # Simple case: no flags between subcommand and PR positional.
        cmd = "gh pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_pre_subcommand_flag_unaffected(self):
        # `--admin` between merge subcommand and PR number.
        cmd = "gh pr merge --admin 663 --squash"
        assert _capture(cmd) == "663"

    def test_global_flag_before_subcommand_unaffected(self):
        # `--repo owner/repo` as a gh global flag (pre-subcommand).
        cmd = "gh --repo owner/repo pr merge 663 --squash"
        assert _capture(cmd) == "663"

    def test_close_with_delete_branch_captures_pr(self):
        # `gh pr close` form with `--delete-branch` (sibling subcommand).
        cmd = "gh pr close 663 --delete-branch"
        assert _capture(cmd) == "663"


# =============================================================================
# ACCEPTABLE-NONE — degradation to permissive behavior
# =============================================================================
# When the PR number cannot be unambiguously located, the regex returns None.
# The caller (_token_authorizes_command) treats None as ambiguous and falls
# back to permissive (returns True). This preserves the existing semantic.

class TestGH_PR_NumberRE_AcceptableNone:
    """Cases where the regex correctly returns None (permissive degradation)."""

    def test_no_positional_pr_number(self):
        # gh pr merge --auto has no positional PR number; None is correct.
        cmd = "gh pr merge --auto"
        assert _capture(cmd) is None


# =============================================================================
# AUTHORIZATION-BYPASS FIXES — regression-protection for prior xfail cases
# =============================================================================
# Two cases that were previously pinned as xfail-strict are now passing
# tests after the regex tightening:
#   1. heredoc body containing embedded `gh pr merge <N>` — fixed by tight
#      flag-walks on BOTH sides of `pr <subcmd>`.
#   2. branch-name argument with digit prefix (e.g., `7352-tests`) — fixed
#      by replacing `\b` with `(?![\w-])` (rejects hyphen continuation).

class TestGH_PR_NumberRE_AuthorizationBypassFixed:
    """Fixed authorization-bypass class — pinned as regression-protection.

    These tests pin the regex tightening that closed an authorization-bypass
    where a body string containing `gh pr merge <fake_PR>` could re-anchor
    the regex past the real positional and cause the token-context check
    to compare against the embedded fake PR rather than the real one.
    """

    def test_heredoc_body_with_embedded_gh_pr_merge(self):
        """Body string with embedded `gh pr merge <N>` no longer re-anchors.

        Previously xfail-strict (the regex captured 999 because the broad
        pre-subcommand flag walk consumed past `663 --body "see also gh `
        and re-anchored at the second `pr merge`). After tightening BOTH
        flag-walks to flag-shaped tokens only, the broad walk cannot
        consume past quoted body content, so the first-occurrence anchor
        sticks and the real positional is captured.
        """
        cmd = 'gh pr merge 663 --body "see also gh pr merge 999 example"'
        assert _capture(cmd) == "663"

    def test_authorization_mismatch_attack(self):
        """End-to-end shape of the authorization-bypass attack now blocked.

        Resolves the BLOCKING finding from PR #697 review.

        Attack shape: an attacker (or an honest user with a verbose body)
        constructs `gh pr merge <real> --body "...gh pr merge <fake>..."`.
        Pre-fix, the AskUserQuestion authorization issued for `<real>`
        would be matched against `<fake>` extracted by the regex and
        REJECTED with "Authorization token exists but does not match this
        operation" — masking the real merge. Worse, an attacker could craft
        the body so that the AskUserQuestion-issued token (for `<fake>`,
        a non-existent PR they mention in the body of a different merge)
        authorizes the actual `<real>` merge they intended to bypass.

        Post-fix: the regex always extracts the real positional regardless
        of body content, so the token-context check matches correctly.
        """
        cmd = (
            'gh pr merge 663 --body "$(cat <<EOF\\n'
            'Fixes #999. See related: gh pr merge 999 --admin\\n'
            'EOF\\n)" --squash'
        )
        assert _capture(cmd) == "663"


    def test_branch_name_with_digit_prefix_suffix_match(self):
        """Branch-name argument with digit prefix no longer captures the digit.

        Previously xfail-strict (Python `\\b` IS a word boundary at
        digit-to-hyphen, so `\\b` after `(\\d+)` allowed `7352` to match
        from `7352-tests`). After replacing `\\b` with `(?![\\w-])` the
        trailing-hyphen continuation is rejected and the regex returns
        None, which the caller treats as ambiguous-and-permissive (the
        intended degradation when no clean digit token can be extracted).
        """
        cmd = "gh pr merge 7352-tests --squash"
        assert _capture(cmd) is None


# =============================================================================
# Boundary verification — `\b` semantics that DO work
# =============================================================================
# Document what `\b` correctly rejects, complementing the limitation above.

class TestGH_PR_NumberRE_BoundaryCorrect:
    """The `\\b` boundary correctly rejects alphanumeric suffix matches."""

    def test_no_match_when_followed_by_alpha(self):
        # `\b` works correctly between digit and letter.
        cmd = "gh pr merge 7352abc --squash"
        assert _capture(cmd) is None


# =============================================================================
# The value arm's `[^-\s]` constraint — WHICH DIGIT IS THE PR NUMBER
# =============================================================================
# `_GH_FLAG_TOKENS` constrains a flag's optional value to `[^-\s]\S*`, so a
# dash-initial token cannot be consumed AS A VALUE by that iteration; the outer
# `(?:...)*` consumes it AS A FLAG on the next one instead.
#
# THAT REPARTITIONING CHANGES WHICH DIGIT IS CAPTURED, and the constraint is
# therefore load-bearing for CORRECTNESS, not only for backtracking. Where the
# flag run stops decides which token is read as the positional PR number, and
# the unconstrained predecessor stops in a different place:
#
#   gh pr merge --squash -t 2024 42       constrained -> 42   pre-fix -> 2024
#   gh pr merge --squash --repo 2024 42   constrained -> 42   pre-fix -> 2024
#
# The predecessor captures the FLAG'S OWN ARGUMENT as the pull-request number.
# On a guard that binds an approval to a specific pull request, that is a
# TARGET-IDENTIFICATION defect: approve one pull request, authorise another.
#
# THE EXAMPLES ARE SHORT-FORM AND `--repo` BECAUSE THE LONG FORMS DO NOT SHOW
# IT. `_extract_pr_number` abstains when the token before the captured digit is
# in `_GH_PR_VALUE_TAKING_FLAGS`, and the caller then recovers the right target
# via `_extract_merge_target`. Every member of that frozenset is long-form, so
# `--subject`/`--body-file` are silently repaired while short aliases and
# `--repo` are not — see TestExtractPrNumberFlagFormAsymmetry below, which pins
# both arms. An earlier version of this comment used `--subject` and
# `--body-file`, so NEITHER of its examples actually misbound: a reader who
# checked them would have concluded the warning overclaimed.
#
# REACHABLE ON GOOD-FAITH COMMANDS, which is what makes it worth pinning: a
# `-t` subject that is a year, a `-F` body file named `1`. No exotic input is
# required, only a number in the wrong place.
#
# WHY THIS WAS NOT CAUGHT BY THE PROSE THAT CERTIFIED IT: the divergent shape
# needs a VALUELESS flag followed by a VALUE-TAKING flag before the positional.
# Hand-picked corpora do not contain it, because an author picks the shapes
# they are already thinking about. Enumerating the space finds it immediately.


class TestGH_PR_NumberRE_DashInitialValues:
    """A dash-initial VALUE is still consumed and the positional still wins.

    ALL THREE OF THESE PASS ON THE BROADENED FORM TOO, and saying so is the
    point rather than an apology. They pin the constraint against a NARROWING —
    that adding `[^-\\s]` did not stop these forms extracting a number at all —
    and both partitions reach the same digit here, so they cannot witness the
    capture divergence. The class below is what discriminates the two forms.

    These were the shapes certified in prose, and their agreement is exactly why
    that prose read as a proof: a corpus of agreeing forms cannot distinguish
    "the two patterns are equivalent" from "I picked the cases where they
    agree". Kept, labelled, and no longer load-bearing for equivalence.
    """

    def test_dash_initial_value_before_the_pr_number(self):
        assert _capture("gh pr merge --subject -x 42") == "42"

    def test_dash_initial_value_with_a_following_flag(self):
        assert _capture("gh pr merge --body -weird --auto 99") == "99"

    def test_dash_initial_value_that_is_itself_a_digit_token(self):
        assert _capture("gh pr merge --body -1 --auto 55") == "55"


class TestGH_PR_NumberRE_NumericFlagValueDoesNotStealTheCapture:
    """A NUMERIC flag argument must never be read as the pull-request number.

    These are the forms where the value arm's constraint actually decides the
    answer — the unconstrained predecessor returns the flag's argument here.
    Pinned because a form-pin containing only AGREEING shapes reproduces the
    sampling error that let the false equivalence stand in the first place.
    """

    def test_numeric_subject_does_not_become_the_pr_number(self):
        assert _capture("gh pr merge --squash --subject 2024 42") == "42"

    def test_numeric_body_file_does_not_become_the_pr_number(self):
        assert _capture("gh pr merge --squash --body-file 1 42") == "42"

    def test_numeric_value_after_an_auto_flag(self):
        assert _capture("gh pr merge --auto --body 7 1234") == "1234"

    def test_close_verb_is_covered_by_the_same_partition(self):
        assert _capture("gh pr close --admin --comment 99 42") == "42"


# =============================================================================
# The long-form / short-form asymmetry, at the layer where it exists
# =============================================================================
# EVERY TEST ABOVE USES THE RAW REGEX, AND THE RAW REGEX TREATS A SHORT FLAG AND
# A LONG FLAG IDENTICALLY. The asymmetry lives one layer up, in
# `_extract_pr_number`, which re-checks the token preceding the captured digit
# against `_GH_PR_VALUE_TAKING_FLAGS` and abstains on a hit so the caller can
# recover the target. No test reached that layer, which is why prose was
# carrying the whole claim.
#
# THE ASYMMETRY IS COUNTERFACTUAL, and that is why no green-tree observation
# exhibits it. On the shipped pattern all of these return "42" and agree — the
# re-check never even fires, because the shipped capture is already the
# positional, so the token before it is a value rather than a flag. The
# frozenset exists to catch a MISBEHAVING pattern. So the only way to observe
# what it does and does not repair is to supply one, which this class does by
# swapping the compiled pattern for its unconstrained predecessor.
#
# IF YOU ARE ASKING WHY NO EXISTING TEST CAUGHT THIS, THE ANSWER IS STRUCTURAL
# AND NOT AN OVERSIGHT TO APPORTION: none could have. The claim being guarded is
# about what a FUTURE change would do, and a property of a change that has not
# been made is not observable in the tree that has not made it. Every arm here
# agrees on shipped code — that is the control below, not a weakness. So until
# someone built the counterfactual the claim had nowhere to live except prose,
# and prose is the one carrier with no failure mode. That is also why this class
# looks unusual: a counterfactual claim needs a counterfactual harness.

import re  # noqa: E402

import pytest  # noqa: E402

from shared import merge_guard_common as _mgc  # noqa: E402

_BROAD_FLAG_TOKENS = r"(?:-\S*(?:\s+\S+)?\s+)*"

# Captured at import, before any fixture swaps the module attribute, so a test
# can compare the two arms within one broadened run.
_SHIPPED_PR_NUMBER_RE = _mgc._GH_PR_NUMBER_RE


class TestExtractPrNumberFlagFormAsymmetry:
    """What the value-taking-flag re-check repairs, and what it leaves broken."""

    @pytest.fixture
    def broadened(self, monkeypatch):
        """Swap in the unconstrained predecessor for the duration of one test.

        Patched on `merge_guard_common` because `_extract_pr_number` reads the
        module global at call time. `monkeypatch` re-arms per test, so a later
        module reload cannot silently disarm it mid-run.
        """
        broad = re.compile(
            _mgc._GH_PR_NUMBER_RE.pattern.replace(
                _mgc._GH_FLAG_TOKENS, _BROAD_FLAG_TOKENS
            )
        )
        assert broad.pattern != _mgc._GH_PR_NUMBER_RE.pattern, (
            "the arm swap produced an identical pattern, so this fixture is "
            "inert and both assertions below would describe the shipped form. "
            "`_GH_FLAG_TOKENS` was renamed or is no longer a literal substring "
            "of the composed pattern — re-point this, do not delete it."
        )
        monkeypatch.setattr(_mgc, "_GH_PR_NUMBER_RE", broad)

    def test_shipped_pattern_binds_correctly_for_both_flag_forms(self):
        """CONTROL, and it is the reason the class needs the fixture at all:
        with no broadening there is no asymmetry to see."""
        assert _mgc._extract_pr_number("gh pr merge --squash --subject 2024 42") == "42"
        assert _mgc._extract_pr_number("gh pr merge --squash -t 2024 42") == "42"

    def test_long_form_in_the_frozenset_is_neutralised(self, broadened):
        """A long form IS re-checked, so the extractor abstains and the caller
        recovers the target. This is why `--subject` cannot demonstrate the
        defect — it is repaired before anything downstream sees it."""
        assert _mgc._extract_pr_number("gh pr merge --squash --subject 2024 42") is None

    def test_short_alias_is_not_neutralised_and_misbinds(self, broadened):
        """A SHORT alias is invisible to the re-check, which is `(--[\\w-]+)$`
        and can only match a long form. So the extractor returns the FLAG'S
        argument as the pull-request number.

        ADDING `-t` TO THE FROZENSET WOULD NOT FIX THIS — the re-check would
        still not match it. That is the half of the mechanism a reader is most
        likely to get wrong, so it is pinned rather than described."""
        assert _mgc._extract_pr_number("gh pr merge --squash -t 2024 42") == "2024"

    def test_repo_is_long_form_but_absent_from_the_frozenset(self, broadened):
        """`--repo` IS visible to the re-check and still misbinds, because it is
        not a member. Unlike the short aliases, this half WOULD be fixed by
        extending the frozenset — the two halves have different remedies."""
        assert "--repo" not in _mgc._GH_PR_VALUE_TAKING_FLAGS
        assert _mgc._extract_pr_number("gh pr merge --squash --repo 2024 42") == "2024"

    def test_the_split_holds_identically_for_the_close_verb(self, broadened):
        """The three arms above are merge-only; this is the close half.

        The comment on `_GH_FLAG_TOKENS` claims the split holds on BOTH verbs,
        and a claim is only pinned where a test exercises it. Long forms in the
        frozenset neutralise, short aliases and `--repo` misbind — same
        partition, same remedies, no crossover.

        Close matters independently rather than as a symmetry check: it reaches
        `_extract_close_target` in the caller, not the merge path, so "merge
        works" is not evidence about it.
        """
        assert _mgc._extract_pr_number("gh pr close --admin --comment 2024 42") is None
        assert _mgc._extract_pr_number("gh pr close --admin --body 2024 42") is None

        assert _mgc._extract_pr_number("gh pr close --admin -c 2024 42") == "2024"
        assert _mgc._extract_pr_number("gh pr close --admin -b 2024 42") == "2024"
        assert _mgc._extract_pr_number("gh pr close --admin --repo 2024 42") == "2024"

    def test_a_single_leading_dash_token_never_diverges(self, broadened):
        """TWO leading dash-tokens are required before the forms can disagree.

        This is why hand-picked corpora kept missing the defect: an author
        writes `gh pr merge --subject X 42`, sees both forms agree, and
        concludes they are equivalent. The divergence needs a VALUELESS flag
        *followed by* a value-taking one, and that shape does not occur to
        someone illustrating a single flag.

        Enumerated rather than sampled, over both verbs — which is the point,
        since sampling is the failure being pinned. Measured: zero divergence
        across every one-token form, against total divergence at two.
        """
        verbs = ("merge", "close")
        value_flags = ("--subject", "--body", "--body-file", "--comment",
                       "--author-email", "--match-head-commit",
                       "-t", "-b", "-F", "-c", "-A", "-R", "--repo")
        booleans = ("--squash", "--auto", "--admin", "--merge", "--rebase",
                    "--delete-branch", "-d")

        singles = [f"gh pr {v} {f} {n} 42"
                   for v in verbs for f in value_flags for n in ("2024", "1", "7")]
        singles += [f"gh pr {v} {b} 42" for v in verbs for b in booleans]

        # NON-VACUITY: the fixture is active, so a TWO-token form must diverge.
        # Without this, an inert fixture would make every assertion below pass
        # for the wrong reason — the shipped pattern agrees with itself.
        assert _mgc._extract_pr_number("gh pr merge --squash -t 2024 42") == "2024", (
            "the broadened fixture is not in effect, so the absence of "
            "divergence below says nothing. Check the fixture, not the claim."
        )

        for cmd in singles:
            broad = _mgc._extract_pr_number(cmd)
            shipped_re = _mgc._GH_PR_NUMBER_RE
            try:
                _mgc._GH_PR_NUMBER_RE = _SHIPPED_PR_NUMBER_RE
                shipped = _mgc._extract_pr_number(cmd)
            finally:
                _mgc._GH_PR_NUMBER_RE = shipped_re
            assert shipped == broad, (
                f"a SINGLE leading dash-token diverged: {cmd!r} gives "
                f"shipped={shipped!r} broad={broad!r}. The comment on "
                f"`_GH_FLAG_TOKENS` states that two are required, and that "
                f"claim explains why hand-picked corpora missed the defect. "
                f"If one token now suffices, the explanation is wrong."
            )


import merge_guard_pre  # noqa: E402


# ── A token belongs to the session that minted it; an empty id matches only an empty id ───
def _session_token(token_dir, session_id, name="one"):
    import json
    import time
    data = {"expires_at": time.time() + 600, "context": {"operation_type": "merge"}}
    if session_id is not _ABSENT:
        data["session_id"] = session_id
    path = token_dir / f"{merge_guard_pre.TOKEN_PREFIX}{name}"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


_ABSENT = object()


@pytest.mark.parametrize("current,minted,found", [
    ("", "other-session", False),                 # was accepted: a reader with no id took any token
    (None, "other-session", False),
    ("", "", True), ("", _ABSENT, True), ("", None, True), (None, "", True),
    ("this-session", "this-session", True),
    ("this-session", "", False), ("this-session", _ABSENT, False), ("this-session", None, False),
    ("this-session", "other-session", False),
    ("this-session", "THIS-SESSION", False),      # no case folding
    ("this-session", "this-session ", False),     # no trimming
    ("this-session", "this‐session", False),      # a look-alike hyphen (U+2010)
    ("", " ", False), ("", "​", False),      # blank-looking is not empty
    ("", "x" * 100_000, False),
    ("this-session", ["this-session"], False), ("this-session", 0, False),
])
def test_token_session_must_equal_the_reader_session(tmp_path, monkeypatch, current, minted, found):
    monkeypatch.setattr(merge_guard_pre, "get_session_id", lambda: current)
    path = _session_token(tmp_path, minted)
    tokens = merge_guard_pre.find_valid_tokens(tmp_path)
    assert [where for _, where in tokens] == ([str(path)] if found else [])
    assert path.exists()                          # another session's token is left alone


# ── GIT, Gh and GLAB run git, gh and glab on a case-blind disk ────────────────────────────
@pytest.mark.parametrize("typed,read", [
    ("GIT push --force origin main", "git push --force origin main"),
    ("Git push origin main", "git push origin main"),
    ("/usr/bin/GIT push origin main", "/usr/bin/git push origin main"),
    ("GH pr merge 12 --squash", "gh pr merge 12 --squash"),
    ("GLAB mr merge 5", "glab mr merge 5"),
    ("ENV GIT push origin main", "env git push origin main"),
    ("SUDO Git push origin main", "sudo git push origin main"),
    ("BASH -c 'GIT push origin main'", "bash -c 'git push origin main'"),
    ("echo $(GIT push origin main)", "echo $(git push origin main)"),
    ("GIT push; GIT\tpush\nGIT", "git push; git\tpush\ngit"),
])
def test_program_words_are_read_in_lower_case(typed, read):
    from shell.merge.views import canonical_spelling
    assert canonical_spelling(typed) == read
    assert (merge_guard_pre.check_merge_authorization(typed, cwd="/nonexistent-repact") is None) == \
        (merge_guard_pre.check_merge_authorization(read, cwd="/nonexistent-repact") is None)


@pytest.mark.parametrize("command", [
    "git -C /src/GIT/repo status", "ls GIT/ Git.txt GIT-notes .GIT", "GIT_DIR=x git status",
    "echo $GIT $GH_TOKEN ${GIT}", "IF x; THEN y; FI", "git log --grep GITHUB", "cat my-GIT",
    "ＧＩＴ push origin main", "GİT push origin main",
])
def test_other_words_keep_their_letter_case(command):
    from shell.merge.views import canonical_spelling
    assert canonical_spelling(command) == command


@pytest.mark.parametrize("typed", [
    "GIT push --force origin main", "Git push origin main", "/usr/bin/GIT push origin main",
    "GIT push origin --delete old-branch", "GIT branch -D feat", "GH pr merge 12 --squash",
    "Gh pr merge 12 --squash", "GLAB mr merge 5", "ENV GIT push --force origin main",
    "command GIT push --force origin main", "RTK Git push --force origin main",
    "sh -c 'GIT push --force origin main'", "echo $(GIT push --force origin main)",
    "GIT push " + "x" * 50_000 + " && GIT push --force origin main",
])
def test_capital_program_word_is_refused_like_the_plain_one(tmp_path, typed):
    assert merge_guard_pre.check_merge_authorization(typed, token_dir=tmp_path, cwd=str(tmp_path))


# A guarded operation inside an eval payload is refused as wrapped, like `sh -c`, whatever quote
# the payload ends on. Before, the payload's closing quote stayed on the last refspec and the line
# passed with no approval.
_EVAL_WRAPPED_DENY = [
    'eval "git push origin main"',                    # push to a protected branch
    'eval "git push --force origin topic"',           # force push
    'eval "git push --delete origin topic"',          # remote branch delete
    'eval "git push origin :topic"',
    'eval "git branch -D topic"',                     # local branch delete
    'eval "gh pr merge 1 --squash"',                  # PR merge
    'eval "glab mr merge 5"',                         # MR merge
    'eval "gh pr close 1 --delete-branch"',           # PR close
    "eval 'git push origin main'",
    "eval $'git push origin main'",
    "eval git push origin main",
    'eval "git push" origin main',
    'eval "git" "push" "origin" "main"',
    'eval -- "git push origin main"',
    'builtin eval "git push origin main"',
    'command eval "git push origin main"',
    'command -p eval "git push origin main"',
    '\\eval "git push origin main"',
    'e""val "git push origin main"',
    'EVAL=1 eval "git push origin main"',
    'true && eval "git push origin main"',
    'true; eval "git push origin main"',
    'true\neval "git push origin main"',
    'sh -c \'eval "git push origin main"\'',
    'eval "sh -c \'git push origin main\'"',
    'eval "eval \\"git push origin main\\""',
    'eval \'eval "git push origin main"\'',
    'eval "true; git push origin main"',
    'eval "git push origin main" ',
    'eval\t"git push origin main"',
    "eval eval eval eval eval git push origin main",  # nested deeper than the payload is read
]


@pytest.mark.parametrize("command", _EVAL_WRAPPED_DENY)
def test_guarded_operation_inside_eval_is_refused(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


@pytest.mark.parametrize("command", [
    'eval "git push" origin main', "eval 'git push --delete origin topic'",
    'builtin eval "git push origin :topic"',
])
def test_eval_wrapped_refusal_tells_the_user_to_run_it(tmp_path, command):
    reason = merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))
    assert "wrapped in an eval string" in reason and "! <command>" in reason


@pytest.mark.parametrize("command", [
    'eval "$(ssh-agent -s)"', 'eval "$(pyenv init -)"', 'eval "$(direnv hook zsh)"',
    'eval "echo hi"', "eval echo hi", "eval", 'eval ""', 'eval "git status"',
    'eval "git push origin topic"', 'eval "git push --force-with-lease origin topic"',
    'echo eval "git push origin main"', 'git commit -m "eval git push origin main"',
    'grep -rn "eval git push origin main" docs',
])
def test_eval_without_a_guarded_operation_passes(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path)) is None


# A push to a protected branch inside a quoted payload is refused whether the quote closes on the
# branch name or on a flag. Before, the closing quote stayed on the refspec and only the flag
# form was refused.
@pytest.mark.parametrize("command", [
    'ssh host "git push origin main"', 'ssh host "git push origin main --quiet"',
    "ssh host 'git push origin main'", 'ssh host "git push origin HEAD:main"',
    'watch "git push origin main"', 'watch "git push origin main --quiet"',
    'xargs -I{} sh -c "git push origin main"', 'xargs -I{} sh -c "git push origin main --quiet"',
])
def test_push_to_main_in_a_quoted_payload_is_refused(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


@pytest.mark.parametrize("command", [
    'echo "git push origin main"', 'git commit -m "git push origin main"',
    'grep "git push origin main" f', 'printf "git push origin main"',
    'ssh host "git push origin topic"', "ssh host 'git push origin topic'",
])
def test_quoted_push_text_that_is_data_or_a_topic_push_passes(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path)) is None


# A guarded operation inside a subshell, a substitution, a function body, a case arm or a
# here-string is refused like the bare command. Before, the closing parenthesis was read as part
# of the branch name (`main)`), a here-string was not read as a script, and an eval or `sh -c`
# after `then`, `do`, `{` or `(` was not seen.
_GROUPED_DENY = [
    "(git push origin main)", "(cd . && git push origin main)", "x=$(git push origin main)",
    "(git push origin HEAD:main)", "(git push -u origin main)",
    "(git merge --no-ff feat && git push origin main)",
    'bash <<< "git push origin main"', 'source /dev/stdin <<< "git push origin main"',
    'if true; then eval "git push origin main"; fi', '( eval "git push origin main" )',
    # neighbours
    "((git push origin main))", "( ( ( (git push origin main) ) ) )", "(git push origin main)&",
    "(git push origin main)|cat", "(git push origin main) 2>&1", "(git push origin main;)",
    "(git push origin main\n)", "(git push origin 'main')", "time (git push origin main)",
    "! (git push origin main)", "if (git push origin main); then :; fi", "$(git push origin main)",
    'echo "$(git push origin main)"', 'echo "x $(git push origin main) y"',
    'echo "$(echo "$(git push origin main)")"', 'echo "${x:-$(git push origin main)}"',
    "echo `git push origin main`", 'echo "`git push origin main`"', "x=`git push origin main`",
    "echo $(( $(git push origin main) ))", "cat <(git push origin main)", "tee >(git push origin main)",
    "f() { git push origin main; }; f", "f(){ git push origin main;}; f",
    "function f { git push origin main; }; f", "case x in x) git push origin main;; esac",
    "cat <<EOF\n$(git push origin main)\nEOF", "(bash <<EOF\ngit push origin main\nEOF\n)",
    "x=$(bash <<'EOF'\ngit push origin main\nEOF\n)",
    'gh pr create --body "$(cat <<EOF\nRun `git push origin main` now\nEOF\n)"',
    # force push, branch delete and a merge in each wrapper
    "x=$(git push --force origin feat)", 'echo "$(git push --force origin feat)"',
    "case x in x) git push --force origin feat;; esac", "(git push origin +feat)",
    "echo `git branch -D topic`", "cat <(git branch -D topic)", "f() { git branch -D topic; }; f",
    'echo "$(git branch -D topic)"', "x=$(gh pr merge 1)", "(git push --delete origin topic)",
    # here-strings
    '. /dev/stdin <<< "git push origin main"', "zsh <<< 'git push origin main'",
    'dash <<<"git push origin main"', 'ksh <<< "git push origin main"', 'bash -s <<< "git push origin main"',
    'sh <<< "git branch -D topic"', 'bash <<< "git push --force origin feat"',
    "bash <<< $'git push origin main'", "bash <<< git\\ push\\ origin\\ main",
    '<<< "git push origin main" bash', 'sudo bash <<< "git push origin main"',
    '/bin/bash <<< "git push origin main"', 'bash <<< "(git push origin main)"',
    'cat <<< "$(git push origin main)"',
    # an eval or a shell -c wherever a command may start
    'if true; then sh -c "git push origin main"; fi', 'while true; do eval "git push origin main"; done',
    'if false; then :; else eval "git push origin main"; fi', '{ eval "git push origin main"; }',
    '! eval "git push origin main"', 'time eval "git push origin main"',
    'true | eval "git push origin main"', '(eval "git push origin main")',
    'x=$(eval "git push origin main")', 'if eval "git push origin main"; then :; fi',
    'eval "(git push origin main)"', 'sh -c "(git push origin main)"',
    "sh -c 'x=$(git push origin main)'", "xargs -I{} sh -c '(git push origin main)'",
    'ssh host "(cd /srv && git push origin main)"', 'watch "(git push origin main)"',
    'busybox bash -c "(git push origin main)"',
]


@pytest.mark.parametrize("command", _GROUPED_DENY)
def test_guarded_operation_inside_a_group_or_a_here_string_is_refused(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


# Quoting, braces, heredoc scripts, function bodies and a shell -c string are read as the shell
# reads them. Before: `\'` inside $'..' (and after a plain quote) ended the quoted span one
# character early, so two such words paired around a guarded command and hid it; a brace list
# was read as one literal word; a group inside a heredoc script, `function f {`, `coproc` and a
# shell -c after a program outside the wrapper catalogue were not read at all.
_READING_DENY = [
    # 1. ANSI-C quotes: a backslash escapes the next character inside $'..'
    "echo $'\\'' ; git push origin main ; echo $'\\''",
    "echo $'a\\'b' ; git push origin main ; echo $'c\\'d'",
    "echo $'\\'' ; git push --force origin feat ; echo $'\\''",
    "echo $'\\'' ; git branch -D topic ; echo $'\\''",
    "echo $'\\'' ; gh pr merge 5 ; echo $'\\''",
    "echo $'\\'' ; eval \"git push origin main\" ; echo $'\\''",
    "echo $'\\'' ; sh -c \"git push origin main\" ; echo $'\\''",
    'echo $"a\\"b" ; git push origin main ; echo $"c\\"d"',
    "echo 'it'\\''s' ; git push origin main ; echo 'a'\\''b'",
    "echo $$'x' ; git push origin main ; echo $$'y'",
    # 2. brace expansion
    "git push origin {main,feat}", "git push origin {feat,main}", "git push origin feat:ma{in,}",
    "git push origin HEAD:{main,feat}", "git push origin {main,}", "git {push,} origin main",
    "gh pr merg{e,} 5", "gh pr {merge,} 5", "git push origin {+feat,x}",
    "git push {--force,} origin feat", "git branch {-D,} topic", "git push origin ma{i..i}n",
    'git push origin {"main",feat}', "git push origin {x,{y,main}}", "{git,} push origin main",
    '(git push origin {main,feat})', 'sh -c "git push origin {main,feat}"',
    # 2. a list too long or too tangled to spell out, in a git leg
    "git push origin x{1..100}", "git push origin {a..z}{a..z}",
    # 3a. an escaped bracket inside an array word list
    "x=(a \\( ) ; (git push origin main)", "x=(a \\) ) ; (git branch -D topic)",
    # 3b. a group or a substitution inside a heredoc body a shell runs
    "bash <<'EOF'\n(git push origin main)\nEOF", "bash <<'EOF'\nx=$(git push origin main)\nEOF",
    "cat <<'EOF' | bash\n(git push origin main)\nEOF", "cat <<'EOF' | sh -s\n(git push origin main)\nEOF",
    "source /dev/stdin <<'EOF'\n(git push origin main)\nEOF",
    "bash <<'EOF'\n(git push --force origin feat)\nEOF", "zsh <<'EOF'\n(git branch -D topic)\nEOF",
    # 3c. the `function` keyword opens a leg
    'function f { eval "git push origin main"; }; f', 'function f() { eval "git push origin main"; }; f',
    'function f { sh -c "git push origin main"; }; f', 'function f { bash <<< "git push origin main"; }; f',
    'function my-fn { eval "git branch -D topic"; }; my-fn',
    # 3d. coproc opens a leg
    'coproc eval "git push origin main"', 'coproc P { eval "git push origin main"; }',
    'coproc sh -c "git push --force origin feat"', 'noglob eval "git push origin main"',
    # 4. a shell and its -c string after any program
    'docker exec c sh -c "git push origin main"', 'script -q /dev/null sh -c "git push origin main"',
    'caffeinate sh -c "git push origin main"', 'arch -arm64 sh -c "git push origin main"',
    'uv run sh -c "git push origin main"', 'uv run bash -e -c "git push --force origin feat"',
    'docker exec c bash -lc "git branch -D topic"', 'docker run --rm img sh -c "(git push origin main)"',
    'foo sh -x -c "ok" bash -c "git push origin main"',
    # 4. and its here-string
    'caffeinate bash <<< "git push origin main"', 'uv run bash <<< "git push --force origin feat"',
    'docker exec -i c sh <<< "git branch -D topic"',
    'echo bash -c "git push origin main"',          # the accepted cost of reading -c anywhere
]


@pytest.mark.parametrize("command", _READING_DENY)
def test_guarded_operation_behind_quoting_braces_or_a_script_reader_is_refused(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


@pytest.mark.parametrize("command", [
    'echo "(git push origin main)"', 'git commit -m "fix {main,feat}"', "echo {a,b}",
    "git log --format='%h {x,y}'", "git push origin feat{1,2}", "git stash show stash@{0}",
    "git rev-parse HEAD@{u}", "git diff main@{1} main", "echo $'it\\'s'",
    "echo $'a\\'b' ; git status ; echo $'c\\'d'", "echo 'it'\\''s' ; git status",
    "cat <<'EOF'\ngit push origin main\nEOF", "python3 <<'EOF'\nprint('(git push origin main)')\nEOF",
    "cat <<'EOF' > notes.txt\n(git push origin {main,x})\nEOF",
    "gh api repos/{owner}/{repo}/pulls/5", "git add src/{a,b}.py", "mkdir -p src/{a,b,c}",
    "for i in {1..100}; do echo $i; done", "function f { git status; }; f", "coproc cat",
    "x=(a \\( ) ; git status", 'docker exec c sh -c "git status"', 'docker exec c sh -c "git push origin feat"',
    "uv run pytest -c pytest.ini", 'grep -c "git push origin main" log.txt',
    'cat <<< "git push origin main"', 'uv run bash <<< "git push origin feat"',
    # the whole $'..' string is the comment's text: its `| sh` is inside the quotes
    "gh pr comment 1 --body $'git push --force origin main\\' | sh'",
])
def test_quoting_braces_and_script_readers_leave_everyday_lines_alone(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path)) is None


@pytest.mark.parametrize("command", [
    "( git push origin main )", "(git push --force origin feat)", "(git branch -D topic)",
    "(gh pr merge 1)", "{ git push origin main; }", "git push origin main|cat",
    "git push origin main>log", "git push origin main&&echo ok",
])
def test_grouped_forms_refused_before_stay_refused(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


@pytest.mark.parametrize("command", [
    "(cd sub && git push origin topic)", "(git status)", "x=$(git rev-parse main)",
    'echo "(git push origin main)"', 'git commit -m "fix (main)"', "git log main)",
    "echo '$(git push origin main)'", "git commit -m 'run `git push origin main` later'",
    'git commit -m "feat(guard): deny (git push origin main)"', "x=(git push origin main)",
    "cat <<'EOF' > notes.md\nrun `git push origin main` or $(git push --force origin x)\n(git push origin main)\nEOF",
    'gh pr create --title t --body "$(cat <<\'EOF\'\nRun `git push origin main` after (git branch -D topic).\nEOF\n)"',
    'git commit -m "$(cat <<\'EOF\'\nfix: it\'s (a) `git push origin main` thing\nEOF\n)"',
    "(git push origin topic)", "x=$(git push origin topic)", "(git push --force-with-lease origin topic)",
    'bash <<< "git status"', 'source /dev/stdin <<< "echo hi"', 'cat <<< "git push origin main"',
    'grep main <<< "git push origin main"', 'if true; then eval "git status"; fi',
    'echo "$(git log --oneline main)"', "find . \\( -name main \\) -print", "echo $((1+2))",
    "git log --format='%(refname)' main", "arr=(a b c); echo ${arr[0]}",
    "git push origin topic # (git push origin main)", 'rg "\\(git push origin main\\)" docs',
    'python3 -c "print(\'git push origin main\')"', "git diff $(git merge-base origin/main HEAD)",
    'while read l; do echo "$l"; done < <(git branch --list main)', "f() { git status; }; f",
    "case $x in main) echo main;; esac", 'ssh host "git push origin topic"', 'watch "git status"',
    'ssh host "echo (hi)"', 'xargs -I{} sh -c "echo {}"',
])
def test_grouped_text_without_a_guarded_operation_passes(tmp_path, command):
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path)) is None


# A guarded operation that no approval can bind (inside a group, or only inside a wrapper's quoted
# payload) is refused with the run-it-yourself text, never with a request for an approval.
@pytest.mark.parametrize("command", [
    "(git push origin main)", "x=$(git push origin main)", 'bash <<< "git push origin main"',
    'sh -c "git push origin main"', 'eval "git push origin main"', 'ssh host "git push origin main"',
    'watch "git push origin main"',
])
def test_unbindable_wrapped_push_tells_the_user_to_run_it(tmp_path, command):
    reason = merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))
    assert "cannot be bound to an approval" in reason and "[rule: mg.unmintable.shape]" in reason


def test_unquoted_eval_push_still_asks_for_an_approval(tmp_path):
    reason = merge_guard_pre.check_merge_authorization(
        "eval git push origin main", token_dir=tmp_path, cwd=str(tmp_path))
    assert reason and "cannot be bound to an approval" not in reason


# ── An approval covers only the one command it was given for ─────────────────────────────
# Each row mints the way the post hook mints from a clicked option holding the approved
# command, writes the token under tmp, and asks the pre hook about the executed line.
import subprocess  # noqa: E402

import merge_guard_post  # noqa: E402

_PUSH = "git push origin main"


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git checkout with an origin remote (so an approval can bind its repository) and an
    empty token directory, both under tmp; no branch named x exists in it."""
    repo, tokens = tmp_path / "repo", tmp_path / "tokens"
    repo.mkdir()
    tokens.mkdir()
    for args in (["init", "-q", "-b", "work"], ["remote", "add", "origin", "https://github.com/o/r.git"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(merge_guard_pre, "get_session_id", lambda: "S")
    monkeypatch.setattr(merge_guard_post, "get_session_id", lambda: "S")
    return repo, tokens


def _approve(approved, tokens):
    context = merge_guard_post._mint_context_from_texts([f"Yes, run `{approved}`"]).context
    assert context is not None, f"no mint for {approved!r}"
    assert merge_guard_post.write_token(context, tokens)


def _asked(command, checkout):
    repo, tokens = checkout
    return merge_guard_pre.check_merge_authorization(command, token_dir=tokens, cwd=str(repo))


def _live_tokens(tokens):
    return [p.name for p in tokens.iterdir() if not p.name.endswith(".consumed") and ".use" not in p.name]


@pytest.mark.parametrize("approved, executed", [
    ("git branch -D x", f'git branch -D x 2>"$({_PUSH})"'),
    ("git branch -D x", f'git branch -D x <<< "$({_PUSH})"'),
    ("git branch -D x", f"git branch -D x <<EOF\n$({_PUSH})\nEOF"),
    ("git push --force origin feat", f'git push --force origin feat 2>"$({_PUSH})"'),
    ("gh pr merge 5 -R o/r", f'gh pr merge 5 -R o/r 2>"$({_PUSH})"'),
    ("git branch -D x", 'git branch -D x 2>"$(gh pr merge 5)"'),
    ("git branch -D x", f'git branch -D "$({_PUSH})"'),            # argument position: denied before too
    ('sh -c "git branch -D x"', f'sh -c "git branch -D x; {_PUSH}"'),
])
def test_a_second_guarded_operation_never_rides_on_an_approval(checkout, approved, executed):
    _approve(approved, checkout[1])
    reason = _asked(executed, checkout)
    assert reason and "[rule: mg.compound.destructive]" in reason, reason
    assert len(_live_tokens(checkout[1])) == 1                       # the approval was not spent
    assert "[rule: mg.compound.destructive]" in merge_guard_pre.check_merge_authorization(
        executed, token_dir=checkout[1] / "none", cwd=str(checkout[0]))
    assert merge_guard_post._mint_context_from_texts([f"Yes, run `{executed}`"]).context is None


@pytest.mark.parametrize("approved, executed", [
    ('sh -c "git branch -D x"', 'ssh host "git branch -D x"'),
    ('sh -c "git branch -D x"', 'sh -c "cd ../other && git branch -D x"'),
    ('sh -c "git branch -D x"', 'sh -c "git -C ../other branch -D x"'),
    ("git branch -D x", 'sh -c "git branch -D x"'),                 # a bare approval passes only the bare command
    ('sh -c "git branch -D x"', "git branch -D x"),
    ("git branch -D x", "git -C ../other branch -D x"),             # controls: denied before too
    ("git branch -D x", "cd ../other && git branch -D x"),
])
def test_wrapper_and_directory_are_part_of_what_was_approved(checkout, approved, executed):
    (checkout[0].parent / "other").mkdir()
    _approve(approved, checkout[1])
    reason = _asked(executed, checkout)
    assert reason and "[rule: mg.token.mismatch]" in reason, reason
    assert len(_live_tokens(checkout[1])) == 1


@pytest.mark.parametrize("command", [
    'sh -c "git branch -D x"', 'sh -c "gh pr merge 5 -R o/r"', "(git branch -D x)",
    "(git push --force origin feat)", "x=$(git branch -D x)", 'eval "git branch -D x"',
    "eval git push origin main",
])
def test_a_wrapped_command_that_mints_asks_for_approval_and_passes_with_it(checkout, command):
    reason = _asked(command, checkout)
    assert reason and "cannot be bound to an approval" not in reason, reason
    assert "AskUserQuestion" in reason
    _approve(command, checkout[1])
    assert _asked(command, checkout) is None


@pytest.mark.parametrize("command", [
    'sh -c "git push --force origin feat"', 'sh -c "gh pr close 5 -R o/r"', 'bash <<< "git branch -D x"',
])
def test_a_wrapped_command_no_approval_can_name_gets_the_run_it_yourself_text(checkout, command):
    assert merge_guard_post._mint_context_from_texts([f"Yes, run `{command}`"]).context is None
    reason = _asked(command, checkout)
    assert "cannot be bound to an approval" in reason and "[rule: mg.unmintable.shape]" in reason
    assert "AskUserQuestion" not in reason


def test_a_bare_approved_command_passes_once_and_its_token_is_retired(checkout):
    _approve("git branch -D x", checkout[1])
    assert _asked("git branch -D x", checkout) is None
    assert merge_guard_post._retire_token_for_command("git branch -D x", "branch-delete", checkout[1])
    assert _live_tokens(checkout[1]) == []
    assert "[rule: mg.branch_delete.explicit]" in _asked("git branch -D x", checkout)


def test_a_wrapped_approval_is_retired_only_by_its_own_wrapped_command(checkout):
    wrapped = 'sh -c "git branch -D x"'
    _approve(wrapped, checkout[1])
    assert not merge_guard_post._retire_token_for_command("git branch -D x", "branch-delete", checkout[1])
    assert merge_guard_post._retire_token_for_command(wrapped, "branch-delete", checkout[1])


@pytest.mark.parametrize("approved, executed", [
    ("git branch -D x", "git branch -D x 2>/dev/null"),
    ("git branch -D x", "git branch -D x 2>&1 | tee log"),
    (_PUSH, f'{_PUSH} 2>"$(mktemp)"'),
])
def test_a_harmless_redirect_or_substitution_is_not_a_second_operation(checkout, approved, executed):
    _approve(approved, checkout[1])
    assert _asked(executed, checkout) is None


@pytest.mark.parametrize("approved", [None, "git push --force origin main"])
def test_a_tag_message_naming_a_push_passes_and_spends_no_approval(checkout, approved):
    if approved:
        _approve(approved, checkout[1])
    assert _asked('git tag v1.0 -m "git push --force origin main"', checkout) is None
    assert len(_live_tokens(checkout[1])) == (1 if approved else 0)


# ── An approval covers the whole git command it was given for ────────────────────────────
# The guarded git leg is bound word for word (`typed`); what stands around it (a redirect, a
# pipe, another leg, a runner word, a trailing comment, a closed list of flags) is not.
_BASIC = ["git push origin main", "git push --force origin feat", "git branch -D x",
          "git push origin --delete feat"]
_AROUND = ["{}", "{} 2>&1 | tail", "{} && echo done", "cd . && {}", "timeout 60 {}",
           "{} || echo failed", "{} ; git status", "git fetch origin && {}", "{} &", "{} # now"]


@pytest.mark.parametrize("command", _BASIC)
@pytest.mark.parametrize("around", _AROUND)
def test_an_approved_command_passes_in_its_everyday_spellings(checkout, command, around):
    """Both ways: the plain approval passes the decorated line, and the decorated approval
    the plain one."""
    for approved, executed in ((command, around.format(command)), (around.format(command), command)):
        _approve(approved, checkout[1])
        assert _asked(executed, checkout) is None, (approved, executed)
        operation = merge_guard_post.detect_command_operation_type(command)
        assert merge_guard_post._retire_token_for_command(executed, operation, checkout[1])
        assert _live_tokens(checkout[1]) == []
        assert _asked(executed, checkout) is not None                # once: a new approval is needed


@pytest.mark.parametrize("approved, executed", [
    ("git push origin main", "git push -u origin main"),
    ("git push --set-upstream -v origin main", "git push origin main"),
    ("git push origin main", "git push origin main --quiet --no-progress"),
    ("git branch -D x", "git branch -q -D x"),
])
def test_the_closed_list_of_harmless_flags_is_not_part_of_the_approval(checkout, approved, executed):
    _approve(approved, checkout[1])
    assert _asked(executed, checkout) is None


@pytest.mark.parametrize("approved, executed, rule", [
    ("git push origin main", "git push origin feat:main", "mg.token.mismatch"),
    ("git push origin main", "git -c remote.origin.pushurl=../other.git push origin main", "mg.token.mismatch"),
    ("git push origin main", "git remote set-url origin ../other.git && git push origin main",
     "mg.unmintable.shape"),
    ("git push origin main", "git config remote.origin.pushurl ../other.git; git push origin main",
     "mg.unmintable.shape"),
    ("git push origin main", "export GIT_SSH_COMMAND=x; git push origin main", "mg.unmintable.shape"),
    ("git push origin main", "git push --receive-pack='x; git-receive-pack' origin main", "mg.token.mismatch"),
    ("git push origin main", "git push --tags origin main", "mg.token.mismatch"),
    ("git push origin main", "git push origin main --tags", "mg.token.mismatch"),
    ("git push origin main", "git push origin main -o ci.skip", "mg.token.mismatch"),
    ("git push origin main", "git push -o ci.skip origin main", "mg.token.mismatch"),
    ("git push origin main", "GIT_TRACE=1 git push origin main", "mg.token.mismatch"),
    ("git push origin main", "git push origin main '#' feat:main", "mg.token.mismatch"),  # a quoted # is a word
    ("git push origin main", "for i in 1 2 3; do git push origin main; done", "mg.unmintable.shape"),
    ("git push --force origin feat", "git push --force origin leak:feat", "mg.token.mismatch"),
    ("git push --force origin feat", "git push --force --no-verify origin feat", "mg.token.mismatch"),
    ("git branch -D x", "git -c core.hooksPath=../h branch -D x", "mg.token.mismatch"),
])
def test_another_word_in_the_git_command_is_another_command(checkout, approved, executed, rule):
    _approve(approved, checkout[1])
    reason = _asked(executed, checkout)
    assert reason and f"[rule: {rule}]" in reason, reason
    assert len(_live_tokens(checkout[1])) == 1                       # the approval was not spent


@pytest.mark.parametrize("command", [
    'cd "$D" && git push --force origin feat',                      # another $D is another repository
    '(cd "$D" && git branch -D x)',
    'git -C "$D" branch -D x',
    "git branch -D $b",
    'git push --force origin "$(git rev-parse --abbrev-ref HEAD)"',
    "for i in 1 2 3; do git push origin main; done",
    "git remote set-url origin ../other.git && git push origin main",
])
def test_a_command_that_cannot_be_bound_mints_nothing_and_is_handed_to_the_user(checkout, command):
    minted = merge_guard_post._mint_context_from_texts([f"Yes, run `{command}`"])
    assert minted == (None, "unbindable_shape")
    for prefix in ("", "D=../other; ", "b=main; "):
        reason = _asked(prefix + command, checkout)
        assert reason and "AskUserQuestion" not in reason, reason
        assert "[rule: mg.unmintable.shape]" in reason


@pytest.mark.parametrize("command", ['sh -c "git branch -D x"', "(git branch -D x)",
                                     "(cd . && git branch -D x)"])
def test_a_wrapped_approval_holds_only_in_the_directory_it_was_given_in(checkout, monkeypatch, command):
    repo, tokens = checkout
    (repo / "sub").mkdir()
    _approve(command, tokens)
    monkeypatch.chdir(repo / "sub")
    reason = merge_guard_pre.check_merge_authorization(command, token_dir=tokens, cwd=str(repo / "sub"))
    assert reason and "[rule: mg.token.mismatch]" in reason, reason
    monkeypatch.chdir(repo)
    assert _asked(command, checkout) is None


def test_a_token_written_before_the_command_was_bound_passes_nothing(checkout):
    context = merge_guard_post._mint_context_from_texts(["Yes, run `git branch -D x`"]).context
    assert context.pop("typed") == "\x1f".join(["git", "branch", "-D", "x"])
    assert merge_guard_post.write_token(context, checkout[1])
    assert "[rule: mg.token.mismatch]" in _asked("git branch -D x", checkout)


@pytest.mark.parametrize("approved, executed", [
    ("gh pr merge 5 -R o/r --squash", 'gh pr merge 5 -R o/r --squash --body "merge now"'),
    ("gh pr close 5 -R o/r", 'gh pr close 5 -R o/r --comment "superseded"'),
    ("gh pr merge 5 -R o/r", "gh pr merge 5 -R o/r && git fetch origin"),
])
def test_a_gh_approval_is_bound_by_target_flags_and_repository_as_before(checkout, approved, executed):
    """A leg of another program carries no `typed`: its message text is no part of the approval."""
    _approve(approved, checkout[1])
    assert _asked(executed, checkout) is None
    _approve(approved, checkout[1])
    assert "[rule: mg.token.mismatch]" in _asked(executed.replace(" 5 ", " 6 ").replace("o/r", "o/q"), checkout)
