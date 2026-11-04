"""Privileged-flag binding RED matrix + CLI-form normalization.

SACROSANCT merge-gate control (risk tier CRITICAL). Before this binding, the
guard bound an approval to its execution by (operation_type, target) only and
DROPPED every dash-flag — so a privileged flag added at execution time rode past
the checkpoint undetected:

    approve `gh pr merge 5`  →  execute `gh pr merge 5 --admin`     (branch-protection bypass)
    approve `gh pr merge 5`  →  execute `gh pr merge 5 -R victim/x` (cross-repo redirect)
    approve `git push origin main --force` → execute `... --no-verify` (pre-push-hook bypass)

The fix adds a `bound_flags` binding dimension enforced as a never-escalate
SET-EQUALITY rule: the executed command's binding-relevant flags must EXACTLY
equal the approved set, so ANY added privilege OR dropped constraint REFUSES.

Layering (which guard each test exercises):
  * READ ARM — `_token_matches_command(token, command)`: the executed `command`
    is scanned LIVE by the real `extract_command_context` SSOT; only the approval
    (token) side is hand-built. Asserts REFUSE on any flag difference and
    AUTHORIZE on an exact, FORM-INVARIANT match.
  * SCANNER — `extract_privileged_flags(command, op_type)`: direct output pins for
    the CLI-form normalization (short / long / =-joined / attached-short /
    combined-short cluster / git-surface prefix abbreviation), incl. the pflag
    `-Rd` value-consumption subtlety and the git-vs-gh abbreviation asymmetry.
  * MULTIPLICITY — `_collect_pairs`: flag variation is a pair ATTRIBUTE, never part
    of (op,target) identity, so it cannot inflate the distinct-pair count and trip
    the SACROSANCT divergence-refusal.

The A1 MINT symmetry (the mint must scan a surface WIDER than the
quote-truncated bare-command region — the command's own leg within the
selected-option text, so a flag after a quoted argument still binds) is proven
through the REAL mint seam in test_merge_guard_auth_symmetry.py — a hand-built
token would bypass the mint widening and be vacuous for that claim.

Non-vacuity (counter-test-by-revert; measured cardinality in the TEST HANDOFF):
  * The read-arm REFUSE matrix is coupled to the C2 set-equality gate in
    merge_guard_pre.py. A SOURCE-ONLY revert of that gate makes every REFUSE flip
    to AUTHORIZE → these tests go RED. (They also die under a C1 revert, which
    removes the `bound_flags` key entirely — but C2 is the clean source-only
    target since it does not touch the scanner module.) The behavioral tests here
    import only STABLE symbols, so they still COLLECT under either revert.
  * The scanner-form output pins are coupled to the C1 scanner in
    merge_guard_common.py: a form the scanner mishandles changes the asserted
    canonical token → RED at HEAD. The git-vs-gh asymmetry control pins the
    security-load-bearing `is_git_surface` branch (a missed git abbreviation is an
    UNDER-block). A C1 source-only revert removes `extract_privileged_flags`
    (ImportError), so these pins isolate the scanner by exact output, not revert.
"""

import pytest
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)
from merge_guard_repo_fixture import minted_context  # noqa: E402  (amend_3 on #141: mint-side repo binding)
from merge_guard_repo_fixture import git_bound  # noqa: E402  (1.4.1 U13: git checkout/remote binding)


def _refuses(token_context: dict, command: str) -> bool:
    """True iff the read arm REFUSES (does not authorize) `command` for a token
    carrying `token_context`. The command's bound_flags are scanned LIVE by the
    production SSOT; only the approval side is hand-built."""
    from merge_guard_pre import _token_matches_command

    return not _token_matches_command({"context": git_bound(token_context, command)}, command)


def _authorizes(token_context: dict, command: str) -> bool:
    from merge_guard_pre import _token_matches_command

    return _token_matches_command({"context": git_bound(token_context, command)}, command)


# ════════════════════════════════════════════════════════════════════════════
# READ ARM — the bypass matrix: every privileged-flag difference REFUSES
# ════════════════════════════════════════════════════════════════════════════


