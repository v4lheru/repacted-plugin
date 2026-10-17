"""
Structural pin tests for the wake/idle message-ordering hardening rules.

Pins the reader-facing instruction surfaces that teach the one-channel rule
(task-system slice 1): every lead directive is on the task file before its
wake, and a wake is one line naming a task and a digest.

  teammate-side (skills/teammate/references/teammate-waiting.md):
    - "On Wake: Disk-First Re-Read (Seam-Agnostic)" — one class of wake:
      durable state is authoritative, the wake is only a signal, and values
      are read with repact-payload.
    - "Counter-Confirm Suppression" — fresh disk read before any
      state-clarification message; suppress when already resolved.
  lead-side (protocols/completion.md §Crossed-Wake Idles and
  agents/pact-orchestrator.md):
    - the four crossed-wake lines (on disk before the wake; an idle notice is
      never evidence about a directive; read the task file once; never
      re-send a value) and the no-synchronous-hook non-goal.
    - "Directive-Reflection Check" — mid-turn directives verified against
      boundary-message deliverables before acting.
  stall-detection (protocols/coordination.md):
    - an idle crossing a just-sent directive in either order, or a live
      intentional_wait, is a delivery-ordering artifact, not a stall.

The two-class wake (content-carrying against pure-signal) and the
timestamp-direction procedure are retired; the counter-test records at the
bottom of this file describe the pins as they stood before that.

PRESENCE pins, not counts. Unlike the Read-Trigger marker phrase (see
test_read_trigger_precondition_pinned.py EXPECTED_COUNTS), none of these
phrases is intended to recur a fixed number of times per surface, so count
pins would add lockstep-maintenance cost without catching a real erosion
shape. No new EXPECTED_COUNTS-style lockstep is introduced by this module.

PHRASES, not line shapes. Several pinned sentences are hard-wrapped in the
shipped markdown and several rule sentences share a line with pre-existing
list-item text (semantically identical markdown renderings), and tool
language inside a pinned span is inline-code formatted. Phrase pins
therefore match against backtick-AND-whitespace-NORMALIZED text (see
_phrase: strip backticks, then `" ".join(text.split())`) so they survive
re-wrapping and inline-code rendering alike — a retired rendering whose
backtick sits inside the phrase span ("`SendMessage` FIRST") is caught,
not masked. Heading pins match line-anchored raw lines
(per the section-presence convention in test_read_trigger_precondition_
pinned.py — substring matching for headings is a phantom-green shape: an
H4 line contains its H3 prefix as a substring).

Mirror discipline: pact-protocols.md is pinned as its own surface for every
phrase that lives in a byte-mirrored region (Completion Authority, Agent
Stall Detection). verify-protocol-extracts.sh enforces byte-parity upstream;
the duplicate pins here match the actual reader-facing surface set rather
than relying solely on the upstream script (same rationale as DOC_SURFACES
in test_read_trigger_precondition_pinned.py).

Counter-test-by-revert (verified at authoring time): with the five doc
surfaces reverted to their pre-hardening state (git checkout <pre-fix-ref>
-- <5 doc paths>), every test in this module fails EXCEPT the absence pins
(which also pass pre-fix only where the retired token was already absent;
the orchestrator absence pin goes RED pre-fix because the retired
Monitor-signal token was present there). Restore with git checkout HEAD --
<paths>. The exact flip-set cardinality observed at authoring time is
recorded in the module-level comment at the bottom of this file.
"""

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# TEAMMATE (m6): the teammate-side wake rules live in the teammate skill's waiting reference.
SKILL = PLUGIN_ROOT / "skills" / "teammate" / "references" / "teammate-waiting.md"
# the protocol consolidation: the completion-authority content moved to completion.md and the stall rule to
# coordination.md; the SSOT pact-protocols.md is deleted, so its mirror rows are gone.
COMPLETION_AUTHORITY = PLUGIN_ROOT / "protocols" / "completion.md"
CT_TEACHBACK = PLUGIN_ROOT / "protocols" / "pact-ct-teachback.md"
ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
AGENT_STALL = PLUGIN_ROOT / "protocols" / "coordination.md"

