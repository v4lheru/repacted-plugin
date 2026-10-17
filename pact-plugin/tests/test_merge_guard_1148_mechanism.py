"""
Location: pact-plugin/tests/test_merge_guard_1148_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: BIDIRECTIONAL certification for the quote-aware comment excision (step 3 of
         `_excise_and_mask`): a `#`-comment carrying `| sh` no longer flips
         `piped_to_shell` on the routing view (which disabled every content carrier
         and gated a faithful commit whose -m value merely NAMED a destructive op).
         Certifies against the REAL classifier, BASE (committed vendored fixture via
         merge_guard_baseline_loader — loud-fail, CI-executable) vs HEAD.

         PREDICATE UNDER CERT: a `#` is a comment iff it SURVIVES the quote mask
         (unquoted by construction) AND its PRE-MASK predecessor (excised[i-1];
         i == 0 counts) is in {start, space, tab, newline, ;, &, |}. Excision is
         VIEW-ONLY and SAME-LENGTH (spaces; newline kept), preserving both view
         consumers' 1:1 offset alignment. FAIL-TOWARD-NOT-COMMENT.

         THE TWO DOCUMENTED UNDER-BLOCK TRAPS (each has a load-bearing canary):
           trap 1 (mask-induced false comment): a view-side predecessor test reads
             a masked closing quote as a space — `echo "x"# tail | sh` would be
             excised and the REAL pipe eaten. The RAW-predecessor test refuses:
             excised[i-1] == '"' -> not a comment.
           trap 2 (pre-mask excision eats executing tails): excising on the raw
             string would eat `' | sh'` THROUGH a quoted `#` (`git commit -m
             'a # b' | sh` — the # is data; the pipe is real). Mask-BEFORE-excise
             refuses: a quoted `#` is already spaces on the view.

         Ships LAST in the three-commit arc, so the composed end-state sweep here
         runs the #1181 + #1155 closure/retention spot sets against the FINAL tree.

         Destructive verbs are assembled at runtime so this file stays inert to the
         live guard.
"""

import pytest

import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_baseline_loader import load_baseline  # noqa: E402

D = mgc.is_dangerous_command

M5 = "gh " + "pr " + "merge 5 --admin"
PF = "git " + "push " + "--force origin main"
REPRO = 'git commit -m "note: %s later" # reviewed, do not pipe | sh' % M5


def _base():
    return load_baseline().is_dangerous_command


class TestClosure:

    def test_repro_mechanism_view_level(self):
        # the excised-surface predecessor test at the mechanism level: the comment
        # (incl. its `| sh`) is spaces on the view, so the routing flag is False.
        view = mgc._executed_surface_view(REPRO)
        assert mgc._has_pipe_to_shell(view) is False
        assert "#" not in view and "| sh" not in view
        base = load_baseline()
        assert base._has_pipe_to_shell(base._executed_surface_view(REPRO)) is True, (
            "baseline discriminator: the comment survived the view at base"
        )


class TestTrapCanaries:

    def test_word_char_predecessor_untouched(self):
        # issue#42 / url#fragment: word-char predecessor -> not a comment -> the view
        # keeps the text (fail-toward-not-comment).
        for text in ["issue#42", "url#fragment"]:
            assert mgc._executed_surface_view(text) == text

    def test_real_pipe_view_flag_survives_quoted_hash(self):
        # trap-2 mechanism pin at the view level (no danger literal needed): the
        # quoted # is data; the pipe must still flip the routing flag.
        view = mgc._executed_surface_view("git commit -m 'a # b' | sh")
        assert mgc._has_pipe_to_shell(view) is True


# =========================================================================================
# SECOND-CONSUMER SAFETY — _procsub_anchor_view relies on 1:1 view/excised offset
# alignment; step 3 is same-length so nothing shifts, and comment-resident procsub
# markers are excised (not "surviving").
# =========================================================================================
class TestProcsubAnchorAlignment:
    def test_same_length_contract(self):
        cmd = 'git commit -m "x" # don' + "'t >(bash)"
        excised, view = mgc._excise_and_mask(cmd)
        assert len(excised) == len(view)

    def test_comment_resident_procsub_not_flagged(self):
        cmd = 'git commit -m "x" # > >(bash)'
        assert mgc._has_process_substitution_to_shell(mgc._procsub_anchor_view(cmd)) is False

    def test_real_procsub_still_flagged(self):
        cmd = 'git commit -m "x" > >(bash)'
        assert mgc._has_process_substitution_to_shell(mgc._procsub_anchor_view(cmd)) is True


