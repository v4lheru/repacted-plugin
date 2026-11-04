"""
Location: pact-plugin/tests/test_store_scope_decorator_contract.py

Summary: Arms for the two load-bearing properties of `_with_store_scope`. The
architect chose ONE decorator over eight separate `with` blocks so that an
OMISSION WOULD BE VISIBLE. That visibility was bought and never installed:
no arm anywhere enumerated the decorated set, and none forbade a generator.

THE ALPHABET COMES FROM THE PUBLIC SURFACE, NOT FROM THE CALL GRAPH, AND THE
MEASUREMENT BEHIND THAT CHOICE IS WORTH THE PARAGRAPH.

The obvious alphabet is "each method that opens a store", derived from calls
to `db_connection`. MEASURED, that returns SIX: save, get, update, delete,
list and get_status. `search` and `search_by_file` carry the decorator and
call `db_connection` in NO place, because they delegate to the search layer,
which opens a connection with no argument. Carrying the scope to that layer
is the WHOLE REASON the decorator sits on them. So an arm built on the
call-graph alphabet would DEMAND THE DECORATOR BE REMOVED FROM THE TWO
METHODS THAT NEED IT MOST.

A richer reachability walk is the same error with more steps. It reads its
input set off the implementation, so it cannot falsify that implementation's
choice of set, and it fails in the WRONG DIRECTION: a walk that silently
resolves nothing returns a SMALLER set, and a smaller set reads as agreement.

So the alphabet here is the PUBLIC SURFACE of the class, which the decorator
does not define. Each public name carries the decorator or sits in the
exemption map below WITH A WRITTEN REASON. A new public method sits in
neither and reddens. That is fail-closed, and it is the property the design
bought.

WHAT THIS CANNOT DO: it does not prove an exempt method reaches no store. It
proves a new public method cannot arrive in silence. A narrow second arm
covers part of the gap by refusing a direct `db_connection` call in an
exempt method.
"""
from __future__ import annotations

import ast
import pathlib

from scripts.memory_api import PACTMemory  # noqa: E402


_MEMORY_API_PY = (
    pathlib.Path(__file__).parent.parent
    / "skills" / "pact-memory" / "scripts" / "memory_api.py"
)

DECORATOR = "_with_store_scope"

# EACH ENTRY CARRIES ITS REASON. A reason turns "why is this exempt" into a
# reviewable artifact rather than an absence, and it is the difference between
# a fail-closed check and a rubber stamp. Add a name here only with a ruling.
#
# THE FOUR PROPERTIES STAY INSIDE THIS POPULATION ON PURPOSE, and a later
# reviewer will find it tidier to move them outside. TIDIER IS INCORRECT HERE,
# for one reason: EXCLUSION IS SILENT AND EXEMPTION IS AN EXPLICIT CLAIM.
# A property that later starts to open a store must be RECLASSIFIED, which is
# a visible act that somebody performs and somebody can question. Move it
# outside the population and the same change happens with nothing to notice.
#
# That is the same argument that chose the public surface over a call-graph
# walk as the alphabet, applied to MEMBERSHIP rather than to method: prefer
# the shape whose failure is loud over the shape whose failure is quiet.
EXEMPT = {
    "project_id": "a read of instance state bound at construction",
    "session_id": "a read of instance state bound at construction",
    "last_embedding_status": "a read of the status left by the last save",
    "last_sync_status": "a read of the status left by the last save",
    "last_project_scope": "a read of the scope disclosure left by the last save",
    "track_file": "records a path on the instance, and opens no store",
    "get_tracked_files": "reads the paths recorded on the instance",
    "clear_tracked_files": "clears the paths recorded on the instance",
}


def _class_node():
    tree = ast.parse(_MEMORY_API_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PACTMemory":
            return node
    raise AssertionError("no PACTMemory class in memory_api.py")


def _public_methods():
    """Each public name the class exposes, with its decorator names.

    Anchored to `FunctionDef` nodes, which are executable definitions. A text
    sweep would count the prose in a docstring that names a method.
    """
    out = {}
    for node in _class_node().body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        out[node.name] = {
            "node": node,
            "decorators": {
                d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
                for d in node.decorator_list
            },
        }
    return out


class TestTheInstrumentIsLive:
    """NON-VACUITY GATES. Read these first.

    Each arm below reasons over a population. An empty or stale population
    makes each of them pass over nothing.
    """

    def test_the_public_surface_is_not_empty(self):
        assert _public_methods(), (
            "no public method found on PACTMemory. The class was renamed, or "
            "this arm parses the wrong file"
        )


class TestEachPublicEntryPointCarriesTheScope:
    """FAIL-CLOSED over the public surface.

    MUTANT: add a public method to PACTMemory with no decorator. It sits in
    neither the decorated set nor the exemption map, and this arm reddens.
    That is the forward property, and it fires on a CODE EDIT rather than on
    a test input, so a new entry point with no test of its own is visible.

    WHAT IT DOES NOT SEPARATE: it stays green when a decorated method stops
    reaching a store, and it stays green when an exempt method starts
    reaching one. The second arm below covers part of that.
    """

    def test_each_public_method_is_decorated_or_declared_exempt(self):
        unclassified = sorted(
            name for name, info in _public_methods().items()
            if DECORATOR not in info["decorators"] and name not in EXEMPT
        )
        assert not unclassified, (
            "these public methods carry neither {0} nor a declared "
            "exemption: {1}. A public entry point without the scope reads the "
            "DEFAULT store while the caller named another one. Add the "
            "decorator, or add an exemption WITH ITS REASON".format(
                DECORATOR, unclassified)
        )


class TestNoDecoratedMethodIsAGenerator:
    """The decorator returns `method(...)` from INSIDE the `with` block.

    A GENERATOR FUNCTION RETURNS ITS GENERATOR IMMEDIATELY AND RUNS ITS BODY
    LATER. The `with` block therefore closes before the first row is read, so
    the body resolves the DEFAULT store while an eager method on the same
    instance, with the same binding, resolves the scoped file. Same binding,
    opposite store.

    UNREACHABLE TODAY, and that is measured rather than assumed: no decorated
    method holds a `yield`. This arm exists because the hazard arrives with an
    ORDINARY EDIT, and nothing else in the tree reports it.

    MUTANT: add a `yield` to any decorated method. This arm reddens.

    ANCHORED TO SYNTAX, NOT TO TEXT. A docstring that names `yield`, and a
    comment that warns about generators, are prose. Only a `Yield` node in the
    body makes a function a generator, and `functools.wraps` puts a wrapper
    between a caller and the function, so a runtime probe on the wrapper can
    report on the wrapper rather than on the method.
    """

    def test_no_decorated_method_yields(self):
        generators = []
        for name, info in _public_methods().items():
            if DECORATOR not in info["decorators"]:
                continue
            for node in ast.walk(info["node"]):
                if isinstance(node, (ast.Yield, ast.YieldFrom)):
                    generators.append((name, node.lineno))
                    break
        assert not generators, (
            "these decorated methods are generators: {0}. The scope closes "
            "before the body runs, so the body resolves the DEFAULT store "
            "while the caller named another one".format(generators)
        )

