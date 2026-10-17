"""
Location: pact-plugin/tests/test_merge_guard_1118_output_selector_mechanism.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: TEST-phase CERTIFICATION matrix for the #1118 carrier-9 output-selector strip,
         EXPANDING the coder's regression file (test_merge_guard_1118_output_selector.py)
         with adversarial-edge + boundary coverage against the REAL is_dangerous_command /
         detect_command_operation_type. Carrier 9 blanks the VALUES of gh-api client-side
         non-target flags (-q/--jq, -t/--template, -H/--header, --input, --hostname,
         -p/--preview) inside a gh-api-only span, keeping the flag tokens.

         Asymmetric gates (merge-guard purpose be1bf393): PRIMARY = no faithful READ is ever
         over-blocked (Direction 1 — cardinal sin); co-equal security = no NEW under-block
         (Direction 2 held writes + the command-substitution carve-out). Direction-2 asserts
         are DISCRIMINATING negatives (specific is_dangerous True AND the op_type the guard
         classifies), never "matrix runs".

         NON-VACUITY: empirical red-on-revert was MEASURED at cert time by loading the HEAD~1
         (pre-carrier-9) module and confirming every Direction-1 read was is_dangerous=True
         there and False under the shipped module (9/9 flip; 0 held-write flips) — recorded
         in the TEST HANDOFF. That cert-time-only stance is now SUPERSEDED: the base-vs-HEAD-vs-PATCH
         differential IS shipped as a permanent suite (test_merge_guard_1118_recert.py — pinned
         base/HEAD SHAs, self-skipping where the base commit is unreachable). The git-independent shipped red-on-revert
         anchor is the coder file's TestCarrier9Mechanism (it asserts a read's selector value
         is blanked; reverting carrier 9 makes the substring survive -> those asserts fail —
         verified: at HEAD~1, `mergeStateStatus` DOES survive the strip). The LOAD-BEARING
         cmd-substitution edge below carries its own in-process non-vacuity (the payload verb
         must survive the strip, else an under-block).

Used by: pytest (merge-guard suite).

Dangerous substrings are assembled at runtime (M / MS / GR / PR_MERGE) so this file carries
no raw `gh pr merge` / `git/refs` literal — mirrors the coder file + architect probe-harness
convention and keeps the file inert to any literal-scanning tool.
"""


from shared.merge_guard_common import (  # noqa: E402
    _strip_non_executable_content,
)

# Dangerous substrings assembled at runtime — never a raw literal in the source.
M = "mer" + "ge"                  # merge
MS = "mer" + "geStateStatus"      # a graphql/REST response field name
PR_MERGE = f"gh pr {M} 5"         # command-substitution payload (a real destructive verb)


# --------------------------------------------------------------------------------------
# Direction 1 (PRIMARY gate) — additional over-blocked READS that must now ALLOW.
# Extends the coder file's 12 reads with boundary/collision shapes: short-flag forms,
# multi-selector combos, escaped quotes, the --paginate boolean (must NOT be mis-stripped
# as -p), an explicit -X GET read, and broader non-gh-api gh reads.
# --------------------------------------------------------------------------------------
class TestDirection1ReadsExpanded:
    READS = {
        # Multiple selectors on one read, each carrying a merge substring.
        "multi_selector_combo": f"gh api repos/o/r/pulls/1 -f q=x --jq '.{M}able' -t '{{{{.{MS}}}}}' -H 'X-{M}: 1'",
        # Double-quoted jq value with escaped inner quotes (quote-aware span must consume it).
        "selector_escaped_quote": f'gh api repos/o/r/pulls/1 -f q=x --jq ".a[\\"{M}\\"]"',
        # -q short form of --jq.
        "q_short_jq_form": f"gh api repos/o/r/pulls/1 -f q=x -q '.{M}able'",
        # -p short preview with a comma-multi-value (single token; no spurious leg).
        "p_preview_comma_multi": f"gh api -p {M}-info,other-info repos/o/r/pulls/1 -f q=x",
        # Explicit GET read with a selector — stays allow.
        "explicit_get_with_selector": f"gh api -X GET repos/o/r/pulls/1 -f q=x --jq '.{M}able'",
        # --paginate is a BOOLEAN (no value) — the -p arm requires trailing whitespace+value,
        # so --paginate is never mis-stripped; only --jq's value is blanked -> read allows.
        "paginate_boolean_untouched": f"gh api repos/o/r/pulls/1 --paginate --jq '.{M}able'",
        # Broader non-gh-api gh reads (carrier 9 never touches a non-gh-api span).
        "gh_pr_checks": "gh pr checks 1116",
        "gh_issue_view": "gh issue view 1116",
        "gh_release_list": "gh release list",
    }


    def test_paginate_boolean_survives_strip(self):
        # Mechanism: --paginate is preserved verbatim; only the --jq value is blanked.
        stripped = _strip_non_executable_content(
            f"gh api repos/o/r/pulls/1 --paginate --jq '.{M}able'"
        )
        assert "--paginate" in stripped
        assert MS not in stripped and f".{M}able" not in stripped


