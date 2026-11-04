"""Merge-gate structural invariants for the self-registration registry (#885).

These are the CROSS-FILE / STRUCTURAL contracts that the registry's own unit
suite (test_session_registry.py) cannot express:

  * TRUST PARTITION (load-bearing security): the registry is LABELING-ONLY. The
    authority surfaces — trustworthy_actor_name (dispatch_helpers) + the
    completion and caller checks (task_write_guard, ops_frame_guard) — MUST NOT import or call the
    registry resolver. The registry value is self-asserted/forgeable; feeding it
    to a who-acted/is-allowed check re-opens the confused-deputy hole that the
    harness-managed agent_id signal closes. Enforced by an AST no-import test
    modeled on test_lead_discriminator_invariant.py.
  * SELF-LOOKUP-ONLY: resolve() takes a session_id, never a name; there is NO
    name-keyed lookup API (a name-keyed scan would let a reader forge another
    agent's identity). Structural.
  * SANITIZER-PARITY: the inlined session_registry._sanitize_agent_name
    char-class is byte-identical to peer_context._sanitize_agent_name (the two
    copies must stay in sync for write/read parity). Catches drift between the
    inline copy and the source.
  * PATH-NOT-TEAM-SCOPED: REGISTRY_PATH is the global fixed team-agnostic path
    under pact-sessions/, NEVER under teams/ (a team-scoped path re-opens the
    bootstrap paradox a tmux teammate cannot resolve).
  * BOTH-MODES MATRIX: the dual-mode teammateMode contract — register/resolve
    behave correctly under in-process (session_id == leadSessionId) AND tmux
    (session_id != leadSessionId) topologies.

Phantom-green protection: the AST no-import detector is proven to FIRE on a
synthetic import so the negative leg is not a vacuous always-pass.
"""

import ast
import json
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"

# The authority surfaces that MUST NOT consume the registry resolver. Keyed by
# the file (relative to hooks/) whose import graph is scanned. dispatch_helpers
# hosts trustworthy_actor_name; task_write_guard refuses a completion and ops_frame_guard
# judges the caller of an op, both from the host payload.
_AUTHORITY_FILES = (
    "shared/dispatch_helpers.py",)

_REGISTRY_MODULE_TOKENS = ("session_registry",)
_REGISTRY_SYMBOL_TOKENS = ("_registry_resolve", "resolve")  # the resolver alias / symbol

# Forbidden callee names in the authority files. An authority surface must call
# NEITHER the registry resolver DIRECTLY (_registry_resolve) NOR a labeling
# resolver that reaches it TRANSITIVELY. resolve_agent_name (pact_context)
# consumes the self-asserted registry value at its Step 3.5, so an authority file
# doing `import pact_context; pact_context.resolve_agent_name(...)` would launder
# the forgeable value into an authority decision WITHOUT importing
# session_registry — invisible to the no-import test above. Forbidding the call
# name closes that transitive-reintroduction path structurally.
_FORBIDDEN_AUTHORITY_CALLS = ("_registry_resolve", "resolve_agent_name")

