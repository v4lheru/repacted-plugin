"""
Location: pact-plugin/tests/test_merge_guard_obs_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: GOOD-FAITH over-block sweep certification (PR #1195 OBS). Certifies against the
         REAL classifier, base (committed vendored fixture via merge_guard_baseline_loader
         — loud-fail, CI-executable) vs live HEAD. NEVER a byte-diff / git-show-by-SHA.

         GOVERNING MODEL — GOOD-FAITH: OVER-BLOCK = cardinal, always fix (census rows must
         CLOSE, base-True -> HEAD-False). UNDER-BLOCK = acceptable IFF it requires
         deliberate/adversarial construction (a good-faith user could never accidentally
         type it); a good-faith DESTRUCTIVE command slipping through unguarded is STILL
         unacceptable. Every section names (a) the census over-blocks it closes and (b)
         the DESTRUCTIVE-STILL-GATES floor that MUST stay is_dangerous=True.

         THE LOAD-BEARING GUARD — the destructive-still-gates floor. The read-verb strip
         and the -m carriers only ever remove STRUCTURALLY-INERT filter/message values
         (git/gh match or print them, never shell-execute), and the strip fires ONLY when
         the resolved head is a read verb from the CLOSED set — so a good-faith destructive
         command is never touched and STILL GATES. The floor rows below are the sole
         regression tripwire; ANY of them flipping to not-dangerous is a good-faith
         under-block (STOP-and-report, NOT good-faith-acceptable).

         Destructive verbs assembled at runtime so this file stays inert to the live guard.
"""
import io
import json
from unittest.mock import patch

import pytest

import merge_guard_post as mgpost  # noqa: E402
import merge_guard_pre as mgpre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)

D = mgc.is_dangerous_command


# =========================================================================================
# OBS-C — force-push target recovery past -o/--push-option (commit 4). The prior naive
# split-filter in _extract_force_push_target_ref counted -o's NON-dash value (`ci.skip`)
# as a 3rd positional -> None -> a faithful force-push carrying a push-option was
# gated-but-unmintable (over-block). The fix reuses the SHARED _push_positionals helper
# (the same one the remote-ref-delete/mass-delete builders use), which skips a value
# flag's value token — recovering the REAL refspec positional. MINT-ENABLING ONLY:
# is_dangerous and the op-class are unchanged; the bound target is the real refspec,
# never the -o value (mint==read via the shared extract_command_context).
# =========================================================================================
_FPUSH = "git " + "push " + "--force "
OBS_C_MINT_RECOVERY = [
    ("o-space", _FPUSH + "-o ci.skip origin main"),
    ("o-space-kv", _FPUSH + "-o ci.skip=true origin main"),
    ("push-option-space", _FPUSH + "--push-option x origin main"),
    ("push-option-inline", _FPUSH + "--push-option=x origin main"),
    ("o-multi", _FPUSH + "-o a -o b origin main"),
    ("o-with-colon-refspec", _FPUSH + "-o x origin feature:main"),
]


# =========================================================================================
# OBS-G — multi-ref push-to-main MINTABILITY (the `push_set` distinct key; MINT layer).
# Closes the gated-but-unmintable over-block: post-OBS-E a multi-ref push including main
# (`git push origin main feature`) GATES but the scalar extractor's deliberate `!=2 -> None`
# conservatism yields target=None -> no mint -> the faithful click cannot authorize it. The
# fix transfers the ratified branch_set/mass_target/_canonical_join precedent: one shared
# `_extract_push_to_main_set` (>=2 refspecs -> canonical sort+dedup FULL-refspec netstring
# identity, remote-AGNOSTIC; <2 -> None = the scalar boundary) feeds BOTH the post-hook mint
# (`_target_value` gains push_set) and the pre-hook read (`_token_matches_command`'s
# push-to-main arm binds target_ref OR push_set, set-EQUALITY) -> mint==read by construction.
# SECURITY CERT = the 4+1 property matrix (executable, base-vs-HEAD via the vendored
# fixture, never a byte-diff): (a) injective set-identity over rich refspec tokens;
# (b) SCOPE BOUNDARY — multi-ref force-push/delete stay unmintable
# (`_extract_force_push_target_ref` byte-untouched); (c) scalar<->set exactly-one-populated;
# (d) mint==read round-trip through the REAL hook functions; (e) the REQUIRED 5th
# bound_flags property — a PLAIN push_set token REFUSES a --force-with-lease multi-ref push
# of the same set (the op-agnostic a2 bound_flags equality, checked before the target arms)
# and REFUSES --force multi-ref (op-gate reclassifies force-push). (e) pins behavior the
# code inherits from a2 so a future PRIVILEGED_FLAGS edit cannot silently regress the
# plain<->lease separation.
# =========================================================================================
_PG = "git " + "push "


