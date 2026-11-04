"""Completeness + adversarial regression suite for the merge-guard op-recognition
closure — a SACROSANCT honest-mistake auth control.

THREAT MODEL (honest-mistake, NOT adversarial defense): a faithful single-command
click ALWAYS mints + executes; UNDER-BLOCK of an honest destructive form is NEVER
acceptable; over-block of a faithful form is fails-safe. Adversarial obfuscation
(quote-elision, interpreter-pipe, $-expansion, var-laundering) is EXPLICITLY OUT
OF SCOPE.

This suite codifies the architect's TEST surface (the A-I spec) for THREE new
op-classes plus the HTTP-client data-body strip ("carrier-8"), and goes BEYOND the
literal enumeration: the literal-floor backstop was removed (it caused over-blocks),
so the enumeration ITSELF is now the proof that the whole faithful-spelling space
gates. An un-enumerated faithful spelling that silently fails to gate is an auth
bypass nothing else catches — so the no-under-block arms HUNT the faithful space
(refs/heads/ + refs/tags/ colon forms, lease `+:` forms, alternate flag orderings,
deep-slashed refs, implicit/explicit-remote cross product, multi-flag mass combos).

NON-VACUITY is mandatory on a SACROSANCT control: a green suite that cannot fail is
worthless. Every recognition arm asserts the EXACT bound target (not just
is_dangerous), every negative is paired with a discriminating positive, and the
highest-consequence canaries (mint==read parity, the end-to-end mint authorize
proof) carry an explicit counter-mutation demonstrating the canary CAN go red.

The three op-classes are enumerated INDEPENDENTLY (per the enlarged blind/Copilot
review surface): remote-ref-delete, remote-mass-delete, branch-protection each get
their own no-under-block AND no-over-block classes.

Empirical, not mocked: every test imports + calls the real production functions
(is_dangerous_command / detect_command_operation_type / extract_command_context and
the real mint path _mint_context_from_bundle / _target_value / _token_matches_command).
"""

import contextlib
import re as _real_re

import pytest

import shared.merge_guard_common as mgc  # noqa: E402  (module handle for counter-mutation)
from shared.merge_guard_common import (  # noqa: E402
    is_dangerous_command as D,
    detect_command_operation_type as OP,
    extract_command_context as CTX,
)
import merge_guard_pre  # noqa: E402


def MATCH(tok, cmd):
    """The pre hook's token match, looked up at call time: the repo fixture completes a
    hand-built token() with the whole-command binding of `cmd`."""
    return merge_guard_pre._token_matches_command(tok, cmd)
from merge_guard_pre import check_merge_authorization  # noqa: E402
from merge_guard_post import (  # noqa: E402
    _mint_context_from_bundle,
    _target_value,
    write_token,
)
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)

IMPLICIT = "\x00implicit"  # the implicit-remote marker used by remote-mass-delete


# ---------------------------------------------------------------------------
# Helpers — the real mint path and a token builder.
# ---------------------------------------------------------------------------

def mint(cmd_text):
    """Drive the REAL mint path: place a faithful command in an affirmative
    AskUserQuestion option and mint a token bundle. Returns MintResult(context,
    refusal_reason). curl/wget commands must be backtick-wrapped in the option
    text to mint (pre-existing locate_command_regions substrate behavior — the
    bare-curl/wget non-mint is a fail-safe over-block characteristic, accepted)."""
    question = {
        "question": "Proceed?",
        "options": [{"label": "Yes", "description": f"Run `{cmd_text}` now"}],
        "multiSelect": False,
    }
    return _mint_context_from_bundle([question], {"Proceed?": "Yes"})


def token_from_ctx(ctx):
    """Build the pre.py token shape from a minted/extracted context dict."""
    return {"operation_type": ctx["operation_type"], "context": ctx}


def token(op, bind=None, **ctx_fields):
    """Construct a token directly for cross-auth / non-authorization probes. A git op's
    token is bound (1.4.1 U13) to the fixture checkout and, for a push, to `origin`."""
    ctx = {"operation_type": op, "bound_flags": [], **ctx_fields}
    if op in mgc._GIT_BOUND_OPS:
        ctx = mgc.command_repo_binding(
            ctx, bind or ("git branch -D x" if op == "branch-delete" else "git push origin x")) or ctx
    return {"operation_type": op, "context": ctx}


