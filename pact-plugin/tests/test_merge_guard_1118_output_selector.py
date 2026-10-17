"""
Location: pact-plugin/tests/test_merge_guard_1118_output_selector.py
Folded 2026-10-04 (refactor shell lane): its row tests are decision-table rows now
         (tests/fixtures/shell/decisions/fold_manifest.json); the mechanism tests stay.
Summary: Bidirectional regression matrix for the #1118 carrier-9 output-selector strip —
         the `_strip_non_executable_content` selector value-strip (re-modeled quote-safe) that blanks the VALUES of
         gh-api client-side non-target flags (--jq/-q, --template/-t, --header/-H,
         --input, --hostname, --preview/-p) within a gh-api-only span. Locks in:
         Direction 1 — a faithful gh-api READ whose output selector / header / body-file
         name / hostname / preview value carries a `merge`/`git/refs` substring now
         ALLOWs (was an OVER-BLOCK by the .*merge implicit-POST arm — a SACROSANCT
         cardinal-sin cure); Direction 2 — every intentionally-held gh-api/CLI WRITE
         STAYS held, because its destructive target lives in the URL positional or the
         -X/--method flag (never a stripped value), and a contents/ span is preserved
         verbatim.
Used by: pytest (merge-guard suite).

Non-vacuity (red-on-revert): the mechanism class asserts carrier 9 actually blanks a
READ's --jq value (so `mergeStateStatus` no longer reaches the danger arms) AND leaves a
WRITE's endpoint positional (`merges`) intact. Reverting carrier 9 makes the READ's
substring survive -> is_dangerous flips to True -> the Direction-1 asserts fail. The
Direction-2 asserts are DISCRIMINATING negatives (each pins the SPECIFIC is_dangerous
True + the op_type the guard classifies it as), not a "matrix executes" smoke check.

Dangerous substrings are constructed at runtime (M / MS / GR) so this file carries no raw
`gh pr merge` / `git/refs` literal — mirrors the architect probe-harness convention and
keeps the file inert to any literal-scanning tool.
"""


from shared.merge_guard_common import (  # noqa: E402
    _strip_non_executable_content,
)

# Dangerous substrings assembled at runtime — never a raw literal in the source.
M = "mer" + "ge"                 # merge
MS = "mer" + "geStateStatus"     # a graphql/REST response field name


# --------------------------------------------------------------------------------------
# Mechanism / non-vacuity — proves carrier 9 does the work (red-on-revert anchor).
# --------------------------------------------------------------------------------------
class TestCarrier9Mechanism:

    def test_read_selector_value_is_blanked(self):
        # The --jq value carrying the response-field substring is removed from the
        # analysis surface, so it never reaches the .*merge arm.
        stripped = _strip_non_executable_content(
            f"gh api graphql -f query='q' --jq '.{MS}'"
        )
        assert MS not in stripped

    def test_write_endpoint_positional_survives(self):
        # The DESTRUCTIVE target lives in the URL positional and is NEVER stripped —
        # this is the invariant that keeps every Direction-2 write held.
        stripped = _strip_non_executable_content(f"gh api repos/o/r/{M}s -f base=main")
        assert f"{M}s" in stripped

    def test_gh_api_template_short_flag_stripped_in_span(self):
        # -t is --template WITHIN a gh-api span, so its value IS blanked (the short-flag
        # arm of carrier 9 fires only under the gh-api anchor).
        stripped = _strip_non_executable_content(
            f"gh api repos/o/r/pulls/1 -f foo=bar -t '.{M}'"
        )
        assert M not in stripped

    def test_curl_header_not_treated_as_selector(self):
        # Scope isolation: carrier 9's -H strip is gh-api-anchored. The SAME -H on a
        # curl command is outside the gh-api span (and -H is not carrier 8's domain),
        # so its value survives — proving carrier 9 never applies globally.
        stripped = _strip_non_executable_content(
            f'curl -H "X-Thing: {M}" https://api.example.com/foo'
        )
        assert M in stripped
