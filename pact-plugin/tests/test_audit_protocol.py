"""
Tests for the pact-audit.md protocol and pact-auditor agent definition.

Tests cover:
1. Audit signal format validation (GREEN/YELLOW/RED with required fields)
2. Audit request format (dispatch conditions, completion lifecycle)
3. Auditor dispatch threshold consistency (protocol vs orchestrate.md)
4. Auditor agent definition structure
5. Completion lifecycle: signal-type with audit_summary
"""
from pathlib import Path

import pytest

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
AGENTS_DIR = Path(__file__).parent.parent / "agents"
SKILLS_DIR = Path(__file__).parent.parent / "skills"

# the protocol consolidation: the audit protocol moved to coordination.md §Concurrent Audit; the extract gate and
# the SSOT it compared against are deleted, so the extract-population arms are gone.
AUDIT_PROTOCOL = PROTOCOLS_DIR / "coordination.md"
ORCHESTRATE_CMD = COMMANDS_DIR / "orchestrate.md"
AUDITOR_AGENT = AGENTS_DIR / "pact-auditor.md"


# =============================================================================
# Audit protocol structure
# =============================================================================


class TestAuditProtocolExists:
    """Verify pact-audit.md exists and has required content."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_protocol_file_exists(self):
        assert AUDIT_PROTOCOL.exists()

    def test_has_concurrent_audit_heading(self, audit_content):
        # Wiring check: protocol must define the concurrent audit section
        assert "Concurrent Audit Protocol" in audit_content

    def test_has_dispatch_conditions(self, audit_content):
        # Wiring check: protocol must specify when to deploy auditor
        assert "Dispatch Conditions" in audit_content

    def test_has_observation_model(self, audit_content):
        # Wiring check: protocol must define observation approach
        assert "Hybrid Observation Model" in audit_content or "Observation" in audit_content

    def test_has_signal_format(self, audit_content):
        # Wiring check: protocol must define how auditor reports findings
        assert "Signal Format" in audit_content

    def test_has_signal_levels(self, audit_content):
        # Wiring check: protocol must define signal severity tiers
        assert "Signal Levels" in audit_content

    def test_has_completion_lifecycle(self, audit_content):
        # Wiring check: protocol must define signal-type completion
        assert "Completion Lifecycle" in audit_content or "completion_type" in audit_content


# =============================================================================
# Signal format validation
# =============================================================================


class TestAuditSignalFormat:
    """Validate audit signal format matches spec."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_signal_levels_present(self, audit_content):
        # Wiring check: protocol must define all three signal levels
        assert "GREEN" in audit_content
        assert "YELLOW" in audit_content
        assert "RED" in audit_content

    def test_signal_format_has_reference_field(self, audit_content):
        # Wiring check: signal must include file/component reference
        assert "Reference" in audit_content

    def test_signal_format_has_scope_field(self, audit_content):
        # Wiring check: signal must specify observation scope
        assert "Scope" in audit_content

    def test_signal_format_has_finding_field(self, audit_content):
        # Wiring check: signal must describe the finding
        assert "Finding" in audit_content

    def test_signal_format_has_evidence_field(self, audit_content):
        # Wiring check: signal must cite evidence
        assert "Evidence" in audit_content

    def test_signal_format_has_action_field(self, audit_content):
        # Wiring check: signal must recommend action
        assert "Action" in audit_content

    def test_green_means_on_track(self, audit_content):
        """GREEN signal means implementation is on track."""
        # The protocol should define GREEN as meaning on-track
        lower = audit_content.lower()
        assert "green" in lower and "on track" in lower

    def test_red_means_intervene(self, audit_content):
        """RED signal means orchestrator should intervene."""
        lower = audit_content.lower()
        assert "red" in lower and "intervene" in lower


# =============================================================================
# Dispatch threshold consistency
# =============================================================================


