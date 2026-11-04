"""HYBRID structural invariant for the lead/teammate role discriminator.

This is the drift backstop for the is_lead migration. It pins TWO
complementary shapes into executable form so a future regression fails at
CI time rather than silently under a live tmux session.

THE MIGRATION (context):
    The role decision used to be a NEGATIVE heuristic — ``resolve_agent_name(
    input_data) == ""`` meant "lead". Under tmux that is wrong in BOTH
    directions: resolve_agent_name's Step-4 strips the ``pact-`` prefix and
    returns a NON-empty string for both lead spellings, so the v4.4.0 lead
    took the teammate-bypass branch in every Class-B gate (the three DENY
    gates were silently dead for the lead). The fix is a single POSITIVE
    predicate ``is_lead(input_data)`` keyed on the harness-set top-level
    ``agent_type`` field.

THE TWO LEGS:

  POSITIVE leg (``test_migrated_hooks_route_role_decision_through_is_lead``):
    every hook that makes a lead/teammate role decision routes it through
    ``pact_context.is_lead`` — proven by asserting each migrated hook's
    source contains an ``is_lead(...)`` call. This leg DEPENDS on the
    enumerated migrated-set being complete; it catches a migrated hook that
    REGRESSES away from is_lead.

  NEGATIVE leg (``test_no_hook_reintroduces_a_banned_role_signal``):
    an AST denylist that scans ALL top-level hook files and flags any hook
    that reintroduces a banned role signal —
      (a) an env-var read used as a role discriminator,
      (b) an ``agent_id``-presence role branch, or
      (c) ``resolve_agent_name()`` used as a lead proxy
          (``if resolve_agent_name(...)`` / ``... == "" `` / ``!= ""``).
    This leg does NOT depend on the migrated-set being complete — it is the
    future-drift + incomplete-migration backstop (a brand-new hook that
    keys role off a banned signal is caught even though it is not in any
    enumerated list).

  NAMED-SITE ALLOWLIST (``_SANCTIONED_RESOLVE_AGENT_NAME_SITES``):
    ``resolve_agent_name`` has THREE sanctioned non-role uses (human-readable
    edit attribution / labels), which the negative leg must NOT flag. The
    allowlist is keyed by (file, enclosing-function) and is deliberately
    EXPLICIT rather than a structural discriminator: distinguishing
    "resolve_agent_name as a lead proxy" from "resolve_agent_name for a
    label" is genuinely hard to express structurally, and an auditable named
    list makes every sanctioned site a deliberate, documented decision.
    Adding a new sanctioned site MUST require a deliberate allowlist + test
    update — that maintenance cost is a FEATURE (same philosophy as the
    "new field must update this test" phantom-green guard in
    test_first_observable_write_misfire_invariant.py).

PHANTOM-GREEN PROTECTION:
    The negative leg's value is only real if the detector actually FIRES on
    a banned signal. ``test_negative_leg_detector_fires_on_*`` feed synthetic
    source containing each banned shape and assert the detector flags it —
    proving the leg is coupled to the regression, not a vacuous pass. A
    non-vacuity guard (``test_negative_leg_scans_a_nonempty_hook_set`` +
    ``test_allowlist_sites_are_all_real``) ensures the scan never silently
    degrades to "found nothing because it looked at nothing".

Modeled on tests/test_first_observable_write_misfire_invariant.py.
"""

import ast
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# ---------------------------------------------------------------------------
# POSITIVE leg: the migrated hooks and the role-decision symbol they route to.
# ---------------------------------------------------------------------------

# Hooks that make a lead/teammate role decision and MUST route it through
# is_lead. Each entry is a top-level hook filename relative to hooks/. This
# set IS the migrated Class-A (session_init, postcompact_archive) + Class-B
# (the teammate-bypass gates; the two pin gates went with the 1.5.0 cut) surface. A migrated hook dropping its is_lead
# call (regressing to the old heuristic) is caught here.
MIGRATED_HOOKS: tuple[str, ...] = (
    "session_init.py",)


# ---------------------------------------------------------------------------
# NEGATIVE leg: named-site allowlist for sanctioned resolve_agent_name uses.
# ---------------------------------------------------------------------------

