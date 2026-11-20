"""
Location: pact-plugin/tests/test_check_unused_imports.py
Summary: Unit tests for the unused-import predicate in
         skills/pact-coding-standards/scripts/check_unused_imports.py —
         the pure function's carve-outs, suppression mechanics, try-scope
         strictness contract, and the CLI's exit/output contract.
Used by: pytest suite. The suite-level import-hygiene gate over the shipped
         tree lives in its own test module (TEST-phase work); these tests
         cover the predicate itself.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "check_unused_imports.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_unused_imports", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cui = _load_module()


def _names(source, try_scope="strict"):
    """Finding names only, for terse assertions."""
    return [f.name for f in cui.find_unused_imports(source, try_scope=try_scope)]


class TestBasicDetection:
    def test_unused_import_detected(self):
        assert _names("import os\n") == ["os"]


class TestNoqaSuppression:
    def test_noqa_f401_suppresses(self):
        assert _names("import os  # noqa: F401\n") == []


class TestCarveOuts:
    def test_future_import_never_flagged(self):
        assert _names("from __future__ import annotations\n") == []


class TestUsageContext:
    """Usage is READS only: Load/Del-context names (plus AugAssign targets,
    which read before rebinding). Pure Store bindings shadow an import
    without reading it and must not mask the finding."""

    def test_pure_store_shadow_does_not_mask(self):
        assert _names("import os\nos = None\n") == ["os"]


class TestTryScopeStrictness:
    def test_strict_flags_try_scoped_import(self):
        src = "try:\n    import os\nexcept ImportError:\n    pass\n"
        assert _names(src, try_scope="strict") == ["os"]


class TestSyntaxErrorBehavior:
    def test_pure_function_propagates_syntax_error(self):
        with pytest.raises(SyntaxError):
            cui.find_unused_imports("def broken(:\n", try_scope="strict")