class TestDispatchThresholdConsistency:
    """Verify auditor dispatch conditions match between protocol and orchestrate.md."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_CMD.read_text(encoding="utf-8")

    def test_protocol_has_variety_threshold(self, audit_content):
        """Protocol specifies variety >= 7 as dispatch condition."""
        # Wiring check: variety threshold must be defined
        assert ">= 7" in audit_content or "7" in audit_content

    def test_protocol_has_parallel_coders_condition(self, audit_content):
        """Protocol specifies parallel coders as dispatch condition."""
        # Wiring check: parallel-coders condition must be defined
        lower = audit_content.lower()
        assert "parallel" in lower and "coder" in lower

    def test_protocol_has_security_condition(self, audit_content):
        """Protocol specifies security-sensitive code as dispatch condition."""
        # Wiring check: security condition must be defined
        lower = audit_content.lower()
        assert "security" in lower

    def test_orchestrate_references_auditor(self, orchestrate_content):
        """orchestrate.md includes auditor dispatch instructions."""
        # Wiring check: orchestrate must reference auditor agent
        assert "pact-auditor" in orchestrate_content

    def test_orchestrate_has_variety_threshold(self, orchestrate_content):
        """orchestrate.md specifies variety >= 7 for auditor dispatch."""
        # Wiring check: orchestrate threshold must match protocol
        assert "variety >= 7" in orchestrate_content or "variety 7" in orchestrate_content.lower()

    def test_orchestrate_has_completion_type_signal(self, orchestrate_content):
        """orchestrate.md sets completion_type: signal for auditor tasks."""
        # Wiring check: orchestrate must use signal-type completion for auditor
        assert "completion_type" in orchestrate_content
        assert "signal" in orchestrate_content


# =============================================================================
# Auditor agent definition
# =============================================================================


class TestAuditorAgentDefinition:
    """Verify pact-auditor.md agent definition structure."""

    @pytest.fixture
    def agent_content(self):
        return AUDITOR_AGENT.read_text(encoding="utf-8")

    def test_agent_file_exists(self):
        assert AUDITOR_AGENT.exists()

    def test_has_frontmatter_name(self, agent_content):
        # Wiring check: agent must declare correct name in frontmatter
        assert "name: pact-auditor" in agent_content

    def test_has_observation_protocol(self, agent_content):
        """Agent has observation phases (A, B, C)."""
        # Wiring check: agent must define all three observation phases
        assert "Phase A" in agent_content
        assert "Phase B" in agent_content
        assert "Phase C" in agent_content

    def test_has_behavioral_rules(self, agent_content):
        # Wiring check: agent must include behavioral constraints
        lower = agent_content.lower()
        assert "behavioral rules" in lower or "behavioural rules" in lower

    def test_has_audit_criteria(self, agent_content):
        # Wiring check: agent must define what it evaluates
        assert "AUDIT CRITERIA" in agent_content or "Audit Criteria" in agent_content

    def test_has_signal_format(self, agent_content):
        # Wiring check: agent must define its output format
        assert "Signal Format" in agent_content or "AUDIT SIGNAL" in agent_content

    def test_has_completion_section(self, agent_content):
        # Wiring check: agent must define signal-type completion lifecycle
        assert "COMPLETION" in agent_content or "Completion" in agent_content
        assert "completion_type" in agent_content or "audit_summary" in agent_content

    def test_has_algedonic_escalation(self, agent_content):
        # Wiring check: agent must reference algedonic escalation path
        assert "algedonic" in agent_content.lower()

    def test_does_not_write_code_boundary(self, agent_content):
        """Agent explicitly states it does not write code."""
        # Wiring check: read-only observer boundary must be stated
        lower = agent_content.lower()
        assert "do not write" in lower or "do not modify" in lower


# =============================================================================
# Structural Verification Discipline (#502)
# =============================================================================


class TestStructuralVerificationDiscipline:
    """Verify STRUCTURAL VERIFICATION DISCIPLINE is present in agent body and protocol.

    Each assertion targets a specific load-bearing property of the discipline
    (canonical term, MUST voice, named failure modes, prior-art citation, incident
    citation, Evidence-field upgrade, BEHAVIORAL RULES integration). A failing
    assertion names the specific erosion shape rather than a generic "rule missing".
    Baseline-fail on b0e3f7e is required — these tests do not pass on pre-#502 HEAD.
    """

    @pytest.fixture
    def agent_content(self):
        return AUDITOR_AGENT.read_text(encoding="utf-8")

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_agent_has_discipline_section(self, agent_content):
        """Agent body declares the discipline section by canonical name."""
        assert "STRUCTURAL VERIFICATION DISCIPLINE" in agent_content

    def test_protocol_has_discipline_section(self, audit_content):
        """Protocol anchor mirrors the discipline section (H3-cased)."""
        assert "Structural Verification Discipline" in audit_content

    def test_agent_uses_must_voice(self, agent_content):
        """Discipline section uses MUST voice, not 'should' or 'recommended'."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "MUST" in section, "Discipline section must use MUST voice"

    def test_agent_references_git_diff_verification(self, agent_content):
        """Discipline requires git diff as the verification substrate."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "git diff" in section

    def test_agent_names_failure_modes(self, agent_content):
        """Discipline names the canonical failure modes for LLM-reader at execution time."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "PHANTOM-SYMMETRIC-CLAIM" in section
        assert "VAGUE-DIFF-CITATION" in section
        assert "STRUCTURAL-DRESSING-ON-JUDGMENT-CALL" in section

    @pytest.mark.parametrize(
        "filename,path",
        [
            ("pact-auditor.md", AUDITOR_AGENT),
            ("coordination.md", AUDIT_PROTOCOL),
        ],
    )
    def test_phantom_symmetric_claim_bullet_enumerates_all_layers(
        self, filename, path
    ):
        """PHANTOM-SYMMETRIC-CLAIM bullet enumerates all four canonical layers
        in every rule-carrying surface.

        Each of the three rule carriers (agent body, protocol anchor, SSOT) has
        a section that asserts "Four internally-consistent layers of prose can
        all be wrong together" in its preamble. The PHANTOM-SYMMETRIC-CLAIM
        bullet in that section must enumerate four named layers, not three.
        Arithmetic inconsistency here (3 enumerated, 4 claimed by the
        preamble) is the same failure shape the rule is installed to prevent:
        prose that self-contradicts on a countable fact. Without this guard,
        a future edit that drops a layer from the bullet on any one carrier
        would stay green on `test_agent_names_failure_modes` (which only
        checks the failure-mode NAMES, not their body text) and survive to
        a blind review — the exact under-propagation pattern #502 B1 caught
        across the protocol mirrors after T1 only fixed the agent body.
        """
        content = path.read_text(encoding="utf-8")
        # Section header form differs across files:
        #   pact-auditor.md uses "### Failure modes to avoid" (H3)
        #   pact-audit.md / pact-protocols.md use "**Failure modes to avoid**:"
        # The bare phrase is present in all three; use it as a uniform marker.
        # Guarded lookup: if a heading is renamed, fail with a pointed
        # diagnostic instead of an uncaught ValueError.
        for marker in (
            "Failure modes to avoid",
            "PHANTOM-SYMMETRIC-CLAIM",
            "VAGUE-DIFF-CITATION",
        ):
            if marker not in content:
                pytest.fail(
                    f"{filename}: section marker '{marker}' missing — "
                    f"heading may have been renamed or section deleted"
                )
        failure_modes_start = content.index("Failure modes to avoid")
        phantom_start = content.index("PHANTOM-SYMMETRIC-CLAIM", failure_modes_start)
        # The bullet ends at the next list item (VAGUE-DIFF-CITATION).
        bullet_end = content.index("VAGUE-DIFF-CITATION", phantom_start)
        bullet = content[phantom_start:bullet_end]
        for layer in (
            "HANDOFF prose",
            "commit message",
            "coder self-attestation",
            "audit signal",
        ):
            assert layer in bullet, (
                f"{filename}: PHANTOM-SYMMETRIC-CLAIM bullet missing canonical "
                f"layer '{layer}' — bullet's section preamble claims 'four "
                f"layers' but enumeration must list all four (see #502 T1/B1)"
            )

    def test_discipline_section_has_structural_ac_table(self, agent_content):
        """The 'What counts as a structural AC' disambiguation table is present.

        A future edit that deletes or drastically trims this table (e.g.,
        below 4 yes-rows / 4 no-rows) leaves the MUST rule without its
        disambiguation substrate. The auditor at execution time needs
        concrete examples for each side to classify a novel AC.
        """
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "### What counts as a structural acceptance criterion" in section, (
            "Disambiguation table subsection missing"
        )
        # The table uses "| yes |" / "| **no** |" cells. Count presence as a
        # structural-erosion guard: at least 4 of each required.
        assert section.count("| yes |") >= 4, (
            "Disambiguation table has fewer than 4 structural-yes example rows"
        )
        assert section.count("| **no** |") >= 4, (
            "Disambiguation table has fewer than 4 non-structural example rows"
        )

    def test_discipline_section_has_five_step_verification_procedure(
        self, agent_content
    ):
        """The Verification procedure is a numbered 5-step list, not prose.

        Dropping the numbered-list structure or reducing the step count
        erodes the procedure's operational teeth. Step 4 in particular
        ('If count/location does NOT match → RED, not GREEN') is the
        enforcement hinge the rule is installed around.
        """
        import re

        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "### Verification procedure" in section, (
            "Verification procedure subsection missing"
        )
        proc_start = section.index("### Verification procedure")
        proc_end = section.index("### Failure modes to avoid", proc_start)
        procedure = section[proc_start:proc_end]
        step_count = len(re.findall(r"^\d+\. ", procedure, re.MULTILINE))
        assert step_count == 5, (
            f"Verification procedure must be exactly 5 numbered steps; "
            f"found {step_count}"
        )

    def test_step_4_requires_red_on_count_mismatch(self, agent_content):
        """Step 4 prose requires RED on count mismatch, not YELLOW.

        The RED-vs-YELLOW disambiguation was added by M5 (cycle 1) to close
        a severity-assignment escape hatch — an auditor under coordination
        pressure could silently downgrade a clear count violation to YELLOW.
        Step 4 must name both 'RED' and 'count mismatch' explicitly so the
        disambiguation is load-bearing at the decision point.
        """
        import re

        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        proc_start = section.index("### Verification procedure")
        proc_end = section.index("### Failure modes to avoid", proc_start)
        procedure = section[proc_start:proc_end]
        # Extract step 4: from "^4\. " to "^5\. " or end of procedure.
        step4_match = re.search(
            r"^4\. .*?(?=^5\. |\Z)", procedure, re.MULTILINE | re.DOTALL
        )
        assert step4_match is not None, "Step 4 not found in Verification procedure"
        step4 = step4_match.group(0)
        assert "RED" in step4 and "count mismatch" in step4, (
            "Step 4 must name both 'RED' and 'count mismatch' to carry the "
            "RED-vs-YELLOW disambiguation (see #502 M5)"
        )

    def test_agent_cites_prior_art(self, agent_content):
        """Discipline cites file inspection beats HANDOFF inference rationale."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "file inspection beats HANDOFF inference" in section

    def test_agent_cites_phantom_symmetric_claim_discipline(self, agent_content):
        """Discipline cites the triggering incident so the rule's context survives compaction.

        The behavioral anchor `PHANTOM-SYMMETRIC-CLAIM` names the failure-mode
        shape (four-layer agreement on a fabricated structural claim) that
        motivated the rule. The name survives planning-artifact scrubs and
        is the durable discipline content; provenance SHAs / PR refs are
        incidental and forbidden in `agents/*.md` per the LLM-load distinction.
        """
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "PHANTOM-SYMMETRIC-CLAIM" in section

    def test_agent_signal_format_evidence_field_upgraded(self, agent_content):
        """Agent's Evidence field requires structural ACs be cited via git diff."""
        start = agent_content.index("## SIGNAL FORMAT")
        end = agent_content.index("## SIGNAL LEVELS", start)
        section = agent_content[start:end]
        assert "structural" in section.lower()
        assert "git diff" in section

    def test_protocol_signal_format_evidence_field_upgraded(self, audit_content):
        """Protocol's Evidence field requires structural ACs be cited via git diff."""
        start = audit_content.index("### Signal Format")
        end = audit_content.index("### Signal Levels", start)
        section = audit_content[start:end]
        assert "structural" in section.lower()
        assert "git diff" in section

    def test_agent_has_behavioral_rule_for_structural_verification(self, agent_content):
        """BEHAVIORAL RULES table references the discipline."""
        start = agent_content.index("## BEHAVIORAL RULES")
        end = agent_content.index("## AUDIT CRITERIA", start)
        section = agent_content[start:end]
        assert "STRUCTURAL VERIFICATION DISCIPLINE" in section