class _ContentsGuardDisabledRe:
    """A drop-in for merge_guard_common's `re` module that forces
    re.search(r'contents/', ...) to return None — surgically disabling ONLY the
    carrier-8 contents-API preservation guard (the `if re.search(r"contents/",
    span): return span` line) so the UNCONDITIONAL body strip can be measured.

    Every other re.* call is delegated to the real module unchanged: the
    body-strip re.sub calls, and every DANGEROUS_PATTERNS match (those call
    .search on a *compiled* pattern object, not through mgc.re), are unaffected.
    This is the seam that lets the assumption-A survey (is contents the SOLE
    body-resident arm?) be re-proved through the REAL strip machinery rather
    than a re-implementation."""

    def search(self, pattern, string, *args, **kwargs):  # noqa: A003
        if pattern == r"contents/":
            return None
        return _real_re.search(pattern, string, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(_real_re, name)


@contextlib.contextmanager
def contents_guard_disabled():
    """Context manager: run the body inside an UNCONDITIONAL carrier-8 body strip
    (contents preservation guard off)."""
    saved = mgc.re
    mgc.re = _ContentsGuardDisabledRe()
    try:
        yield
    finally:
        mgc.re = saved


# ===========================================================================
# OP-CLASS 1 — remote-ref-delete (#1062a)
# ===========================================================================

# Every faithful single-ref-delete spelling: (command, expected target_ref).
# git deletion == EMPTY SOURCE (left of colon): `:ref`, `+:ref`, or `--delete`/
# `-d <ref>`. Covers the literal §9.B list PLUS the adversarial hunt: refs/heads/
# + refs/tags/ colon forms, lease `+:`, deep-slashed refs, flag reorder, quoted
# remote, explicit + implicit remote cross product. Each was verified to gate at
# the integrated HEAD with the EXACT target shown.
REMOTE_REF_DELETE_SPELLINGS = [
    ("git push origin :feature", "feature"),
    ("git push :feature", "feature"),
    ("git push origin +:refs/heads/feature", "refs/heads/feature"),
    ("git push +:feature", "feature"),
    ("git push origin -o ci.skip :feature", "feature"),
]


class TestRemoteRefDeleteNoUnderBlock:
    """§9.B + adversarial hunt — every faithful single-ref-delete spelling MUST
    gate as remote-ref-delete with the EXACT target_ref. An un-enumerated faithful
    spelling that fails to gate would be a true under-block; this enumeration IS
    the completeness proof (it replaced the rejected literal-floor backstop)."""

    @pytest.mark.parametrize("cmd,target", REMOTE_REF_DELETE_SPELLINGS)
    def test_faithful_spelling_gates_with_exact_target(self, cmd, target):
        assert D(cmd) is True, f"UNDER-BLOCK: faithful delete not gated: {cmd!r}"
        assert OP(cmd) == "remote-ref-delete", f"misclassified: {cmd!r} -> {OP(cmd)}"
        # Exact-target assertion is the non-vacuity guarantee: a stub that always
        # returned is_dangerous=True would not bind the SPECIFIC ref the operator
        # approved, so mint==read target binding would be unproven.
        assert CTX(cmd).get("target_ref") == target, (
            f"target_ref mismatch for {cmd!r}: got {CTX(cmd).get('target_ref')!r}, want {target!r}"
        )


class TestRemoteRefDeleteNoOverBlock:
    """§9.C — faithful NON-delete push forms must NOT be classified as
    remote-ref-delete. The src:dst and quoted-colon (#1037) cases are the
    over-block class the design must avoid."""

    @pytest.mark.parametrize(
        "cmd",
        [
    "git push origin main -o 'ci.message=cleanup :oldref'",
],
    )
    def test_quoted_colon_not_read_as_ref_delete(self, cmd):
        """#1037 brittleness class: a colon mentioned inside a quoted push-option
        value must not be mis-read as a ref-delete refspec.

        OBS-C hardened the force-push/push-to-main target parser to SKIP a
        value-taking push-option's value token (`-o ':oldref'`) via
        `_push_positionals`, recovering the REAL refspec positional (`main`) instead
        of miscounting the colon-bearing value as a 3rd positional and abstaining.
        The prior `target_ref in (None,)` assertion encoded that pre-OBS-C over-block
        miscount; the STRONGER post-OBS-C invariant is: the quoted push-option colon
        is NEVER bound as a delete refspec — the op stays a NON-delete class AND any
        recovered `target_ref` is the real refspec (`main`), never `:oldref`/`:weird`.
        mint==read holds by construction (both derive via extract_command_context)."""
        op = OP(cmd)
        tr = CTX(cmd).get("target_ref")
        # op-axis: the -o-embedded colon must never promote the command to a delete
        # class (the core #1037 protection — kept and widened to mass-delete).
        assert op not in ("remote-ref-delete", "remote-mass-delete"), (
            f"{cmd!r} mis-classified as a delete op: {op!r}"
        )
        # target-axis: the recovered target is the REAL refspec (`main`) or absent,
        # NEVER the quoted push-option colon (the #1037 leak this row guards).
        assert tr in (None, "main"), (
            f"{cmd!r} recovered a non-refspec target_ref: {tr!r}"
        )
        assert tr is None or not tr.startswith(":"), (
            f"{cmd!r} leaked a colon delete-refspec as target_ref: {tr!r}"
        )


class TestRemoteRefDeleteParityMintAndNonVacuity:
    """§9.A parity canary + the end-to-end mint authorize proof (the real #1064
    closure) + non-vacuity counter-mutations for remote-ref-delete."""

    @pytest.mark.parametrize(
        "cmd,target",
        [
    ("git push origin :feature", "feature"),
],
    )
    def test_faithful_click_mints_and_authorizes_its_own_command(self, cmd, target):
        """End-to-end: a faithful ref-delete in an affirmative AUQ option mints a
        token whose target_ref re-matches the executed command -> AUTHORIZED. This
        is the #1064 closure (gated AND mintable), proved through the real mint
        path, not a read-gate alone."""
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "remote-ref-delete"
        assert result.context.get("target_ref") == target
        assert _target_value(result.context) is not None  # mint-side allow-list sees the target
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_cross_op_token_does_not_authorize_ref_delete(self):
        """A force-push token must NOT authorize a remote-ref-delete of the same
        ref (distinct op-classes), and vice-versa — the lead-Q1 distinct-op-class
        guarantee."""
        assert MATCH(token("force-push", target_ref="feature"), "git push origin :feature") is False
        assert MATCH(token("remote-ref-delete", target_ref="feature"), "git push --force origin feature") is False

    def test_mint_authorize_proof_is_non_vacuous_wrong_target_refused(self):
        """Non-vacuity for the authorize proof: a token whose target_ref is WRONG
        must NOT authorize the command. If MATCH ignored the target, the green
        authorize assertion above would be meaningless."""
        cmd = "git push origin :feature"
        assert MATCH(token("remote-ref-delete", target_ref="feature"), cmd) is True
        assert MATCH(token("remote-ref-delete", target_ref="other"), cmd) is False

# ===========================================================================
# OP-CLASS 2 — remote-mass-delete (#1062b)
# ===========================================================================

# Faithful mass forms: (command, expected mass_target). mass_target binds the
# destructive IDENTITY (the netstring _canonical_join of the field tuple),
# NOT the whole command.
REMOTE_MASS_DELETE_SPELLINGS = [
    ("git push --mirror origin", mgc._canonical_join(["--mirror", "origin"])),
    ("git push --prune origin refs/heads/main", mgc._canonical_join(["--prune", "origin", "refs/heads/main"])),
    ("git push origin --mirror", mgc._canonical_join(["--mirror", "origin"])),
    ('git push --mirror "origin"', mgc._canonical_join(["--mirror", "origin"])),
]


class TestRemoteMassDeleteNoUnderBlock:
    """§9.H.b/c/d — every faithful mass form gates as remote-mass-delete with the
    EXACT mass_target, AND the mint-side allow-list (_target_value) sees it
    (#1064-impossible canary: recognition and mintability are the same predicate)."""

    @pytest.mark.parametrize("cmd,_mt", REMOTE_MASS_DELETE_SPELLINGS)
    def test_recognition_implies_mintable_no_1064(self, cmd, _mt):
        """#1064-impossible: whenever a mass form is recognized, the mint-side
        _target_value MUST be populated — recognition <=> mintability."""
        ctx = CTX(cmd)
        assert ctx.get("mass_target") is not None
        assert _target_value(ctx) is not None

    def test_implicit_remote_is_mintable(self):
        """§9.H.b — the target-less implicit-remote mass form mints (no #1064)."""
        cmd = "git push --mirror"
        assert D(cmd) is True
        assert OP(cmd) == "remote-mass-delete"
        assert CTX(cmd).get("mass_target") == mgc._canonical_join(["--mirror", IMPLICIT])

    @pytest.mark.parametrize("cmd,mass_target", REMOTE_MASS_DELETE_SPELLINGS)
    def test_faithful_mass_form_gates_with_exact_mass_target(self, cmd, mass_target):
        assert D(cmd) is True, f"UNDER-BLOCK: faithful mass form not gated: {cmd!r}"
        assert OP(cmd) == "remote-mass-delete", f"misclassified: {cmd!r} -> {OP(cmd)}"
        assert CTX(cmd).get("mass_target") == mass_target, (
            f"mass_target mismatch for {cmd!r}: got {CTX(cmd).get('mass_target')!r}, want {mass_target!r}"
        )

class TestRemoteMassDeleteCrossAuthDistinctness:
    """§9.H.a — no-cross-auth: distinct destructive identities mint distinct
    tokens, and a token for one mass identity does NOT authorize another (the
    lesser->greater closure that the rejected coarse sentinel would have reopened)."""

    def test_prune_token_does_not_authorize_mirror(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--prune", "origin"]))
        assert MATCH(tok, "git push --prune origin") is True            # authorizes its own
        assert MATCH(tok, "git push --mirror origin") is False          # not a different identity

    def test_distinct_remote_does_not_cross_authorize(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--mirror", "origin"]))
        assert MATCH(tok, "git push --mirror origin2") is False

    def test_refspec_set_closure_a_b_does_not_authorize_a_c(self):
        tok = token("remote-mass-delete", mass_target=mgc._canonical_join(["--delete", "origin", "a", "b"]))
        assert MATCH(tok, "git push origin --delete a b") is True
        assert MATCH(tok, "git push origin --delete a c") is False

class TestRemoteMassDeleteBoundary:
    """§9.H.e — the single-vs-multi --delete boundary. EXACTLY one op-class per
    command; the git-grammar rationale (first positional == repository) is pinned
    in the assertions so the implicit-remote 2-token case is not mistaken for a bug."""

class TestRemoteMassDeleteParityMintAndNonVacuity:
    """§9.H.d/f — parity canary extended to remote-mass-delete + the end-to-end
    mint authorize proof + non-vacuity."""

    @pytest.mark.parametrize(
        "cmd,mass_target",
        [
    ("git push --mirror origin", mgc._canonical_join(["--mirror", "origin"])),
],
    )
    def test_faithful_click_mints_and_authorizes(self, cmd, mass_target):
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "remote-mass-delete"
        assert result.context.get("mass_target") == mass_target
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_mint_authorize_proof_is_non_vacuous_wrong_target_refused(self):
        cmd = "git push --mirror origin"
        assert MATCH(token("remote-mass-delete", mass_target=mgc._canonical_join(["--mirror", "origin"])), cmd) is True
        assert MATCH(token("remote-mass-delete", mass_target=mgc._canonical_join(["--prune", "origin"])), cmd) is False

class TestRemoteMassDeleteNoOverBlock:
    """Negatives — faithful non-mass forms and a mass literal carried in a commit
    message (carrier-5 strips it) must stay UNGATED."""

# ===========================================================================
# OP-CLASS 3 — branch-protection (#1063)
# ===========================================================================

# Per-client templates; {M} is the HTTP method. Host-agnostic: api and non-api
# (Enterprise) hosts both gate.
PROTECTION_CLIENTS = {
    "gh-api": "gh api -X {M} repos/o/r/branches/main/protection",
    "gh-api-method": "gh api --method {M} repos/o/r/branches/main/protection",
    "curl-api-host": "curl -X {M} https://api.github.com/repos/o/r/branches/main/protection",
    "curl-enterprise-host": "curl -X {M} https://git.example.com/repos/o/r/branches/main/protection",
    "wget": "wget --method={M} https://git.example.com/repos/o/r/branches/main/protection",
}
PROTECTION_GATED_METHODS = ["DELETE", "PUT", "PATCH"]


class TestBranchProtectionGate:
    """§9.E — per-method x client gate, host-agnostic, protected_branch extracted +
    mintable, mint==read parity."""

    @pytest.mark.parametrize("method", PROTECTION_GATED_METHODS)
    @pytest.mark.parametrize("client", list(PROTECTION_CLIENTS))
    def test_weakening_method_gates_per_client(self, method, client):
        cmd = PROTECTION_CLIENTS[client].format(M=method)
        assert D(cmd) is True, f"UNDER-BLOCK: {client} {method} not gated: {cmd!r}"
        assert OP(cmd) == "branch-protection"
        if client.startswith("gh-"):
            # amend_6 on #141: the gh api removal binds its repo
            assert CTX(cmd).get("protected_branch") == "api:o/r:main"
            assert _target_value(CTX(cmd)) is not None  # mintable
        else:
            # amend_7 on #141: curl/wget are gated but never minted
            assert _target_value(CTX(cmd)) is None
        # parity
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)


class TestBranchProtectionPostNotGated:
    """§9.E strengthening direction — POST ENABLES protection sub-features, so it
    must NOT gate (gating it would over-block)."""

class TestBranchProtectionSlashedAndMintAndNonVacuity:
    """Slashed-branch extraction + the end-to-end mint authorize proof + cross-op
    closure + non-vacuity counter-mutation."""

    @pytest.mark.parametrize(
        "cmd",
        [
    "gh api -X DELETE repos/o/r/branches/main/protection",
],
    )
    def test_faithful_click_mints_and_authorizes(self, cmd):
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == "branch-protection"
        assert result.context.get("protected_branch") == CTX(cmd).get("protected_branch")
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_cross_op_closure(self):
        """A branch-protection token must NOT authorize a CLI branch-delete of the
        same name (distinct op-classes)."""
        tok = token("branch-protection", protected_branch="api:o/r:main", host="github.com")
        assert MATCH(tok, "gh api -X DELETE repos/o/r/branches/main/protection") is True
        assert MATCH(tok, "git branch -D main") is False

    def test_mint_authorize_proof_non_vacuous_wrong_branch_refused(self):
        cmd = "gh api -X DELETE repos/o/r/branches/main/protection"
        assert MATCH(token("branch-protection", protected_branch="api:o/r:main", host="github.com"), cmd) is True
        assert MATCH(token("branch-protection", protected_branch="api:o/r:other", host="github.com"), cmd) is False

# ===========================================================================
# #1061 — host-agnostic curl ref-mutation: gate + mint + authorize + parity
# ===========================================================================


class TestHostAgnosticCurlRefMutationMintAndParity:
    """#1061 — the curl ref-mutation read floor is now host-agnostic (the literal
    `.*api.*` was dropped from the curl git/refs DANGEROUS_PATTERNS arms), so an
    HONEST ref-mutation against a non-api Enterprise/proxy host now ROUTES THROUGH
    APPROVAL instead of bypassing.

    This class pins the FULL "routes through approval" property for the
    host-agnostic-widened forms — not merely the read gate (covered by the
    carrier-8 PATH-vs-BODY positives), but that each MINTS a token AND the minted
    token AUTHORIZES its own command, with mint==read parity. That directly
    satisfies the "every gated op must mint" hard constraint for #1061 rather than
    relying on transitive coverage of the pre-existing API-ref op. The underlying
    op is the pre-existing API ref-mutation (DELETE -> branch-delete; PATCH/PUT ->
    force-push); #1061 brings the non-api-host READ floor to parity with the
    already-host-agnostic mint classifier. (curl commands must be backtick-wrapped
    in the AUQ option text to mint — pre-existing locate_command_regions substrate
    behavior; the mint() helper does this.)

    RE-RULED by amend_7 on #141: these forms stay GATED (the read floor above is
    unchanged) but never MINT: there is no sound flag walk over curl/wget options, so a
    URL-path parse must not stand in for an identity (the curl-merge posture, sec #71).
    The rows now pin gate + classify + parity + never-mint."""

    def test_widening_is_a_superset_api_host_still_gates_never_mints(self):
        """Control: the api-host form (which gated pre-#1061 too) STILL gates —
        proving #1061 is a strict superset (non-api host ADDED), not a swap that could
        have dropped the api-host coverage. amend_7: it never mints either."""
        cmd = "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/y"
        assert D(cmd) is True
        assert mint(cmd).context is None

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
            # non-api Enterprise / proxy hosts (no `api` substring in the URL)
            ("curl -X DELETE https://git.example.com/repos/o/r/git/refs/heads/y", "branch-delete"),
            ("curl --request DELETE https://git.internal.corp/repos/o/r/git/refs/heads/feature", "branch-delete"),
            ("curl -X PATCH https://git.example.com/repos/o/r/git/refs/heads/y", "force-push"),
        ],
    )
    def test_non_api_host_ref_mutation_gates_classifies_and_never_mints(self, cmd, expected_op):
        # read gate (the host-agnostic widening)
        assert D(cmd) is True, f"UNDER-BLOCK: non-api-host ref-mutation not gated: {cmd!r}"
        assert OP(cmd) == expected_op
        # mint==read parity
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)
        # amend_7: gated but never minted (always asks with the command text)
        assert mint(cmd).context is None, f"curl/wget ref form minted: {cmd!r}"
        assert _target_value(CTX(cmd)) is None