def PUSH_SET(cmd):
    """Call-time module-attr resolution of the OBS-G-only extractor (revert
    non-vacuity: a source-only revert of OBS-G fails these rows PER-ROW, never as a
    collection-wide ImportError — module-level imports stay limited to stable symbols)."""
    return mgc._extract_push_to_main_set(cmd)


def _gctx(cmd):
    """HEAD command context via the ONE extract_command_context SSOT (both hooks' derivation),
    bound to this checkout and remote the way both hooks bind it (1.4.1 U13)."""
    ctx = mgc.extract_command_context(cmd)
    return mgc.command_repo_binding(ctx, cmd) or ctx


def _gtok(cmd):
    """Mint-shaped token: context derived exactly as the post-hook mint derives it."""
    return {"context": _gctx(cmd)}


def _greads(tok, cmd):
    """The REAL pre-hook read predicate (op-gate -> a2 bound_flags -> per-op target arm)."""
    return mgpre._token_matches_command(tok, cmd)


# =========================================================================================
# OBS-G — REAL mint -> execute round-trip (the cert BACKBONE, the sibling of the #1129
# harness). Drives the ACTUAL post_main (AskUserQuestion approval whose clicked option
# embeds the command in backticks -> token mint) then the ACTUAL pre_main (Bash exec ->
# allow/deny) against the same token dir. This layer is MANDATORY for a mint-layer fix:
# the recorded #1129 miss (branch_set registered at extract+read but MISSING from
# _target_value) was invisible to every test that hand-constructs token context — a
# faithful click minted ZERO tokens while all read-side units stayed green. minted==1 is
# asserted FIRST on every refusal row so a DENY is proven a READ decision, never a
# mint-side miss masquerading as one. The three sites are independently load-bearing
# through these rows: EXTRACT (exactly-one-populated above), MINT (minted==1 — a
# _target_value miss goes red here), READ (exec-same ALLOW — a read-arm miss goes red).
# =========================================================================================
_G_ALLOW, _G_DENY = 0, 2


def _g_live_token_ids(tok):
    """Inode identity of the LIVE (non-retired) token files in `tok`.

    Keyed on INODE and not on filename, because here the two are not
    interchangeable. `cleanup_unused_tokens` retires the previous token with
    `os.rename`, which PRESERVES its inode, while a mint is an `O_EXCL` create
    that allocates a NEW one. So a name-keyed diff counts a retirement as a
    mint, AND is blind to a mint that reuses the name the retirement just
    vacated — which second-granularity token names make the ordinary case
    rather than a rare one. Those two errors cancel often enough to look
    correct: a same-second pair reads 1 by counting the retirement while the
    real token is invisible. Neither error is reachable by narrowing the glob,
    because the defect is the identity, not the filter.

    Non-tokens are dropped by EXCLUSION, never by matching an expected token
    shape. An inclusion pattern such as `merge-authorized-<digits>` silently
    drops the millisecond-disambiguated fallback name that `write_token` falls
    back to on collision — a real token — and would go on dropping every later
    shape nobody thought to add. Exclusion can only ever over-count, which
    fails loudly; inclusion under-counts, which does not.
    """
    ids = set()
    for path in tok.glob(mgc.TOKEN_PREFIX + "*"):
        if path.name.endswith(".consumed") or mgc.USE_MARKER_SUFFIX in path.name:
            continue
        ids.add(path.stat().st_ino)
    return ids


def _g_mint(cmd, tok):
    """Drive the REAL post hook with an approval embedding `cmd`; return the count of
    tokens minted by this call. See `_g_live_token_ids` for why the count is
    keyed on inode rather than on filename."""
    before = _g_live_token_ids(tok)
    env = json.dumps({
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Proceed?",
            "options": [
                {"label": "Yes", "description": "Run `%s`" % cmd},
                {"label": "Cancel", "description": "Abort"},
            ],
        }]},
        "tool_response": {"answers": {"Proceed?": "Yes"}},
        "session_id": "obs-g-cert",
    })
    with patch.object(mgpost, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit as e:
            assert e.code == 0, "post hook exited nonzero: %r" % (e.code,)
    return len(_g_live_token_ids(tok) - before)


def _g_execute(cmd, tok):
    """Run `cmd` through the REAL pre hook; return exit code (0=ALLOW, 2=DENY).

    AMBIGUOUS ON PURPOSE, AND THE CALLER RESOLVES IT. A return of 0 means only
    that the hook did not DENY. It cannot distinguish "a token authorized this
    command" from "this command was never gated at all", because an ungated
    command reaches no decision and so exits 0 too.

    The discriminator is the calling convention, not this function: every row
    asserts `minted == 1` BEFORE `rc == _G_ALLOW`, and since only a gated
    command mints, that mint assertion IS the proof that the command was gated.
    Read `rc` alone and a never-gated command reads exactly like an authorized
    one. Keep the mint assertion first.
    """
    env = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": "obs-g-cert",
    })
    with patch.object(mgpre, "TOKEN_DIR", tok), \
            patch("sys.stdin", io.StringIO(env)), \
            patch("sys.stdout", io.StringIO()), \
            patch("sys.stderr", io.StringIO()):
        try:
            pre_main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 0


