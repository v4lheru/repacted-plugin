"""
Three-surface architectural split enforcement (Arch-F4).

Pins the documentation discipline that the three surfaces of the
session-start ritual stay disjoint:

1. **Directive surface** (`pact-plugin/agents/pact-orchestrator.md` §2):
   describes WHAT the ritual achieves and WHEN to invoke it. Owns
   imperative governance (YOUR FIRST ACTION, when-to-re-invoke).
   FORBIDDEN: shell mechanics (mkdir, touch, literal session-dir paths,
   shell command examples). Persona body must NOT duplicate the
   command file's mechanics.

2. **Mechanics surface** (`pact-plugin/commands/bootstrap.md`):
   per-step procedural instructions for the orchestrator to execute.
   Owns shell commands, marker-write semantics, placeholder substitution.
   FORBIDDEN: governance directives ("S5 POLICY", "Algedonic",
   "Completion Authority", "MISSION", "MOTTO", "SACROSANCT",
   "FINAL MANDATE") and persona-body-style mandatory imperatives
   (`**You MUST`) outside `## Step N` sections. Mandatory voice INSIDE
   Step sections is legitimate (e.g., "Substitute `<path>` with...").

3. **Enforcement surface** (`pact-plugin/hooks/bootstrap_gate.py`):
   PreToolUse hook blocking Edit/Write/Agent/NotebookEdit until the
   bootstrap-complete marker exists. NOT TESTED HERE — the gate's
   behavior is covered by `test_bootstrap_gate.py` (the test in this
   module is for the directive↔mechanics coupling only).

Design rationale (per architect-review of PR #641, finding #4):
the mechanics-vs-enforcement coupling is structurally enforced via
the load-bearing `bootstrap-complete` literal (`shared.BOOTSTRAP_MARKER_NAME`
referenced by `bootstrap_gate.py`, `bootstrap_prompt_gate.py`, and
`commands/bootstrap.md`). The directive-vs-mechanics coupling is
documentation-discipline only; this test makes it structural.

Apply MEMORY pattern `convention_must_be_enforced_not_just_documented.md`:
README-only conventions decay under contributor pressure; structural
tests catch drift at the moment of introduction.
"""

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PERSONA_PATH = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
BOOTSTRAP_MD_PATH = PLUGIN_ROOT / "commands" / "bootstrap.md"


def _extract_persona_section_2(text: str) -> str:
    """Return the body of `## 2. Session-Start Ritual` from the persona,
    bounded by the next `---` separator OR the next top-level `## ` heading.

    Raises AssertionError if the section heading is not found — pins the
    invariant that persona §2 exists.
    """
    # Match the §2 heading line and capture forward.
    section_match = re.search(
        r"^## 2\.\s+Session-Start Ritual\s*$",
        text,
        re.MULTILINE,
    )
    assert section_match is not None, (
        "Persona body must contain a `## 2. Session-Start Ritual` heading. "
        "If the renumber drifted (e.g., to ## 3.), update this regex AND "
        "audit downstream cross-references."
    )
    start = section_match.end()
    # End of section: next `---` separator OR next `## ` heading.
    rest = text[start:]
    end_sep = rest.find("\n---\n")
    next_h2_match = re.search(r"\n## [^\n]", rest)
    candidates = [c for c in (end_sep, next_h2_match.start() if next_h2_match else -1) if c >= 0]
    end = min(candidates) if candidates else len(rest)
    return rest[:end]


# =============================================================================
# Directive surface: persona §2 contains NO mechanics prose
# =============================================================================




# =============================================================================
# Mechanics surface: bootstrap.md contains NO governance directives
# =============================================================================