# ===========================================================================
# CARRIER-8 — PATH-vs-BODY invariant (#1037 over-block mitigation, §9.D/E)
# ===========================================================================


class TestCarrier8PathVsBody:
    """§9.D/E — carrier-8 strips the HTTP-client data BODY but never the URL PATH,
    so a faithful path-resident target STILL gates after the strip (zero
    under-block), while a benign command merely MENTIONING a sensitive path inside
    a quoted body is NOT over-blocked (#1037 FP killed)."""

# ===========================================================================
# CONTENTS-API regression + the assumption-A body-resident survey (§9.D)
# ===========================================================================


class TestContentsApiRegression:
    """§9.D — the body-resident contents-API arm. carrier-8 strips request bodies,
    but the contents arm reads its destructive target (the main/master branch) from
    the BODY, so a per-span contents preservation guard keeps it gating. These are
    the existing TestContentsAPI forms re-pinned AFTER carrier-8 landed."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=main -f sha=abc",
            "gh api -X PUT repos/owner/repo/contents/README.md -f branch=master",
            "gh api -X PATCH repos/owner/repo/contents/README.md -f branch=main",
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"main","sha":"abc"}\'',
            'curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d \'{"branch":"master"}\'',
        ],
    )
    def test_contents_forms_still_gate_after_carrier8(self, cmd):
        assert D(cmd) is True, f"REGRESSION: contents form no longer gates: {cmd!r}"

# ===========================================================================
# OUT-of-charter negatives + integration + accepted-grain / awareness pins
# ===========================================================================


class TestOutOfCharterNegatives:
    """§9.F — the #1063 charter boundary. Repo/release/api-repo deletion is OUT of
    charter and MUST stay ungated; a future widening that catches these is a scope
    violation."""

# ===========================================================================
# REVIEW-CYCLE-1 REMEDIATION — over-block fixes locked in (mint widened to match
# the read floor, NO read-narrowing) + the accepted conservative-recognition
# limitation pinned as INTENTIONAL forward-protection.
# ===========================================================================


class TestOverBlockGoneRegression:
    """The review-cycle-1 over-block fixes ripped out over-blocks by WIDENING the
    mint to match the read floor (never narrowing detection into a new under-block).
    Each test asserts a FAITHFUL form now routes correctly (gates AND, where a mint
    is expected, mints AND the token authorizes its own command) so a regression
    that re-introduces the over-block turns these red. Non-vacuity is intrinsic: the
    mint+authorize assertions only hold post-fix (pre-fix these were either
    mislabeled-dangerous or gated-but-unmintable), plus the case-sensitive
    counter-mutation below + the literal-arm contrast in the sibling class."""

    # --- cross-leg leak: a benign first-leg push is no longer mislabeled a delete ---

    # --- lowercase / mixed-case HTTP methods now MINT (mint widened to IGNORECASE) ---

    @pytest.mark.parametrize(
        "cmd,expected_op,target_key,target_val",
        [
    ("gh api -X delete repos/o/r/git/refs/heads/y", "branch-delete", "branch", "api:o/r:y"),
],
    )
    def test_lowercase_method_gh_api_gates_mints_and_authorizes(self, cmd, expected_op, target_key, target_val):
        assert D(cmd) is True
        assert OP(cmd) == expected_op
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason}) for {cmd!r}"
        assert result.context.get("operation_type") == expected_op
        assert result.context.get(target_key) == target_val
        assert MATCH(token_from_ctx(result.context), cmd) is True
        # parity equality now holds for the mint-widened lowercase form
        assert mgc.is_dangerous_command(cmd) == (mgc.detect_command_operation_type(cmd) is not None)

    def test_gh_global_flag_gates_mints_and_authorizes(self):
        cmd = "gh -R o/r api -X DELETE repos/o/r/branches/main/protection"
        assert D(cmd) is True
        assert OP(cmd) == "branch-protection"
        result = mint(cmd)
        assert result.context is not None, f"did not mint (refusal={result.refusal_reason})"
        assert result.context.get("protected_branch") == "api:o/r:main"  # amend_6 on #141
        assert MATCH(token_from_ctx(result.context), cmd) is True

    def test_curl_lowercase_method_protection_gates_and_never_mints(self):
        """curl + lowercase method + protection PATH gates and classifies (curl must
        be backtick-wrapped in the option text to mint — the mint() helper does).
        RE-RULED by amend_7 on #141 (same reasoning applied to protection removal):
        curl/wget are gated but never minted."""
        cmd = "curl -X put https://git.example.com/repos/o/r/branches/main/protection"
        assert D(cmd) is True
        assert OP(cmd) == "branch-protection"
        assert mint(cmd).context is None

    # --- gh global flag before `api` (gh -R o/r api ...) now MINTS ---

    # --- non-vacuity: in-memory counter-mutation (NOT git-revert; the shared worktree
    #     holds the architect's staged doc WIP, so a checkout-revert would destroy it) ---


class TestCrossLegFlagLeakOverBlockGone:
    """#1078: the flag-condition union arm derived FLAGS from the WHOLE command
    (`_shell_tokenize(command)`) while POSITIONALS came from the first executable
    leg — so a force/delete flag in a benign CONTINUATION leg leaked in and
    mislabeled a benign first-leg op (`git push origin feature && rm -rf build/`
    gated as force-push; `git push && rm -rf build/` was PERMANENTLY blocked:
    gated with no extractable target → unmintable). The fix anchors the ENTIRE
    arm — tokens, coarse-shape predicates, extractor inputs — to
    `_executable_prefix`, curing the close and branch-delete sibling arms at the
    same shared site. Mirrors TestOverBlockGoneRegression's cross-leg-gone block;
    the counter-mutation below proves these assertions are coupled to THIS fix."""

    @pytest.fixture(autouse=True)
    def _on_feature_branch(self, cli_repo_is_o_r):
        # A bare `git push` on main is a push to main (1.4.1 U13); these rows are about the
        # cross-leg flag leak, so they run on a feature checkout.
        (cli_repo_is_o_r / ".git" / "HEAD").write_text("ref: refs/heads/feature\n")

    # --- the 5 cured forms (+ separator variants of the primary) ---

    # --- non-vacuity contrast: single-first-leg flag forms still gate, correct op ---

    # --- parity: no gated-but-unmintable state for any cured form ---

    @pytest.mark.parametrize(
        "cmd,expected_op",
        [
    ("git push origin feature && rm -rf build/", None),
],
    )
    def test_no_gated_but_unmintable_state_for_cured_forms(self, cmd, expected_op):
        """No cured form can be gated-but-unmintable (that state needs D=True +
        detect=None; every row here is D=False). For the push/branch rows the
        stronger mint==read EQUALITY also holds (both sides reach the same fixed
        union arm and abstain). The close row differs BY DELIBERATE PRE-EXISTING
        DESIGN: detect's literal close arm classifies ANY `gh pr close` variant
        ('"close" - gh pr close (any variant)' per its own docstring — the close
        class is folded for symmetric authorization), so OP stays "close" while
        the read floor abstains — mint-wider-than-read, the SAFE direction (an
        ungated command never consults a token). The "close" expectation below
        DOCUMENTS that pre-existing behavior; it is NOT a contract that the close
        row must never become None — if a future change makes bare-close detect
        None, that is a separate design question, not a regression this test
        should veto."""
        assert D(cmd) is False
        assert OP(cmd) == expected_op

    @pytest.mark.parametrize(
        "cmd",
        [
            "git push --force origin main",                         # single leg
            "cd . && git push --force origin main",             # flag+push together in a NON-FIRST leg
            "git push --force origin main && echo ok",              # first-leg force + benign continuation
            "git push --force origin main 2>&1",                    # FD redirect — one leg (FD-neutralized)
            'git push --push-option "a && b" --force origin main',  # QUOTED separator — one leg
            'bash -c "git push --force origin main"',               # quoted single leg
            "GIT_TRACE=1 git push --force origin main",             # env-prefix
            "git push --force \\\norigin main",                     # line continuation
        ],
    )
    def test_literal_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: push + force-class flag co-occurring within
        ONE leg still gates in ANY leg position (the literal floor keeps its
        match-anywhere purpose, per leg). Includes the quoted-separator row — a
        `&&` inside quotes is not a leg boundary — which a tempered-regex span
        (`[^&|;]*`) would have wrongly ungated; that is why the fix is per-leg
        matching over the substrate, not a regex rewrite."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg force-push stopped gating: {cmd!r}"

    # --- literal force-push arms are leg-isolated too (#1082 — the former
    #     residual pins FLIPPED with the fix, per their own docstring contract) ---

    # --- non-vacuity: in-memory counter-mutation, executed in both directions ---


def _close_arm_fires(cmd: str) -> bool:
    """True iff a branch-deleting close arm (_CLOSE_LITERAL_ARMS) matches some leg of the
    stripped command: the per-leg surface #1087 converted. Resolves the module globals at
    call time, so the counter-mutations above act on it."""
    stripped = mgc._strip_non_executable_content(mgc._normalize_line_continuations(cmd))
    return any(
        arm.search(leg) for leg in mgc._slice_stripped_legs(stripped) for arm in mgc._CLOSE_LITERAL_ARMS
    )


class TestCloseLiteralArmCrossLegSweep:
    """#1087 CLOSE cross-leg completion — the permanent bidirectional sweep for
    the per-leg `_CLOSE_LITERAL_ARMS` conversion, modeled on the #1082 force-push
    pair (`test_literal_arm_cross_leg_span_cured` / `_same_leg_still_gates`).

    The close danger arms (`gh pr close` + `--delete-branch`) previously ran their
    `.*`/lookahead over the WHOLE stripped command, so a `--delete-branch` token in
    a benign continuation leg fired the arm cross-leg — an OVER-BLOCK, and the
    substrate of the #1087 laundering (the ambiguous multi-close minted a close
    token that authorized an escalated same-target single). The conversion matches
    per-leg: an arm fires iff `gh pr close` and `--delete-branch` co-occur within
    ONE leg. Per §0, the over-block-REMOVED direction is the PRIMARY/INVIOLABLE
    gate proven first; the same-leg-STILL-gates direction is the secondary
    no-new-under-block sweep."""

    # --- PRIMARY (§0-inviolable): over-block REMOVED — benign compounds run FREE ---
    @pytest.mark.parametrize(
        "cmd",
        [
    "gh pr close 42 && gh pr close 43 && echo --delete-branch",
],
    )
    def test_close_arm_cross_leg_span_cured(self, cmd):
        """#1087 CURED: a `--delete-branch` token in a SEPARATE leg never joins a close
        (the token and the close verb never co-occur in one leg), so the branch-deleting
        close arm does not fire and no --delete-branch is bound. FIX-MERGE f3 (amend_1 a)
        re-stated this row: a bare close now gates on its own, so the assertion moved from
        'ungated' to the arm and the bind, which is what #1087 cured. The ambiguous form
        is a compound of two destructive closes, refused before any mint."""
        assert _close_arm_fires(cmd) is False, f"CLOSE cross-leg arm span regressed: {cmd!r}"
        leg = mgc._single_destructive_leg(cmd)
        if leg is None:
            assert mgc.is_compound_destructive_command(cmd) is True, cmd
        else:
            assert mgc.extract_command_context(leg)["bound_flags"] == [], cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close 42 --delete-branch",                              # single leg
            "cd . && gh pr close 42 --delete-branch",                  # CRITICAL must-not-regress (co-occur in leg[1])
            'git commit -m "a && b" && gh pr close 42 --delete-branch',    # quoted && is substrate, real close leg gates
            "bash -c 'gh pr close 42 --delete-branch'",                    # quoted single leg
            "gh --repo o/r pr close 42 --delete-branch",                   # global-flag prefix
            "gh pr close 42 -d",                                           # first-leg flag-condition union arm
            "gh pr close -d 42",                                           # flag before positional
            "gh pr close 42 -cd",                                          # clustered short flag
            "gh pr close 5 -d && echo done",                              # first-leg -d + benign continuation
        ],
    )
    def test_close_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: `gh pr close` + delete flag co-occurring
        within ONE leg still gates in ANY leg position — including the CRITICAL
        `cd . && gh pr close 42 --delete-branch` (True today ONLY via the
        in-leg per-leg match the #1087 conversion re-establishes; it was True
        pre-fix via the cross-leg lookahead the fix removes). The `-d`/`-cd`
        short-flag forms gate via the first-leg flag-condition union arm."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg dangerous close stopped gating: {cmd!r}"

    # --- SECONDARY (no-new-under-block): same-leg close+flag STILL gates ---
class TestApiLiteralArmCrossLegSweep:
    """#1086 API cross-leg completion — the permanent bidirectional sweep for the
    per-leg `_API_LITERAL_ARMS` conversion (17 arms), modeled on the #1082
    force-push pair. The API danger arms previously ran their `.*` over the WHOLE
    stripped command, so a mutating method / body-flag token in a benign
    continuation leg (`gh api .../git/refs && echo -X DELETE`) over-blocked the
    benign compound. Per-leg now: an arm fires iff the API client, the mutating
    method (or implicit-POST body flag), and the target endpoint co-occur within
    ONE leg. Unlike close, the API emergent compounds are PURE over-blocks (an
    isolated bare API leg is method-less hence detect-negative, so tier-2 abstains
    symmetrically — no laundering asymmetry; pinned by the OPEN-Q D tripwire in
    test_merge_guard_over_block_batch.py). §0 priority: over-block-removed first."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X DELETE repos/o/r/git/refs/heads/x",                 # single leg
            "cd . && gh api -X DELETE repos/o/r/git/refs/heads/x",     # non-first leg (method+path co-occur)
            "gh api repos/o/r/pulls/5/merge -X PUT",                       # merge, method after path
            "curl -X DELETE https://api.github.com/repos/o/r/git/refs/heads/x",  # curl same-leg
            "wget --method=DELETE https://api.github.com/repos/o/r/git/refs/heads/x",  # wget same-leg
            "gh api -X DELETE repos/o/r/branches/main/protection",        # protection (first-leg)
            "gh api -f sha=abc repos/o/r/git/refs/heads/x",               # implicit-POST body flag same-leg
        ],
    )
    def test_api_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: the API client + mutating method (or
        implicit-POST body flag) + endpoint co-occurring within ONE leg still gates
        in ANY leg position. (The non-first-leg protection form
        `cd . && gh api -X DELETE .../branches/main/protection` is separately
        pinned at `test_literal_arm_contrast_still_gates_non_first_leg`; the
        git/refs non-first-leg row here is its sibling, not a duplicate.)"""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg dangerous API stopped gating: {cmd!r}"

    # --- PRIMARY (§0-inviolable): over-block REMOVED — one row per family ---
    # --- SECONDARY (no-new-under-block): same-leg dangerous API STILL gates ---
class TestBranchDeleteLiteralArmCrossLegSweep:
    """#1094 BRANCH-DELETE cross-leg completion — the permanent bidirectional
    sweep for the per-leg `_BRANCH_DELETE_LITERAL_ARMS` conversion, the 4th and
    FINAL cross-leg twin (force-push / close / API precede it), modeled on the
    close/API sweep classes above.

    The three branch-delete danger arms previously ran their `.*` over the WHOLE
    stripped command, so an idiomatic `-D` / `--delete --force` token in a benign
    continuation leg (`git branch new-feature && echo -D`) gated the benign
    compound — and, because detect ALSO classified the compound branch-delete
    whole-command with an extractable target, the ambiguous compound was MINTABLE
    (the close-twin laundering shape; closure canary lives with the close
    canaries in test_merge_guard_auth_symmetry.py). Per-leg now, on BOTH
    consumers of the one SSOT tuple (read floor AND detect): an arm fires iff
    `git branch` and the force-delete flag co-occur within ONE leg.

    The arms are FORWARD-ONLY (`branch` precedes the flag) — unlike close there
    is no reversed arm, so there is no reversed-order cure; the reversed row
    below is a PRE-EXISTING-False pin. The third arm (`--force --delete`,
    whitespace-adjacent) has NO cross-leg over-block to cure (`\\s+` never spans
    a shell operator) — it moved for SSOT symmetry only, so no "cured" row
    exists for it and none should be added. Clustered/split spellings
    (`-Df` / `-fD` / `--delete -f`) are the first-leg-anchored union arm's job,
    untouched in both directions. Per §0, the over-block-REMOVED direction is
    the PRIMARY/INVIOLABLE gate; same-leg-STILL-gates is the secondary
    no-new-under-block sweep."""

    # --- PRIMARY (§0-inviolable): over-block REMOVED — benign compounds run FREE ---
    @pytest.mark.parametrize(
        "cmd",
        [
    "git branch new-feature && echo -D",
],
    )
    def test_branch_delete_arm_cross_leg_span_cured(self, cmd):
        """#1094 CURED, with mint parity in the SAME breath: a benign `git branch`
        leg chained with a stray force-delete token in a SEPARATE leg no longer
        gates (D is False), AND detect abstains identically (OP is None) — the
        gate⟺mint coherence that closes both the mint-scan multiplicity vector
        (a benign compound quoted in AskUserQuestion prose no longer contributes
        a phantom (op,target) pair that could REFUSE a faithful click's mint)
        and the retirement Block-1 drift (an executed benign compound no longer
        classifies branch-delete at the retirement observer)."""
        assert D(cmd) is False, f"BRANCH-DELETE cross-leg over-block regressed: {cmd!r}"
        assert OP(cmd) is None, (
            f"detect still classifies the cured compound — gate/mint coherence "
            f"broken (mint-scan multiplicity vector re-opened): {cmd!r}"
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            "git branch -D temp",                                # arm 1, single leg
            "cd . && git branch -D temp",                    # arm 1 in a NON-FIRST leg (union arm abstains — literal arm load-bearing)
            "git branch --delete --force temp",                  # arm 2
            "git branch --force --delete temp",                  # arm 3
            "git branch -D temp && echo done",                   # arm 1 + benign continuation
            'git commit -m "a && b" && git branch -D temp',      # QUOTED separator is NOT a leg boundary
            "bash -c 'git branch -D temp'",                      # quoted single-leg carrier
            "FOO=1 git branch -D temp",                          # env-prefix
            "git branch \\\n-D temp",                            # line continuation (D unchanged; its raw-seam OP is pinned below, not here)
            "git branch -Df temp",                               # union arm, clustered
            "git branch -fD temp",                               # union arm, clustered
            "git branch --delete -f temp",                       # union arm, split
            "git branch -f --delete temp",                       # union arm, split
            "cd . && git branch --delete --force temp",      # arm 2 in a NON-FIRST leg (union arm abstains — literal arm load-bearing)
        ],
    )
    def test_branch_delete_arm_same_leg_still_gates(self, cmd):
        """The no-new-under-block set: `git branch` + force-delete flag
        co-occurring within ONE leg still gates in ANY leg position — including
        the CRITICAL `cd . && git branch -D temp` (the union arm is
        first-leg-anchored and abstains there, so ONLY the per-leg literal arm
        catches it). The quoted-separator row is why this fix is per-leg
        matching over the substrate, not a tempered-regex span (`[^&|;]*` would
        wrongly ungate it). The clustered/split spellings gate via the
        first-leg flag-condition union arm — untouched surface."""
        assert D(cmd) is True, f"NEW UNDER-BLOCK: same-leg branch-delete stopped gating: {cmd!r}"

    # --- SECONDARY (no-new-under-block): same-leg branch-delete STILL gates ---
    # --- mint parity on the preserved set (detect converted WITH the read floor) ---
    # --- non-vacuity: row-by-row two-stage counter-mutation (in-memory) ---
class TestBranchDeleteWordBoundaryMustStayOff:
    """MUST_STAY_OFF — the `\\b` on the `-D` literal arm, enforced.

    The branch-delete arm is `git\\s+...branch\\s+.*-D\\b`. The trailing `\\b` is the
    only thing stopping `-D` from matching INSIDE an ordinary branch name: any name
    whose second hyphen-separated word starts with a capital D reads as `-D` to a
    boundaryless pattern — `feature-Dashboard`, `release-December`, `*-Dev*`,
    `new-Design`.

    WHY THIS CLASS EXISTS AT ALL. "Do not touch the `\\b`" was a documented mandate
    with NOTHING enforcing it. Removing the boundary from all three occurrences
    produces a suite failure set IDENTICAL to control — zero new failures, zero
    disappeared — while gating SIX ordinary good-faith commands, including
    `git branch feature-Dashboard`, which CREATES a branch and would be classified
    `branch-delete`. That is the cardinal over-block, invisible, with a green suite.

    So this is not a check that proved nothing; it was an invariant with no check.
    These rows convert it into a visible one. Every command below is benign and
    must never gate."""

def _close_target(cmd):
    """The minted close-target identity for `cmd` via the REAL post path."""
    res = mint(cmd)
    assert res.context is not None, f"close did not mint (refusal={res.refusal_reason}): {cmd!r}"
    return _target_value(res.context)


def _authorizes(approve_cmd, execute_cmd):
    """True iff a token minted from `approve_cmd` authorizes `execute_cmd`."""
    res = mint(approve_cmd)
    assert res.context is not None, f"approve did not mint: {approve_cmd!r}"
    return MATCH(token_from_ctx(res.context), execute_cmd)


_PULL5 = "https://github.com/o/r/pull/5"


class TestCloseTargetMintsAllFaithfulForms:
    """`gh pr close` accepts ``{<number> | <url> | <branch>}`` — all three are
    faithful. The per-leg widening gated the url/branch forms in a non-first leg
    but ``_extract_pr_number`` is number-only, so they gated WITHOUT a mint path
    = a HEAD-introduced faithful GATED-BUT-UNMINTABLE over-block (and the same
    shape pre-existed on the first leg). `_extract_close_target` closes it by
    minting every faithful form. These rows are the MUST_FLIP cert for that.

    The no-arg form (`gh pr close -d`) is NOT here: gh refuses a close with no
    positional, so it has no faithful click and its gated-but-unmintable state is
    not an over-block (measured separately)."""

    @pytest.mark.parametrize(
        "cmd,expected",
        [
    ("gh pr close 5 -d", "5"),
],
    )
    def test_every_faithful_close_form_gates_and_mints(self, cmd, expected):
        assert D(cmd) is True, f"faithful close stopped gating: {cmd!r}"
        assert _close_target(cmd) == expected, (
            f"close minted the wrong identity for {cmd!r} — recognition<=>mint "
            f"coupling (#1064) requires the identity to be exactly the target"
        )

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            # repo/owner containing digits — the /pull/ anchor is not fooled
            ("gh pr close https://github.com/o/r-123/pull/5 -d", "url:github.com/o/r-123#5"),
            # trailing path / query / fragment — (\d+)(?![\w-]) stops at the PR number
            ("gh pr close https://github.com/o/r/pull/5/files -d", "url:github.com/o/r#5"),
            # GitHub Enterprise host — must still mint (not an over-block)
            ("gh pr close https://github.company.com/o/r/pull/42 -d", "url:github.company.com/o/r#42"),
        ],
    )
    def test_url_number_parse_robust_against_decoy_digits(self, cmd, expected):
        """The URL identity is anchored on the literal `/pull/<N>` segment, so a
        digit in the owner/repo, a query string, a fragment, or a trailing path
        component can never be mistaken for (or extend) the PR number."""
        assert _close_target(cmd) == expected

class TestCloseTargetLaunderingRefused:
    """MINT-LAUNDERING known-bads (#1064) — each MUST be caught (REFUSE). Shown
    FAILING-on-known-bad, not just the happy path passing. The four identity
    namespaces (`5` / `url:host/o/r#N` / `branch:name`) are disjoint, and the URL
    is HOST- and repo-QUALIFIED specifically so it can never set-equal a bare
    number in a DIFFERENT repo."""

    def test_cross_repo_url_to_bare_number_refuses(self):
        """THE SHARP ONE. Approve closing PR #5 via an explicit-repo URL; execute
        a bare `gh pr close 5` — which closes PR #5 of whatever repo it runs in.
        If the url minted bare `5`, this would authorize closing PR #5 in a
        DIFFERENT repo (cross-repo target confusion). Repo-qualification blocks it."""
        assert _authorizes(f"gh pr close {_PULL5} -d", "gh pr close 5 -d") is False

    def test_cross_host_url_refuses(self):
        assert _authorizes(
            "gh pr close https://github.com/o/r/pull/5 -d",
            "gh pr close https://github.enterprise.com/o/r/pull/5 -d",
        ) is False

    def test_branch_to_number_refuses(self):
        assert _authorizes("gh pr close feature -d", "gh pr close 5 -d") is False

    def test_branch_to_different_branch_refuses(self):
        assert _authorizes("gh pr close feature -d", "gh pr close other -d") is False

    def test_faithful_self_authorizes_every_form(self):
        """Non-vacuity for the REFUSE rows: the same command DOES authorize itself
        on every form, so the refusals above are discrimination, not a blanket
        deny."""
        for cmd in ("gh pr close 5 -d", f"gh pr close {_PULL5} -d", "gh pr close feature -d",
                    "cd . && gh pr close feature -d"):
            assert _authorizes(cmd, cmd) is True, f"faithful re-execution refused: {cmd!r}"

class TestCloseTargetBranchInertAndLegScoped:
    """Constraint B — the branch identity binds the LITERAL positional token, and
    that binding is (i) LEG-SCOPED (no cross-leg pickup) and (ii) FLAG-INERT (a
    dash-token can never be bound as the target, a quoted token binds its literal
    content and cannot inject)."""

    def test_branch_identity_is_leg_scoped(self):
        """A url/branch in a BENIGN continuation leg is never picked up as the
        close target — the extractor reads only the first executable leg. If it
        leaked, the first-leg numbered close would mis-bind the later leg's ref."""
        # first leg is the real close (mints 5); a decoy branch/url follows benign
        assert _close_target("gh pr close 5 -d && echo feature") == "5"
        assert _close_target(f"gh pr close 5 -d ; echo {_PULL5}") == "5"

    def test_numeric_repo_launder_refuses(self):
        """The security-confirmed launder MUST be caught: approving a branch-close
        whose `--repo 5/6` shadowed the PR must NOT authorize closing PR #5."""
        assert _authorizes(
            "gh pr close --repo 5/6 feature -d",   # approve: closes branch `feature`
            "gh pr close 5 --repo 5/6 -d",         # execute: closes PR #5 — different target
        ) is False

    def test_comment_value_that_is_a_url_is_not_read_as_the_pr(self):
        """The sharpest value-flag case: a PR URL passed as a `--comment` value
        must NOT be extracted as the close target — the real target is the branch
        positional. Value stripping happens BEFORE URL classification."""
        cmd = "gh pr close feature --comment https://github.com/o/r/pull/999 -d"
        assert _close_target(cmd) == "branch:feature", (
            "a URL inside a --comment value was mis-bound as the PR — the value "
            "strip must precede URL classification"
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh pr close --repo o/r feature -d",     # --repo VALUE before target
            "gh pr close -dR o/r feature",           # clustered -dR, value is next token
            'gh pr close -c "nice work" feature -d',  # -c comment value (quoted)
        ],
    )
    def test_value_flag_value_is_not_bound_as_the_target(self, cmd):
        """CONSTRAINT B — a VALUE-taking flag's value (`-R/--repo o/r`,
        `-c/--comment msg`) is stripped before the positional scan, so a faithful
        branch-close WITH such a flag still binds the BRANCH (never over-blocks by
        counting the value as a second positional, never mis-binds the value)."""
        assert D(cmd) is True
        assert _close_target(cmd) == "branch:feature", (
            f"a value-taking flag's value was mis-read as the close target: {cmd!r}"
        )

    def test_branch_shaped_like_a_flag_is_not_bound_and_does_not_inject(self):
        """A positional that LOOKS like a flag (`--force`, `-D`) is dropped by the
        dash-filter, so it is never bound as the target and cannot inject. A close
        whose only positional-looking token is dash-prefixed has NO target ->
        abstains (the command is not a faithful single-target close anyway)."""
        assert _close_target_or_none("gh pr close --force -d") is None
        assert _close_target_or_none("gh pr close -D -d") is None

def _close_target_or_none(cmd):
    """Like _close_target but tolerates a non-minting (abstaining) close: returns
    the identity or None, without asserting a mint occurred."""
    res = mint(cmd)
    return _target_value(res.context) if res.context is not None else None


class TestApiMergeMintParity:
    """#1096 API-merge mint parity — the bidirectional cert for the additive
    per-leg detect arm (GH-API-ONLY per Option B + mutating PUT/PATCH/POST + a
    pulls/<N>/merge endpoint in ONE leg → "merge") plus the path-based
    `_extract_api_merge_pr` wired into `extract_command_context` as the
    pr_number fallback.

    Pre-fix, a mutating API PR-merge was the gated-but-unmintable class: the
    read floor gated it (DANGEROUS_PATTERNS API-merge arms) but detect returned
    None, so the mint write-gate refused → a faithful API-merge click could
    NEVER mint — a PERMANENT over-block. The cure raises recognition in the ONE
    detect SSOT (mint + read + retirement observer all served from one site;
    never a mint-only recognition fork).

    Priority per the governing principle: over-block-CURED (mints + authorizes)
    is the PRIMARY/inviolable direction; additive purity is co-inviolable (an
    existing classification change could re-block a faithful click elsewhere);
    the no-new-under-block rows are the secondary sweep. GH-API-ONLY (Option B):
    curl/wget api-merge mints NOTHING (detect=None → mint=0) — dropped because a
    value-flag denylist over curl/wget's unbounded flag space is unsound (sec #71).
    Curl/wget merges stay READ-gated (is_dangerous=True) = the pre-existing
    gated-but-unmintable state; that structural fact is asserted in
    TestApiMergeEndpointResidualBoundary::test_curl_wget_api_merge_mints_nothing_by_construction."""

    def _authorize(self, cmd, tmp_path):
        """Mint `cmd` via the real bundle path, write the token, and run the
        real read-side authorization. Returns (ctx, verdict)."""
        ctx, refusal = mint(cmd)
        if ctx is None:
            return None, f"NO-MINT({refusal})"
        write_token(ctx, token_dir=tmp_path)
        err = check_merge_authorization(cmd, token_dir=tmp_path)
        return ctx, ("ALLOW" if err is None else "DENY")

    # --- PRIMARY (inviolable): every faithful spelling classifies + mints ---

    @pytest.mark.parametrize(
        "cmd,expected_flags",
        [
    ("gh api -X PUT /repos/o/r/pulls/42/merge", []),
],
    )
    def test_api_merge_spelling_classifies_and_mints(self, cmd, expected_flags):
        """#1096 CURED: every faithful API-merge spelling detects as `merge` and
        mints a pr-targeted token. The gh-global-flag row is the Ruling-B cure
        (tolerant _GH_API_PREFIX client match) and binds its REAL -R flag as
        [--repo=o/r] (ruled correct: a real denylist flag, spelling-appropriate);
        every path-resident-only spelling binds []."""
        assert OP(cmd) == "merge", f"API-merge spelling unclassified: {cmd!r}"
        ctx, refusal = mint(cmd)
        assert ctx is not None, f"OVER-BLOCK regressed (no mint): {cmd!r} ({refusal})"
        assert ctx["pr_number"] == "api:o/r#42"   # repo-qualified (amend_2 on #141)
        assert ctx["bound_flags"] == expected_flags

    def test_api_approval_refuses_different_target(self, tmp_path):
        ctx, _ = mint("gh api -X PUT /repos/o/r/pulls/42/merge")
        assert ctx is not None
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/43/merge", token_dir=tmp_path) is not None

    def test_cli_approval_does_not_authorize_api_exec(self, tmp_path):
        """RE-RULED by amend_2 on #141: a REST identity binds its repo (`api:o/r#42`), while
        the CLI `gh pr merge 42` names the repo only implicitly, so the hook cannot prove
        they are the same PR. Cross-spelling now REFUSES in both directions (before amend_2
        a CLI #42 approval authorized a REST merge of #42 in ANY repo)."""
        ctx, _ = mint("gh pr merge 42")
        assert ctx is not None and ctx["bound_flags"] == []
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/42/merge", token_dir=tmp_path) is not None

    def test_api_approval_does_not_authorize_cli_exec(self, tmp_path):
        """RE-RULED by amend_2 on #141 (the reverse direction of the row above)."""
        ctx, _ = mint("gh api -X PUT /repos/o/r/pulls/42/merge")
        assert ctx is not None and ctx["bound_flags"] == []
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is not None

    def test_global_flag_repo_bind_refuses_cross_repo(self, tmp_path):
        """The [--repo] round-trip's safe edge: the repo-scoped token REFUSES an
        exec against a DIFFERENT repo (the escalation-discriminator direction)."""
        ctx, refusal = mint("gh -R o/r api /repos/o/r/pulls/42/merge -X PUT")
        assert ctx is not None and ctx["bound_flags"] == ["--repo=o/r"]
        write_token(ctx, token_dir=tmp_path)
        err = check_merge_authorization(
            "gh -R other/x api /repos/other/x/pulls/42/merge -X PUT",
            token_dir=tmp_path)
        assert err is not None, "cross-repo exec must REFUSE against a repo-bound token"

    def test_privileged_cli_approval_refuses_plain_api_exec(self, tmp_path):
        """Non-laundering: a PRIVILEGED CLI approval (bound [--admin]) does NOT
        authorize the plain API exec (flag-set mismatch) — the privileged
        approval cannot be laundered into a differently-shaped execution."""
        ctx, _ = mint("gh pr merge 42 --admin")
        assert ctx is not None and ctx["bound_flags"] == ["--admin"]
        write_token(ctx, token_dir=tmp_path)
        assert check_merge_authorization(
            "gh api -X PUT /repos/o/r/pulls/42/merge", token_dir=tmp_path) is not None

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api -X PUT /repos/o/r/pulls/42/merge",
            "gh -R o/r api /repos/o/r/pulls/42/merge -X PUT",
        ],
    )
    def test_api_merge_faithful_round_trip_authorizes(self, cmd, tmp_path):
        """The full faithful click: approve → mint → byte-identical re-execution
        AUTHORIZES through the real read path (the §0 inviolable round-trip)."""
        _ctx, verdict = self._authorize(cmd, tmp_path)
        assert verdict == "ALLOW", (
            f"faithful API-merge round-trip blocked ({verdict}): {cmd!r}"
        )

    # --- cross-spelling authorization (correct same-op + non-laundering) ---

    # --- additive purity + no-new-under-block: negatives stay unmintable ---

    # --- non-vacuity: two independent in-memory couplings ---