def _g_roundtrip(mint_cmd, exec_cmd, tok):
    return _g_mint(mint_cmd, tok), _g_execute(exec_cmd, tok)


def _g_write_token_that_retires_then_fails_to_create(fired):
    """Build a `write_token` stand-in that retires, then creates nothing.

    `write_token` runs its retirement BEFORE the `O_EXCL` create, so a create
    that fails leaves a retirement on disk and no new token. This reproduces
    that SHAPE. It does not reproduce any particular CAUSE of the failure, and
    it stops being faithful if that order changes —
    `TestObsGStubTracksTheRealFailurePath` is what notices if it does.

    The name states the POST-STATE rather than the outcome. "Creates nothing"
    is true of all four ways `write_token` returns None; leaving a retirement
    behind belongs to exactly one of them, and naming the shared outcome
    claimed three modes this stand-in does not model.

    Defined once and shared by the pin and by its guard on purpose: a guard
    comparing against its own private copy of the stub would certify the copy,
    not the stub, which is the defect this arrangement exists to close.
    """
    def _stub(context, token_dir=None):
        mgc.cleanup_unused_tokens(token_dir or mgpost.TOKEN_DIR, context,
                                  mgpost.get_session_id())
        fired.append(True)
        return None
    return _stub


def _g_token_shape(name):
    """`merge-authorized-1785902062.consumed` -> `.consumed`.

    Strips the prefix and the epoch stamp so two directories seeded moments
    apart compare on WHICH ARTIFACTS EXIST rather than on when they were made.
    A live token yields `''`, a retirement `'.consumed'`, a use marker `'.use-1'`.
    """
    return name[len(mgc.TOKEN_PREFIX):].lstrip("0123456789")


class TestObsGRealMintExecuteRoundTrip:
    def test_faithful_multi_ref_click_mints_and_self_authorizes(self, tmp_path):
        # THE over-block cure, end-to-end: the faithful multi-ref click MINTS (base
        # minted zero — gated-but-unmintable) and its own execution is ALLOWED.
        cmd = _PG + "origin main feature"
        minted, rc = _g_roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "faithful multi-ref click did not mint (the over-block persists)"
        assert rc == _G_ALLOW

    def test_reorder_and_dup_authorize(self, tmp_path):
        # The same refs reordered or named twice are the same target set and another text:
        # the approval covers the git command word for word.
        minted, rc = _g_roundtrip(_PG + "origin main feature", _PG + "origin feature main", tmp_path)
        assert minted == 1 and rc == _G_DENY
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "origin main feature feature", tmp_path)
        assert minted == 1 and rc == _G_DENY

    @pytest.mark.parametrize(
        "label,exec_tail",
        [
            ("superset", "origin main feature staging"),
            ("subset-scalar", "origin main"),
            ("different-set", "origin main develop"),
        ],
    )
    def test_set_equality_denies_other_sets(self, label, exec_tail, tmp_path):
        minted, rc = _g_roundtrip(_PG + "origin main feature", _PG + exec_tail, tmp_path)
        assert minted == 1, "the {main,feature} approval must MINT (refuse must be a read decision)"
        assert rc == _G_DENY, "a {main,feature} token wrongly authorized the %s" % label

    def test_scalar_token_denies_set_execution(self, tmp_path):
        minted, rc = _g_roundtrip(_PG + "origin main", _PG + "origin main feature", tmp_path)
        assert minted == 1, "the scalar approval must still MINT (pre-existing path)"
        assert rc == _G_DENY, "a scalar main token wrongly authorized a multi-ref push"

    def test_plain_set_token_denies_lease_and_force_execution(self, tmp_path):
        # the 5th property END-TO-END: a PLAIN multi-ref token refuses the
        # history-rewriting spellings of the same set (a2 bound_flags / op-gate).
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "--for" + "ce-with-lease origin main feature", tmp_path)
        assert minted == 1
        assert rc == _G_DENY, "plain push_set token authorized a lease multi-ref push"
        minted, rc = _g_roundtrip(_PG + "origin main feature",
                                  _PG + "--for" + "ce origin main feature", tmp_path)
        assert minted == 1
        assert rc == _G_DENY, "plain push_set token authorized a --force multi-ref push"

    def test_multi_ref_force_push_approval_now_mints_end_to_end(self, tmp_path):
        # CONTROL-SWAP (OBS-I): the multi-ref FORCE approval now MINTS (the OBS-G
        # boundary reversed under its own proof) and the faithful click
        # self-authorizes end-to-end.
        cmd = _PG + "--for" + "ce origin main feature"
        minted, rc = _g_roundtrip(cmd, cmd, tmp_path)
        assert minted == 1, "the faithful multi-ref force-push click did not mint"
        assert rc == _G_ALLOW

