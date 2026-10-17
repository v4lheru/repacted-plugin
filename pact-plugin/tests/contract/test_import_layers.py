"""tests/contract/test_import_layers.py: one dependency direction between hooks/ and bin/.

Layer map (the design notes §1): entry (hooks/<check>.py and
bin/) -> shared (hooks/shared/) -> shell (hooks/shell/) -> stdlib. Every `.py` under hooks/ and
bin/ and every `#!` script in bin/ is parsed with `ast`; every Import / ImportFrom at any depth
counts (a lazy import inside a function is the edge that bites at runtime), a relative one
(`from .x import y`, resolved against the importer's package) as much as an absolute one, and
every source line that inserts a bin/ directory into sys.path counts as an edge too.

Rules: R1 nothing under hooks/ imports bin/; R2 nothing under bin/ imports a hook entry module;
R3 shared/ and shell/ import no entry module; R4 shell/ imports a shared module only when that
module is a leaf (imports nothing from the plugin); R5 no import cycle anywhere; R6 no sys.path
insertion toward bin/ under hooks/. Every rule holds with no exception: the allowlists the
layering pass started from (33 edges, 3 cycles, 17 sys.path sites) were emptied and deleted.

One module sits below every layer: hooks/_fail_closed.py, the stdlib-only deny emitter a gate
imports BEFORE its import gauntlet so it can still deny when shared/ is absent or broken (the
degraded-install vectors in test_bootstrap_gate and test_task_lifecycle_gate_degraded). It is
the one top-level module shared/ and the gates may import; it must import nothing but the stdlib.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
HOOKS = PLUGIN_ROOT / "hooks"
SYS_PATH_TO_BIN = re.compile(r"""sys\.path\.(insert|append)\(.*(BIN_DIR|bin_dir|["']bin["']|/bin["'])""")
# The stdlib-only emitter (module docstring): below every layer, so an edge INTO it is never forbidden.
STDLIB_EMITTER = "hooks/_fail_closed"
EMITTER_IMPORTS = frozenset({"__future__", "json", "sys", "typing"})


def _files(root: Path) -> list[Path]:
    hooks, bin_dir = root / "hooks", root / "bin"
    out = [p for p in hooks.rglob("*.py") if "__pycache__" not in p.parts]
    out += [p for p in bin_dir.rglob("*.py") if "__pycache__" not in p.parts]
    out += [p for p in bin_dir.iterdir() if p.is_file() and p.suffix == "" and p.read_bytes()[:2] == b"#!"]
    return sorted(out)


def _module_id(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return str(rel.with_suffix("")) if rel.suffix == ".py" else str(rel)


def _relative_base(mid: str, level: int) -> str | None:
    """The dotted package a `from .`-import of `level` dots names inside module `mid`, or None."""
    package = mid.split("/")[1:-1]          # drop the hooks/ or bin/ root and the module itself
    if level > len(package):
        return None
    return ".".join(package[:len(package) - level + 1])


def _layer(mid: str) -> str:
    if mid.startswith("hooks/shared/"):
        return "shared"
    if mid.startswith("hooks/shell/"):
        return "shell"
    return "entry" if mid.startswith("hooks/") else "bin"


class Graph:
    """Module ids, their plugin-internal import edges and the bin-ward sys.path sites."""

    def __init__(self, root: Path = PLUGIN_ROOT) -> None:
        self.bin_dir = root / "bin"
        self.ids = {_module_id(p, root): p for p in _files(root)}
        self.bin_top = {p.stem if p.suffix == ".py" else p.name for p in self.bin_dir.iterdir()
                        if p.name != "__pycache__"}
        self.hook_top = {p.stem for p in (root / "hooks").glob("*.py")}
        self.edges: dict[str, set[str]] = defaultdict(set)
        self.path_sites: set[str] = set()
        for mid, path in self.ids.items():
            src = path.read_text("utf-8", "replace")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue  # a bin script that is not Python 3 source (bin/pact, bin/repact)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level > 0:
                    base = _relative_base(mid, node.level)
                    if base is None:
                        continue
                    module = ".".join(part for part in (base, node.module) if part)
                    names = [module] + [f"{module}.{a.name}" for a in node.names]
                else:
                    continue
                for name in names:
                    dst = self._resolve(name)
                    if dst and dst != mid:
                        self.edges[mid].add(dst)
            if mid.startswith("hooks/") and any(SYS_PATH_TO_BIN.search(line) for line in src.splitlines()):
                self.path_sites.add(mid)

    def _resolve(self, name: str) -> str | None:
        head, *rest = name.split(".")
        if head == "shared":
            mid = "hooks/shared/" + "/".join(rest) if rest else "hooks/shared/__init__"
            if mid in self.ids:
                return mid
            parent = "hooks/shared/" + "/".join(rest[:-1] + ["__init__"]) if len(rest) > 1 else "hooks/shared/__init__"
            return parent if parent in self.ids else "hooks/shared/__init__"
        if head == "shell":
            mid = "hooks/shell/" + "/".join(rest) if rest else "hooks/shell/__init__"
            if mid in self.ids:
                return mid
            parent = "hooks/shell/" + "/".join(rest[:-1] + ["__init__"]) if len(rest) > 1 else "hooks/shell/__init__"
            return parent if parent in self.ids else None
        if head in self.hook_top:
            return f"hooks/{head}"
        if head in self.bin_top:
            if (self.bin_dir / head).is_dir():
                mid = f"bin/{head}/{rest[0]}" if rest else f"bin/{head}/__init__"
                return mid if mid in self.ids else f"bin/{head}/__init__"
            return f"bin/{head}"
        return None

    def leaves(self) -> set[str]:
        return {mid for mid in self.ids if not self.edges.get(mid)}

    def forbidden_edges(self) -> set[tuple[str, str]]:
        leaves = self.leaves()
        bad = set()
        for src, dsts in self.edges.items():
            for dst in dsts:
                if dst == STDLIB_EMITTER:
                    continue
                ls, ld = _layer(src), _layer(dst)
                if ls != "bin" and ld == "bin":                      # R1
                    bad.add((src, dst))
                elif ls == "bin" and ld == "entry":                  # R2
                    bad.add((src, dst))
                elif ls in ("shared", "shell") and ld == "entry":    # R3
                    bad.add((src, dst))
                elif ls == "shell" and ld == "shared" and dst not in leaves:   # R4
                    bad.add((src, dst))
        return bad

    def cycles(self) -> set[tuple[str, ...]]:
        """Strongly connected components of two or more modules (Tarjan, iterative)."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on: set[str] = set()
        stack: list[str] = []
        found: set[tuple[str, ...]] = set()
        counter = 0
        for root in list(self.edges):
            if root in index:
                continue
            work = [(root, iter(sorted(self.edges.get(root, ()))))]
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on.add(root)
            while work:
                v, it = work[-1]
                advanced = False
                for w in it:
                    if w not in index:
                        index[w] = low[w] = counter
                        counter += 1
                        stack.append(w)
                        on.add(w)
                        work.append((w, iter(sorted(self.edges.get(w, ())))))
                        advanced = True
                        break
                    if w in on:
                        low[v] = min(low[v], index[w])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        found.add(tuple(sorted(comp)))
        return found


GRAPH = Graph()


def test_no_forbidden_edge():
    assert GRAPH.forbidden_edges() == set(), sorted(GRAPH.forbidden_edges())


def test_no_cycle():
    assert GRAPH.cycles() == set(), sorted(GRAPH.cycles())


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_a_cycle_through_relative_imports_fails_r5(tmp_path):
    """The session_state <-> pact_context cycle shipped while relative imports were skipped: a
    cycle spelled `from .x import`, one edge lazy as it was there, must still be found."""
    graph = Graph(_write_tree(tmp_path, {
        "hooks/shared/__init__.py": "",
        "hooks/shared/a.py": "from .b import x\n",
        "hooks/shared/b.py": "def f():\n    from . import a\nx = 1\n",
        "hooks/shared/pkg/__init__.py": "from ..a import x\n",
    }))
    assert graph.cycles() == {("hooks/shared/a", "hooks/shared/b")}
    assert "hooks/shared/a" in graph.edges["hooks/shared/pkg/__init__"]


def test_no_sys_path_insertion_toward_bin_under_hooks():
    assert GRAPH.path_sites == set(), sorted(GRAPH.path_sites)


def test_the_fail_closed_emitter_imports_only_the_stdlib():
    """The exemption's price: the emitter has no plugin edge and names only these stdlib modules."""
    tree = ast.parse(GRAPH.ids[STDLIB_EMITTER].read_text("utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= EMITTER_IMPORTS, sorted(names - EMITTER_IMPORTS)
    assert not GRAPH.edges.get(STDLIB_EMITTER), sorted(GRAPH.edges[STDLIB_EMITTER])


def test_the_shared_package_is_a_docstring():
    tree = ast.parse((HOOKS / "shared" / "__init__.py").read_text("utf-8"))
    # The one statement beside the docstring is the compiler directive test_py39_annotation_compat
    # requires of every module; it imports and re-exports nothing.
    allowed = [isinstance(node, ast.Expr) or (isinstance(node, ast.ImportFrom) and node.module == "__future__")
               for node in tree.body]
    assert all(allowed), "shared/__init__.py re-exports nothing"