# The ONLY hook files permitted to import the self-registration registry. Pairs
# the POSITIVE 2-file authority no-import test with a NEGATIVE all-files scan: a
# NEW importer ANYWHERE under hooks/ — an authority file, a gate, a new helper —
# trips the backstop, even one the _AUTHORITY_FILES tuple does not enumerate.
# The four sanctioned consumers are all LABELING / coordination / lifecycle uses
# (the registry value never reaches an authority decision through any of them):
#   * shared/pact_context.py — resolve_agent_name Step 3.5 (human-readable label)
#   * shared/session_init_teammate.py — teammate-branch lead-team resolution
#     (peer display; moved out of session_init.py in A4b)
#   * session_end.py         — prune (imports REGISTRY_PATH only, no resolver)
#   * task_claim_gate.py     — F2 strict identity resolve() for the #961 claim
#     gate: a COORDINATION use, never authority. The resolved name only gates a
#     fail-open advisory nudge and (M2) an auto-flip. The auto-flip targets a
#     task whose owner == the registry-resolved name. Identity is
#     COORDINATION-ONLY (the registry is forgeable/labeling). A forged or
#     last-wins-collapsed registry entry could resolve to a DIFFERENT member's
#     name, so the flip is NOT guaranteed to act on the acting teammate's OWN
#     task. No-escalation holds NOT via 'own-task-only' but because the same OS
#     user already has full TaskUpdate/FS access and the only mutation is a
#     benign pending→in_progress flip — the gate crosses no privilege boundary.
#     A miss → advisory, never a typed guess.
#   * shared/background_work.py — bind_launcher_identity Step 3.5, the tmux
#     fallback for attributing a background launch. A LABELING use, never
#     authority. The resolved name becomes the `agent_name` on a registry
#     record, and the only thing that record can cause is an advisory — a
#     teammate-facing nudge at three unflagged idles, and a lead-facing line
#     naming who has unflagged background work. Nothing is granted, denied,
#     merged or deleted on it.
#     THE RESIDUAL, STATED RATHER THAN CLAIMED AWAY: the registry is forgeable
#     and last-wins-collapsed, so a forged or collided entry MIS-ATTRIBUTES an
#     advisory to a teammate who did not launch the work. That is a wrong
#     label on a message, not an escalation: the same OS user already has full
#     write access to the team-scoped registry the record lands in, so no
#     privilege boundary is crossed. Forging an entry requires local write
#     access, and anyone holding that can already edit the hooks, the task
#     store or the source directly — the forgery buys strictly less than the
#     access it requires.
#     SCOPE, STATED PRECISELY BECAUSE THE OBVIOUS SENTENCE IS WRONG: the
#     branch is UNREACHABLE in-process (MEASURED — Steps 1/2 and the validated
#     agent_type route resolve first, and the in-process self-guard leaves the
#     registry empty), and REACHABLE under tmux. It is UNEXERCISED here
#     because this machine has no tmux teams — which bounds OUR VERIFICATION,
#     not the exposure. This plugin ships to consumers who may run tmux teams,
#     so "no teams exist" is a fact about the test bench and must never be
#     read as a fact about how often the path is taken. The labeling
#     classification holds in both topologies regardless.
# Paths are POSIX-relative to HOOKS_DIR. session_registry.py itself is NOT here:
# the module does not import itself, so the detector does not flag it.
_ALLOWED_REGISTRY_IMPORTERS = frozenset({
    "shared/pact_context.py",
    "shared/session_init_teammate.py",
    "session_end.py",
    "shared/background_work.py",
    # Frame.registry_name: the docker lock's caller label for a separate-process
    # teammate. Frame.actor (the authority signal) never reads it.
    "shared/frame.py",
})


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _registry_imports(tree: ast.AST) -> list[str]:
    """Return descriptions of any import that references session_registry — in
    ANY of the shapes a drift author might reach for:
      * ``import shared.session_registry``               (Import, module token)
      * ``from shared.session_registry import resolve``  (ImportFrom, module token)
      * ``from shared import session_registry``          (ImportFrom, NAME token —
            the module imported as a bare name from a parent package)
    Matching the imported NAMES too (not just node.module) closes the aliased
    ``from shared import session_registry`` hole."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(tok in alias.name for tok in _REGISTRY_MODULE_TOKENS):
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            module_hit = any(tok in mod for tok in _REGISTRY_MODULE_TOKENS)
            # `from shared import session_registry` → the module is imported as a
            # NAME; check the imported names for the module token too.
            name_hit = any(
                tok in a.name
                for a in node.names
                for tok in _REGISTRY_MODULE_TOKENS
            )
            if module_hit or name_hit:
                names = ", ".join(a.name for a in node.names)
                found.append(f"from {mod} import {names}")
    return found


def _forbidden_resolver_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (name, lineno) for every Call whose callee name is in
    ``_FORBIDDEN_AUTHORITY_CALLS`` — matching BOTH a bare name
    ``resolve_agent_name(...)`` (ast.Name) and an attribute
    ``pact_context.resolve_agent_name(...)`` (ast.Attribute, the realistic
    transitive-reintroduction shape). Mirrors ``_registry_imports`` so the
    call-detector gets the same phantom-green fire-check treatment."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute)
                else None
            )
            if name in _FORBIDDEN_AUTHORITY_CALLS:
                found.append((name, node.lineno))
    return found


# ===========================================================================
# TRUST PARTITION — AST no-import (load-bearing security boundary)
# ===========================================================================

class TestTrustPartitionNoRegistryImport:
    """The authority surfaces must NOT import the registry resolver."""

    @pytest.mark.parametrize("rel_file", _AUTHORITY_FILES)
    def test_authority_file_does_not_import_session_registry(self, rel_file):
        path = HOOKS_DIR / rel_file
        assert path.exists(), f"authority file {rel_file} not found at {path}"
        offending = _registry_imports(_parse(path))
        assert offending == [], (
            f"{rel_file} imports the self-registration registry: {offending}. "
            f"The registry is LABELING-ONLY — its value is self-asserted and "
            f"forgeable. An authority surface (trustworthy_actor_name / the "
            f"self-completion gate) consuming it re-opens the confused-deputy "
            f"hole. Authority checks MUST read the harness-managed agent_id, "
            f"never the registry."
        )


# ===========================================================================
# IMPORTER ALLOWLIST — negative all-files backstop (pairs the positive no-import)
# ===========================================================================

class TestRegistryImporterAllowlist:
    """Exactly the four sanctioned LABELING/coordination/lifecycle consumers may
    import the registry. The no-import test above pins the TWO authority files specifically;
    this backstop scans EVERY hook file so a NEW importer anywhere — including one
    the _AUTHORITY_FILES tuple does not list — trips immediately."""

    def test_only_known_consumers_import_registry(self):
        actual: set[str] = set()
        for path in sorted(HOOKS_DIR.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if _registry_imports(_parse(path)):
                actual.add(path.relative_to(HOOKS_DIR).as_posix())
        # Non-vacuity guard: a broken scan (detector silently returns [] for all
        # files) would make `actual` empty — fail LOUDLY with a clear message
        # rather than via a confusing set-diff.
        assert actual, (
            "the registry-import scan found NO importers at all — the detector or "
            "the HOOKS_DIR glob likely broke; this backstop would be vacuous."
        )
        assert actual == set(_ALLOWED_REGISTRY_IMPORTERS), (
            f"the set of hook files importing session_registry drifted from the "
            f"sanctioned allowlist.\n"
            f"  expected: {sorted(_ALLOWED_REGISTRY_IMPORTERS)}\n"
            f"  actual:   {sorted(actual)}\n"
            f"A NEW importer MUST be reviewed: the registry value is "
            f"self-asserted / forgeable and LABELING-ONLY. If the new consumer is "
            f"a labeling/lifecycle use, add it to _ALLOWED_REGISTRY_IMPORTERS; if "
            f"it is an AUTHORITY surface, it must NOT import the registry at all "
            f"(see the no-import / no-call tests above)."
        )


# ===========================================================================
# SELF-LOOKUP-ONLY — no name-keyed lookup API exists
# ===========================================================================

class TestSelfLookupOnly:
    """resolve() takes a session_id, never a name; no name-keyed scan API."""

    def test_resolve_signature_takes_session_id_not_name(self):
        tree = _parse(HOOKS_DIR / "shared" / "session_registry.py")
        resolve_def = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "resolve"
        )
        arg_names = [a.arg for a in resolve_def.args.args]
        assert arg_names == ["session_id"], (
            f"resolve() args are {arg_names}; must be exactly ['session_id'] — "
            f"a name parameter would enable a name-keyed forge scan."
        )


# ===========================================================================
# SANITIZER-PARITY — inline copy byte-identical to peer_context's
# ===========================================================================

class TestSanitizerParity:
    """The inlined session_registry._sanitize_agent_name char-class must stay
    byte-identical to peer_context._sanitize_agent_name (write/read parity)."""

    def _sanitizer_charclass(self, rel_file: str) -> str:
        """Extract the re.sub char-class literal (the one containing \\x00) from
        the named module's source."""
        tree = _parse(HOOKS_DIR / rel_file)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sub"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and r"\x00" in node.args[0].value):
                return node.args[0].value
        raise AssertionError(f"no sanitizer re.sub char-class found in {rel_file}")

    def test_charclass_compiled_identical(self):
        peer = self._sanitizer_charclass("shared/peer_context.py")
        reg = self._sanitizer_charclass("shared/session_registry.py")
        assert reg == peer, (
            "session_registry._sanitize_agent_name char-class drifted from "
            "peer_context._sanitize_agent_name. They MUST stay byte-identical "
            "for write/read parity (the inline copy is intentional — see the "
            "self-containment constraint). Re-sync the regex literal."
        )