# =========================================================================================
# OBS-H — cd-prefix per-leg EXTRACTION mintability (the `_extraction_surface` fix; MINT
# surface, the arc's last cure). Closes the last over-block: a QUOTED single-destructive
# cd-prefix compound approval (`Approve: \`cd . && git push origin main\``) detected
# push-to-main (whole-candidate, per-leg detection) but extracted first-leg-anchored (the
# cd leg -> no target) -> no pair -> gated-but-unmintable. The fix: extraction at the TWO
# mint call sites consumes `_extraction_surface(region)` — the read side's VERBATIM
# three-tier expression (tier-1 unique dangerous leg; tier-2 unique detectable leg; else
# the whole region = the fail-safe unmintable collapse). SECURITY = the REDUCTION THEOREM
# (ratified #89/#91): mint(quoted cd-prefix X) == mint(bare leg-of-X on already-certified
# bytes) by dict-equality on the FULL (op, target, bound_flags); the read side is
# byte-untouched, so every post-fix outcome maps to an already-certified pre-fix outcome.
# REQUIRED addition (the #91 RATIFY-WITH-CHANGES condition): NON-PUSH-class cd-prefix
# quoted mint PARITY pins — the surface rewires extraction for ALL op-classes, so the
# merge/close/branch-delete/ref-delete parity is pinned against a future helper/tier-fn
# edit silently regressing a non-push class (mirrors the OBS-G 5th-property pin).
# =========================================================================================
def _h_mint(desc):
    """Context-level mint through the REAL `_mint_context_from_bundle` (the mint entry
    the post hook runs after option selection): returns (context, refusal_reason)."""
    q = {"question": "Proceed?",
         "options": [{"label": "Yes", "description": desc}],
         "multiSelect": False}
    return mgpost._mint_context_from_bundle([q], {"Proceed?": "Yes"})


def _h_reads(ctx, cmd):
    return mgpre._token_matches_command({"context": ctx}, cmd)


_H_CMD = "cd . && " + _PG + "origin main"
_H_LEG = _PG + "origin main"
_H_CMDS = "cd . && " + _PG + "origin main feature"
_H_LEGS = _PG + "origin main feature"
_H_CMDF = "cd . && " + _PG + "--for" + "ce origin main"
_H_LEGF = _PG + "--for" + "ce origin main"
_H_CMDL = "cd . && " + _PG + "--for" + "ce-with-lease origin main"
_H_LEGL = _PG + "--for" + "ce-with-lease origin main"
_H_MERGE = "gh " + "pr " + "merge"
_H_CLOSE = "gh " + "pr " + "close"


# =========================================================================================
# OBS-I — multi-ref force-push MINTABILITY (`force_push_set`, reversing the OBS-G scope
# boundary under its own ratified proof). Cures the peer-review-found cardinal over-block
# (`git push origin +feature main` gated-but-unmintable). THREE security layers, each
# load-bearing: (1) FORCED-FORM NORMALIZATION — class-wide --force upgrades every element
# to its `+` form so the identity tracks WHICH REFS GET FORCED (the plain->forced
# mixed-set collision cure; --force is NOT a bound flag, so the identity must carry the
# semantics); (2) the ALLOWLIST-INVERSION guard — force_push_set populates ONLY when EVERY
# flag token is ref-scope-NEUTRAL (a closed, SECURITY-OWNED membership set pinned below);
# delete/mass op-triggers, scope expanders, dry-run, lease-on-force, unknown/future flags
# all fail SAFE to None. The census walks the RAW tail (never `_normalized_flags`, which
# is condition-scoped and BLIND to --tags/--all — a census built on it would false-green
# the smuggle) with getopt-style value-`-o` resolution in ANY cluster position, feeding
# the SHARED byte-untouched `_push_positionals` a value-free tail so the census and the
# positional gather agree by construction; (3) the ':'-element delete guard
# (defense-in-depth). mint==read via the ONE shared extractor => the READ direction closes
# by construction: a non-neutral EXECUTION derives force_push_set=None and no pure-force
# token matches (None-identity denial).
# =========================================================================================
def FORCE_SET(cmd):
    """Call-time module-attr resolution (revert non-vacuity: a source-only revert of
    OBS-I fails these rows PER-ROW, never as a collection-wide ImportError)."""
    return mgc._extract_force_push_set(cmd)


