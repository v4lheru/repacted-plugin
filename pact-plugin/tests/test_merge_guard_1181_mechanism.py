"""
Location: pact-plugin/tests/test_merge_guard_1181_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: BIDIRECTIONAL certification for the read-verb value over-block class — the
         `_strip_read_verb_values` carve-out (pre-d2 in `_maybe_strip_leg`). Certifies
         against the REAL `is_dangerous_command`, BASE (committed vendored fixture via
         merge_guard_baseline_loader — loud-fail, CI-executable, never skip) vs HEAD
         (live worktree module). NEVER a byte-diff / additive-lines argument.

         THREAT POLARITY (SACROSANCT merge_guard control):
           - OVER-block = cardinal sin: a faithful read-verb search click
             (`git log --grep "…"`, `git grep '…'`, `gh pr list --search '…'`) whose
             quoted VALUE merely names a destructive op must never gate. CLOSURE rows
             certify base-True -> HEAD-False.
           - UNDER-block = the hole the fix must not open. Executing values
             (git grep -O pager), expanded/eval'd values (g2), command/process
             substitution (g3/g4), destructive sibling legs, and unquoted in-leg danger
             MUST stay caught: RETENTION rows certify base-True -> HEAD-True with the
             danger placed so a wrong strip flips them False.

         NON-VACUITY: every closure row asserts BASE(cmd) is True (the form was a
         genuine over-block at the baseline) AND HEAD(cmd) is False. Every retention
         row asserts True on BOTH. `=form` rows are FALSE on both (carrier-4-closed
         upstream — controls proving this carve-out does NOT re-handle them).

         KNOWN RESIDUAL (documented, pre-existing — NOT a closure failure of this fix):
         `git log/show --committer '<value containing " merge ">'` stays True on HEAD.
         The git-merge span carrier's prefix word class crosses quote boundaries, so a
         `merge` INSIDE the quoted value heads a span over the leg, and that carrier's
         short `-m` cluster arm (no token-start lookbehind) rewrites `--com|mitter` ->
         `--comSTRIPPED` BEFORE the leg walk runs — the carve-out's flag arm can then no
         longer anchor. Verified byte-identical corruption on the BASE classifier
         (pre-existing interaction, not introduced here). Pinned below as a residual so
         a future fix of that span prefix flips the pin deliberately.

         Destructive verbs are assembled at runtime so this file carries no raw
         force-delete / force-push / merge literal and stays inert to the live guard.
"""


import shared.merge_guard_common as mgc  # noqa: E402


# =========================================================================================
# STRUCTURAL NEGATIVE CONTROLS — booleans are excluded BY OMISSION; listing one would
# consume the next real token (the curl/wget-shaped fail-open). Assert the known
# booleans appear in NO flag_arm and the verb set stays CLOSED.
# =========================================================================================
class TestStructuralNegatives:
    _KNOWN_BOOLEANS = ["--invert-grep", "--all-match", "--web", "--draft", "--email"]

    def test_no_boolean_in_any_flag_arm(self):
        for key, spec in mgc._READ_VERB_SPECS.items():
            if spec.flag_arm is None:
                continue
            for boolean in self._KNOWN_BOOLEANS:
                assert boolean not in spec.flag_arm.pattern, (
                    "arity-0 flag %s found in flag_arm for %r — it would consume the "
                    "next real token" % (boolean, key)
                )

    def test_verb_set_is_closed(self):
        assert ("git", "diff") not in mgc._READ_VERB_SPECS
        assert ("git", "push") not in mgc._READ_VERB_SPECS
        assert ("git", "branch") not in mgc._READ_VERB_SPECS

    def test_shortlog_has_no_short_flags(self):
        # the -e (grep: value) vs --email (shortlog: boolean) collision — per-verb keying.
        arm = mgc._READ_VERB_SPECS[("git", "shortlog")].flag_arm.pattern
        assert "-[SG]" not in arm and "-[ef]" not in arm and "-e" not in arm

    def test_flag_arms_are_single_capture_group(self):
        # the _strip_flag_values flag_sep contract: exactly ONE capturing group.
        for key, spec in mgc._READ_VERB_SPECS.items():
            if spec.flag_arm is not None:
                assert spec.flag_arm.groups == 1, key