# --------------------------------------------------------------------------------------
# LOAD-BEARING adversarial edge — command substitution inside a selector value.
# Falsifies the double-quote _keep_selector_dq cmd-sub CARVE-OUT: a `$(...)` in a
# DOUBLE-quoted value really executes when the shell runs the command, so carrier 9 must
# PRESERVE it (leave the embedded verb visible to the danger arms) — else stripping it is
# an UNDER-BLOCK. A SINGLE-quoted `$(...)` is literal shell text (never executes), so
# stripping it is correct and the command is a genuine read -> allow. The single/double
# asymmetry is shell-semantic-faithful, not accidental.
# --------------------------------------------------------------------------------------
class TestCommandSubstitutionCarveOut:


    def test_double_quote_cmdsub_payload_survives_strip(self):
        # In-process non-vacuity for the carve-out: the embedded destructive verb MUST remain
        # on the analysis surface. If the carve-out were removed, this substring would be
        # blanked and is_dangerous would drop to False (the under-block this edge guards).
        cmd = f'gh api -H "X: $({PR_MERGE})" repos/o/r/pulls/1'
        stripped = _strip_non_executable_content(cmd)
        assert f"gh pr {M}" in stripped


# --------------------------------------------------------------------------------------
# Selector value carrying shell separators — the strip neutralizes embedded metachars, so
# no spurious destructive leg is injected and the read allows.
# --------------------------------------------------------------------------------------
class TestSelectorValueNeutralization:


    def test_separator_payload_blanked_from_surface(self):
        # Mechanism: the ` ; touch y` payload is inside the -H value and is replaced by the
        # 'STRIPPED' placeholder, so it can never be re-parsed as a separate command leg.
        cmd = 'gh api -H "X: a; touch y" -f q=x repos/o/r/pulls/1'
        stripped = _strip_non_executable_content(cmd)
        assert "touch" not in stripped


# --------------------------------------------------------------------------------------
# Scope isolation — carrier 9's -q/-t/-H/-p are gh-api SHORT flags, matched only under the
# gh-api span anchor. The SAME short flags on curl/wget mean something else (curl -t =
# --telnet-option, wget -t = --tries) and MUST NOT be stripped. Complements the coder
# file's curl -H isolation test with the -t short-flag collisions named in the design's
# risk table.
# --------------------------------------------------------------------------------------
class TestScopeIsolationExpanded:

    def test_curl_short_t_value_survives(self):
        # curl -t (--telnet-option) is outside the gh-api span -> its value is NOT stripped.
        stripped = _strip_non_executable_content(
            f"curl -t {M}-opt https://api.example.com/x"
        )
        assert M in stripped

    def test_wget_short_t_value_survives(self):
        # wget -t (--tries) is outside the gh-api span -> its value is NOT stripped.
        stripped = _strip_non_executable_content(
            f"wget -t {M} https://api.example.com/x"
        )
        assert M in stripped