class TestReadArmBypassRefuses:
    """A privileged flag ADDED at execution past a flagless (or differently-
    flagged) approval REFUSES — the headline #1042 bypasses, across every CLI
    form and op-class."""

    # (id, approved-token-context, executed-command)
    _BYPASS = [
    ("admin_add_bf_empty",
         {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 --admin"),
    ("repo_redirect_equals_joined",
         {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 --repo=victim/repo"),
    ("delete_branch_add_short",
         {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
         "gh pr merge 5 -d"),
    ("lease_value_add_past_plain_push",
         {"operation_type": "push-to-main", "target_ref": "main", "bound_flags": []},
         "git push --force-with-lease=main:abc123 origin main"),
]

    @pytest.mark.parametrize(
        "ctx,command", [(c, cmd) for (_id, c, cmd) in _BYPASS],
        ids=[c[0] for c in _BYPASS],
    )
    def test_added_privileged_flag_refuses(self, ctx, command):
        assert _refuses(ctx, command)

    def test_git_no_verif_abbreviation_refuses(self):
        """SECURITY-LOAD-BEARING: git's parse-options accepts an unambiguous
        long-prefix ABBREVIATION, so `git push --no-verif ...` REALLY disables the
        pre-push hook. Matching only the exact `--no-verify` would scan the
        abbreviation to [] → [] == [] → AUTHORIZE → a silent UNDER-block. The
        git-surface prefix expansion (--no-verif → --no-verify) makes the executed
        set {--no-verify} != {} → REFUSE. This is the one normalization gap whose
        omission under-blocks rather than over-blocks (architect §7.1)."""
        assert _refuses(
            {"operation_type": "force-push", "target_ref": "main", "bound_flags": []},
            "git push --no-verif origin main --force",
        )

class TestReadArmCombinedShortClusterRefuses:
    """The combined-short cluster is the under-block the lead required CLOSED (not
    shipped-and-flagged): a boolean-only cluster parser would DROP a value-taking
    short embedded mid-cluster (the -R in `-dR owner/repo`), so an unapproved
    cross-repo redirect would ride a `-dR` cluster past a `-d`-only approval. The
    general per-character walk captures every bound short regardless of ordering."""

    def test_mixed_cluster_dR_adds_unapproved_repo_refuses(self):
        """Approved `-d` (delete-branch) only; executed `-dR owner/repo` adds an
        unapproved --repo=owner/repo → REFUSE. (The cluster yields
        {--delete-branch, --repo=owner/repo} vs the approved {--delete-branch}.)"""
        assert _refuses(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--delete-branch"]},
            "gh pr merge 5 -dR owner/repo",
        )

    def test_mixed_cluster_Rd_value_consumption_refuses(self):
        """pflag semantics: in `-Rd owner/repo` the value-taking -R consumes the
        cluster remainder `d` as its value (→ --repo=d) and STOPS, so the approved
        {--delete-branch} never matches {--repo=d} → REFUSE. Pins that the cluster
        walk follows real pflag value-consumption, not naive per-char booleans."""
        assert _refuses(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--delete-branch"]},
            "gh pr merge 5 -Rd owner/repo",
        )

    def test_attached_cluster_dRvalue_adds_unapproved_repo_refuses(self):
        """`-dRowner/repo` (boolean -d then value-taking -R with attached value)
        binds {--delete-branch, --repo=owner/repo}; a flagless approval REFUSES —
        no bound short is dropped from the attached-value cluster form."""
        assert _refuses(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5 -dRowner/repo",
        )

class TestReadArmDroppedConstraintRefuses:
    """Set-equality is symmetric: an approval that carried a bound flag, executed
    WITHOUT it, also REFUSES (the dropped-constraint direction a subset rule would
    silently allow). Over-block-safe — the operator re-approves."""

    def test_approved_admin_executed_bare_refuses(self):
        assert _refuses(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5",
        )

    def test_approved_repo_executed_bare_refuses(self):
        assert _refuses(
            {"operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=owner/repo"]},
            "gh pr merge 5",
        )

    def test_approved_lease_executed_plain_refuses(self):
        """Approve a LEASE push to main, execute a PLAIN push → REFUSE — the
        dropped-flag direction of the plain↔lease token separation (#1064)."""
        assert _refuses(
            {"operation_type": "push-to-main", "target_ref": "main",
             "bound_flags": ["--force-with-lease"]},
            "git push origin main",
        )

class TestMatchHeadCommitDroppedConstraint:
    """--match-head-commit <sha> is a value-carrying SAFETY constraint (merge only
    if HEAD is still <sha>) — the canonical DROPPED-CONSTRAINT case set-equality
    catches that a subset rule (executed ⊆ approved) would silently ALLOW. Bound
    value-taking on the merge op-class (R4 denylist add).

    NON-VACUITY (essential): the approval token is SCANNER-DERIVED via
    extract_command_context, NOT hand-built. A hand-built
    {--match-head-commit=ABC123} token would REFUSE the bare execute REGARDLESS of
    whether --match-head-commit is in the denylist — the token/cmd asymmetry would
    drive the refusal, not the binding (vacuous w.r.t. the R4 config add). By
    deriving the token through the live scanner, a SOURCE-ONLY revert of the R4
    denylist add makes the scanner bind [] for --match-head-commit → the approval
    token's bound_flags collapse to {} → {} == {} → the dropped constraint
    AUTHORIZES → these REFUSE tests flip RED (the dropped-constraint, added, and
    different-value cases; the identical-match positive stays GREEN)."""

    _APPROVAL = "gh pr merge 5 --match-head-commit ABC123"

    @staticmethod
    def _scanner_token(approval_command: str) -> dict:
        """Build the approval token by SCANNING the approval command through the
        production SSOT, so bound_flags reflect the LIVE denylist (couples the
        dropped-constraint refusal to the --match-head-commit binding)."""
        return {"context": minted_context(approval_command)}

    def test_dropped_constraint_refuses(self):
        """Approve WITH the safety constraint, execute WITHOUT it → REFUSE — the
        dropped-constraint direction a subset rule would silently allow."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(self._scanner_token(self._APPROVAL), "gh pr merge 5")

    def test_added_constraint_refuses(self):
        """Approve bare, execute WITH --match-head-commit → REFUSE (added direction;
        the executed side is scanned live)."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._scanner_token("gh pr merge 5"), self._APPROVAL
        )

    def test_different_constraint_value_refuses(self):
        """Approve --match-head-commit ABC123, execute --match-head-commit DEF456 →
        REFUSE — a DIFFERENT head-sha is a different binding (the VALUE is bound,
        not merely the flag's presence)."""
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._scanner_token(self._APPROVAL), "gh pr merge 5 --match-head-commit DEF456"
        )

# ════════════════════════════════════════════════════════════════════════════
# READ ARM — bounded over-block: an EXACT (form-invariant) match AUTHORIZES
# ════════════════════════════════════════════════════════════════════════════


class TestReadArmExactMatchAuthorizes:
    """Matching flags AUTHORIZE — the binding does not over-block a faithful
    re-execution, and it is FORM-INVARIANT (an approval in one CLI form authorizes
    an execution in any equivalent form, because both canonicalize identically)."""

    def test_admin_exact_match_authorizes(self):
        assert _authorizes(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5 --admin",
        )

    def test_clean_no_flag_match_authorizes(self):
        assert _authorizes(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5",
        )

    @pytest.mark.parametrize("command", [
        "gh --repo owner/repo pr merge 5",
        "gh pr merge 5 --repo=owner/repo",
        "gh pr merge 5 -R owner/repo",
        "gh pr merge 5 -Rowner/repo",
        "gh pr merge 5 -R=owner/repo",
    ], ids=["global_long", "equals_joined", "short_spaced", "short_attached",
            "short_equals_joined"])
    def test_repo_form_invariant_match_authorizes(self, command):
        """Approve the canonical --repo=owner/repo; every equivalent -R/--repo form
        of the same repo AUTHORIZES (all normalize to one token)."""
        assert _authorizes(
            {"host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--repo=owner/repo"]},
            command,
        )

# ════════════════════════════════════════════════════════════════════════════
# SCANNER — extract_privileged_flags: CLI-form canonicalization output pins
# ════════════════════════════════════════════════════════════════════════════


class TestScannerCanonicalForms:
    """Direct output pins for the scanner's form normalization (the C1 contract).
    Every CLI form of a bound flag canonicalizes to ONE token so the read-side
    set-equality is form-invariant."""

    _FORMS = [
    ("gh pr merge 5 --admin", "merge", ["--admin"]),
    ("gh pr merge 5 -d", "merge", ["--delete-branch"]),
    ("git push origin main", "push-to-main", []),
]

    @pytest.mark.parametrize(
        "command,op,expected", _FORMS,
        ids=[f"{op}:{cmd}" for (cmd, op, expected) in _FORMS],
    )
    def test_canonical_output(self, command, op, expected):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(command, op) == expected

    @pytest.mark.parametrize("op", [
    None,
])
    def test_unflagged_op_classes_bind_empty_set(self, op):
        """None / API / the (today) flagless branch-delete class yield [] — the
        over-block-safe default for op-classes with no denylist entry."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("git push --no-verify origin main", op) == []


class TestGitGhAbbreviationAsymmetry:
    """The is_git_surface branch is SECURITY-LOAD-BEARING (architect §7.1): git
    EXPANDS unambiguous long-prefix abbreviations (a miss under-blocks), gh REJECTS
    them (the command errors out, so a miss cannot bypass). The scanner expands on
    the git surface ONLY."""

    @pytest.mark.parametrize("command", [
    "git push --no-verif origin main --force",
], ids=[
    "no-verif",
])
    def test_git_surface_expands_unambiguous_prefix(self, command):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(command, "force-push") == ["--no-verify"]

    def test_gh_surface_does_not_expand_abbreviation(self):
        """gh rejects abbreviation, so `--admi` is NOT expanded to --admin on the
        merge (gh) surface → [] (no spurious over-block; gh would error the command
        anyway). The contrast with the git case above pins the asymmetry."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("gh pr merge 5 --admi", "merge") == []
        # Control: the SAME logical intent on the git surface DOES expand.
        assert extract_privileged_flags(
            "git push --no-verif origin main", "force-push"
        ) == ["--no-verify"]

class TestLeaseNegationResidualTripwire:
    """Documented-behavior pin for an ACCEPTED residual (#1064 design): a boolean
    bind treats an explicit `=false|0|no` as flag-DISABLED (the safe form, shared
    with --admin=false), so `--force-with-lease=false` binds NOTHING — yet git
    reads `false` as a REFNAME, making the command a live lease push that a
    plain-push token would authorize. Accepted because the negation values are
    implausible branch names in honest use, the executed command is still
    lease-protected (never plain-force), the guard's charter is honest mistakes
    (not adversarial value-crafting), and the same corner exists by design for
    EVERY boolean privileged flag. Do NOT "fix" this by marking the flag
    value-taking — that would consume the next positional on the bare spelling
    and import mint-side adjacency-sensitivity (an over-block risk, the cardinal
    sin); if this pin flips, re-read the residual's disposition first."""

    def test_negated_lease_value_binds_nothing(self):
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags(
            "git push --force-with-lease=false origin main", "push-to-main"
        ) == []


class TestEndOfOptionsMarkerOverBlock:
    """Documented OVER-BLOCK edge: the scanner does NOT treat `--` (end-of-options)
    as a terminator, so a privileged flag positioned AFTER `--` is still bound —
    even though gh/git would treat post-`--` tokens as positionals (and would
    likely not honor --admin as a flag at all). This is over-block-SAFE and
    arm-SYMMETRIC: both arms call the same SSOT, so an identical approve/execute
    pair still MATCHES → AUTHORIZE; the binding only ever over-refuses a DIVERGENT
    pair, never under-blocks. Pinned so a future `--`-handling change cannot
    silently flip it into an UNDER-block (binding [] past `--` would let
    `approve bare / execute -- --admin` AUTHORIZE)."""

    def test_scanner_does_not_terminate_on_double_dash(self):
        """Arm-symmetric by construction: the shared SSOT binds --admin past `--`,
        so BOTH arms classify it identically (no per-arm `--` divergence)."""
        from shared.merge_guard_common import extract_privileged_flags

        assert extract_privileged_flags("gh pr merge 5 -- --admin", "merge") == ["--admin"]

    def test_symmetric_double_dash_pair_authorizes(self):
        """approve `... -- --admin` / execute the SAME → AUTHORIZE: both arms bind
        --admin, so the over-block does not perturb a faithful re-execution."""
        assert _authorizes(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": ["--admin"]},
            "gh pr merge 5 -- --admin",
        )

    def test_divergent_double_dash_pair_refuses_as_over_block(self):
        """approve bare / execute `... -- --admin` → REFUSE: the executed --admin
        (still bound past `--`) was never approved. Over-block-safe — re-approval
        recovers; this is never an under-block."""
        assert _refuses(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5", "bound_flags": []},
            "gh pr merge 5 -- --admin",
        )

# ════════════════════════════════════════════════════════════════════════════
# MULTIPLICITY — bound_flags is a pair ATTRIBUTE, not (op,target) identity
# ════════════════════════════════════════════════════════════════════════════


class TestFlagVariationIsPairAttribute:
    """Flag variation must NOT change the distinct-(op,target) count: bound_flags
    is excluded from pair identity, so a bundle whose surfaces differ only by a
    privileged flag stays ONE pair and cannot spuriously trip the SACROSANCT
    multiplicity (>1 distinct pair) refusal."""

    def test_flag_variation_does_not_inflate_pair_count(self):
        from merge_guard_post import _collect_pairs

        pairs = _collect_pairs(["Approve: gh pr merge 5", "Execute: gh pr merge 5 --admin"])
        assert len(pairs) == 1
        assert ("merge", "5") in pairs

    def test_many_flag_forms_same_pr_stay_one_pair(self):
        from merge_guard_post import _collect_pairs

        pairs = _collect_pairs([
            "gh pr merge 5 --admin",
            "gh pr merge 5 -R owner/repo",
            "gh pr merge 5 -d",
        ])
        assert len(pairs) == 1
        assert ("merge", "5") in pairs

# ════════════════════════════════════════════════════════════════════════════
# REMOTE-DELETE SPLIT BINDING — data shape + the coupling it depends on
# ════════════════════════════════════════════════════════════════════════════


class TestRemoteDeleteSplitBinding:
    """The two remote-delete classes get DIFFERENT bindings, and the asymmetry is
    deliberate:

        remote-ref-delete   --mirror, --prune, --no-verify
        remote-mass-delete  --no-verify only

    WHY THESE ASSERTIONS ARE STRUCTURAL AND NOT BEHAVIORAL. A behavioral row here
    would be decorative by construction. The correct SPLIT binding and a defective
    SYMMETRIC one (all three flags on BOTH classes) produce ZERO observable
    behavioral differences: every mass-class flag escalation ALREADY refuses at
    base, because `_extract_mass_delete_target` embeds the flag in the identity
    netstring. So an authorize-outcome test passes identically under both, and
    someone who binds all three on both classes reports every cert row green,
    truthfully. The check would prove nothing.

    The reason the split still stands is AUDITABILITY, not behavior: a redundant
    binding installs a guard that changes no outcome while reading as protection.
    On a control whose dominant failure mode is checks that prove nothing, every
    entry in the table must be load-bearing. So the shape itself is what gets
    asserted."""

    def test_ref_delete_binds_all_three_modifiers(self):
        """--mirror/--prune are MODIFIERS on this class (the trigger is
        --delete/-d/empty-source colon), so they must be bound. This is the half
        that closes the escalation where approving a single-branch delete
        authorized a wholesale remote wipe."""
        from shared.merge_guard_common import PRIVILEGED_FLAGS

        bound = set(PRIVILEGED_FLAGS["remote-ref-delete"])
        assert bound == {"--mirror", "--prune", "--no-verify"}, (
            "remote-ref-delete's binding changed. --mirror/--prune are LOAD-BEARING "
            "here: without them a `git push origin :main` approval set-equals a "
            "`git push origin --mirror :main` execution, which deletes every remote "
            f"ref with no local counterpart. Found: {sorted(bound)}"
        )

    def test_mass_delete_binds_only_no_verify(self):
        """--mirror/--prune TRIGGER this class and are already folded into the
        mass_target identity, so binding them here would be inert. Asserted as an
        exact set so an 'improvement' that symmetrizes the two entries turns RED
        and has to justify itself against the pin below."""
        from shared.merge_guard_common import PRIVILEGED_FLAGS

        bound = set(PRIVILEGED_FLAGS["remote-mass-delete"])
        assert bound == {"--no-verify"}, (
            "remote-mass-delete's binding changed. --mirror/--prune are the "
            "op-TRIGGER for this class and are already embedded in the mass_target "
            "identity, so binding them here adds an entry that changes no outcome — "
            "a guard that does nothing while reading as protection. If you are "
            "symmetrizing the two remote-delete entries, read the coupling pin "
            f"below first. Found: {sorted(bound)}"
        )
