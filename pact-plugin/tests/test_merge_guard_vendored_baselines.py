"""
Location: pact-plugin/tests/test_merge_guard_vendored_baselines.py
Summary: pins the certification bases that the baked-SHA certification files load
         through `load_vendored`. Each base is a captured decision table
         (tests/fixtures/shell/decisions/base_<sha8>.jsonl; integrity and size are
         pinned in tests/test_shell_decision_tables.py).
Used by: the suite. Path setup is conftest-owned; see tests/test_path_setup_pin.py.

A certification file certifies nothing unless two things hold:
  1. every commit a cert file loads is in the loader's table, so no cert reaches
     for a base that has no decision table;
  2. no cert file reads git history or carries a skip marker, so a missing base
     or row is a failure and never a silent pass.
"""

import ast
from pathlib import Path

import pytest

import merge_guard_baseline_loader as loader

TESTS_DIR = Path(__file__).resolve().parent

# Certification files that load their bases through load_vendored. A file joins
# this list in the same commit that removes its skips and its git calls.
_CERT_FILES = [
    "test_merge_guard_1118_mechanism.py",
    "test_merge_guard_1129_r2_mechanism.py",
]


def _cert_tree(name):
    return ast.parse((TESTS_DIR / name).read_text(encoding="utf-8"))


def _loaded_shas(tree):
    """Every commit a module passes to load_vendored, resolving module-level
    string constants. An argument it cannot resolve is reported, not dropped."""
    constants = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    shas = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "load_vendored":
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            shas.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in constants:
            shas.add(constants[arg.id])
        else:
            shas.add("<unresolved: %s>" % ast.unparse(arg))
    return shas


def _history_and_skip_sites(tree):
    """Line-tagged skip markers, pytest.skip calls, subprocess calls whose
    argument names git, and `if` tests that compare a baseline classifier
    (a `D_` name) with None, which skip a row as silently as a marker."""
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "skipif":
            sites.append("L%d skipif" % node.lineno)
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func in ("pytest.skip", "skip"):
                sites.append("L%d %s()" % (node.lineno, func))
            if "subprocess" in func or func in ("check_output", "run", "Popen"):
                argv = ast.unparse(node.args[0]) if node.args else ""
                if "'git'" in argv or '"git"' in argv:
                    sites.append("L%d %s(%s)" % (node.lineno, func, argv[:60]))
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "None" in test and any(
                isinstance(n, ast.Name) and n.id.startswith("D_") for n in ast.walk(node.test)
            ):
                sites.append("L%d if %s" % (node.lineno, test[:60]))
    return sites


@pytest.mark.parametrize("cert", _CERT_FILES)
def test_every_sha_a_cert_file_loads_is_vendored(cert):
    shas = _loaded_shas(_cert_tree(cert))
    assert shas, "%s loads no base through load_vendored" % cert
    missing = sorted(s for s in shas if s not in loader._VENDORED)
    assert not missing, (
        "%s loads bases with no vendored fixture: %s. Vendor each one, or the "
        "cert fails at import" % (cert, missing)
    )


@pytest.mark.parametrize("cert", _CERT_FILES)
def test_no_cert_file_reads_git_history_or_skips(cert):
    sites = _history_and_skip_sites(_cert_tree(cert))
    assert not sites, (
        "%s still reads git history or skips: %s. A certification file must load "
        "its bases through load_vendored and fail, never skip, when one is "
        "missing" % (cert, sites)
    )
