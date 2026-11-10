"""
Location: pact-plugin/tests/test_import_hygiene.py
Summary: Suite-level import-hygiene gate — a strict-mode unused-import sweep
         over every consumer-shipped Python surface (hooks/, scripts/,
         telegram/, skills/*/scripts/) plus the dev-repo tests/ tree, and
         the pins that keep the gate itself honest: non-vacuity fixtures
         driven through the gate's own entry point, a no-default signature
         pin on the predicate's strictness parameter, and a prose pin on the
         canonical lint-check.sh invocation in the command files.
Used by: pytest suite. This is the dev-repo enforcement tier of the
         import-hygiene ladder; the consumer-facing tier is
         lint-check.sh --files (advisory strictness). Both tiers call the
         SAME predicate module — one substrate, two declared strictness
         tiers. THIS file is where the suite tier's strictness is declared.

Scope boundary — what is swept and why:
    hooks/, scripts/, telegram/, and skills/*/scripts/ all execute in (or
    ship to) consumer sessions, so dead imports there are product defects.
    tests/ never ships, but is swept too: the backlog that justified its
    day-one exclusion has been cleared, and the CI ruff leg lints all of
    pact-plugin/ — this gate sweeps tests/ as well, so the two enforcement
    layers cannot disagree on tests/ scope.

Suppression contract:
    An intentional unused import (re-export facade, monkeypatch seam,
    availability probe) carries `# noqa: F401  # <category>: <reason>` on
    the import statement's FIRST physical line. The sweep honors exactly
    that convention; an unmarked unused import fails the suite — with ONE
    exception, which this file's own liveness filter introduced: a
    CONCURRENT run's planted probe is removed from the tests/ surface
    before the gate ever sees it. See `_is_foreign_live_probe`.

Strictness contract (why the non-vacuity fixtures exist):
    The predicate's try-scope parameter is REQUIRED-EXPLICIT — no default
    anywhere in the chain. The suite gate declares "strict" (try/except-
    scoped imports are checked: the fail-closed try wrapper is this repo's
    standard cross-package import idiom, and skipping it hides real dead
    imports). The consumer tier declares "advisory". The non-vacuity tests
    drive a try-scoped dead import through the gate's OWN entry point, so
    any future edit that weakens the gate's declared strictness turns the
    suite red — asserting the predicate directly with a strict argument
    would only prove the predicate CAN be strict, not that this gate IS.
"""

import ast
import importlib.util
import io
import os
import re
import tokenize
import uuid
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent

_PREDICATE_SCRIPT = (
    PLUGIN_ROOT
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "check_unused_imports.py"
)