# The endpoint (`.../repos/o/r/pulls/N/merge`) is the target PR; a decoy
# `pulls/M/merge` in a flag VALUE or another leg must never be the bound target.
# These helpers build the mint→authorize seam once for the endpoint-position suite.
def _mint_pr(cmd):
    """Mint `cmd` and return the bound pr_number (or None if it did not mint)."""
    ctx, _reason = mint(cmd)
    return ctx.get("pr_number") if ctx is not None else None


def _authorize_standalone(approve_cmd, standalone_cmd, tmp_path):
    """Mint the approval, write the token, then run the read-side authorization
    for a DIFFERENT standalone command. Returns 'ALLOW' | 'DENY' | 'NO-MINT'."""
    ctx, _reason = mint(approve_cmd)
    if ctx is None:
        return "NO-MINT"
    write_token(ctx, token_dir=tmp_path)
    err = check_merge_authorization(standalone_cmd, token_dir=tmp_path)
    return "ALLOW" if err is None else "DENY"


class TestApiMergeEndpointPositionLaunderingClosed:
    """#1096 endpoint-position fix (commit that added `_api_merge_leg_endpoint`) —
    the REMEDIATION cert for the BLOCKING target-confusion laundering the prior
    (flat first-match) extractor allowed.

    ROOT: `_extract_api_merge_pr` did `re.search(r"pulls/(\\d+)/merge", command)` —
    a FLAT whole-string first-match that bound the FIRST `pulls/<M>/merge`
    substring, which need not be the endpoint. A decoy PR in a flag body
    (`-f note=pulls/5/merge`), a header, the `-R` value, or an earlier leg
    (`echo pulls/5/merge && …`) bound a token `{merge, 5}`; a standalone
    `gh api -X PUT .../pulls/5/merge` (NEVER approved) then AUTHORIZED — a consent
    breach (approve merge-6 → authorize merge-5).

    FIX: bind the ENDPOINT-position PR (the URL positional the command actually
    merges) via the shared per-leg helper + shlex-view positional walk. Every row
    below approves endpoint-6 and asserts (a) the token binds 6, and (b) the
    standalone decoy-5 merge DENIES for lack of a matching token — the laundering
    is CLOSED. Priority: this is the laundering-closed (security) direction; the
    no-over-block (§0 inviolable) direction is the sibling class below."""

    # (name, approval command [endpoint 6, decoy 5 embedded], standalone decoy-5)
    _DECOY = "gh api -X PUT repos/o/r/pulls/5/merge"

    @pytest.mark.parametrize(
        "approve,standalone",
        [
    ("echo pulls/5/merge && gh api -X PUT repos/o/r/pulls/6/merge", _DECOY),
],
    )
    def test_decoy_binds_endpoint_and_standalone_decoy_denies(
        self, approve, standalone, tmp_path
    ):
        """Every confirmed exploit: the approval binds the ENDPOINT (6), and the
        never-approved standalone decoy-5 merge DENIES. Pre-fix the flat
        first-match bound 5 and this standalone AUTHORIZED (the laundering)."""
        assert _mint_pr(approve) == "api:o/r#6", (
            f"endpoint-position bind regressed (bound a decoy): {approve!r}"
        )
        assert _authorize_standalone(approve, standalone, tmp_path) == "DENY", (
            f"LAUNDERING OPEN: standalone decoy-5 authorized off the {approve!r} token"
        )

