"""
Location: pact-plugin/tests/test_merge_guard_1178_f2_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: Durable BIDIRECTIONAL companion cert for the F2 over-block closure (commit 6730f908),
         the peer-review-phase remediation stacked on the #1178 fix. F2 = the pre-existing
         COARSE-wrapper WRAPPED-INERT over-block: at base 89061755, busybox and stdbuf were
         _EXEC_WRAPPERS_COARSE, so a faithful inert click behind them (`busybox mycmd "…git
         branch -D…"`, `stdbuf -oL mycmd "…"`) preserved the WHOLE wrapped command -> over-block
         (cardinal sin). The fix: busybox+stdbuf COARSE->RECURSE (find/flock STAY COARSE per the
         #1098 unbounded-grammar wall); hush/lash/msh added to _SHELL_STRING_EXECUTORS; busybox
         empty-grammar + stdbuf value-flag _WrapperGrammar; a GENERAL attached-short-value walker
         branch (`-oL` = short value-flag `-o` + glued value `L`, never bundled bools).

         Certifies against the REAL is_dangerous_command, base(89061755)-vs-HEAD(6730f908). Baked
         baseline from a vendored fixture (crash-atomic; the code is committed, so code-then-tests ordering
         is satisfied). The 184-row test_merge_guard_1178_cert.py already covers c21eae19->#1178;
         this is its F2 companion. Destructive verbs assembled at runtime (BD/PF) — file stays
         inert to the live guard.

         MONOTONICITY IS SCOPED (the honest-cert crux). backend-r2's fix has two parts with
         DIFFERENT monotonicity behavior:
           (1) busybox/stdbuf COARSE->RECURSE + the attached-short-value walker are STRICTLY
               monotonic-toward-False (they only close over-blocks) -> TestF2MonotonicityFaithfulInert
               asserts NO base-False->HEAD-True for these rows.
           (2) Adding hush/lash/msh to _SHELL_STRING_EXECUTORS CORRECTLY produces base-False->HEAD-True
               for head-basename in {hush,lash,msh} -> pinned EXPLICITLY as intended in
               TestF2StandaloneShellUnderBlockFix (an under-block FIX, not a new over-block).
         A blanket "0 base-False->HEAD-True" sweep would FALSELY red on (2). The property that
         matters — no FAITHFUL INERT click newly blocked — is carried by the CLOSURE rows
         (busybox/stdbuf/walker mycmd forms all base-True->HEAD-False = freed, never newly blocked).

         NON-VACUITY: every closure row asserts D_BASE(89061755) is True (genuinely over-blocked
         at base) AND D is False; every retention asserts D_BASE True AND D True; the standalone
         rows assert D_BASE False AND D True (the under-block that the fix closes). So the cert
         FAILS wholesale against base 89061755 (>=1 row proof, mandated). Plus a mutant-of-live-
         source proof that busybox RECURSE membership is load-bearing for the retention.
"""


import shared.merge_guard_common as mgc  # noqa: E402

D = mgc.is_dangerous_command          # PATCH = live worktree HEAD (6730f908, F2 fix)


BD = "git " + "branch " + "-D victim"           # danger inside the quoted arg


# =========================================================================================
# MUTANT-OF-LIVE-BINDING — busybox RECURSE membership is a TESTED property. Remove `busybox`
# from _EXEC_WRAPPERS_RECURSE on the live binding (a setattr on the façade reaches the package
# module that reads the global at call time) and assert the `busybox bash -c "danger"`
# retention FLIPS True->False: without the recursion the nested executor's arg is stripped ->
# under-block. Proves the retention pin is load-bearing, not vacuous.
# =========================================================================================
class TestF2MutantOfLiveSource:
    def test_busybox_recurse_membership_is_load_bearing(self, monkeypatch):
        assert "busybox" in mgc._EXEC_WRAPPERS_RECURSE, "precondition: F2 added busybox to RECURSE"
        # `watch`, not `bash -c`: a shell -c string is now read after ANY program, so the
        # catalogue no longer decides that form; it still decides a string another program runs.
        assert D('busybox watch "%s"' % BD) is True, "precondition: live catches busybox watch"
        monkeypatch.setattr(mgc, "_EXEC_WRAPPERS_RECURSE", mgc._EXEC_WRAPPERS_RECURSE - {"busybox"})
        assert D('busybox bash -c "%s"' % BD) is True, "a shell -c string needs no catalogue entry"
        assert D('busybox watch "%s"' % BD) is False, \
            "busybox RECURSE membership is NOT load-bearing (retention pin would be vacuous)"