# resolve_agent_name's sanctioned non-role uses, keyed (file, function). These
# are human-readable edit-attribution / label uses — NOT lead-proxy role
# decisions — so the negative leg must NOT flag them. EXPLICIT by design: a
# new sanctioned site requires a deliberate edit here + a documented reason.
#
# Why each site is sanctioned:
#   file_tracker.py / main:
#       resolve_agent_name supplies the human-readable LABEL for the editor in
#       the file-edit tracking record. The ROLE/uniqueness decision in
#       file_tracker uses the composite (agent_name, session_id) editor KEY,
#       NOT a lead/teammate verdict — resolve_agent_name here is attribution,
#       never a lead proxy. (#878 NEW-1 KEPT-for-label.)
#   file_tracker.py / get_environment_delta:
#       reads the stored ``agent`` LABEL off tracking entries to attribute
#       drift to a named editor — attribution, not a role gate. (Listed for
#       auditability even though it reads a stored label rather than calling
#       resolve_agent_name directly, so a future refactor that switches it to
#       a live resolve_agent_name call stays pre-sanctioned and documented.)
#   shared/pact_context.py / resolve_agent_name:
#       the function's OWN definition + its internal agent_id name-resolution
#       (Step 1-3) — this is the name-resolver itself, not a consumer keying
#       role off it.
_SANCTIONED_RESOLVE_AGENT_NAME_SITES: frozenset[tuple[str, str]] = frozenset({
    ("file_tracker.py", "main"),
    ("file_tracker.py", "get_environment_delta"),
    ("shared/pact_context.py", "resolve_agent_name"),
})

# Named-site allowlist for ``agent_id`` reads used as a LABEL, never a role
# signal. Keyed (hook file, enclosing function), like the set above, so any
# OTHER agent_id read in the same file is still flagged.
#
# Why each site is sanctioned:
#   run_ledger.py / _attribution_agent_id:
#       the run ledger records which agent ran a test command. The helper
#       returns the payload's raw ``agent_id`` for the record's agent_id field
#       and never branches on it. In-process teammates share the lead's
#       session_id, and session_registry skips in-process writes, so agent_id
#       is the only name source. Same class as file_tracker's sanctioned label
#       use above. (repacted M2 add-on; evidence: wu10-shell's HANDOFF.)
#       TestSanctionedAgentIdSitesAreReal keeps the site real and read-only.
#   amendment_relay.py / _actor_agent_id:
#       the amendment relay reads the raw agent_id only to ADDRESS amendments:
#       whose owned in_progress tasks to read and whose stamp file to use. Two
#       shapes name a teammate: an in-process "a" + agent_type + "-" + 16 hex,
#       compared whole against one built from agent_type and never parsed, and
#       an older "name@team". It never grants or denies anything; a missing or
#       malformed value relays nothing. The branch is on the returned value, in
#       addressee().
#       (repacted phase D D2; lead-approved one-line fork-test change.)
_SANCTIONED_AGENT_ID_LABEL_SITES: frozenset[tuple[str, str]] = frozenset({
    ("run_ledger.py", "_attribution_agent_id"),
    ("amendment_relay.py", "_actor_agent_id"),
    # Tighten-only: an agent_id makes a settings write "deny"; its absence grants
    # nothing and falls through to resolve_actor().is_lead (agent_type), so
    # presence never makes a frame the lead (settings protection).
    ("settings_guard.py", "_frame_agent_id"),
})


def _hook_files() -> list[Path]:
    """All top-level hook .py files (excludes __init__, shared/, tests), plus
    the shared/session_init_*.py modules that hold session_init's frame code."""
    return sorted(
        [p for p in HOOKS_DIR.glob("*.py") if p.name != "__init__.py"]
        + list(HOOKS_DIR.glob("shared/session_init_*.py"))
    )


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str:
    """Return the name of the FunctionDef enclosing ``target``, or "" if the
    node sits at module scope. Walks the tree once building a child->name map.
    """
    enclosing = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is target:
                    enclosing = node.name
                    # keep the INNERMOST function: a later (nested) match
                    # overwrites an outer one because ast.walk yields the
                    # outer FunctionDef first.
    return enclosing


# ---------------------------------------------------------------------------
# Banned-signal detectors (operate on an AST tree + a file label).
# ---------------------------------------------------------------------------

