"""
Location: pact-plugin/tests/contract/test_shell_package.py
Summary: Code-quality pins for the shell lane (the design notes §2):
         C2 one fail-closed emitter in the tree and every site denies through it.
         C5 the import direction inside hooks/shell is acyclic, with one declared
         call-time edge and no `shared` import; C6 every shell module carries a header
         and fits its line and byte ceilings; the merge_guard_common façade propagates
         a setattr to the package module that reads the name (test monkeypatches).
         Later steps add C3 (one tokeniser) and C4 (policy is data).
Used by: the contract suite; run-tests QUICK.
"""
from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent
HOOKS = PLUGIN / "hooks"

# Module name -> import path. Each binds `_emit_load_failure_deny` to the shared emitter.
FAIL_CLOSED_SITES = {
    "bootstrap_gate": "bootstrap_gate",
    "dispatch_gate": "dispatch_gate",
    "worktree_guard": "worktree_guard",
    "git_commit_check": "git_commit_check",
    "merge_guard_pre": "merge_guard_pre",
    "dispatch_helpers": "shared.dispatch_helpers",
}


def _py_sources():
    return [p for p in HOOKS.rglob("*.py") if ".venv" not in p.parts]


def test_c2_exactly_one_fail_closed_emitter_definition():
    """One `def emit_load_failure_deny` under hooks/ and no private twin anywhere."""
    canonical = [p for p in _py_sources()
                 if re.search(r"^def emit_load_failure_deny\(", p.read_text(), re.M)]
    twins = [p for p in _py_sources()
             if re.search(r"^def _emit_load_failure_deny\(", p.read_text(), re.M)]
    assert [p.name for p in canonical] == ["_fail_closed.py"], canonical
    assert twins == [], [str(p) for p in twins]


def test_c2_one_bounded_error_renderer():
    """The twin renderer of bootstrap_gate and task_lifecycle_gate is the shared one."""
    renderers = [p for p in _py_sources()
                 if re.search(r"^def _?bounded_error_text\(", p.read_text(), re.M)]
    assert [p.name for p in renderers] == ["_fail_closed.py"], renderers


@pytest.mark.parametrize("check", sorted(FAIL_CLOSED_SITES))
def test_every_site_denies_through_the_shared_emitter(check, capsys):
    """Each site's `_emit_load_failure_deny(stage, error)` prints the deny envelope
    naming the check, with hookEventName, one stderr line, exit 2."""
    module = importlib.import_module(FAIL_CLOSED_SITES[check])
    with pytest.raises(SystemExit) as exc:
        module._emit_load_failure_deny("module imports", RuntimeError("boom"))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == (
        f"PACT {check} module imports failure — blocking for safety. "
        "RuntimeError: boom. Check hook installation and shared module availability."
    )
    assert captured.err.strip() == f"Hook load error ({check} / module imports): boom"