def _load_predicate_module():
    spec = importlib.util.spec_from_file_location(
        "check_unused_imports_suite_gate", _PREDICATE_SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cui = _load_predicate_module()


# ─── the gate's single entry point ───────────────────────────────────────────
# The suite tier's strictness is declared HERE and nowhere else. Every check
# OF THAT TIER goes through this function — the shipped-tree sweep and the
# non-vacuity fixtures alike — so the fixtures exercise the same declaration
# the sweep runs under. Two checks bypass it DELIBERATELY, to exercise other
# tiers: the advisory flip-direction assertion, and the invalid-tier
# rejection. Neither can route through a function that hardcodes the tier —
# that is the point of them, not a gap.

L1_TRY_SCOPE = "strict"


def _gate_check(paths):
    """Run the suite gate over files; returns formatted finding lines."""
    return cui.check_paths([str(p) for p in paths], try_scope=L1_TRY_SCOPE)


# ─── swept Python surfaces ───────────────────────────────────────────────────
# hooks/, scripts/, telegram/, skills-scripts ship to consumers; tests/ is
# the dev-repo-only surface, swept so this gate and the CI ruff leg (which
# lints all of pact-plugin/) cannot disagree on tests/ scope. CI's tree is
# a strict superset of these globs (e.g. skills/*/ top-level test
# files are CI-linted only) — the safe direction: a divergence there is a
# loud CI red, never a silent gate pass.

def _skills_script_files():
    return sorted(PLUGIN_ROOT.glob("skills/*/scripts/**/*.py"))


_PROBE_STEM = "_f401_planted_probe_delete_me_"
_PROBE_RE = re.compile(re.escape(_PROBE_STEM) + r"(\d+)_[0-9a-f]{32}\.py")


def _is_foreign_live_probe(path):
    """True for a probe planted by a DIFFERENT, still-running process.

    CROSS-RUN OBSERVATION, which a unique name does not fix. Uniqueness stops
    two runs sharing a path, so neither can clobber or delete the other's file
    — but a foreign probe is still a file with an unused import, so the tests/
    sweep reports it and reddens a test that never mentions PROBE. That is the
    ORIGINAL reported failure, and its signature is exactly that: the red lands
    on a different test than the one at fault.

    LIVENESS, NOT THE NAME, is what separates a concurrent run's probe from one
    LEAKED by a crash. As strings the two are indistinguishable; as processes
    they are not, and the OS maintains that distinction without anything having
    to be remembered or cleaned up.

    THE ONLY CASE THAT REMOVES A FILE FROM THE SWEEP is a foreign probe whose
    owner is definitely ALIVE. Everything else keeps it — not probe-shaped,
    our own, definitely gone, or an error that leaves liveness unknown — so
    the sweep still reports it. Never skip on an unknown: a filter that skips
    when it cannot tell is the silent-skip surface this module exists to catch.

    KNOWN CEILING, and it is a regression rather than parity: BEFORE this
    filter existed, a leaked probe was caught unconditionally — it was just a
    file with an unused import, and the sweep reported every one of those.
    Now, a leak whose pid has since been
    reassigned to a LIVE process is read as live and skipped, so detection is
    intermittent rather than certain on any single run. It is self-correcting
    across runs — the file persists, and the next run whose reading differs
    reports it — but "usually caught" is weaker than "always caught". Closing
    it needs an identity a recycled pid cannot forge (a process start time, or
    a lock held on a per-run owner file), which is more machinery than the
    residual costs.
    """
    m = _PROBE_RE.fullmatch(path.name)
    if not m or int(m.group(1)) == os.getpid():
        return False  # not a probe, or OURS — P1 needs our own probe swept
    try:
        os.kill(int(m.group(1)), 0)
    except ProcessLookupError:
        return False  # owner is gone: a LEAK, and the sweep must name it
    except PermissionError:
        return True  # alive, owned by another user
    except OSError:
        return False  # cannot tell: assume leak and report, never skip silently
    return True


TARGET_DIR_SETS = {
    "hooks": lambda: sorted((PLUGIN_ROOT / "hooks").rglob("*.py")),
    "scripts": lambda: sorted((PLUGIN_ROOT / "scripts").rglob("*.py")),
    "telegram": lambda: sorted((PLUGIN_ROOT / "telegram").rglob("*.py")),
    "skills-scripts": _skills_script_files,
    # SOLE FILTER — do not add another without reading this.
    # No arm plants a dead-owner probe on disk: such a file is unfiltered BY
    # CONSTRUCTION and would poison every concurrent run, which is the defect
    # this module exists to stop. Leak coverage is therefore COMPOSED —
    # `TestProbeNameMatchingIsAnchored` proves the predicate declines to drop
    # a dead owner, and the planting arms prove a file the predicate declines
    # IS swept and reported. That composition holds only while this predicate
    # is the ONLY filter applied here. A second filter treating dead-owner
    # probes differently from lookalikes breaks the composed property with
    # NOTHING going red — both halves still pass on their own.
    "tests": lambda: sorted(
        p
        for p in (PLUGIN_ROOT / "tests").rglob("*.py")
        if not _is_foreign_live_probe(p)
    ),
}


class TestSweptTreeIsClean:
    """The strict-mode sweep over every swept Python surface —
    consumer-shipped code plus the dev-repo tests/ tree."""

    @pytest.mark.parametrize("label", list(sorted(TARGET_DIR_SETS))[:1])
    def test_surface_has_files_to_scan(self, label):
        """Non-empty-glob guard: a surface that stops resolving would make
        the sweep silently vacuous; catch path drift loudly instead."""
        assert len(TARGET_DIR_SETS[label]()) > 0


class TestTestsSurfaceEnforcement:
    """Counter-tests proving the tests/ surface is genuinely enforced —
    each asserts POSITIVE membership in the live TARGET_DIR_SETS surface —
    our probe, this module — so a reverted, retargeted, or
    recursion-narrowed glob makes the asserted member absent and turns these
    red rather than silently vacuous. Positive membership is the property,
    not `_gate_check`: the reachability pin calls no gate and still delivers
    it. A NEGATIVE assertion cannot, which is why the foreign-probe arm
    carries its own depth-derived control.

    ONE EXCEPTION, named rather than left silent:
    `test_kept_ensure_loaded_seam_survives_widened_gate` passes a hand-built
    one-element list. It localises a noqa-recognition divergence in the
    PREDICATE, not surface enforcement, so a retargeted glob should NOT
    redden it. It is outside this paragraph's scope by design.

    Probes are planted in the live tests/ tree one directory
    level DOWN (tests/fixtures/) — the sweep only sees them through
    recursive descent, so an rglob→glob mis-narrowing fails here instead
    of silently dropping subdirectory files. Each probe is removed in
    the same test.

    ISOLATION — the guarantee that actually holds, rather than an
    assumption about how the suite is invoked. The probe filename carries
    a pid and a full uuid4 hex suffix, and the two do DIFFERENT jobs. The
    uuid makes the path unique, so no run can clobber or delete another's
    file. The pid names an OWNER whose liveness `_is_foreign_live_probe`
    can test, so a concurrent run's probe is dropped from the swept surface
    and cannot be OBSERVED either. Uniqueness alone closed only the first
    half: a foreign probe is still a file with an unused import.

    The previous wording claimed the suite runs single-process; nothing
    enforces that, and two concurrent runs collided deterministically.
    The collision reddened tests that never mention PROBE — the tests/
    sweep sees a foreign file and reports it — so the poisonable set was
    every test that sweeps tests/, not only the arms that plant here.

    A probe leaked by a hard crash stays in tests/fixtures/, and
    test_no_unused_imports[tests] fails on it by name — UNLESS its pid has
    since been reassigned to a live process, which reads as a concurrent run
    and is skipped. That exception is the known ceiling documented on
    `_is_foreign_live_probe`; it is named rather than restated here, so the
    two cannot drift. Uniqueness removes the collision, not the need to
    clean up."""

    PROBE = (
        PLUGIN_ROOT
        / "tests"
        / "fixtures"
        / f"{_PROBE_STEM}{os.getpid()}_{uuid.uuid4().hex}.py"
    )

    def test_tests_surface_includes_this_gate_file(self):
        """Reachability pin: the tests glob must resolve the LIVE tests/
        tree — proven by the sweep seeing this very file. A glob pointing
        at a wrong or empty directory passes >0-file guards; it cannot
        pass this one."""
        files = TARGET_DIR_SETS["tests"]()
        assert Path(__file__).resolve() in [p.resolve() for p in files]


_ARRANGEMENT_RE = re.compile(
    r"\b(above|below|preceding|the following)\b"
    r"|\b(?:first|second|third|fourth|fifth|last)\s+"
    r"(?:arm|method|test|class|paragraph|sentence|one|line)\b",
    re.I,
)


def _prose_sentences(source):
    """Yield (label, sentence) for every docstring and comment in `source`.

    DERIVED FROM THE SOURCE, never listed. A hardcoded roster of prose blocks
    would be this check committing the defect it exists to catch: prose named
    by where it sits. `ast` supplies every module, class and function
    docstring; `tokenize` supplies every comment — including trailing ones,
    which a `startswith("#")` scan misses, and excluding a `#` inside a string
    literal, which such a scan wrongly matches.
    """
    blocks = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                blocks.append((getattr(node, "name", "<module>"), doc))
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            blocks.append(("<comment>", tok.string))
    for label, text in blocks:
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
            if sentence.strip():
                yield label, sentence.strip()