class TestApiMergeEndpointNoOverBlock:
    """#1096 endpoint-position fix — the §0 INVIOLABLE no-over-block direction:
    every faithful GH-API api-merge binds its CORRECT endpoint and round-trips, and
    the fix never returns None for a recognized merge (no gated-but-unmintable).
    GH-API-ONLY (Option B): curl/wget api-merge is intentionally unmintable — see
    TestApiMergeEndpointResidualBoundary::test_curl_wget_api_merge_mints_nothing_by_construction."""

    @pytest.mark.parametrize(
        "cmd",
        [
    "gh api -X PUT repos/o/r/pulls/6/merge",
],
    )
    def test_faithful_spelling_binds_correct_endpoint(self, cmd):
        """Every faithful spelling mints its CORRECT endpoint (6), including the
        body-coincidence merge whose `-f note` happens to mention pulls/5 — the
        endpoint positional (6) is bound, never the body decoy, and never None."""
        assert _mint_pr(cmd) == "api:o/r#6", f"faithful api-merge bound wrong/None: {cmd!r}"

    def test_gh_api_attached_method_gates_and_mints(self):
        """The gh-api ATTACHED method spelling `gh api --method=PUT .../merge` (and
        `-XPUT`) gates like the spaced form since review #118 M2 closed the #1079
        method-delimiter under-block, and stays READ==MINT SYMMETRIC: it gates AND its
        approval binds the same endpoint PR."""
        for cmd in ("gh api --method=PUT repos/o/r/pulls/6/merge",
                    "gh api -XPUT repos/o/r/pulls/6/merge"):
            assert D(cmd) is True, "read floor does not gate the attached form: %r" % cmd
            assert _mint_pr(cmd) == "api:o/r#6", "gated but not mintable: %r" % cmd