class TestTeachbackBlockingSemanticGuard:
    """Semantic-regression guard for the teachback-doc reconciliation to ONE
    blocking model.

    The byte-mirror gate (test_verify_protocol_extracts_* above) guards
    extract<->SSOT BYTE-IDENTITY: it catches a DESYNC of a mirrored pair but is
    BLIND to the blocking-vs-non-blocking SEMANTIC. A future edit that
    re-introduces GEN-1 non-blocking phrasing IDENTICALLY on both the extract and
    its SSOT mirror stays byte-identical and passes the byte-gate. This guard
    asserts the reconciled teachback surface does not re-introduce the specific
    GEN-1 phrases the reconciliation removed.

    False-positive safety: this is a DENYLIST of high-specificity phrases
    distinctive to the removed GEN-1 teachback prose (each verified to appear
    NOWHERE in the current sources). It deliberately does NOT denylist the
    generic word "non-blocking", which legitimately occurs in these files (e.g.
    pact-protocols.md: "The auditor is **non-blocking**", plus hook fail-open /
    HANDOFF-processing contexts) and would false-positive. Phrase-specificity is
    what lets this assert file-wide without region-bounding the multi-topic SSOT.

    Bounded-completeness residual (intentional): a phrase denylist catches a
    copy-paste REVERT to the old GEN-1 wording (the likely regression) but NOT a
    re-worded non-blocking model expressed in fresh prose. Guarding the latter
    would require the generic word + region-bounding + context exclusions, which
    is fragile and false-positive-prone; that completeness is out of scope by
    design (copy-paste-revert is the valuable, false-positive-safe coverage).
    """

    # The reconciled teachback surface (the reconciliation's edited file set).
    RECONCILED_FILES = [
        # TEAMMATE (m6): the ct-teachback teammate half and pact-teachback merged into this reference.
        SKILLS_DIR / "teammate" / "references" / "teammate-teachback.md",
        PROTOCOLS_DIR / "completion.md",
        AGENTS_DIR / "pact-orchestrator.md",
        COMMANDS_DIR / "peer-review.md",
    ]

    # High-specificity GEN-1 (non-blocking) phrases the reconciliation removed.
    # Distinctive enough that legitimate current prose never contains them, so a
    # match means a GEN-1 regression re-entered the teachback surface. Matched
    # case-insensitively so a re-cased revert is still caught. Note: "stays
    # hidden" is the least-specific entry (it guards the corrected false
    # blockedBy-visibility claim); the other three are unmistakably GEN-1
    # teachback prose. The generic word "non-blocking" is intentionally EXCLUDED
    # (it occurs legitimately, e.g. the auditor description in pact-protocols.md).
    GEN1_REGRESSION_PHRASES = [
        "Why Non-Blocking",                 # removed section heading
        "Proceeding unless corrected",      # removed proceed-optimistically clause
        "proceed with work after sending",  # removed non-blocking flow line
        "stays hidden",                     # corrected false blockedBy claim
    ]

    def test_no_gen1_nonblocking_phrase_in_reconciled_surface(self):
        for path in self.RECONCILED_FILES:
            assert path.is_file(), f"reconciled surface file missing: {path}"
            text = path.read_text(encoding="utf-8").lower()
            for phrase in self.GEN1_REGRESSION_PHRASES:
                assert phrase.lower() not in text, (
                    f"GEN-1 non-blocking phrase {phrase!r} re-introduced into "
                    f"{path.name} — the teachback surface reconciliation moved it "
                    f"to ONE blocking model. The byte-mirror gate cannot catch "
                    f"this (it guards byte-identity, not blocking-vs-non-blocking "
                    f"semantics). If this phrasing is intentionally returning, the "
                    f"reconciliation is being reverted; update this guard "
                    f"deliberately."
                )


