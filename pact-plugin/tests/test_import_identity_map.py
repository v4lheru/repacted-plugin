"""Tests for tests/import_identity_map.py — the layer-4 harness prototype.

Mechanism tests use synthetic fixtures; live-session tests assert only what
conftest.py guarantees in EVERY invocation shape (hooks/ on sys.path, so
`shared` resolves) — bare `working_memory` needs a carrier insert and is
asserted conditionally so this file passes STANDALONE as well as in the gate.
"""

import textwrap

from import_identity_map import (
    imported_names,
)


def _write(tmp_path, source):
    f = tmp_path / "test_sample.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_imported_names_catches_module_and_function_level(tmp_path):
    f = _write(
        tmp_path,
        """\
        import os
        import working_memory
        from shared.marker_schema import MARKER_SCHEMA_VERSION

        def test_x():
            from bootstrap_gate import main
            import scripts.config
        """,
    )
    names = imported_names(f)
    assert "os" in names
    assert "working_memory" in names
    assert "shared.marker_schema" in names  # full dotted path, not 'shared'
    assert "bootstrap_gate" in names        # function-level import found
    assert "scripts.config" in names


def test_imported_names_skips_relative_imports(tmp_path):
    f = _write(tmp_path, "from .helpers import thing\n")
    assert imported_names(f) == set()


def _fresh_state(monkeypatch):
    import import_identity_map as iim

    monkeypatch.setattr(
        iim, "_state", {"sessionstart_path": None, "collected_files": set()}
    )
    return iim