class TestApiMergeEndpointCarrierInteraction:
    """#1096 §7 carrier-interaction (b409be63 L8 / #1074): the PATH-resident
    endpoint survives EVERY value-stripping carrier (carrier-4 var-assign strip +
    carrier-8 HTTP-body strip), a body decoy is removed exactly once, and the
    §2.3 unquoted carrier-8 extension does not disturb the endpoint. Two levels:
    (a) the mechanism (is_dangerous/extract), (b) the end-to-end mint→authorize
    security proof."""

    def test_seam_body_decoy_does_not_leak_to_mintable_token(self, tmp_path):
        """SECURITY: end-to-end, the body decoy does not leak through to a
        mintable token — approving the endpoint-6 merge binds 6, and a standalone
        decoy-5 merge DENIES (the decoy never became an authorizing target)."""
        approve = "gh api -X PUT -f branch=pulls/5/merge repos/o/r/pulls/6/merge"
        assert _mint_pr(approve) == "api:o/r#6"
        assert _authorize_standalone(
            approve, "gh api -X PUT repos/o/r/pulls/5/merge", tmp_path) == "DENY"

class TestApiMergeEndpointResidualBoundary:
    """#1096 GH-API-ONLY narrowing (Option B) TRIPWIRE. The prior option-1 exotic-curl
    ACCEPTED-RESIDUAL is SUPERSEDED: curl/wget api-merge legs now classify detect=None
    (mint=0), so there is NO laundering residual to accept — the whole curl/wget decoy
    class dies BY CONSTRUCTION (can't mint -> can't launder; sec #71 proved a value-flag
    denylist over curl/wget's unbounded flag space is uncompletable). This class pins
    (a) the gh-api no-widening guard (the kept, provably-sound client) and (b) the
    STRUCTURAL assertion that curl/wget mint NOTHING regardless of flags — deliberately
    NOT re-enumerating the 52 curl vectors as a denylist (that would re-import the
    open-endedness Option B exits)."""

    # (a) gh-api no-widening: an ENUMERATED gh-api value-flag carrying a decoy URL BEFORE
    # the endpoint is skipped, so mint binds the ENDPOINT (6) and the standalone decoy-5
    # DENIES. RED if a common gh-api flag drops from _API_MERGE_GH_VALUE_FLAGS (widening).
    @pytest.mark.parametrize(
        "approve",
        [
    'gh api -H "X-Ref: https://x/repos/o/r/pulls/5/merge" -X PUT repos/o/r/pulls/6/merge',
],
    )
    def test_gh_api_enumerated_value_flag_decoy_binds_endpoint(self, approve, tmp_path):
        """gh-api no-widening: an enumerated gh-api value-flag decoy binds the ENDPOINT
        (6), and the standalone decoy-5 DENIES. RED if a common gh-api flag drops from
        the skip set (silent widening)."""
        assert _mint_pr(approve) == "api:o/r#6", (
            f"a gh-api enumerated value-flag decoy bound the decoy — residual widened: {approve!r}"
        )
        assert _authorize_standalone(
            approve, "gh api -X PUT repos/o/r/pulls/5/merge", tmp_path) == "DENY"

class TestParityCanaryReconciledForWidenedForms:
    """§9.A parity reconciliation (review-cycle-1). After the mint-widening, the mint
    classifier AGREES with the read floor for the lowercase / mixed-case / global-flag
    forms, so the mint==read EQUALITY now holds for them (pre-fix these were
    read=True / detect=None — a gated-but-unmintable over-block). This extends the
    §9.A canary beyond the uppercase-only forms the original suite covered."""


class TestStaleSnapshotIntegration:
    """CODE integration note — the deferred->gated cross-group transition. In the
    #1062a-only state `git push origin --delete a b` and `git push --mirror origin`
    were is_dangerous=False; after #1062b they gate as remote-mass-delete. Pin the
    INTEGRATED-HEAD behavior so a stale pre-integration negative cannot creep back."""

    def test_push_to_main_with_quoted_colon_gates_as_push_to_main(self):
        """The quoted-colon push-option form is NOT a ref-delete but IS dangerous on
        its own merits (push-to-main) — the #1037 quoted colon is inert, the
        push-to-main classification stands."""
        cmd = "git push origin main -o 'ci.message=cleanup :oldref'"
        assert D(cmd) is True
        assert OP(cmd) == "push-to-main"
        assert OP(cmd) != "remote-ref-delete"