def test_emitter_survives_a_hostile_error(capsys):
    """A raising __str__ must not turn the exit 2 into a traceback exit 1."""
    from _fail_closed import emit_load_failure_deny

    class Hostile(Exception):
        def __str__(self):
            raise ValueError("no text for you")

    with pytest.raises(SystemExit) as exc:
        emit_load_failure_deny("probe", "runtime", Hostile())
    assert exc.value.code == 2
    captured = capsys.readouterr()
    reason = json.loads(captured.out.strip())["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Hostile: <exception str() raised>" in reason
    assert "<exception str() raised>" in captured.err


# ─── C5: the import direction inside hooks/shell is acyclic and downward ──────────────
SHELL = HOOKS / "shell"
# No edge is resolved at call time (a function-local import). The two upward recursions
# (merge: carrier 10 asks the classifier about a masked leg; env: a sub-shell, heredoc or
# script body is judged by the script layer above command_verdict) are wired by registration
# when the upper module loads (classify sets inert_legs._surface_danger, script_verdict sets
# judge._UPPER), so the import graph stays downward and tests/contract/test_import_layers.py
# sees no cycle.
CALL_TIME_EDGES: set[tuple[str, str]] = set()
# The one upward edge (design C5, listed for LAYERING to sever): tokens.py loads the
# config-dir SSOT by file, so nothing under hooks/shell imports the `shared` package.
UPWARD_EDGE_FILE = SHELL / "merge" / "tokens.py"


def _shell_modules():
    for path in sorted(SHELL.rglob("*.py")):
        name = ".".join(("shell", *path.relative_to(SHELL).with_suffix("").parts))
        yield name.removesuffix(".__init__"), path


def _imports(path):
    """(module-level targets, function-level targets) of one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top, nested = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {node.module or ""}
            if node.module in ("shell", "shell.merge", "shell.env"):   # `from shell.merge import x`
                names = {f"{node.module}.{alias.name}" for alias in node.names}
        else:
            continue
        (top if node.col_offset == 0 else nested).update(names)
    return top, nested


def test_c5_nothing_under_shell_imports_the_shared_package():
    offenders = {name: sorted(t | n) for name, path in _shell_modules()
                 for t, n in [_imports(path)]
                 if any(target.split(".")[0] == "shared" for target in t | n)}
    assert offenders == {}, offenders
    assert 'from shell.words import' not in SHELL.joinpath("words.py").read_text(encoding="utf-8")
    assert _imports(SHELL / "words.py") == ({"__future__"}, set())
    assert _imports(SHELL / "__init__.py") == ({"__future__"}, set())
    assert 'spec_from_file_location("_pact_shell_paths"' in UPWARD_EDGE_FILE.read_text(encoding="utf-8")


def test_c5_module_level_edges_are_acyclic_and_the_call_time_edge_is_the_only_one():
    edges, nested_edges = {}, set()
    for name, path in _shell_modules():
        top, nested = _imports(path)
        edges[name] = {t for t in top if t.startswith("shell.")}
        nested_edges |= {(name, t) for t in nested if t.startswith("shell.")}
    assert nested_edges == CALL_TIME_EDGES, nested_edges
    # Kahn: every module drains when the graph is acyclic.
    remaining = {m: set(deps) for m, deps in edges.items()}
    while remaining:
        free = [m for m, deps in remaining.items() if not deps - (set(edges) - set(remaining))]
        assert free, f"import cycle among {sorted(remaining)}"
        for m in free:
            del remaining[m]
    assert all(target in edges or target == "shell.words" for deps in edges.values()
               for target in deps), "an import names a module that is not in the package"


# ─── C6: headers and ceilings ────────────────────────────────────────────────────────
LINE_CEILING = 700
# Known limit: shell_words.py was 751 lines when it moved to shell/words.py, and the merge
# guard's three lenient scanners added 78. It gets its own ceiling until a tokeniser
# retirement (step 15) or a reader split brings it under the common one.
LINE_CEILINGS = {"shell.words": 850}
BYTE_CAP = 40_000              # the lane's cap (design target 36 K)


@pytest.mark.parametrize("name,path", list(_shell_modules()), ids=lambda v: v if isinstance(v, str) else "")
def test_c6_every_shell_module_has_a_header_and_fits(name, path):
    text = path.read_text(encoding="utf-8")
    ceiling = LINE_CEILINGS.get(name, LINE_CEILING)
    assert text.count("\n") <= ceiling, f"{name}: {text.count(chr(10))} lines, ceiling {ceiling}"
    assert len(text.encode("utf-8")) <= BYTE_CAP, f"{name}: {len(text.encode())} bytes"
    if path.name == "__init__.py":
        return
    head = text[:4000]
    for marker in ("Location:", "Summary:", "Used by:"):
        assert marker in head, f"{name}: header lacks {marker}"
    if path.parent.name == "merge":
        assert "Map:" in head, f"{name}: header lacks Map:"


# ─── the merge_guard_common façade: a patch on it reaches the package reader ─────────
def test_facade_setattr_propagates_to_the_package_and_undoes():
    import shared.merge_guard_common as mgc
    import shell.merge.arms as arms
    import shell.merge.classify as classify

    original = classify._PER_LEG_OPS
    assert arms._PER_LEG_OPS is original and mgc._PER_LEG_OPS is original
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mgc, "_PER_LEG_OPS", frozenset())
        assert classify._PER_LEG_OPS == frozenset() and arms._PER_LEG_OPS == frozenset()
        assert mgc._PER_LEG_OPS == frozenset()
    assert classify._PER_LEG_OPS is original and arms._PER_LEG_OPS is original
    assert mgc._PER_LEG_OPS is original


# ── C3: one tokeniser ────────────────────────────────────────────────────────────────────

BIN = HOOKS.parent / "bin"
_QUOTE_CONSTANTS = {"'", '"', "'\"", "\"'"}
# Modules under hooks/shell other than words.py that still compare a character against a
# quote (a value's own quotes stripped, a dq-inner scan in views). Known limit: each count may
# fall, never rise, and no other module may join (step 15; design C3 names words.py alone).
RESIDUAL_QUOTE_READERS = {
    "shell.env.script": 4, "shell.env.script_verdict": 1, "shell.merge.checkout": 2,
    "shell.merge.remotes": 1, "shell.merge.views": 20,
    # The merge guard's one context scanner (carrier 1, the two-reading join, awk's computed
    # text). words.read_script cannot serve it: read_script raises Unreadable where this scan
    # must go on leniently, keep every offset and skip each heredoc body by the close rule.
    # words.py is also at its 40,000 B ceiling. strip.py reads no quote of its own.
    "shell.merge.heredocs": 12,
}


def _python_sources(root):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.suffix == ".py" or (text.startswith("#!") and "python" in text.split("\n", 1)[0]):
            try:
                yield path, ast.parse(text)
            except SyntaxError:
                continue


def test_c3_no_shlex_split_call_under_hooks_or_bin():
    """Producers keep shlex.quote/join (quoting for execution); every reader goes through
    shell.words. A docstring that names shlex.split is prose, so the pin reads the AST."""
    hits = []
    for path, tree in list(_python_sources(HOOKS)) + list(_python_sources(BIN)):
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "split"
                    and isinstance(node.value, ast.Name) and node.value.id == "shlex"):
                hits.append(f"{path}:{node.lineno}")
            if (isinstance(node, ast.ImportFrom) and node.module == "shlex"
                    and any(alias.name == "split" for alias in node.names)):
                hits.append(f"{path}:{node.lineno} from-import")
    assert hits == [], hits


def test_c3_words_is_the_only_module_that_reads_a_quote_character():
    counts = {}
    for path, tree in _python_sources(SHELL):
        name = "shell." + ".".join(path.relative_to(SHELL).with_suffix("").parts)
        n = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and node.value in _QUOTE_CONSTANTS)
        if n and name != "shell.words":
            counts[name] = n
    assert set(counts) <= set(RESIDUAL_QUOTE_READERS), set(counts) - set(RESIDUAL_QUOTE_READERS)
    grown = {name: (n, RESIDUAL_QUOTE_READERS[name]) for name, n in counts.items()
             if n > RESIDUAL_QUOTE_READERS[name]}
    assert not grown, grown