# ===========================================================================
# TEAM-SEGMENT VALIDATOR PARITY — dual-copy _is_safe_team_segment (behavioral)
# ===========================================================================

# Behavioral-parity battery for the dual-copy _is_safe_team_segment. The validator
# was introduced THIS PR in TWO inlined copies — session_end.py (prune) and
# session_registry.py (_name_is_team_member) — because the registry leaf cannot
# import from session_end (session_end imports FROM it), so the copy is duplicated
# on purpose. Nothing else guards the two from silently diverging. Each
# (input, expected) row documents intent AND feeds the cross-copy agreement check.
#   REJECT: empty / NUL / C0-low / C0-high / DEL / fwd-slash / back-slash / dot / dotdot
#   ACCEPT: a legit pact-<hex> team, "red-team", AND "pact-sessions-evil" — a VALID
#           single path segment (both copies MUST accept it). The sibling-prefix
#           rejection of "pact-sessions-evil" belongs to the PATH check
#           (_is_under_pact_sessions, F4), NOT this SEGMENT check; conflating the
#           two would wrongly reject a legitimately-named team.
_TEAM_SEGMENT_PARITY_BATTERY = (
    ("", False),
    ("\x00", False),
    ("\x01", False),
    ("\x1f", False),
    ("\x7f", False),
    ("a/b", False),
    ("a\\b", False),
    (".", False),
    ("..", False),
    ("pact-deadbeef", True),
    ("pact-sessions-evil", True),
    ("pact-6f81f147", True),
    ("red-team", True),
)


