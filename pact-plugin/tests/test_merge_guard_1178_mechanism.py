"""
Location: pact-plugin/tests/test_merge_guard_1178_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: COMPREHENSIVE BIDIRECTIONAL certification for #1178 — the positional-argument
         over-block class. The fix ships as two commits on top of c21eae19 (v4.6.5):
           P1 be33ab81 — git --trailer + gh --assignee carrier extensions.
           P2 c732a39f — strip-inert-default general strip (arbitrary-command quoted
                         positionals) + the exec-prefix RECURSION catalog + the
                         head-displacement (leading env-assignment / redirect) skip.
         Certifies against the REAL is_dangerous_command, base(c21eae19)-vs-PATCH(HEAD),
         NEVER a byte-diff / additive-lines argument (the #1118 trap: a shared-strip
         pipeline change opened BOTH an over-block and an under-block, invisible to a
         byte-diff — only behavioral data-flow review against the real classifier caught
         it). Baked baseline is loaded from git (crash-atomic, no working-tree mutation).

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: blocking a faithful inert click (mycmd/logger/
             notify-send "...danger...") is wrong by definition. #1178 CLOSES this class.
           - UNDER-block = security hole the fix MUST NOT open. A real executor/wrapper/
             http-client whose danger is a preserved arg MUST stay caught.

         NON-VACUITY (the crux, base-vs-PATCH, in-test + permanent): every CLOSURE row
         asserts D_BASE(cmd) is True (the form was a genuine over-block at c21eae19) AND
         D(cmd) is False (the faithful click is freed). A row that is False at BOTH
         baselines is vacuous. Every RETENTION row asserts D_BASE is True AND D is True
         with the danger literal placed INSIDE the preserved quoted arg, so a wrong strip
         flips it False. The danger-inside-the-quoted-arg construction is what makes a
         "stays caught" pin fail precisely when the fix wrongly strips a preserved arg.

         THE HEAD-DISPLACEMENT SUB-CLASS (P0, co-equal with currently-caught-stays-caught):
         a leading env-assignment or redirect prefix that displaces the head token
         (`FOO=bar bash -c "danger"`, `2>&1 bash -c "danger"`, `LC_ALL=C curl -X DELETE
         "url"`). These are base-True -> PATCH-True RETENTION pins — NOT controls. They are
         LOAD-BEARING because a wrongly-freed executor is base-True -> PATCH-False, the SAME
         transition as a legitimate closure, so the monotonicity sweep (which only sees
         base-False -> PATCH-True) is BLIND to them. The retention pins are the only thing
         that catches an executor masquerading-as-a-closure under-block. (An auditor caught
         this exact under-block in the pre-amendment P2 88bec86e; c732a39f closes it.)

         Destructive verbs are assembled at runtime (BD/BDR/PF/M5/DEL) so this file carries
         no raw force-delete / force-push / merge / DELETE-method literal and stays inert to
         the live guard. Mirrors test_merge_guard_1140_carrier5_cert.py.
"""


import shared.merge_guard_common as mgc  # noqa: E402

D = mgc.is_dangerous_command          # PATCH = live worktree HEAD (c732a39f)
STRIP = mgc._strip_non_executable_content


# --- Destructive verbs assembled at runtime so this file carries no raw literal and stays
#     inert to the live guard (mirrors the 1140 cert). Each is is_dangerous=True on its own.
BD = "git " + "branch " + "-D victim"           # force branch-delete (danger inside quoted arg)
BDR = "git " + "branch " + "-D real"            # force branch-delete (executing-tail canary)


# =========================================================================================
# CLASS C — BINDING INVARIANT (P0). Dangerous legs are NEVER stripped, so the target-bearing
# quoted value survives for mint/read binding (#1042). Caught on both AND target preserved.
# =========================================================================================
class TestBindingInvariant:

    def test_target_survives_strip(self):
        # The dangerous leg is NOT stripped -> the branch target 'victim' survives verbatim
        # (a naive value-strip would rewrite it to STRIPPED and break the #1042 set-equality).
        out = STRIP('git branch -D "victim"')
        assert "victim" in out, "binding target stripped (over-binding under-block): %r" % (out,)


# =========================================================================================
# STRIP-SURFACE — document the transform directly (complements the is_dangerous differential):
# a closure's inert value is stripped; a retention leg's danger arg is preserved; the binding
# target survives; leg-locality holds under an unquoted separator.
# =========================================================================================
class TestStripSurface:
    def test_inert_value_stripped(self):
        assert STRIP('mycmd "%s"' % BD) == "mycmd STRIPPED"

    def test_executor_arg_preserved(self):
        # the danger arg of a real executor survives the strip (else it would un-gate).
        assert BD in STRIP('bash -c "%s"' % BD)

    def test_headdisplaced_executor_arg_preserved(self):
        assert BD in STRIP('FOO=bar bash -c "%s"' % BD)

    def test_binding_target_preserved(self):
        assert "victim" in STRIP('git branch -D "victim"')

    def test_leg_locality_tail_preserved(self):
        # the executing tail after an unquoted && stays OUTSIDE the stripped span.
        assert BDR in STRIP('mycmd "safe" && %s' % BDR)


# =========================================================================================
# MUTANT-OF-LIVE-BINDING — catalog / skip membership is a TESTED property, not just an
# assertion. Remove a catalog member from the live binding (a setattr on the façade reaches
# the package module that reads the global at call time) and assert the matching retention
# pin FLIPS to under-block. Proves the mechanism is load-bearing (mirrors the 1140 control).
# =========================================================================================
class TestMutantOfLiveSource:
    def test_wrapper_membership_is_load_bearing(self, monkeypatch):
        # Remove `taskset` from the recursion catalog -> `taskset ... watch "danger"` can no
        # longer recurse to the nested watch -> the strip eats the arg -> under-block. (`watch`,
        # not `bash -c`: a shell -c string is now read after ANY program.)
        base_caught = D('taskset -c 0-3 watch "%s"' % BD)
        assert base_caught is True, "precondition: live classifier catches taskset+watch"
        monkeypatch.setattr(mgc, "_EXEC_WRAPPERS_RECURSE", mgc._EXEC_WRAPPERS_RECURSE - {"taskset"})
        assert D('taskset -c 0-3 bash -c "%s"' % BD) is True, "a shell -c string needs no catalogue entry"
        assert D('taskset -c 0-3 watch "%s"' % BD) is False, \
            "taskset catalog membership is NOT load-bearing (pin would be vacuous)"

    def test_wrapper_recurse_catalog_nonempty(self):
        # Guard the mutant's premise: the catalog is a mutable frozenset containing taskset.
        assert "taskset" in mgc._EXEC_WRAPPERS_RECURSE