# =========================================================================================
# R1 / F1 — ESCAPE-AWARE COMMENT PREDECESSOR (remediation commit 4). Commit 3's comment
# excision was escape-BLIND: a `#` after a BACKSLASH-ESCAPED delimiter (`\ #`, `\<tab>#`,
# `\;#`, `\&#`, `\|#`) is NOT a bash comment (the escaped delimiter is a literal word char,
# so bash EXECUTES what follows), but commit 3 excised it, deleting a real `| sh` from the
# routing view and neutralizing the gate (a token-independent under-block). _delimiter_is_
# unescaped (backslash-PARITY) closes it: ODD run = escaped = NOT a comment = text stays
# visible = gated; EVEN run (incl. 0) = real delimiter = comment (unchanged).
#
# DISCRIMINANT (design intent) = does bash execute the pipe? Verified below against a live
# `bash -c` with a touch-MARKER oracle: escaped delimiter -> pipe executes -> must gate;
# unescaped/plain -> comment -> pipe suppressed -> stays closed (the #1148 closure, preserved).
# =========================================================================================
_ESC = [  # escaped delimiter -> NON-comment -> routing token survives -> stays GATED
    ("esc-space-force-pipe-sh", 'echo "%s"\\ #x | sh' % PF),
    ("esc-space-force-pipe-bash", 'echo "%s"\\ #x | bash' % PF),
    ("esc-space-force-pipe-sh-nospace", 'echo "%s"\\ #x |sh' % PF),
    ("esc-tab-force", 'echo "%s"\\\t#x | sh' % PF),
    ("esc-semicolon-force", 'echo "%s"\\;#x | sh' % PF),
    ("esc-amp-force", 'echo "%s"\\&#x | sh' % PF),
    ("esc-pipe-force", 'echo "%s"\\|#x | sh' % PF),
    ("esc-space-branch-delete", 'echo "%s"\\ #x | sh' % ("git " + "branch " + "-D victim")),
    ("esc-space-merge-payload", 'echo "%s"\\ #x | sh' % M5),
    ("multi-escape-odd-run-3", 'echo "%s"\\ \\ \\ #x | sh' % PF),
]
_GENUINE = [  # real comment -> excised -> not dangerous (over-block safety; F1 must PRESERVE)
    ("plain-space-comment", 'echo "%s" #x | sh' % PF),
    ("unescaped-semicolon-then-comment", 'echo "%s" ;# c | sh' % PF),
    ("even-backslash-run-2-comment", 'echo "%s"\\\\ #x | sh' % PF),
    ("orig-1148-repro", REPRO),
]


class TestR1EscapeAwarePredecessor:
    @pytest.mark.parametrize("label,cmd", _ESC, ids=[r[0] for r in _ESC])
    def test_escaped_delimiter_stays_gated(self, label, cmd):
        # PRIMARY: the fixed classifier GATES the executing form (under-block closed).
        assert D(cmd) is True, "escaped-delimiter under-block re-opened (gate bypassed)"
        # NON-VACUITY at the mechanism level: the comment is NOT excised, so the routing
        # `| sh`/`| bash` survives on the view (a revert of _delimiter_is_unescaped would
        # excise it and flip this False). Base fixture also caught it (no excision pre-#1148).
        view = mgc._executed_surface_view(cmd)
        assert mgc._has_pipe_to_shell(view) is True, "escaped-# comment was wrongly excised"
        assert _base()(cmd) is True, "row not caught at base (vacuous retention)"

    @pytest.mark.parametrize("label,cmd", _GENUINE, ids=[r[0] for r in _GENUINE])
    def test_genuine_comment_still_excised(self, label, cmd):
        # OVER-BLOCK SAFETY: a real comment (unescaped / even-backslash delimiter) is still
        # excised -> not dangerous. F1 must not gate a faithful #-comment click.
        assert D(cmd) is False, "F1 over-blocked a genuine #-comment (regressed #1148 closure)"
        view = mgc._executed_surface_view(cmd)
        assert mgc._has_pipe_to_shell(view) is False, "genuine comment survived the view"

    def test_delimiter_parity_unit(self):
        # d points at the DELIMITER (the space here); count the backslash run ending at d-1.
        # 0/2 backslashes -> unescaped (real delimiter); 1/3 -> escaped (literal char).
        assert mgc._delimiter_is_unescaped("x #", 1) is True         # 0 backslashes
        assert mgc._delimiter_is_unescaped("x\\ #", 2) is False      # 1 backslash (escaped)
        assert mgc._delimiter_is_unescaped("x\\\\ #", 3) is True     # 2 backslashes (literal \\ + real space)
        assert mgc._delimiter_is_unescaped("x\\\\\\ #", 4) is False  # 3 backslashes (escaped)

    def test_bash_oracle_discriminant(self):
        # Ground truth: the design intent is "does bash execute the pipe?". This pins the
        # classifier's gate/excise decision to real bash comment semantics.
        import os
        import subprocess
        import tempfile

        cases = {
            " #x": False,        # plain space -> comment -> pipe suppressed
            "\\ #x": True,       # escaped space -> non-comment -> pipe executes
            "\\;#x": True,       # escaped ; -> non-comment
            "\\&#x": True,       # escaped & -> non-comment
            "\\\\ #x": False,    # even backslash -> comment
        }
        for tail, should_execute in cases.items():
            with tempfile.TemporaryDirectory() as td:
                marker = os.path.join(td, "M")
                subprocess.run(
                    ["bash", "-c", 'echo "hi"%s | touch %s' % (tail, marker)],
                    capture_output=True,
                )
                assert os.path.exists(marker) is should_execute, (
                    "bash oracle mismatch for tail %r" % tail
                )


