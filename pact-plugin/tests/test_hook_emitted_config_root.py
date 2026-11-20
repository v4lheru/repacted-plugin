"""Location: pact-plugin/tests/test_hook_emitted_config_root.py
Summary: Pins the config-root literals that hooks EMIT as user-facing text.
         Hook-emitted strings are prose a user or agent reads and acts on, so a
         `~/.claude/` literal in one misdirects every non-default-root install.
Used by: the merge gate only — nothing imports this module.

WHY THIS IS NOT THE PROSE GATE'S JOB: tests/test_prose_root_gate.py scans
agents/commands/skills/protocols. Extending that population to hooks/ would
sweep in ~68 docstring and comment occurrences that are documentation, not
instruction, and would need an allowlist larger than the finding. So the
population here is the EMITTED strings only, and role is decided
syntactically rather than by grep.

THE CLASSIFIER IS THE DIFFERENCE BETWEEN TWO PARSERS, because each is blind to
what the other sees. `ast` never contains comments at all, so a literal in the
token stream but absent from the AST is a comment BY CONSTRUCTION — no `#`
heuristics. Docstrings are named positively via the Constant at body[0].
Everything left is emitted.

TWO TRAPS THIS FILE HANDLES, both live in the files it guards:
  - an f-string is a JoinedStr, NOT a Constant. Its literal segments are
    Constant nodes nested among FormattedValue siblings, so a walk over
    top-level constants silently misses every literal inside one. `ast.walk`
    descends, which is why it is used here.
  - implicit adjacent concatenation ("a" "b") is FOLDED by ast into one
    Constant. That is what we want (we judge the RENDERED string), but it also
    means the rendered sentence is NOT a contiguous substring of the source, so
    no line-oriented grep could pin these messages.
"""
import ast
import re
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "hooks"
LITERAL = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude")

# The ONE emitted literal that must survive: bootstrap_gate's degraded path
# runs BECAUSE an import failed, so it resolves the root from os.environ
# rather than calling the plugin's own resolver, and needs a literal fallback
# for the unset case. Same shape as the resolver contract's mandated
# $HOME/.claude fallback. Keyed on the rendered VALUE, not a line number, so
# it survives edits above it.
ALLOWED_EMITTED = {
    ("hooks/bootstrap_gate.py", "$HOME/.claude"),
    # The `repact` shim's shell line: the same mandated fallback, applied by the user's shell
    # each time the shim runs, so it follows CLAUDE_CONFIG_DIR at run time, not write time.
    ("hooks/shared/repact_shim.py", 'config="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"'),
}

GUARDED_FILES = (
    "hooks/bootstrap_gate.py",
    "hooks/session_init.py",
    "hooks/shared/claude_md_manager.py",
)


def _emitted_literals():
    """Yield (relpath, lineno, rendered) for every EMITTED config-root literal."""
    for path in sorted(HOOKS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(HOOKS.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and body \
                    and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and LITERAL.search(node.value) and id(node) not in docstrings:
                yield rel, node.lineno, node.value


def test_no_hook_emits_a_hardcoded_config_root():
    """The class gate. Docstrings and comments are out of population by role."""
    found = [(rel, ln, val) for rel, ln, val in _emitted_literals()
             if (rel, val.strip()) not in ALLOWED_EMITTED]
    assert not found, (
        "a hook EMITS a hardcoded config root. The user reads this text and "
        "acts on it, so on a non-default root it names a file or directory "
        "that is not the one the code just operated on. Interpolate "
        "get_claude_config_dir() (or, on a degraded path that must not import "
        f"it, os.environ). Sites: {found}"
    )


def test_control_the_classifier_can_see_an_emitted_literal():
    """A zero from the gate above is only meaningful if the walk reaches code.

    Without this, a broken HOOKS path or a walk that never descends into
    JoinedStr yields an empty `found` — byte-identical to a clean tree.
    """
    allowed = [(rel, ln, val) for rel, ln, val in _emitted_literals()
               if (rel, val.strip()) in ALLOWED_EMITTED]
    assert allowed, (
        "the classifier found NO emitted literal at all, not even the "
        "allowlisted degraded-path fallback — the walk or the pattern is dead"
    )


def test_control_docstrings_and_comments_are_out_of_population():
    """The population is EMITTED strings. If this arm ever fails, the
    classifier has started counting documentation and the gate above will red
    on ~68 occurrences that are correct as written."""
    emitted = list(_emitted_literals())
    # symlinks.py carries the literal ONLY in a docstring and a comment.
    assert not [r for r in emitted if r[0].endswith("shared/symlinks.py")], (
        "classifier is counting docstrings/comments as emitted"
    )


# --------------------------------------------------------------------------
# Behavioural arms — the literal being absent is not the same as the RESOLVED
# root being present. These drive the real producers under an alternate root.


@pytest.fixture
def alt_root(tmp_path, monkeypatch):
    root = tmp_path / "alt-config"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