ALL_SURFACES = [SKILL, COMPLETION_AUTHORITY, ORCHESTRATOR, AGENT_STALL]


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phrase(text: str) -> str:
    """Phrase-matching normalization: strip backticks, then collapse any
    whitespace run (including newlines from hard-wrapping) to a single
    space. Tool language is inline-code formatted in the shipped markdown,
    so a pinned phrase must match a subject rendered with backticks inside
    the phrase span. Measured blindness this closes: the retired rendering
    "`SendMessage` FIRST" was invisible to the contiguous "SendMessage
    FIRST" absence token — the backtick sat between the token halves, and a
    whitespace-only normalizer keeps it there. Applied to BOTH sides (file
    text and pinned phrase) so phrases stored with or without backticks
    match consistently."""
    return " ".join(text.replace("`", "").split())


def _normalized(path: Path) -> str:
    """Backtick-and-whitespace-normalized file text for phrase pins: an
    intentional re-wrap of a rule sentence does not fail the pin while a
    re-WORD still does, and inline-code backticks inside a phrase span do
    not mask a retired rendering. Heading pins deliberately match RAW
    lines (see _lines_outside_fences) — backticks are part of the heading
    contract there and are NOT stripped."""
    return _phrase(_raw(path))


def _lines_outside_fences(path: Path) -> list:
    """Stripped lines of the file, excluding fenced-code-block content and
    the fence delimiter lines themselves. A heading-shaped line inside a
    ``` / ~~~ fence is example text, not a real section heading, and must
    not satisfy a heading pin (a section deletion that leaves behind a
    fenced example of its own heading would otherwise stay green)."""
    lines = []
    in_fence = False
    for line in _raw(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(stripped)
    return lines


# ---------------------------------------------------------------------------
# Heading pins — line-anchored exact match.
# ---------------------------------------------------------------------------

HEADING_PINS = [
    (SKILL, "### On Wake: Disk-First Re-Read (Seam-Agnostic)"),
    (SKILL, "### Counter-Confirm Suppression"),
    (SKILL, "## Boundary-Drain Rule"),
    # the protocol consolidation: the plan §5 anchors are H2 now.
    (COMPLETION_AUTHORITY, "## Crossed-Wake Idles"),
    (COMPLETION_AUTHORITY, "## Directive-Reflection Check"),
    # The orchestrator persona carries the lead-side check as one sentence
    # of its Teachback Review section, linked to the heading above (no
    # heading of its own since the persona slim).
]




# ---------------------------------------------------------------------------
# Phrase pins — whitespace-normalized presence.
# ---------------------------------------------------------------------------

PHRASE_PINS = [
    # One channel (task-system slice 1): every lead directive is on the task file before its
    # wake, and a wake is one line with a task id and a digest. The pins for the retired
    # two-class wake (content-carrying against pure-signal) and for the timestamp-direction
    # procedure (straggler, redundant confirm, two idle ticks) left with that prose.
    # --- teammate-side reference ---
    (SKILL, "Durable state is authoritative"),
    (SKILL, "Durable state is authoritative; the wake is only a signal"),
    (SKILL, "every wake is a signal that the task file changed"),
    (SKILL, "never carries a value"),
    # A wake naming a write the disk does not show yet gets a re-read, never an action.
    (SKILL, "never act on a single empty read"),
    (SKILL, "re-read once after a brief pause; never act on a single empty read"),
    (SKILL, "in-process and tmux teammateMode"),
    # The wait flag has a lead-side consumer.
    (SKILL, "missed_wake_scan"),
    # Counter-confirm suppression: a resolved state gets no clarification message.
    (SKILL, "send NOTHING"),
    (SKILL, "Durable state IS the reply"),
    # A deliverable that predates a crossed directive is revised without being asked.
    (SKILL, "revise it on the same task without waiting to be asked"),
    # --- lead-side completion protocol: the four crossed-wake lines and the non-goal ---
    (COMPLETION_AUTHORITY, "Every directive is on disk before its wake"),
    (COMPLETION_AUTHORITY, "An idle notice is never evidence about a directive"),
    (COMPLETION_AUTHORITY, "On doubt, read the task file once"),
    (COMPLETION_AUTHORITY, "Never re-send a value"),
    (COMPLETION_AUTHORITY, "no synchronous hook detects a send"),
    (COMPLETION_AUTHORITY, "missed_wake_scan"),
    (COMPLETION_AUTHORITY, "Disk is the only copy"),
    (COMPLETION_AUTHORITY, "Delivery is not processing"),
    (COMPLETION_AUTHORITY, "The notify SendMessage is the content-arrival signal"),
    # --- orchestrator persona ---
    (ORCHESTRATOR, "a directive is on disk before its wake"),
    (ORCHESTRATOR, "never re-send a value"),
    (ORCHESTRATOR, "never accelerate nudging in response to idle ticks"),
    (ORCHESTRATOR, "wait for teammate's wake-signal SendMessage before the raw read"),
    (ORCHESTRATOR, "missed_wake_scan"),
    (ORCHESTRATOR, "tick every amend_* key off against handoff.amendments"),
    # --- stall detection ---
    (AGENT_STALL, "delivery-ordering artifacts, not stalls"),
    (AGENT_STALL, "task-file-mtime plus sustained-silence"),
    (AGENT_STALL, "crosses a directive you just sent in either order"),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    PHRASE_PINS,
    ids=[f"{p.name}::{ph[:40]}" for p, ph in PHRASE_PINS],
)
def test_rule_phrase_present(doc_path: Path, phrase: str):
    """Each load-bearing rule phrase must be present on its surface.
    Matching is whitespace-normalized: hard-wrap and same-line-rider
    renderings both satisfy the pin; a re-WORD does not. If a phrase was
    changed intentionally, update the pin in lockstep — otherwise the rule
    has eroded on a surface an LLM loads at runtime."""
    normalized_phrase = _phrase(phrase)
    assert normalized_phrase in _normalized(doc_path), (
        f"{doc_path.name}: rule phrase {phrase!r} not found "
        f"(backtick-and-whitespace-normalized match). If the wording was changed "
        f"intentionally, update this pin in lockstep; otherwise the "
        f"wake-ordering rule this phrase carries is missing from a "
        f"runtime-loaded surface."
    )


# ---------------------------------------------------------------------------
# Cross-ref pins — literal anchor slugs (what markdown actually navigates to).
# ---------------------------------------------------------------------------

CROSSED_WAKE_SLUG = "#crossed-wake-idles-discriminate-by-timestamp-direction"
DIRECTIVE_REFLECTION_SLUG = "#directive-reflection-check"
ON_WAKE_SLUG = "#on-wake-disk-first-re-read-seam-agnostic"
BOUNDARY_DRAIN_SLUG = "#boundary-drain-rule"

CROSS_REF_PINS = [
    # Orchestrator persona lazy-loads the full rules from the protocol.
    (ORCHESTRATOR, CROSSED_WAKE_SLUG),
    (ORCHESTRATOR, DIRECTIVE_REFLECTION_SLUG),
    # SKILL forward-links the teammate-side rules to the lead-side rule and
    # to its own sections.
    (SKILL, CROSSED_WAKE_SLUG),
    (SKILL, ON_WAKE_SLUG),
    (SKILL, BOUNDARY_DRAIN_SLUG),
    # Completion-authority links back to the teammate-side complements.
    (COMPLETION_AUTHORITY, ON_WAKE_SLUG),
    (COMPLETION_AUTHORITY, BOUNDARY_DRAIN_SLUG),
    # Stall protocol delegates the crossed-wake handling to the rule.
    (AGENT_STALL, CROSSED_WAKE_SLUG),
]




# ---------------------------------------------------------------------------
# Anchor-slug integrity — pinned headings must slugify to the slugs the
# referrers actually use, so a heading rename cannot silently strand them.
# ---------------------------------------------------------------------------


def _github_slug(heading: str) -> str:
    """GitHub-flavored-markdown anchor slug for a heading line: strip the
    leading hashes, lowercase, drop everything but alphanumerics, spaces,
    and hyphens, then hyphenate spaces."""
    text = heading.lstrip("#").strip().lower()
    kept = "".join(ch for ch in text if ch.isalnum() or ch in " -")
    return "#" + kept.replace(" ", "-")


ANCHOR_INTEGRITY = [
    ("### Crossed-Wake Idles: Discriminate by Timestamp Direction", CROSSED_WAKE_SLUG),
    ("### Directive-Reflection Check", DIRECTIVE_REFLECTION_SLUG),
    ("### On Wake: Disk-First Re-Read (Seam-Agnostic)", ON_WAKE_SLUG),
    ("## Boundary-Drain Rule", BOUNDARY_DRAIN_SLUG),
]


@pytest.mark.parametrize(
    "heading, expected_slug",
    ANCHOR_INTEGRITY,
    ids=[s.lstrip("#")[:40] for _, s in ANCHOR_INTEGRITY],
)
def test_heading_slugifies_to_referenced_anchor(heading: str, expected_slug: str):
    """The pinned heading text must derive exactly the anchor slug the
    referrer surfaces use. Combined with the heading-presence and
    slug-presence pins above, this closes the rename loop: a heading
    rename fails here unless every referrer moves in lockstep."""
    assert _github_slug(heading) == expected_slug, (
        f"Heading {heading!r} slugifies to {_github_slug(heading)!r} but "
        f"referrers link {expected_slug!r} — the cross-refs would be 404 "
        f"nav targets."
    )


# ---------------------------------------------------------------------------
# Absence pin — retired Monitor-signal token.
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Absence pin — retired "wake-send" send-term.
# ---------------------------------------------------------------------------

# The surfaces that carried the retired term before the vocabulary
# unification. The teammate-side SKILL and the stall protocol never used it
# (verify-only surfaces for the sweep), so the absence set is the three
# swept surfaces, not ALL_SURFACES.
SWEPT_SURFACES = [COMPLETION_AUTHORITY, ORCHESTRATOR]




# ---------------------------------------------------------------------------
# Acceptance-pair ordering pins — TaskUpdate-first two-call atomic pair.
# ---------------------------------------------------------------------------

# The five command files teach the teachback-acceptance one-sentence form of
# the pair. They join the absence set (the retired ordering tokens must not
# reappear there) but not the presence set — the commands' sentence form is
# "`TaskUpdate(A, status="completed")` FIRST", which does not contain the
# contiguous "TaskUpdate FIRST" token the normative surfaces carry.
COMMAND_SURFACES = [
    PLUGIN_ROOT / "commands" / "orchestrate.md",
    PLUGIN_ROOT / "commands" / "peer-review.md",
    PLUGIN_ROOT / "commands" / "plan-mode.md",
    PLUGIN_ROOT / "commands" / "comPACT.md",
    PLUGIN_ROOT / "commands" / "rePACT.md",
]

ORDERING_PRESENCE_SURFACES = [COMPLETION_AUTHORITY, ORCHESTRATOR]
ORDERING_ABSENCE_SURFACES = ORDERING_PRESENCE_SURFACES + COMMAND_SURFACES

# The ct-teachback extract (and its byte-mirrored teachback-flow region in
# the SSOT) taught the pair in the SAME retired call-syntax rendering as the
# commands — wake-`SendMessage` FIRST, then `TaskUpdate(A, ...)`. Neither
# surface carries the contiguous ordering tokens the arms above lock, and
# the extract and its SSOT region can revert to the retired rendering in
# LOCKSTEP without tripping the byte-parity audit (extract and region stay
# mutually byte-equal), so this arm is the only witness on those surfaces.
# Fragment verified green on both at the flipped HEAD and present in both
# pre-flip forms (whitespace-normalized; the retired rendering hard-wraps
# mid-fragment).
COMMAND_FORM_SURFACES = COMMAND_SURFACES + [CT_TEACHBACK]








# ---------------------------------------------------------------------------
# Counter-test flip-set record (measured at authoring time; see module
# docstring). With the five surfaces reverted to their pre-hardening state
# and this module run against them: {53 failed, 8 passed}. Heading pins
# 8/8 RED, phrase pins 33/33 RED (no pinned phrase pre-existed on any
# surface), cross-ref pins 11/11 RED, absence pin RED on
# pact-orchestrator.md (retired token present pre-fix) and GREEN on the
# other four surfaces (token never present there). The 8 GREEN =
# anchor-integrity 4/4 (pure functions of module constants — intentionally
# revert-immune) + the 4 vacuously-satisfied absence pins.
# Section-body deletion probes (measured after the body-pin additions):
# gutting the Counter-Confirm Suppression body while keeping its heading
# fails exactly its 2 body pins; deleting the On Wake residual-race step
# fails exactly the rule-home span pin while the shorter single-empty-read
# pin stays green via the On-Rejection parenthetical.
# Matcher-robustness probes (measured): rewriting a referrer's slug to a
# longer slug that prefix-engulfs the pinned one flips the slug pin RED
# (terminator guard); deleting a section heading while leaving a fenced
# code example of the same heading line flips the heading pin RED
# (fence exclusion). Neither hardening changes the flip-set above.
#
# 2026-08-27 addendum (pre-directive idle-straggler cycle): the phrase-pin
# inventory above grew 33 (authoring) -> 39 (#1525 rule pins: straggler
# classification, bidirectional opener, anti-acceleration x both mirrored
# surfaces) -> 50 (remediation cycle 1: ambiguity durable-read + claim-absent
# fallthrough + two-idle gate + straggler-exclusion x both mirrored surfaces,
# stall-exemption bidirectional clause x stall + SSOT, persona straggler
# outcome). Module cases 61 -> 67 -> 78. Counter-test for the #1525 pins
# (measured 2026-08-27): identical both-surface reword of the straggler
# phrase — extract/SSOT byte-parity preserved, so the audit gate stays
# green and this module is the sole red source — flips exactly the 2
# per-surface pins for that phrase (predicted pre-run; observed exactly
# 2 failed). All 17 post-authoring phrase pins go RED under a pre-#1525
# revert: none of their phrases pre-existed on any surface
# (occurrence-verified at pin time).
#
# 2026-08-27 addendum (#1530 send-vocabulary unification cycle): the
# retired "wake-send" term was unified to "directive send" across the
# three surfaces that carried it (completion-authority extract, its SSOT
# region, orchestrator persona §5/§12); the teammate SKILL and stall
# protocol never carried it. Phrase pins 50 -> 53 (unified postdating
# send-term x 3 surfaces) and a new retired-term absence test x 3 swept
# surfaces; module cases 78 -> 84. PHANTOM-GREEN CAUGHT BY MEASUREMENT:
# the short pin "postdates your directive send" is satisfied pre-revert
# on both mirrored surfaces by the #1529 bidirectional opener's own
# sentence ("When the notification postdates your directive send"), so
# the mirrored-surface pins anchor to the longer postdating-item span
# ("On an idle notification that postdates your directive send"), which
# exists only at the reworded item. Counter-test (measured 2026-08-27,
# docs reverted to pre-unification in an isolated copy): exactly 6 failed
# = the 3 unified-term presence pins + the 3 retired-term absence cases;
# 78 passed; post-unification 84/84 green.
#
# 2026-08-28 addendum (acceptance-pair flip cycle): the lead-side
# acceptance/rejection two-call atomic pair was flipped from
# SendMessage-first to TaskUpdate-first across 9 files (persona, 5
# commands, completion-authority extract, ct-teachback extract, SSOT
# mirrors). New arm: 3 "TaskUpdate FIRST" presence cases (normative
# surfaces) + 8 retired-ordering absence cases (3 normative + 5
# commands); module cases 84 -> 95. Counter-test (measured 2026-08-28,
# 9 doc files stash-reverted to pre-flip): exactly 6 failed = the 3
# presence cases + the absence cases on the 3 normative surfaces (the
# retired tokens pre-existed there); the 5 command absence cases stayed
# GREEN pre-flip (the commands' retired form was
# "`SendMessage(to=X, ...)` FIRST" — not the contiguous token this pin
# locks out), so they guard reintroduction, not the flip itself.
# Post-flip 95/95 green.
#
# 2026-08-28 follow-up (TEST-phase, lead-ordered): the gap noted above —
# the command surfaces' own retired call-syntax rendering was unpinned —
# closed by test_retired_command_call_ordering_absent: 5 absence cases
# over COMMAND_SURFACES locking the fragment "FIRST, then `TaskUpdate"
# (the retired rendering's load-bearing sequence; the flipped form cannot
# produce it). Discriminator verified against BOTH forms before pinning:
# present in all 5 pre-flip files, absent from all 5 flipped files and
# every other instruction file. Module cases 95 -> 100. Counter-test
# (measured 2026-08-28): orchestrate.md alone reverted to pre-flip —
# exactly 1 failed = this test's orchestrate.md case (the 8 sibling
# ordering cases stayed green, as the original addendum predicted);
# post-restore 100/100 green.
#
# 2026-08-28 review-cycle extension (review-test fixer, lead-ordered): the
# same retired call-syntax rendering was unpinned on the ct-teachback
# extract and its byte-mirrored SSOT region — surfaces in NO arm of this
# module — and a LOCKSTEP revert of the pair leaves extract and region
# mutually byte-equal, so the protocol-extract audit passes too (measured
# during review: module + audit green with both reverted). The arm now
# parametrizes over COMMAND_FORM_SURFACES (5 commands + ct-teachback +
# SSOT): module cases 100 -> 102. Fragment discriminator verified in
# whitespace-normalized text (the retired rendering hard-wraps mid-fragment,
# so a raw line grep reads absent on both sides): present at base on both
# new surfaces, absent at flipped HEAD on both. Counter-test (measured
# 2026-08-28): ct-teachback.md alone reverted to pre-flip — exactly 1
# failed = this test's pact-ct-teachback.md case (101 passed); post-restore
# 102/102 green, audit module green alongside.
#
# 2026-08-28 cycle 2 (review-test, lead-ordered, user-directed EXCL-1
# adoption): _normalized now strips backticks via _phrase (applied to BOTH
# the file text and every pinned phrase), closing the rendering class where
# a backtick sits inside a phrase span — the retired "`SendMessage` FIRST"
# form was invisible to the contiguous absence token under a
# whitespace-only matcher. Full pin audit at HEAD under the new matcher:
# 79 pin x surface verdicts compared (53 phrase pins + 3 ordering-presence
# + 16 ordering-absence + 7 command-form) — ZERO verdict changes; module
# 102/102 green before and after. Counter-tests in a TEMP CLONE (no
# live-worktree mutation), all predictions exact: cell 1 — minimal
# backticked form "`SendMessage` FIRST" appended to orchestrate.md: OLD
# matcher 102 passed (blind), NEW matcher exactly 1 failed =
# ordering-absence[orchestrate.md]; cell 2 — full retired rendering
# wake-`SendMessage` FIRST, then `TaskUpdate(A, ...)`: NEW matcher exactly
# 2 failed (ordering-absence + command-form, both orchestrate.md), OLD
# matcher exactly 1 failed (command-form only — the measured pre-change
# blindness). Heading/slug/anchor/INBOX_GREW/wake-send pins match raw
# text or pure functions by design and are untouched.
# ---------------------------------------------------------------------------