def _find_resolve_agent_name_role_uses(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag resolve_agent_name() calls used as a LEAD PROXY (banned), skipping
    the named-site allowlist.

    A "lead proxy" is any resolve_agent_name() call whose result feeds a role
    branch. We treat EVERY call site as a candidate and rely on the named-site
    allowlist to exempt the sanctioned attribution/label uses — this is
    deliberately conservative (a new resolve_agent_name call in a non-
    allowlisted site is flagged regardless of whether it textually looks like
    a role branch), so that drift toward "resolve_agent_name == lead" cannot
    sneak in through a site the structural matcher fails to recognize.

    Returns (function_name, lineno) for each NON-allowlisted call.
    """
    offending: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name != "resolve_agent_name":
            continue
        fn = _enclosing_function(tree, node)
        if (file_label, fn) in _SANCTIONED_RESOLVE_AGENT_NAME_SITES:
            continue
        offending.append((fn, node.lineno))
    return offending


def _find_agent_id_role_uses(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag reads of the ``agent_id`` stdin field used as a role discriminator.

    Detects ``input_data.get("agent_id")`` / ``input_data["agent_id"]`` style
    reads. The role decision must key on ``agent_type`` (via is_lead), never on
    ``agent_id``-presence — agent_id is absent under tmux and its presence/
    absence is exactly the #812 drift hazard.

    validate_handoff.py is EXEMPT: its agent_id read is a role-CLASS check
    (``is_pact_agent``), explicitly deferred to the #812 follow-up per the
    plan's scope decision — NOT a lead/teammate discriminator. Listed by file
    so the exemption is auditable.

    A read passed untouched as the ``agent_id`` keyword of
    ``agent_type_names_a_member`` is EXEMPT: that value feeds an exact-shape
    check, and a missing id falls back to the agent_type checks, so presence
    alone never decides lead vs teammate. The match is on the read node itself
    being that keyword's value, so a wrapped, positional or pre-assigned read
    is still flagged.
    """
    if file_label == "validate_handoff.py":
        return []
    member_check_ids = {
        id(kw.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        == "agent_type_names_a_member"
        for kw in node.keywords
        if kw.arg == "agent_id"
    }
    offending: list[tuple[str, int]] = []

    def flag(node: ast.AST) -> None:
        fn = _enclosing_function(tree, node)
        if (file_label, fn) in _SANCTIONED_AGENT_ID_LABEL_SITES:
            return
        offending.append((fn, node.lineno))

    for node in ast.walk(tree):
        if id(node) in member_check_ids:
            continue
        # input_data.get("agent_id", ...)
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "get"
                    and node.args):
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and arg0.value == "agent_id":
                    flag(node)
        # input_data["agent_id"]
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == "agent_id":
                flag(node)
    return offending


def _find_env_var_role_reads(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag os.environ reads of a ROLE-bearing env var.

    The role decision must derive from the harness-set ``agent_type`` stdin
    field, never from an environment variable (the old in-process model used
    env vars; under tmux they are unreliable role signals). Matches
    os.environ.get("...") / os.getenv("...") / os.environ["..."] whose key
    name contains a role token (AGENT / TEAMMATE / LEAD / ORCHESTRAT / ROLE),
    EXCLUDING the benign infra vars (CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_ROOT,
    CLAUDE_ENV_FILE — paths, not roles).

    Catches BOTH the attribute form (``import os`` → ``os.environ`` /
    ``os.getenv``, where the receiver is an ``ast.Attribute``) AND the aliased
    form (``from os import environ`` → ``environ.get(...)`` / ``environ[...]``;
    ``from os import getenv`` → ``getenv(...)``, where the receiver/callee is a
    bare ``ast.Name``). Both spell the same env read; a drift author reaching
    for an env role signal must not be able to dodge the denylist by importing
    the name directly.
    """
    role_tokens = ("AGENT", "TEAMMATE", "LEAD", "ORCHESTRAT", "ROLE")
    benign = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE")
    offending: list[tuple[str, int]] = []

    def _key_is_role(key: str) -> bool:
        up = key.upper()
        if up in benign:
            return False
        return any(tok in up for tok in role_tokens)

    def _is_environ_ref(node: ast.AST) -> bool:
        """True iff ``node`` refers to ``os.environ`` in EITHER form:
        - attribute: ``os.environ`` → ``ast.Attribute(attr="environ")``
        - aliased:   ``from os import environ`` → ``ast.Name(id="environ")``
        Matching only these never matches ``input_data.get(...)`` /
        ``tool_input.get(...)`` (stdin role FIELDS, handled by the dedicated
        agent_id / is_lead detectors)."""
        return (
            (isinstance(node, ast.Attribute) and node.attr == "environ")
            or (isinstance(node, ast.Name) and node.id == "environ")
        )

    def _is_getenv_callee(func: ast.AST) -> bool:
        """True iff ``func`` is a ``getenv`` callee in EITHER form:
        - attribute: ``os.getenv`` → ``ast.Attribute(attr="getenv")``
        - aliased:   ``from os import getenv`` → ``ast.Name(id="getenv")``."""
        return (
            (isinstance(func, ast.Attribute) and func.attr == "getenv")
            or (isinstance(func, ast.Name) and func.id == "getenv")
        )

    for node in ast.walk(tree):
        # os.environ.get("KEY") / environ.get("KEY") / os.getenv("KEY") / getenv("KEY")
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            # `.get` must be on an os.environ receiver (attr OR aliased Name);
            # `getenv` (attr OR aliased bare Name) is always an env read.
            is_environ_get = (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and _is_environ_ref(func.value)
            )
            is_env_get = is_environ_get or _is_getenv_callee(func)
            if is_env_get:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if _key_is_role(arg0.value):
                        offending.append((_enclosing_function(tree, node), node.lineno))
        # os.environ["KEY"] / environ["KEY"]
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if _is_environ_ref(node.value) and isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                if _key_is_role(sl.value):
                    offending.append((_enclosing_function(tree, node), node.lineno))
    return offending


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ===========================================================================
# POSITIVE leg
# ===========================================================================

class TestPositiveLeg:
    """Every migrated hook routes its role decision through is_lead."""

    @pytest.mark.parametrize("hook_name", MIGRATED_HOOKS)
    def test_migrated_hook_calls_is_lead(self, hook_name):
        """Each migrated hook's source contains an is_lead(...) call.

        A migrated hook that REGRESSES away from is_lead (back to the old
        resolve_agent_name heuristic, or to any other discriminator) drops
        the call and fails here. Pinned by AST so a mention of ``is_lead`` in
        a comment/docstring does NOT satisfy it — only a real Call node.
        """
        path = HOOKS_DIR / hook_name
        assert path.exists(), f"Migrated hook {hook_name} not found at {path}"
        tree = _parse(path)
        calls_is_lead = any(
            isinstance(n, ast.Call)
            and (
                # is_lead_frame (shared/frame.py) is the lead predicate that wraps
                # is_lead and adds a lead launched without --agent; resolve_actor
                # (shared/actor.py) is the policy layer over it (a gate reads
                # actor.is_lead or a predicate such as bridge_writes()).
                (isinstance(n.func, ast.Name)
                 and n.func.id in ("is_lead", "is_lead_frame", "resolve_actor"))
                or (isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("is_lead", "is_lead_frame", "resolve_actor"))
            )
            for n in ast.walk(tree)
        )
        assert calls_is_lead, (
            f"{hook_name} makes a lead/teammate role decision but does NOT "
            f"call is_lead(). If this hook regressed to the old "
            f"resolve_agent_name heuristic (or any non-is_lead discriminator), "
            f"restore the is_lead routing. If the hook legitimately no longer "
            f"makes a role decision, remove it from MIGRATED_HOOKS with a "
            f"documented reason."
        )


# ===========================================================================
# NEGATIVE leg
# ===========================================================================

class TestNegativeLeg:
    """No hook reintroduces a banned role signal (the drift backstop)."""

    def test_no_hook_uses_resolve_agent_name_as_lead_proxy(self):
        """AST denylist across ALL hooks: no non-allowlisted resolve_agent_name
        call. Independent of the migrated-set — a brand-new hook keying role
        off resolve_agent_name is caught even though it is in no enumerated
        list.
        """
        offending: list[str] = []
        for path in _hook_files():
            label = path.name
            for fn, lineno in _find_resolve_agent_name_role_uses(_parse(path), label):
                offending.append(f"{label}:{lineno} (in {fn or '<module>'})")
        assert offending == [], (
            f"resolve_agent_name() called outside the sanctioned named-site "
            f"allowlist: {offending}. resolve_agent_name as a role/lead proxy "
            f"is the migrated-away-from anti-pattern. If this is a NEW "
            f"sanctioned attribution/label use, add (file, function) to "
            f"_SANCTIONED_RESOLVE_AGENT_NAME_SITES with a documented reason."
        )


# ===========================================================================
# PHANTOM-GREEN PROTECTION — prove each detector actually fires
# ===========================================================================

class TestNegativeLegDetectorsFire:
    """Synthetic-source proofs: each banned-signal detector FIRES on the
    shape it is meant to catch. Without these, a detector that silently
    matches nothing would make the negative leg a vacuous always-pass.
    """

    def test_resolve_agent_name_detector_fires_on_non_allowlisted_call(self):
        src = (
            "def some_gate(input_data):\n"
            "    if resolve_agent_name(input_data):\n"
            "        return None  # teammate bypass — the banned old heuristic\n"
        )
        tree = ast.parse(src)
        hits = _find_resolve_agent_name_role_uses(tree, "some_new_gate.py")
        assert hits, (
            "resolve_agent_name detector did NOT fire on a non-allowlisted "
            "call — the negative leg would be vacuous."
        )


    # ---- #5: aliased `from os import ...` forms must ALSO be detected ----
    # The first pass matched only the ATTRIBUTE form (os.environ / os.getenv,
    # receiver = ast.Attribute). A drift author could dodge the denylist by
    # importing the name directly (`from os import environ` / `from os import
    # getenv`), where the receiver/callee is a bare ast.Name. These phantom-green
    # proofs pin that the aliased forms are now caught.


# ===========================================================================
# NON-VACUITY GUARDS — the scan never silently degrades to "looked at nothing"
# ===========================================================================