# =========================================================================================
# R2 / F3 — BASH-CORRECT LINE-CONTINUATION SPLICE (remediation commit 6). The 3rd instance
# of _excise_comments_view mis-modeling bash comment semantics: a `#` whose predecessor is a
# shell LINE-CONTINUATION (`\<newline>`) was over-excised as a comment. Bash SPLICES
# `\<newline>` (removes it, gluing surrounding text), so `echo "…"\<newline>#x` becomes
# `echo "…"#x` — `#` glued to the closing quote (a NON-delimiter) = NOT a comment = the pipe
# EXECUTES. The former space-join (`\<newline>` -> " ") turned `#` into a false comment,
# excising a real `| sh` and bypassing the gate. FIX = the one-char SSOT splice in
# _normalize_line_continuations (`replace("\\n"," ")` -> `replace("\\n","")`); F1's predicate
# is byte-untouched (it is correct once fed a bash-spliced surface).
#
# DISCRIMINANT (design intent) = does bash execute the pipe? Line-continuation splices ->
# the `#` glues to a non-delimiter -> pipe executes -> gate. Bare-newline / plain-space /
# CRLF -> genuine comment -> pipe suppressed -> stay closed. Verified below against real bash.
# =========================================================================================
_FP = "git " + "push " + "--force origin main"
_BRD = "git " + "branch " + "-D victim"
_MRG = "gh " + "pr " + "merge 5 --admin"
_LC = "\\\n"   # backslash + newline (a shell line-continuation)

# F3 under-block: line-continuation before # -> bash splices -> NOT a comment -> gate.
_F3_CLOSE = []
for _pl_label, _pl in [("force", _FP), ("branch", _BRD), ("merge", _MRG)]:
    for _pipe_label, _pipe in [("pipe-sh", "| sh"), ("pipe-bash", "| bash"), ("pipe-sh-nospace", "|sh")]:
        _F3_CLOSE.append(
            ("f3-%s-%s" % (_pl_label, _pipe_label), 'echo "%s"%s#x %s' % (_pl, _LC, _pipe))
        )
# 4th-edge: double line-continuation (global splice removes BOTH by construction).
_F3_CLOSE.append(("f3-double-continuation", 'echo "%s"%s%s#x | sh' % (_FP, _LC, _LC)))
# procsub routing after a spliced #: still routes to a shell -> gate.
_F3_CLOSE.append(("f3-procsub-tail", 'echo "%s"%s#x > >(bash)' % (_FP, _LC)))


def _bash_pipe_executes(cmd):
    """Real-bash oracle: substitute the `| sh` sink with `| touch MARKER` and report whether
    bash actually ran the pipe (i.e. the `#` was NOT a comment)."""
    import os
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        marker = os.path.join(td, "M")
        full = cmd.replace("| sh", "| touch %s" % marker).replace("|sh", "|touch %s" % marker)
        subprocess.run(["bash", "-c", full], capture_output=True)
        return os.path.exists(marker)


class TestR2LineContinuationSplice:


    def test_bash_oracle_discriminant(self):
        # Ground truth pinned to real bash: line-continuation -> pipe executes (gate);
        # bare-newline / plain-space / CRLF -> comment -> pipe suppressed (stay closed).
        assert _bash_pipe_executes('echo "hi"%s#x | sh' % _LC) is True         # line-cont -> executes
        assert _bash_pipe_executes('echo "hi"%s%s#x | sh' % (_LC, _LC)) is True  # double -> executes
        assert _bash_pipe_executes('echo "hi"\n#x | sh') is False               # bare newline -> comment
        assert _bash_pipe_executes('echo "hi" #x | sh') is False                # plain space -> comment
        assert _bash_pipe_executes('echo "hi"\\\r\n#x | sh') is False           # CRLF -> comment

    def test_crlf_no_splice_disposition(self):
        # CRLF `\<CR><LF>` needs NO code handling — the splice matches backslash+`\n` only,
        # so CRLF is left unchanged, AND that is bash-correct: bash does not treat `\<CR><LF>`
        # as a line-continuation (the backslash escapes the CR; the LF stays a real newline).
        # These two rows pin the intentional no-splice-of-CRLF property (design decision).
        crlf = "\\\r\n"
        # (1) CRLF before # -> line 2 is a genuine comment -> pipe suppressed -> not-dangerous.
        assert D('echo "%s"%s#x | sh' % (_FP, crlf)) is False
        assert _bash_pipe_executes('echo "hi"%s#x | sh' % crlf) is False
        # (2) CRLF then a real command on line 2 -> the LF ends line 1, leg-split catches the
        #     line-2 merge -> dangerous (the CR does not swallow line 2).
        assert D('echo "hi"%sgh pr merge 5 --admin' % crlf) is True
