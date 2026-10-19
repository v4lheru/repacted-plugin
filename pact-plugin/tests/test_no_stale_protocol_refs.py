"""No model-facing file points at a protocol that moved (CLEANUP-A).

the protocol consolidation folded the protocols into single-source files and left one-line "> Moved: see ..."
stubs behind. A link or a "per <file>" pointer into such a stub, or into a path that does not
exist from the linking file's directory, sends the reader to a dead end. Model-facing = every
markdown file under agents/, commands/, protocols/, reference/ and skills/.

A stub that keeps its headings, each with its own "> Moved" line, is a forwarding table: links
into it stay allowed. None is left since 1.3.8 deleted skills/pact-agent-teams; whether a named
skill, protocol or reference file exists at all is tests/contract/test_pointers_exist.py.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MODEL_FACING = ("agents", "commands", "protocols", "reference", "skills")
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
STUB = re.compile(r"\A(?:---\n.*?\n---\n\s*)?> Moved: see ", re.S)


def model_facing_files() -> list[Path]:
    return sorted(p for d in MODEL_FACING for p in (PLUGIN_ROOT / d).rglob("*.md"))


def is_stub(path: Path) -> bool:
    return bool(STUB.match(path.read_text(encoding="utf-8")))


def is_dead_end(path: Path) -> bool:
    """A moved-stub with no forwarding headings of its own."""
    return is_stub(path) and not re.search(r"^#{1,6} ", path.read_text(encoding="utf-8"), re.M)


def test_the_stub_detector_sees_a_moved_file(tmp_path):
    """Guards the check below against going vacuous: a stub is a stub, a live file is not.
    1.5.0 deleted the last protocol stubs (pact-variety.md, pact-completion-authority.md)."""
    forwarding = tmp_path / "forwarding.md"
    forwarding.write_text("> Moved: see workflows.md §Variety.\n\n#### A heading\n\n> Moved: see x.md.\n")
    dead_end = tmp_path / "dead-end.md"
    dead_end.write_text("> Moved: see completion.md §Completion Authority.\n")
    assert is_stub(forwarding) and is_stub(dead_end)
    assert not is_stub(PLUGIN_ROOT / "protocols" / "workflows.md")
    assert is_dead_end(forwarding) is False  # it forwards one heading
    assert is_dead_end(dead_end)


def test_no_link_resolves_to_a_missing_or_moved_file():
    dead = []
    for path in model_facing_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in LINK.finditer(line):
                if match.group(1).startswith(("http:", "https:")):
                    continue
                target = (path.parent / match.group(1)).resolve()
                in_protocols = target.parent == (PLUGIN_ROOT / "protocols").resolve()
                if not target.is_file() or (is_stub(target) and (in_protocols or is_dead_end(target))):
                    state = "missing" if not target.is_file() else "moved-stub"
                    dead.append(f"{path.relative_to(PLUGIN_ROOT)}:{n}: {match.group(1)} ({state})")
    assert not dead, "links into missing or moved files:\n" + "\n".join(dead)


def test_no_per_pointer_names_the_moved_variety_protocol():
    hits = [f"{p.relative_to(PLUGIN_ROOT)}:{n}"
            for p in model_facing_files()
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "per pact-variety.md" in line]
    assert not hits, hits


def _docstring_nodes(tree) -> set[int]:
    import ast
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


# Protocol files that were deleted; a hook string naming one sends the model to nothing.
RETIRED_PROTOCOLS = (
    "pact-agent-stall.md", "pact-audit.md", "pact-completeness.md", "pact-completion-authority.md",
    "pact-ct-teachback.md", "pact-phase-transitions.md", "pact-protocols.md", "pact-s1-autonomy.md",
    "pact-s2-coordination.md", "pact-s4-checkpoints.md", "pact-s4-environment.md",
    "pact-s4-tension.md", "pact-s5-policy.md", "pact-scope-contract.md", "pact-scope-detection.md",
    "pact-scope-phases.md", "pact-state-recovery.md", "pact-task-hierarchy.md", "pact-variety.md",
    "pact-workflows.md",
)


def test_no_hook_message_sends_the_model_to_a_moved_protocol():
    """Hook strings reach the model (advisories, deny reasons). None may name a protocol file that
    is a one-line "> Moved" stub or was deleted (RETIRED_PROTOCOLS); comments and docstrings are
    for maintainers and not scanned."""
    import ast
    moved = sorted({p.name for p in (PLUGIN_ROOT / "protocols").glob("*.md") if is_stub(p)}
                   | set(RETIRED_PROTOCOLS))
    assert "pact-variety.md" in moved  # the deleted protocols are in the list
    hits = []
    for path in sorted((PLUGIN_ROOT / "hooks").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                for name in moved:
                    if name in node.value:
                        hits.append(f"{path.relative_to(PLUGIN_ROOT)}:{node.lineno}: {name}")
    assert not hits, hits


def test_the_readme_links_only_live_plugin_files():
    readme = PLUGIN_ROOT.parent / "README.md"
    dead = [target for target in re.findall(r"\]\((pact-plugin/[^)#\s]+)(?:#[^)]*)?\)", readme.read_text(encoding="utf-8"))
            if not (PLUGIN_ROOT.parent / target).exists()]
    assert not dead, dead
