"""
Location: pact-plugin/tests/test_hook_infra_classifier.py
Summary: CI meta-tests (hard teeth) for the hook-infra seam classifier SSOT
(shared/hook_infra_classifier.py) — the seam-dependent-hook enumeration + the
per-hook transitive helper closure that the non-mocked seam-test requirement
references. Three families:
  C6-A (LOAD-BEARING) — CLOSURE NON-VACUITY: re-derive the per-hook transitive
        helper-import closure from the LIVE hooks/ import graph (an independent
        AST walk) and assert the classifier's precomputed _SEAM_HOOK_HELPER_CLOSURE
        literal MATCHES it. This is NOT a restate-the-literal self-comparison: the
        oracle is computed from the real `import`/`from shared.X` edges, so a wrong
        or drifted literal recreates the #903 inert trap AT THE CLASSIFIER LAYER
        and this test catches it. Non-vacuity is PROVEN below by perturbing the
        live graph (drop a top-level edge AND a shared edge) and asserting the
        equality breaks.
  C6-B PRESENCE — every SEAM_DEPENDENT_HOOKS member is accounted for: it either
        has a non-mocked L2 integration test (COVERED) or is on a documented
        forward-only BACKLOG. A NEW seam hook with neither trips the partition.
  C6-B ANTI-MOCK — the seam-hook L2 integration files do NOT monkeypatch the
        resolver symbols (mocking the seam reproduces the inert trap) AND carry a
        revert-cardinality non-vacuity docstring.

Used by: the C4 GitHub Actions suite run (locus-a auto-enforcement). Plugin-
internal CI only — NOT a runtime hook, so no consumer pollution.

ORACLE SCOPE (must match the classifier literal's documented semantics): the
closure is TRANSITIVE over EVERY hooks/ module — both top-level helpers AND
hooks/shared/ helpers — and follows BOTH absolute (`from X import` /
`from shared.X import` / `import X`) AND relative (`from .X import`) edges. A
shared-only OR absolute-only OR direct-only derivation would FALSELY pass on a
sub-graph it never traverses (e.g. it would miss a hook->top-level-helper edge, task_lifecycle_gate->teachback_schema->variety_scorer shared, or
the relative pact_context->.session_registry edge that makes a session_registry
edit implicate the L3 hooks). The two-hop edges are pinned BY NAME below, and a
top-level-edge perturbation is the explicit non-vacuity anchor.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

HOOKS = (Path(__file__).parent.parent / "hooks").resolve()

from shared.hook_infra_classifier import (  # noqa: E402
    SEAM_DEPENDENT_HOOKS,
    SEAM_READING_HELPERS,
    L3_LIVE_PROBE_HOOKS,
    _SEAM_HOOK_HELPER_CLOSURE,
    classify_diff,
)

# ── Resolver symbols that an L2 seam integration test MUST NOT monkeypatch ──
# Mocking any of these reproduces the exact gap that shipped the missed-wake
# surfacer inert (the broken seam was the one every prior test stubbed).
RESOLVER_SYMBOLS = (
    "get_task_list",
    "iter_team_task_jsons",
    "find_stale_missed_wakes",
    "read_task_json",
    "get_team_name",
    "get_session_id",
)


# ── The independent oracle: re-derive the closure from the live import graph ──

def _module_index() -> dict[str, Path]:
    """stem -> path for every top-level hooks/*.py AND hooks/shared/*.py."""
    idx: dict[str, Path] = {}
    for p in sorted(HOOKS.glob("*.py")):
        idx.setdefault(p.stem, p)
    for p in sorted((HOOKS / "shared").glob("*.py")):
        idx.setdefault(p.stem, p)
    return idx


def _is_shared(stem: str, idx: dict[str, Path]) -> bool:
    """True iff the module resolves under hooks/shared/ (vs a top-level hooks/ helper)."""
    return "/shared/" in f"/{idx[stem].as_posix()}"


def _direct_hook_imports(
    path: Path, idx: dict[str, Path], shared_only: bool = False,
) -> set[str]:
    """hooks/ module stems imported DIRECTLY by `path` — resolves top-level
    (`import X` / `from X import`), shared (`from shared.X import` /
    `import shared.X`), relative (`from .X import`, level>0), AND
    module-in-the-alias (`from . import X` / `from shared import X`) edges, and
    descends into function/try-nested imports via ast.walk (a function-level
    `from X import ...` counts).

    The module-in-the-alias forms name the MODULE after `import`, not after
    `from`, so reading only `node.module` misses them: `from . import X` has
    no module at all, and `from shared import X` names only the package. An
    alias that is a function or constant rather than a module is not in `idx`
    and adds nothing.

    `shared_only` models the BUG the architect caught — a derivation that only
    follows hooks/shared/ edges and never traverses top-level helper modules."""
    out: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split(".")
                cand = parts[1] if parts[0] == "shared" and len(parts) > 1 else parts[0]
                if cand in idx:
                    out.add(cand)
            if node.module is None or node.module == "shared":
                for alias in node.names:
                    if alias.name in idx:
                        out.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                cand = parts[1] if parts[0] == "shared" and len(parts) > 1 else parts[0]
                if cand in idx:
                    out.add(cand)
    if shared_only:
        out = {m for m in out if _is_shared(m, idx)}
    return out


def derive_closure(
    hook: str,
    idx: dict[str, Path],
    drop_edges: frozenset[tuple[str, str]] = frozenset(),
    shared_only: bool = False,
) -> frozenset[str]:
    """BFS the live import graph from `hook`, collecting every reachable helper
    module (top-level OR shared), EXCLUDING the seam hooks themselves. `drop_edges`
    removes specific (module, imported) edges and `shared_only` restricts to
    shared edges — both used ONLY by the non-vacuity tests to prove the oracle
    responds to a real graph change / scope restriction."""
    seam = set(SEAM_DEPENDENT_HOOKS)
    seen: set[str] = set()
    stack = [hook]
    result: set[str] = set()
    while stack:
        mod = stack.pop()
        if mod not in idx:
            continue
        for dep in _direct_hook_imports(idx[mod], idx, shared_only=shared_only):
            if (mod, dep) in drop_edges or dep in seen:
                continue
            seen.add(dep)
            result.add(dep)
            stack.append(dep)
    return frozenset(result - seam)


def _derive_all(drop_edges: frozenset[tuple[str, str]] = frozenset()) -> dict[str, frozenset[str]]:
    idx = _module_index()
    return {h: derive_closure(h, idx, drop_edges) for h in SEAM_DEPENDENT_HOOKS}


# ═══════════════════════════════════════════════════════════════════════════
# C6-A — CLOSURE NON-VACUITY (the load-bearing meta-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestClosureMatchesLiveImportGraph:
    """The committed _SEAM_HOOK_HELPER_CLOSURE literal must equal an INDEPENDENT
    transitive AST re-derivation from the live hooks/ import graph. Guards the
    classifier from a drifted/wrong closure (the classifier-layer inert trap)."""

    def test_each_hook_closure_matches_live_derivation(self):
        derived = _derive_all()
        for hook in sorted(SEAM_DEPENDENT_HOOKS):
            assert _SEAM_HOOK_HELPER_CLOSURE[hook] == derived[hook], (
                f"closure literal for {hook!r} has DRIFTED from the live import "
                f"graph.\n  literal-only: "
                f"{sorted(_SEAM_HOOK_HELPER_CLOSURE[hook] - derived[hook])}\n"
                f"  graph-only:   {sorted(derived[hook] - _SEAM_HOOK_HELPER_CLOSURE[hook])}"
            )

    def test_literal_covers_exactly_the_seam_hooks(self):
        assert set(_SEAM_HOOK_HELPER_CLOSURE) == set(SEAM_DEPENDENT_HOOKS)

    def test_seam_reading_helpers_is_the_union(self):
        union = frozenset().union(*_SEAM_HOOK_HELPER_CLOSURE.values())
        assert SEAM_READING_HELPERS == union

    def test_the_oracle_sees_a_module_named_in_the_import_alias(self, tmp_path):
        """`from . import X` and `from shared import X` are edges to module X.

        Both forms put the module after `import`. An oracle reading only the
        `from` part sees no module in the first and only the package in the
        second, so a helper imported either way drops out of every derived
        closure and the literal can omit it while the drift arm stays green.
        A function or constant named the same way is not a module and adds
        nothing.
        """
        idx = _module_index()
        probe = tmp_path / "probe.py"
        probe.write_text(
            "from . import state_file\n"
            "def f():\n"
            "    from shared import pact_context, get_team_name\n",
            encoding="utf-8",
        )
        assert {"state_file", "pact_context"} <= _direct_hook_imports(probe, idx)
        assert "get_team_name" not in _direct_hook_imports(probe, idx)


class TestTwoHopEdgesPinnedByName:
    """Pin BOTH transitive 2-hop classes by name so a future direct-only OR
    shared-only OR absolute-only regression is caught with a named failure, not
    a silent under-derivation."""

    def test_shared_2hop_task_lifecycle_teachback_variety(self):
        # task_lifecycle_gate -> teachback_schema -> variety_scorer (shared chain).
        tlg = _SEAM_HOOK_HELPER_CLOSURE["task_lifecycle_gate"]
        assert "teachback_schema" in tlg
        assert "variety_scorer" in tlg, (
            "variety_scorer is reachable ONLY 2-hop via teachback_schema; its "
            "absence means a direct-import-only derivation (the bug class)"
        )
        idx = _module_index()
        # Drop the 2-hop spine edge -> variety_scorer must disappear from the
        # derived closure (proves it's genuinely a transitive, not direct, dep).
        without = derive_closure(
            "task_lifecycle_gate", idx,
            drop_edges=frozenset({("teachback_schema", "variety_scorer")}),
        )
        assert "variety_scorer" not in without


class TestClosureOracleIsNonVacuous:
    """PROVE the equality assertion is non-vacuous: perturb the LIVE graph and
    show the derived closure changes, so a real edge drift WOULD break
    test_each_hook_closure_matches_live_derivation. A vacuous (literal==literal)
    oracle would NOT respond to these perturbations."""

    def test_shared_only_derivation_misses_toplevel_helpers(self, tmp_path):
        # The architect-caught bug: a shared-only walk never traverses a
        # top-level helper edge, so the closure LOSES the helper and all it
        # reaches. No live seam hook reaches a top-level helper since the 1.5.0
        # pin-gate cut removed the last one (session_init -> staleness ->
        # pin_caps), so a synthetic graph keeps the full-traversal arm honest.
        (tmp_path / "shared").mkdir()
        hook = tmp_path / "probe_hook.py"
        helper = tmp_path / "probe_helper.py"
        leaf = tmp_path / "shared" / "probe_leaf.py"
        hook.write_text("from probe_helper import x\n", encoding="utf-8")
        helper.write_text("from shared.probe_leaf import y\n", encoding="utf-8")
        leaf.write_text("y = 1\n", encoding="utf-8")
        idx = {"probe_hook": hook, "probe_helper": helper, "probe_leaf": leaf}
        full = derive_closure("probe_hook", idx)
        shared_only = derive_closure("probe_hook", idx, shared_only=True)
        assert full == {"probe_helper", "probe_leaf"}
        assert not _is_shared("probe_helper", idx)
        assert shared_only == frozenset()

    def test_dropping_a_shared_edge_changes_derivation(self):
        idx = _module_index()
        full = derive_closure("task_lifecycle_gate", idx)
        perturbed = derive_closure(
            "task_lifecycle_gate", idx,
            drop_edges=frozenset({("teachback_schema", "variety_scorer")}),
        )
        assert "variety_scorer" in full and "variety_scorer" not in perturbed
        assert _SEAM_HOOK_HELPER_CLOSURE["task_lifecycle_gate"] != perturbed

    def test_dropping_the_relative_registry_edge_changes_derivation(self):
        # pact_context -> .session_registry is a RELATIVE edge; the full-transitive
        # oracle DOES follow it, so a relative-only-blind derivation differs.
        # This pins that relative edges are part of the canonical closure.
        #
        # BOTH edges must be dropped, and the reason is a real graph change
        # rather than test bookkeeping. missed_wake_scan reaches
        # session_registry by TWO routes now: the pact_context edge this arm
        # is named for, and background_work -> session_registry, which the
        # Layer 3 fold introduced (background_work's Layer 1 half resolves a
        # launcher identity, and a static closure follows that import even
        # though Layer 3 never calls it). Dropping only the first leaves the
        # module reachable and the ablation stops discriminating — MEASURED:
        # one edge -> still present, both edges -> absent. Naming one edge
        # would leave an arm that passes without measuring anything.
        idx = _module_index()
        full = derive_closure("missed_wake_scan", idx)
        perturbed = derive_closure(
            "missed_wake_scan", idx,
            drop_edges=frozenset({
                ("pact_context", "session_registry"),
                ("background_work", "session_registry"),
            }),
        )
        assert "session_registry" in full, (
            "the canonical (full-transitive) closure follows the relative "
            "pact_context->.session_registry edge"
        )
        assert "session_registry" not in perturbed


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — PRESENCE (every seam hook is COVERED by an L2 test or on the BACKLOG)
# ═══════════════════════════════════════════════════════════════════════════

# Seam hook -> the non-mocked L2 integration test file that exercises its real
# seam. teammate_idle's coverage is the IT-6 smoke inside the missed_wake file.
TESTS_DIR = Path(__file__).parent
COVERED_L2 = {
    "missed_wake_scan": "test_missed_wake_scan_integration.py",
    "teammate_idle": "test_missed_wake_scan_integration.py",
    "session_end": "test_session_end_integration.py",
    "agent_handoff_emitter": "test_agent_handoff_emitter_integration.py",
    "session_init": "test_session_init_integration.py",
    "dispatch_gate": "test_dispatch_gate_integration.py",
    # The on-disk authorization token is the post(mint)→pre(read) integration
    # seam for the merge guards; the non-mocked S1 seam test exercises it for
    # real (real mint → real read over a temp token_dir). Guards DENY via
    # exit(2) (fail-LOUD), so they are L2-only / never-L3 (no live-probe).
    "merge_guard_pre": "test_merge_guard_seam_integration.py",
    "merge_guard_post": "test_merge_guard_seam_integration.py",
    # validate_handoff's degrade-path handoff_refusal_degraded telemetry is
    # fail-open (silent on loss), so the journal seam gets a real composition
    # test: real init -> session-dir resolution -> real append -> read_events
    # over a tmp root. Its turn-end background block, a seam-dependent decision
    # since it was composed in, is driven for real by
    # test_validate_handoff_turn_end.py.
    "validate_handoff": "test_validate_handoff_integration.py",
    # track_files joined SEAM_DEPENDENT_HOOKS with Layer 1 of the
    # background-work registry (task-dir resolution + team config). It goes in
    # COVERED, not BACKLOG: parking a brand-new seam dependency in the backlog
    # would ship an untested seam, which is the inert-feature shape this
    # classifier exists to prevent.
    "track_files": "test_track_files_background_integration.py",
    # wait_filler_gate joined SEAM_DEPENDENT_HOOKS when its launch advisory began
    # reading team config and the session registry. COVERED, not BACKLOG, for
    # the same reason as track_files above.
    "wait_filler_gate": "test_launch_advisory_population.py",
    # stop_background_gate joined SEAM_DEPENDENT_HOOKS with the Stop turn-end
    # gate. Its non-mocked L2 test drives the production hook against a real
    # team config, task store, background-work registry and session registry.
    "stop_background_gate": "test_stop_background_gate.py",
    # postcompact_archive joined SEAM_DEPENDENT_HOOKS with teammate-compaction
    # attribution. Its non-mocked L2 test runs the real hooks over a temporary
    # projects tree with the platform's transcript layout.
    "postcompact_archive": "test_compaction_owner_seam.py",
    # bootstrap_gate settles staged compaction summaries before a Read or Bash
    # that names one. The same non-mocked L2 test runs the real hook as a child
    # process after the transcript record lands.
    "bootstrap_gate": "test_compaction_owner_seam.py",
}

# Documented forward-only BACKLOG: seam hooks whose non-mocked L2 test is a named
# fast-follow (not this cycle). The requirement BINDS new/modified seam hooks;
# this list is the auditable record of the known gaps (promote on touch).
#   - task_lifecycle_gate: heavy unit coverage; L3 real-session probe is the
#     documented follow-up. Its L2 seam test is fast-follow.
#   - bootstrap_marker_writer: iter_team_task_jsons readers.
#   - file_tracker / peer_inject: L2-only (held), watch-list per the classifier.
# (validate_handoff promoted to COVERED_L2 when its degrade telemetry gained
#  the real-seam composition test — the hook was touched, so the gap closed.
#  bootstrap_gate was promoted when its settle seat gained a child-process seam
#  test; that test does not exercise its iter_team_task_jsons reads.)
BACKLOG_L2 = frozenset({
    "task_lifecycle_gate", "bootstrap_marker_writer",
    "file_tracker", "peer_inject",
})


def test_bootstrap_gate_is_covered_by_a_seam_file_that_runs_it_as_a_child_process():
    """The mapped file hands hooks/bootstrap_gate.py to a helper that starts it
    with subprocess.run, so the settle seat is exercised in a real process."""
    assert COVERED_L2.get("bootstrap_gate") == "test_compaction_owner_seam.py"
    assert "bootstrap_gate" not in BACKLOG_L2
    tree = ast.parse((TESTS_DIR / COVERED_L2["bootstrap_gate"]).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    hands_over = any(call.args and isinstance(call.args[0], ast.Constant)
                     and call.args[0].value == "bootstrap_gate.py" for call in calls)
    spawns = any(isinstance(call.func, ast.Attribute) and call.func.attr == "run"
                 and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"
                 for call in calls)
    assert hands_over and spawns


class TestSeamHookL2Presence:
    def test_every_seam_hook_is_covered_or_backlogged(self):
        accounted = set(COVERED_L2) | set(BACKLOG_L2)
        unaccounted = set(SEAM_DEPENDENT_HOOKS) - accounted
        assert not unaccounted, (
            f"seam hook(s) with NEITHER an L2 test NOR a backlog entry: "
            f"{sorted(unaccounted)} — a new seam hook MUST ship an L2 "
            f"non-mocked integration test or be added to the documented backlog"
        )

    def test_covered_and_backlog_are_disjoint_and_exact(self):
        assert set(COVERED_L2).isdisjoint(BACKLOG_L2)
        assert set(COVERED_L2) | set(BACKLOG_L2) == set(SEAM_DEPENDENT_HOOKS)

    def test_covered_l2_test_files_exist(self):
        for hook, fname in sorted(COVERED_L2.items()):
            assert (TESTS_DIR / fname).exists(), (
                f"COVERED seam hook {hook!r} maps to missing L2 file {fname!r}"
            )

    def test_presence_fires_on_a_seam_hook_without_test(self):
        # NON-VACUITY (quiet side): a hypothetical new seam hook absent from both
        # COVERED and BACKLOG must be flagged as unaccounted.
        hypothetical = set(SEAM_DEPENDENT_HOOKS) | {"brand_new_seam_hook"}
        accounted = set(COVERED_L2) | set(BACKLOG_L2)
        assert (hypothetical - accounted) == {"brand_new_seam_hook"}


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — ANTI-MOCK (seam L2 tests must exercise the REAL seam, not stub it)
# ═══════════════════════════════════════════════════════════════════════════

def _monkeypatches_resolver(text: str) -> list[str]:
    """Return resolver symbols this file monkeypatches/patches (the anti-pattern).
    Heuristic tripwire (backstopped by the revert-cardinality non-vacuity gate —
    a mocked-seam test, however it mocks, cannot pass a source-revert): flags any
    patch/setattr line that names a resolver symbol as the TARGET. Ignores plain
    imports/calls of the resolver (those are the REAL-seam exercise we want).

    FIX (review cycle 1, finding MINOR-2): the original only caught
    `monkeypatch.setattr` / `.patch(` / `@patch`. It was EVADABLE by
    `patch.object(...)`, a bare builtin `setattr(...)`, and an aliased
    `mp.setattr(...)`. Broadened below to catch all three; still a tripwire, not
    a proof (a string-built target or an exotic idiom can still slip — the
    revert-cardinality gate is the airtight layer)."""
    hits: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        is_patch = (
            "setattr(" in s            # monkeypatch.setattr / aliased mp.setattr / bare builtin setattr
            or ".patch(" in s          # mock.patch( / patch( context-manager
            or "patch.object(" in s    # mock.patch.object(...) — the prior evasion
            or s.startswith("patch(")  # bare patch(
            or s.startswith("@patch")  # @patch / @patch.object decorator
        )
        if not is_patch:
            continue
        for sym in RESOLVER_SYMBOLS:
            if sym in s:
                hits.append(sym)
    return hits


# The seam-hook L2 files that exist NOW and must obey the anti-mock invariant.
_EXISTING_L2_FILES = sorted({
    f for f in COVERED_L2.values() if (TESTS_DIR / f).exists()
})


class TestSeamL2FilesDoNotMockTheResolver:
    @pytest.mark.parametrize("fname", _EXISTING_L2_FILES)
    def test_no_resolver_monkeypatch(self, fname):
        text = (TESTS_DIR / fname).read_text(encoding="utf-8")
        hits = _monkeypatches_resolver(text)
        assert not hits, (
            f"{fname} monkeypatches resolver symbol(s) {sorted(set(hits))} — "
            f"mocking the seam reproduces the inert trap; exercise the REAL "
            f"resolver via a Path.home / tasks_base_dir redirect instead"
        )

    @pytest.mark.parametrize("fname", _EXISTING_L2_FILES)
    def test_carries_revert_cardinality_docstring(self, fname):
        text = (TESTS_DIR / fname).read_text(encoding="utf-8").lower()
        assert "revert" in text and ("cardinality" in text or "failed" in text), (
            f"{fname} must document a revert-cardinality non-vacuity gate "
            f"(source-revert the resolver fix -> documented {{N failed}} cardinality)"
        )

    def test_anti_mock_detector_is_non_vacuous(self):
        # Proves the detector FIRES on a real violation and is SILENT on a clean
        # snippet — else test_no_resolver_monkeypatch could pass vacuously.
        violating = 'monkeypatch.setattr(task_utils, "get_task_list", lambda: [])'
        clean = "tasks = task_utils.get_task_list(tasks_base_dir=str(root))"
        assert _monkeypatches_resolver(violating) == ["get_task_list"]
        assert _monkeypatches_resolver(clean) == []

    def test_anti_mock_detector_catches_evasion_idioms(self):
        # FIX (MINOR-2): the 3 idioms that EVADED the original detector are now
        # caught. A plain CALL of the resolver (not a patch target) stays clean.
        assert _monkeypatches_resolver(
            'patch.object(task_utils, "get_task_list")') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'setattr(task_utils, "get_task_list", x)') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'mp.setattr(task_utils, "get_task_list", x)') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'with mock.patch.object(tu, "get_task_list"):') == ["get_task_list"]
        # a real-seam CALL (the pattern we WANT) must NOT be flagged:
        assert _monkeypatches_resolver(
            "members = dispatch_gate._team_member_names(team)") == []


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — CLASSIFIER quiet/loud behavior (silent on non-hook, fires on seam)
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifierQuietAndLoud:
    def test_silent_on_a_non_hook_pr(self):
        c = classify_diff(["README.md", "pact-plugin/agents/pact-test-engineer.md"])
        assert not c.primary and not c.secondary and not c.waiver_required

    def test_primary_only_change_requires_waiver(self):
        # A hooks/ file that is neither a seam hook nor a seam helper -> PRIMARY,
        # not SECONDARY -> the auditable waiver path (never a silent pass). gh_helpers served
        # here until session_init, session_end and bootstrap_marker_writer came to reach it.
        c = classify_diff(["pact-plugin/hooks/shared/payload_digest.py"])
        assert c.primary and not c.secondary and c.waiver_required

    def test_fires_secondary_on_a_seam_hook(self):
        c = classify_diff(["pact-plugin/hooks/missed_wake_scan.py"])
        assert c.primary and c.secondary
        assert "missed_wake_scan" in c.seam_hooks

    def test_l3_set_is_subset_of_seam_set(self):
        assert L3_LIVE_PROBE_HOOKS <= SEAM_DEPENDENT_HOOKS


# ═══════════════════════════════════════════════════════════════════════════
# C6-A BOUND BACKSTOP — enforce the oracle's STATIC-import / module-set bound
# (review cycle 1, finding MINOR-1)
# ═══════════════════════════════════════════════════════════════════════════
#
# The closure oracle (TestClosureMatchesLiveImportGraph) re-derives the closure
# from STATIC `import` / `from X import` AST nodes within the hooks/*.py +
# hooks/shared/*.py module set. Two edge classes would be invisible to BOTH the
# oracle AND the SSOT literal's authoring derivation (same algorithm) → they
# would AGREE VACUOUSLY (a false-pass) on a missed edge:
#   (1) a DYNAMIC import (importlib.import_module / __import__) of a hook/helper;
#   (2) an edge into the hooks/refresh/ subpackage (NOT in the oracle's idx glob).
# Both are ABSENT today (verified at authoring). This backstop fails LOUDLY if a
# future edit introduces either, so the oracle's bound is ENFORCED rather than
# silently exceeded. If a legitimate non-hook dynamic import is ever needed,
# allowlist that specific line explicitly (never blanket-disable the scan).

import re as _re  # noqa: E402


def _is_dynamic_import_line(line: str) -> bool:
    """True for a dynamic-import idiom the static AST oracle cannot resolve."""
    s = line.strip()
    if s.startswith("#"):
        return False
    return ("importlib" in s) or bool(_re.search(r"\b__import__\s*\(", s))


def _is_refresh_edge_line(line: str) -> bool:
    """True for an import edge into a hooks/refresh/ subpackage. The package
    was removed from the hooks tree; this detector stays as a canary because
    any reintroduced refresh subpackage would sit OUTSIDE the oracle's idx
    glob (hooks/*.py + hooks/shared/*.py) and its edges would false-pass."""
    s = line.strip()
    if s.startswith("#"):
        return False
    return bool(_re.search(r"\b(?:from|import)\s+\.?refresh\b", s))


def _scan_hook_modules(predicate) -> list[tuple[str, int, str]]:
    """Run `predicate(line)` over every line of hooks/*.py + hooks/shared/*.py;
    return (filename, lineno, line) hits. Mirrors the oracle's module-set scope."""
    hits: list[tuple[str, int, str]] = []
    for d in (HOOKS, HOOKS / "shared"):
        for p in sorted(d.glob("*.py")):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if predicate(line):
                    hits.append((p.name, i, line.strip()))
    return hits


# Dynamic imports the static oracle cannot follow, allowed line by line.
# Two hooks load a shared helper BY FILE PATH so their common path never
# imports the `shared` package: wait_filler_gate.py loads background_launch.py
# for a Bash call that is not a teammate's background launch, and
# stop_background_gate.py loads turn_end_jobs.py at a turn end with no counted
# job. The oracle cannot see either edge. Each hides nothing from the closure
# only while the loaded helper imports nothing outside the stdlib and nothing
# from `shared`, which the arm below pins for both.
_ALLOWED_DYNAMIC_IMPORT_LINES = frozenset({
    ("wait_filler_gate.py", "import importlib.util"),
    ("wait_filler_gate.py",
     'spec = importlib.util.spec_from_file_location("_pact_background_launch", path)'),
    ("wait_filler_gate.py", "module = importlib.util.module_from_spec(spec)"),
    ("stop_background_gate.py", "import importlib.util"),
    ("stop_background_gate.py",
     'spec = importlib.util.spec_from_file_location("_turn_end_jobs", path)'),
    ("stop_background_gate.py", "module = importlib.util.module_from_spec(spec)"),
    # egress_code.py imports nothing dynamically: this line is one piece of a regular expression
    # that REFUSES scanned code text naming a dynamic import.
    ("egress_code.py",
     r'r"|\bcompile\s*\(|__import__|importlib|\brunpy\b|\bpickle\b|yaml\.load\s*\(|unsafe_load"'),
    # doctor_brief.py (D10 P3) loads the task log reader project_tasks.py by path on a cache miss,
    # because importing the `shared` package would break the brief's 25 ms pin. project_tasks.py
    # is stdlib-only by design (D10 section 7); test_project_tasks_core's
    # test_the_read_side_imports_only_the_stdlib_and_no_subprocess_or_network pins that.
    ("doctor_brief.py", "import importlib.util"),
    ("doctor_brief.py",
     'spec = importlib.util.spec_from_file_location("repacted_project_tasks_brief", TASK_READER)'),
    ("doctor_brief.py", "reader = importlib.util.module_from_spec(spec)"),
    # dispatch.py loads a native check ("module:function" in dispatch_registry.REGISTRY) by
    # module name. Every such module is a hooks/*.py file, inside the oracle's glob;
    # test_dispatch's test_every_native_check_is_a_hooks_module pins that.
    ("dispatch.py", "import importlib"),
    ("dispatch.py", "func = getattr(importlib.import_module(module_name), func_name)"),
    # dispatch_rules.py (U1.6) loads the rule registry shared/rules.py by path under a private
    # name, because the dispatcher keeps the `shared` package out of its process. rules.py is a
    # stdlib-only leaf; the arm below pins that.
    ("dispatch_rules.py", "import importlib.util"),
    ("dispatch_rules.py", "spec = importlib.util.spec_from_file_location(_REGISTRY_NAME, _RULES_FILE)"),
    ("dispatch_rules.py", "module = importlib.util.module_from_spec(spec)"),
})


class TestOracleStaticImportBoundBackstop:
    """Enforce the C6-A oracle's bound so a future dynamic/refresh edge fails
    HERE instead of slipping past the closure equality as a vacuous false-pass."""

    def test_no_dynamic_import_of_hook_modules(self):
        hits = [hit for hit in _scan_hook_modules(_is_dynamic_import_line)
                if (hit[0], hit[2]) not in _ALLOWED_DYNAMIC_IMPORT_LINES]
        assert not hits, (
            "a DYNAMIC import (importlib/__import__) appeared in the hooks tree — "
            "the C6-A static-AST oracle CANNOT see it, so the closure literal could "
            "silently false-pass on this edge. Either use a static import, or (if a "
            "legit non-hook dynamic import) allowlist this exact line + extend the "
            f"oracle. Offending: {hits}"
        )

    @pytest.mark.parametrize("helper", ["background_launch.py", "turn_end_jobs.py", "rules.py"])
    def test_the_by_path_loaded_helper_imports_only_the_stdlib(self, helper):
        """A helper loaded by file path (background_launch.py by
        wait_filler_gate, turn_end_jobs.py by stop_background_gate, rules.py by
        dispatch_rules) is an edge
        the oracle cannot see. The edge hides nothing only while the file imports nothing
        but the stdlib: a `shared` or relative import reaches back into the
        hooks tree, and a third-party import is a dependency no consumer
        session is promised.

        Stdlib membership is read from where each module is found, not from
        sys.stdlib_module_names, which Python 3.9 in the CI matrix lacks.
        Both sides are resolved with realpath: Homebrew's sysconfig reports
        the stdlib through an opt/ symlink while find_spec returns the Cellar
        path, so a raw prefix test flags stdlib modules such as __future__.
        """
        import ast
        import importlib.util
        import os
        import sysconfig

        paths = {k: os.path.realpath(v) for k, v in sysconfig.get_paths().items()}
        site = (paths["purelib"], paths["platlib"])
        source = (HOOKS / "shared" / helper).read_text(encoding="utf-8")
        offending = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level:
                offending.append("." * node.level + (node.module or ""))
                continue
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                spec = None if top == "shared" else importlib.util.find_spec(top)
                origin = getattr(spec, "origin", None) or ""
                if origin not in ("built-in", "frozen"):
                    origin = os.path.realpath(origin) if origin else ""
                in_stdlib = origin in ("built-in", "frozen") or (
                    origin.startswith(paths["stdlib"]) and not origin.startswith(site)
                )
                if not in_stdlib:
                    offending.append(name)
        assert not offending, (
            "hooks/shared/%s imports %r. A hook loads that file by path, an edge "
            "the closure oracle cannot see, so it must import nothing outside the "
            "stdlib and nothing from `shared`" % (helper, offending)
        )

    def test_no_refresh_subpackage_edge_in_hooks(self):
        hits = _scan_hook_modules(_is_refresh_edge_line)
        assert not hits, (
            "an import edge into a hooks/refresh/ subpackage appeared — the package "
            "was removed, and a reintroduced one is OUTSIDE the C6-A oracle's idx "
            "glob (hooks/*.py + hooks/shared/*.py), so both the oracle and the "
            "literal would miss its edges (vacuous false-pass). Extend the oracle's "
            f"idx to cover the subpackage before adding such an edge. Offending: {hits}"
        )

    def test_backstop_detectors_are_non_vacuous(self):
        # PROVE the backstop would FIRE on an injected edge (else it is vacuous).
        assert _is_dynamic_import_line('mod = importlib.import_module("staleness")')
        assert _is_dynamic_import_line('__import__("task_utils")')
        assert not _is_dynamic_import_line("from shared.task_utils import get_task_list")
        assert not _is_dynamic_import_line("# importlib note in a comment")
        assert _is_refresh_edge_line("from refresh.checkpoint_builder import build")
        assert _is_refresh_edge_line("import refresh.patterns")
        assert _is_refresh_edge_line("from .refresh import x")
        assert not _is_refresh_edge_line("refresh_token = compute()")  # word-boundary
        assert not _is_refresh_edge_line("# refresh the cache")