class TestBootstrapCommandExcludesGovernanceDirectives:
    """Pins the mechanics↔governance asymmetry: commands/bootstrap.md
    owns per-step procedural mechanics; governance lives in the
    --agent-delivered persona body. Governance leakage into bootstrap.md
    creates the same v3.x duplication that PR #621 deliberately removed.

    Coverage extends `test_bootstrap_command_excludes_governance_fossils`
    (in test_628_coverage.py) with architect-review's recommended
    additions: `S5 POLICY`, `Algedonic`, `Completion Authority` (literal
    governance-section names) AND a pattern guard against
    `**You MUST ...**` mandatory imperatives outside Step-N sections.
    """

    GOVERNANCE_KEYWORDS = (
        "MISSION:",
        "MOTTO:",
        "FINAL MANDATE",
        "SACROSANCT",
        "S5 POLICY",
        "Algedonic",
        "Completion Authority",
    )

    @pytest.fixture
    def bootstrap_text(self):
        return BOOTSTRAP_MD_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _governance_leak_pattern(keyword: str) -> re.Pattern:
        """Build the heading-shape detector for a governance keyword.

        Two heading-shape contexts qualify as governance LEAK:
          1. Markdown heading line (H1-H6): `^#{1,6}\\s.*<keyword>` — line
             starts with one to six `#` markers followed by whitespace,
             then arbitrary text including the keyword. Empirically this
             is the canonical persona-body shape (e.g., `## 3. S5 POLICY
             — SACROSANCT Non-Negotiables`, `## 4. Algedonic Signals
             (Emergency Bypass)`, `## 12. Completion Authority, Teachback
             Review & Intentional Waiting`).
          2. Bold-emphasis form: `\\*\\*[^*\\n]*?<keyword>[^*\\n]*?\\*\\*` —
             keyword wrapped between `**` markers on a SINGLE line.
             Catches faux-heading usage like `**MISSION:**` or
             `**FINAL MANDATE** to all agents`. The `[^*\\n]*?` bound is
             load-bearing: excluding newlines prevents the non-greedy
             `*?` from pulling content across lines (e.g., a `**create**`
             on one line could otherwise reach forward to a `MISSION:`
             on a later line and an arbitrary `**` after that, producing
             a multi-line false match).

        Both contexts use case-insensitive matching: a future contributor
        lowercasing a heading (e.g., `## sacrosanct non-negotiables`) or
        mixing case (`Mission:`) would bypass a case-sensitive guard.

        Returns a compiled regex with both alternatives joined.
        """
        escaped = re.escape(keyword)
        # Combined pattern: heading-line OR same-line bold-emphasis.
        # MULTILINE so `^` matches per-line, IGNORECASE so case variants
        # are caught. The `[^*\n]*?` non-greedy bound on the bold form
        # prevents matching across `**` boundaries AND across newlines.
        return re.compile(
            rf"^#{{1,6}}\s.*{escaped}|\*\*[^*\n]*?{escaped}[^*\n]*?\*\*",
            flags=re.MULTILINE | re.IGNORECASE,
        )


    def test_governance_keyword_pattern_catches_known_leaks(self):
        """Positive control for the heading-shape detector.

        Pins that `_governance_leak_pattern` catches representative
        governance-leak shapes for each keyword. Without this control,
        the heading-shape detector could silently regress to a pattern
        that no longer fires on real leaks (e.g., a future "tighten the
        regex" edit that breaks the bold-emphasis branch) and the
        `test_bootstrap_md_excludes_governance_keyword` parametrize
        would still pass by reporting no match against unrelated text.

        Per-keyword fixture leaks (one heading-shape AND one bold-shape
        per keyword) are constructed inline. Each fixture must trigger
        a match — failure indicates the pattern has lost discriminative
        power on that keyword.

        The companion negative-control assertions confirm the pattern
        does NOT fire on inline prose, the canonical Recommended Agent
        Prompting Structure reference, or quoted-example blocks. These
        are the false-positive shapes the original case-insensitive
        substring check over-fired on.
        """
        # POSITIVE controls: each (keyword, leak_text) pair MUST match.
        positive_fixtures = [
            ("MISSION:", "## MISSION:"),
            ("MISSION:", "**MISSION:** all agents must..."),
            ("MOTTO:", "## MOTTO: ship correctness over speed"),
            ("MOTTO:", "**MOTTO:** be excellent"),
            ("FINAL MANDATE", "## FINAL MANDATE for orchestrators"),
            ("FINAL MANDATE", "**FINAL MANDATE** to all agents"),
            ("SACROSANCT", "## 3. S5 POLICY — SACROSANCT Non-Negotiables"),
            ("SACROSANCT", "**SACROSANCT** rules cannot be bypassed"),
            ("S5 POLICY", "## 3. S5 POLICY — SACROSANCT Non-Negotiables"),
            ("S5 POLICY", "**S5 POLICY** governs viability"),
            ("Algedonic", "## 4. Algedonic Signals (Emergency Bypass)"),
            ("Algedonic", "**Algedonic** emit on viability threats"),
            ("Completion Authority", "## 12. Completion Authority, Teachback Review"),
            ("Completion Authority", "### Completion Authority"),
            ("Completion Authority", "**Completion Authority** belongs to the lead"),
        ]
        for keyword, leak in positive_fixtures:
            pattern = self._governance_leak_pattern(keyword)
            assert pattern.search(leak), (
                f"Positive control failed: keyword {keyword!r} pattern "
                f"did not match heading-shape leak {leak!r}. The "
                f"heading-shape detector has lost discriminative power "
                f"on this keyword — `_governance_leak_pattern` may have "
                f"been edited to over-narrow."
            )

        # NEGATIVE controls: each (keyword, prose) pair must NOT match.
        # These are the shapes that the original case-insensitive
        # substring check over-fired on; the refined heading-shape
        # detector correctly ignores them.
        negative_fixtures = [
            ("MISSION:", "the mission of the secretary is to deliver briefings"),
            ("MISSION:", "CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per persona §13"),
            ("MISSION:", '`description="<full mission: deliver session briefing on spawn>"`'),
            ("MISSION:", "2. `TaskCreate(subject=\"...\", description=\"<full mission: deliver session briefing on spawn, answer memory queries during the session, process HANDOFFs at workflow boundaries; CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per the orchestrator persona §13 Recommended Agent Prompting Structure>\")`"),
            ("SACROSANCT", "A SACROSANCT non-negotiable was clarified, refined, or newly discovered."),
            ("Algedonic", "agents emit algedonic signals when they recognize viability threats"),
            ("Completion Authority", "See Completion Authority section above"),
        ]
        for keyword, prose in negative_fixtures:
            pattern = self._governance_leak_pattern(keyword)
            assert not pattern.search(prose), (
                f"Negative control failed: keyword {keyword!r} pattern "
                f"OVER-FIRED on prose {prose!r}. The heading-shape "
                f"detector has regressed to substring-match behavior — "
                f"`_governance_leak_pattern` has been edited to "
                f"over-broaden, undoing the Option E refinement."
            )

