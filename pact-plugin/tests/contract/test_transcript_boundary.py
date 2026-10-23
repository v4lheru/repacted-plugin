"""tests/contract/test_transcript_boundary.py: no hook opens a transcript it was not already
reading. The context ledger measures `transcript_path` by os.stat alone (the lead's ruling on
the refactor core design: size only, never opened), and the set of hooks that both name a
transcript path and open files is the shrinking allowlist READERS: the run ledger
(session_ledger), the compaction archive (postcompact_archive, compaction_owner) and the
session cost cap (shared/cost_cap.py: shared/session_spend.py reads token counts only) and the
merge guard's approval record (shared/approval_record.py: the AskUserQuestion answers only). A new hook
that names a transcript and opens anything lands here and must be added on purpose.

Two AST walks over hooks/**/*.py: (1) `transcript_size` in shared/context_ledger.py calls
nothing but payload.get, isinstance and os.stat; (2) a file whose source names
`transcript_path` and whose Call nodes include open / read_text / read_bytes / fdopen on any
receiver but `state_file` (state files are the plugin's own, never a transcript) is in
READERS, exactly. Docstrings and comments cannot match a Call node.

The distiller's raw view (bin/_distill/view.py, KB full) reads transcripts outside hooks/:
it is the one distill module that imports shared/transcripts.py, and it uses VIEW_USES only, so
the module is read through, never widened for it.
"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
HOOKS = PLUGIN_ROOT / "hooks"
OPENERS = frozenset({"open", "read_text", "read_bytes", "fdopen"})
DISTILL = PLUGIN_ROOT / "bin" / "_distill"
VIEW_USES = frozenset({"message", "parse_ts", "session_files"})
READERS = frozenset({
    "hooks/subagent_stop.py",
    "hooks/postcompact_archive.py", "hooks/session_ledger.py",
    "hooks/shared/compaction_owner.py", "hooks/shared/cost_cap.py",
    "hooks/shared/approval_record.py",
})


def _call_name(node: ast.Call) -> tuple[str, str]:
    """(callee name, receiver name) of a Call: `os.stat(p)` is ("stat", "os")."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id, ""
    if isinstance(func, ast.Attribute):
        receiver = func.value.id if isinstance(func.value, ast.Name) else ""
        return func.attr, receiver
    return "", ""


def _opens_outside_state_file(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name, receiver = _call_name(node)
            if name in OPENERS and receiver != "state_file":
                return True
    return False


def test_the_ledger_reads_the_transcript_by_stat_alone():
    tree = ast.parse((HOOKS / "shared" / "context_ledger.py").read_text(encoding="utf-8"))
    [func] = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "transcript_size"]
    calls = sorted({_call_name(n) for n in ast.walk(func) if isinstance(n, ast.Call)})
    assert calls == [("get", "payload"), ("isinstance", ""), ("stat", "os")]


def test_only_the_known_readers_name_a_transcript_and_open_files():
    found = set()
    for path in sorted(HOOKS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if "transcript_path" in path.read_text(encoding="utf-8") and _opens_outside_state_file(path):
            found.add(path.relative_to(PLUGIN_ROOT).as_posix())
    assert found == set(READERS)


def test_the_allowlist_holds_no_stale_entry():
    for rel in READERS:
        path = PLUGIN_ROOT / rel
        assert path.is_file() and "transcript_path" in path.read_text(encoding="utf-8"), rel
        assert _opens_outside_state_file(path), rel


def test_the_distill_view_is_the_one_importer_and_uses_the_module_as_it_is():
    importers = sorted(p.name for p in DISTILL.glob("*.py")
                       if "transcripts" in {a.name for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                                            if isinstance(n, ast.ImportFrom) and n.module == "shared"
                                            for a in n.names})
    assert importers == ["view.py"]
    tree = ast.parse((DISTILL / "view.py").read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "T"}
    assert used == VIEW_USES
    source = ast.parse((HOOKS / "shared" / "transcripts.py").read_text(encoding="utf-8"))
    defined = {n.name for n in source.body if isinstance(n, ast.FunctionDef)}
    defined |= {t.id for n in source.body if isinstance(n, ast.Assign) for t in n.targets
                if isinstance(t, ast.Name)}
    assert VIEW_USES <= defined