def _segment_parity_divergences(fn_a, fn_b, battery):
    """Return [(input, a_result, b_result), ...] for inputs where the two
    validators DISAGREE. Empty list == perfect behavioral parity. Pure: calls only
    the two passed predicates over the battery, no I/O."""
    out = []
    for item, _expected in battery:
        a, b = fn_a(item), fn_b(item)
        if a != b:
            out.append((item, a, b))
    return out


class TestTeamSegmentValidatorParity:
    """The dual-copy _is_safe_team_segment (session_end + session_registry, inlined
    to keep the registry's zero-shared-import leaf property) MUST behave
    identically. BEHAVIORAL parity (not source-byte) is the right level — these are
    explicit ord()/membership-check validators, not a shared regex literal like the
    sanitizer above."""

    def test_dual_copy_behaves_identically(self):
        """Cross-copy agreement: the two inlined validators return the SAME bool for
        every battery input. A divergence means a @team segment rejected by one
        FS-path builder (prune vs membership) is accepted by the other."""
        from session_end import _is_safe_team_segment as end_seg
        from shared.session_registry import _is_safe_team_segment as reg_seg
        diverged = _segment_parity_divergences(
            end_seg, reg_seg, _TEAM_SEGMENT_PARITY_BATTERY
        )
        assert diverged == [], (
            f"the two _is_safe_team_segment copies DIVERGED on {diverged} "
            f"(input, session_end, session_registry). They MUST stay behaviorally "
            f"identical — re-sync the reject branches (the registry copy is inlined "
            f"on purpose; it cannot import session_end's)."
        )


# ===========================================================================
# PATH-NOT-TEAM-SCOPED — global fixed team-agnostic path
# ===========================================================================

class TestPathNotTeamScoped:
    def test_registry_path_under_pact_sessions_not_teams(self):
        from shared.session_registry import get_registry_path
        p = get_registry_path()
        parts = p.parts
        assert "pact-sessions" in parts, (
            f"registry path {p} must live under pact-sessions/ (PACT-owned)."
        )
        assert "teams" not in parts, (
            f"registry path {p} is team-scoped (under teams/). It MUST be global "
            f"+ team-agnostic — a team-scoped path re-opens the bootstrap paradox "
            f"(a tmux teammate cannot compute its lead's team to locate a "
            f"team-scoped file)."
        )


# ===========================================================================
# BOTH-MODES MATRIX — in-process (session_id == leadSessionId) AND tmux (!=)
# ===========================================================================

class TestBothModesMatrix:
    """The dual-mode teammateMode contract. The registry resolver itself keys on
    the caller's OWN session_id (structural, mode-agnostic); these rows assert
    the correct OUTCOME under both topologies for the team-resolution use:

      * tmux (session_id != leadSessionId): a teammate registered its own
        session_id, so a self-lookup resolves name@team.
      * in-process (session_id == leadSessionId): the lead's own SessionStart
        frame self-looks-up the LEAD's session_id; absent a lead registration,
        resolve returns None and the caller keeps current behavior (the
        in-process fail-safe default).
    """

    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        import shared.session_registry as sr
        # get_registry_path() follows the patched Path.home() via the inline
        # _config_root() (C4b's accessor refactor removed the module-level
        # REGISTRY_PATH constant, so there is nothing to override here).
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

        def write_team(team, members):
            d = tmp_path / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                json.dumps({"members": [{"name": m} for m in members]}), encoding="utf-8"
            )

        return sr, write_team

    def test_tmux_mode_self_lookup_resolves(self, registry, monkeypatch):
        sr, write_team = registry
        _LEAD_SESSION = "lead-session-0000"
        _OWN_SESSION = "teammate-session-1111"  # != lead → tmux topology
        assert _OWN_SESSION != _LEAD_SESSION
        write_team("pact-leadteam", ["devops"])
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _OWN_SESSION)
        sr.register("devops@pact-leadteam")
        assert sr.resolve(_OWN_SESSION) == "devops@pact-leadteam"


