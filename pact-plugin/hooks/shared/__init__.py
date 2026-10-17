"""
Location: pact-plugin/hooks/shared/__init__.py
Summary: The shared library tier of the plugin; the layer map is §1 of
         the design notes. This file is a docstring on purpose:
         it imports nothing and re-exports nothing, so `from shared.paths import ...` loads one
         module, not the package graph (it used to pull eleven modules, the merge guard among
         them, into every hook).
Used by: every hook and bin script that imports `shared.<module>`; `hooks/` is on sys.path for
         every check (hooks/dispatch.py) and every test (tests/conftest.py), and a bin script
         inserts `<plugin>/hooks` once before its first `shared` import.
Rules: a shared module imports shared siblings (acyclic), `shell.*` and the stdlib, never a hook
       entry module and never `bin/`; tests/contract/test_import_layers.py walks the graph.
       Names live in their owning module only: `shared.constants.BOOTSTRAP_MARKER_NAME`,
       `shared.pact_context.get_team_name`, `shared.gh_helpers.check_pr_state`, and so on.
"""

from __future__ import annotations