class TestLeadSidePartialVarietyWriteWarning:
    """The lead-facing re-stamp instruction must carry its partial-write warning.

    THE SUBJECT. `TaskUpdate` merges metadata at the TOP LEVEL only, so a write
    that names `variety` REPLACES the whole object and erases each field it did
    not carry. Two lead-facing surfaces instruct the lead to RE-STAMP
    `metadata.variety` after it was already written, which is precisely that
    shape. Each of the two must state the hazard beside the instruction.

    WHY AN ARM AND NOT PROSE ALONE. The warning is prose that nothing executes.
    A later editor can drop it from one carrier while the other keeps it, and
    no run reports anything. That is the stale-twin direction, and it is the one
    failure this arm is built to catch.

    THIS ARM ASSERTS A PROPERTY, NOT A SENTENCE, AND THE DISTINCTION IS THE
    WHOLE DESIGN. An arm that pinned the wording would redden on any rewording
    that preserves the instruction, which is an over-block, and an arm that
    obstructs the next contributor gets removed rather than repaired. So the
    check requires three CONCEPTS near the instruction, each satisfied by any
    member of a synonym group:
      G1 REPLACEMENT: the write destroys what it did not carry.
      G2 WHOLE-OBJECT: the correction must re-send all of it.
      G3 READ-BACK: check the result on disk afterwards.
    A rewriter free to choose words stays green while all three concepts
    survive. Dropping one of the three reddens, which is correct, because each
    one alone leaves a lead able to reason its way back to a partial write.

    WHAT THIS ARM CANNOT SEE. It cannot judge whether the wording is CLEAR, and
    it cannot see a warning that states all three concepts incorrectly. It reads
    concept presence in a bounded window, nothing more.

    A MEASURED GAP THIS ARM DOES NOT CLOSE, RECORDED SO IT IS NOT READ AS
    COVERED. The same hazard is stated on two TEAMMATE-facing carriers, the
    rejection-correction step and Step 1 of the teammate skill's handoff and
    teachback references (TEAMMATE m6). NO arm reads either of those two. Their omission
    here is deliberate: widening this arm to cover them is a decision about
    another author's ruled scope, not a tidy-up.
    """

    # The two lead-facing carriers of the re-stamp instruction: the variety
    # workflow, and the completion protocol's teachback validation (moved
    # there from the persona body, which now points at it).
    RESTAMP_CARRIERS = [
        PROTOCOLS_DIR / "workflows.md",
        PROTOCOLS_DIR / "completion.md",
    ]

    # The instruction this arm attaches to. Each line is lower-cased and its
    # runs of whitespace are collapsed to one space before the comparison, so
    # a re-cased edit and a re-wrapped line are both still found.
    RESTAMP_TOKEN = "re-stamp `metadata.variety`"

    # Lines to scan after the instruction line for its warning. Four allows a
    # blank line, the warning, and a trailing blank, with one line of slack.
    WINDOW = 4

    # Each group is satisfied by ANY member. Substring matching against
    # lower-cased text, so inflections that contain a listed stem also match.
    CONCEPT_GROUPS = {
        "G1 replacement": ("replace", "overwrite", "erase", "discard", "drop"),
        "G2 whole object": ("full", "whole", "entire", "complete", "all of"),
        "G3 read-back": ("read the task file back", "read back", "read-back",
                         "re-read", "enumerate"),
    }

    def _warning_window(self, path):
        """Return the text that follows the single re-stamp instruction."""
        assert path.is_file(), f"re-stamp carrier missing: {path}"
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [
            i for i, line in enumerate(lines)
            if self.RESTAMP_TOKEN in " ".join(line.split()).lower()
        ]
        # NON-VACUITY. Zero hits means the instruction was reworded or removed,
        # and the window below would be empty, which would pass every concept
        # check against nothing. That fails LOUDLY here instead.
        assert len(hits) == 1, (
            f"{path.name} carries {len(hits)} instruction(s) containing "
            f"{self.RESTAMP_TOKEN!r}, and this arm attaches its warning "
            "check to exactly one. If the instruction moved, was reworded, or "
            "was duplicated, re-point this arm deliberately."
        )
        start = hits[0] + 1
        return "\n".join(lines[start:start + self.WINDOW]).lower()

    def test_each_lead_carrier_states_the_partial_write_hazard(self):
        for path in self.RESTAMP_CARRIERS:
            window = self._warning_window(path)
            for group, members in self.CONCEPT_GROUPS.items():
                assert any(member in window for member in members), (
                    f"{path.name} instructs a re-stamp of `metadata.variety` "
                    f"without stating {group} within {self.WINDOW} lines of "
                    f"the instruction.\n"
                    f"A partial write to `variety` REPLACES the whole object "
                    f"and reports no error, so a lead reading this carrier "
                    f"alone can lose the fields it did not re-send.\n"
                    f"This arm accepts ANY of: {members}. It does not pin a "
                    f"wording. Reword freely, and keep the concept."
                )