_I_PURE = _PG + "--for" + "ce origin main feature"


def _i_pure_id():
    return mgc._canonical_join(["+feature", "+main"])


class TestObsIRealMintExecuteRetireRoundTrip:
    def test_pure_force_approval_mints_and_self_authorizes(self, tmp_path):
        minted, rc = _g_roundtrip(_I_PURE, _I_PURE, tmp_path)
        assert minted == 1, "the faithful multi-ref force click did not mint"
        assert rc == _G_ALLOW

    def test_reorder_and_plus_spelling_authorize(self, tmp_path):
        # Same force-push set, another text: refused since the approval covers the command
        # word for word (the reordered form and the `+ref` spelling alike).
        minted, rc = _g_roundtrip(_I_PURE, _PG + "--for" + "ce origin feature main", tmp_path)
        assert minted == 1 and rc == _G_DENY
        minted, rc = _g_roundtrip(_I_PURE, _PG + "origin +main +feature", tmp_path)
        assert minted == 1 and rc == _G_DENY

    @pytest.mark.parametrize(
        "label,exec_tail",
        [
    ("different-set", "--for" + "ce origin main develop"),
    ("subset-scalar", "--for" + "ce origin main"),
    ("tags-spelling", "--for" + "ce --tags origin main feature"),
],
    )
    def test_deny_rows_minted_first(self, label, exec_tail, tmp_path):
        minted, rc = _g_roundtrip(_I_PURE, _PG + exec_tail, tmp_path)
        assert minted == 1, "the pure approval must MINT (refusal must be a read decision)"
        assert rc == _G_DENY, "the pure force token wrongly authorized the %s" % label




class TestObsIForcePushSetMintability:

    @pytest.mark.parametrize(
        "label,exec_flags",
        [
            ("delete-exec", "--for" + "ce --delete"),
            ("tags-exec", "--for" + "ce --tags"),
            ("mirror-exec", "--for" + "ce --mirror"),
            ("dry-run-exec", "--for" + "ce -n"),
        ],
    )
    def test_pure_token_denies_non_neutral_executions(self, label, exec_flags):
        # the mint guard and the read refusal are the SAME code: the non-neutral
        # execution derives force_push_set=None -> None-identity denial.
        pure_tok = {"context": _gctx(_I_PURE)}
        exec_cmd = _PG + exec_flags + " origin main feature"
        assert _greads(pure_tok, exec_cmd) is False, "%s wrongly authorized" % label

    def test_plain_forced_collision_closed_both_directions(self):
        plain_mixed = _gctx(_PG + "origin +feature main")
        all_forced = _gctx(_PG + "--for" + "ce origin +feature main")
        assert plain_mixed["force_push_set"] != all_forced["force_push_set"]
        assert _greads({"context": plain_mixed}, _PG + "--for" + "ce origin +feature main") is False
        assert _greads({"context": all_forced}, _PG + "origin +feature main") is False



class TestObsHExtractionSurfaceMintability:

    def test_gap5_refuses_chimeric_before_pair_collection(self):
        # refusal_reason MUST be compound_command (GAP5), not multiple_commands or
        # no_command — proving the compound refuse still fires BEFORE pair collection
        # with the extraction-surface rewire in place.
        _, refusal = _h_mint("Run `" + _H_MERGE + " 5 && " + _PG + "--for" + "ce origin main` now")
        assert refusal == "compound_command"

    def test_deny_negatives_on_the_set_compound_token(self):
        ctx, _ = _h_mint("Run `%s` now" % _H_CMDS)
        assert ctx is not None
        assert _h_reads(ctx, "cd . && " + _PG + "origin main feature staging") is False
        assert _h_reads(ctx, _H_CMD) is False
