"""Twin-copy drift tests for pact-memory's working_memory.py.

working_memory.py keeps local twins of hook-side helpers (file_lock,
_sanitize_prompt_field, the atomic write, the line-ending helpers,
ContainmentError and the project-root layout). Each class below pins one twin
against its canonical copy. Moved here from test_staleness.py when the
CLAUDE.md pin gates (staleness.py among them) were retired in 1.5.0.
"""
import ast
import inspect
import textwrap
from pathlib import Path

import pytest


class TestFileLockTwinCopyDrift:
    """Drift detection for the file_lock twin vendored into working_memory.

    file_lock is twin-copied from hooks/shared/claude_md_manager into
    skills/pact-memory/scripts/working_memory (skills/ cannot import from
    hooks/shared/). The function BODY must stay byte-identical so the skill
    serializes against the hook on the same sidecar inode; the docstring may
    differ. This fails loudly when the executable logic drifts.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body, stripping decorators, the def line,
        and a leading docstring; then dedent + normalize.

        Unlike TestEstimateTokensEquivalence._extract_body, file_lock carries a
        @contextmanager decorator, so this skips ALL leading ``@`` lines before
        dropping the def line. This makes the comparison docstring-tolerant
        (the twin carries a shorter docstring) while pinning the logic.
        """
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        # lines[idx] is the def line; drop it too.
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    def test_file_lock_bodies_are_identical(self):
        """The file_lock body MUST be byte-identical across the two copies.

        Cross-process serialization correctness depends on the twin behaving
        exactly like the canonical lock (same sidecar path, same fcntl flock
        semantics, same timeout/poll loop). Compares logic only — docstrings
        are allowed to differ.
        """
        from shared.claude_md_manager import file_lock as canonical
        from scripts.working_memory import file_lock as twin

        canonical_body = self._extract_body(inspect.getsource(canonical))
        twin_body = self._extract_body(inspect.getsource(twin))

        assert canonical_body == twin_body, (
            "file_lock twin drift between hooks/shared/claude_md_manager.py "
            "and skills/pact-memory/scripts/working_memory.py — update both "
            "in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )

    def test_lock_timeout_constants_match(self):
        """The two lock-tuning constants are part of the twin and must match."""
        import shared.claude_md_manager as cmm
        from scripts import working_memory as wm

        assert cmm._LOCK_TIMEOUT_SECONDS == wm._LOCK_TIMEOUT_SECONDS, (
            "_LOCK_TIMEOUT_SECONDS drift between claude_md_manager.py and "
            "working_memory.py — update both in the same commit"
        )
        assert cmm._LOCK_POLL_INTERVAL == wm._LOCK_POLL_INTERVAL, (
            "_LOCK_POLL_INTERVAL drift between claude_md_manager.py and "
            "working_memory.py — update both in the same commit"
        )


class TestSanitizePromptFieldTwinCopyDrift:
    """Drift detection for the _sanitize_prompt_field twin in working_memory.

    _sanitize_prompt_field is twin-copied from hooks/shared/session_resume
    into skills/pact-memory/scripts/working_memory (skills/ cannot import
    from hooks/shared/). The function BODY must stay byte-identical so both
    the hook write path and the skill write path collapse the SAME control
    characters at the SAME bound; the docstring may differ.

    NO OTHER TEST COVERS THIS PAIR, WHICH IS WHY THE CLASS EXISTS RATHER
    THAN A CASE ADDED ELSEWHERE. The neighbouring twin gates import
    _atomic_write_text and file_lock, so a divergence in the SANITIZER
    reddens none of them: without this class a one-copy edit is green and
    silent, and the two write paths would disagree about what a field value
    can carry into CLAUDE.md.
    """

    @staticmethod
    def _extract_body(func) -> str:
        """Return the executable body, dropping the signature and docstring.

        Unlike the two extractors above, this one parses rather than counts
        lines. _sanitize_prompt_field carries a MULTI-LINE signature, so
        dropping a fixed number of leading lines would leave parameter lines
        in the "body" and compare the signature as if it were logic.
        """
        source = textwrap.dedent(inspect.getsource(func))
        statements = ast.parse(source).body[0].body
        first = statements[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            statements = statements[1:]
        body_start = statements[0].lineno - 1
        return textwrap.dedent(
            "\n".join(source.split("\n")[body_start:])
        ).strip()

    @staticmethod
    def _extract_signature(func):
        """Return an ast-NORMALISED signature, for comparison across twins.

        WHY A NORMALISED FORM AND NOT THE SOURCE LINES. The extractor above
        DROPS the signature, on purpose, so the body comparison does not
        compare parameter lines as if they were logic. That leaves the
        signature compared by nobody, which is the gap this closes. Comparing
        the raw source lines instead would go red when somebody re-wraps a
        multi-line signature onto one line, which changes no behaviour.

        THE COMPARED SURFACE, so a later reader can reproduce it: the ORDERED
        parameter names, the `ast.unparse` of each annotation, and the
        `ast.unparse` of each default EXPRESSION.

        SOURCE FORM, NOT RESOLVED VALUE, AND THAT IS THE POINT. The default
        `limit` reads `_REFRESH_FIELD_TRUNCATION_LIMIT` in the two copies. A
        copy rewritten to spell the bare literal 200 has the SAME resolved
        value today, so a value comparison stays green while the two copies
        stop tracking one constant. The unparse of the expression separates
        them.
        """
        args = ast.parse(textwrap.dedent(inspect.getsource(func))).body[0].args

        positional = args.posonlyargs + args.args
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)

        rendered = [
            (a.arg,
             ast.unparse(a.annotation) if a.annotation else None,
             ast.unparse(d) if d is not None else None)
            for a, d in zip(positional, defaults)
        ]
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            rendered.append(
                ("*" + a.arg,
                 ast.unparse(a.annotation) if a.annotation else None,
                 ast.unparse(d) if d is not None else None)
            )
        for star, name in (("*", args.vararg), ("**", args.kwarg)):
            if name is not None:
                rendered.append(
                    (star + name.arg,
                     ast.unparse(name.annotation) if name.annotation else None,
                     None)
                )
        return tuple(rendered)

    def test_sanitize_prompt_field_bodies_are_identical(self):
        """The _sanitize_prompt_field body MUST be byte-identical across twins.

        The body decides which characters can reach a line of CLAUDE.md and
        at what length. A divergence would let one write path admit a
        character the other collapses — so a heading injected through the
        skill path would be stripped through the hook path, or the reverse,
        and the guard would hold on only one of the two writers.
        """
        from shared.session_resume import _sanitize_prompt_field as canonical
        from scripts.working_memory import _sanitize_prompt_field as twin

        canonical_body = self._extract_body(canonical)
        twin_body = self._extract_body(twin)

        assert canonical_body == twin_body, (
            "_sanitize_prompt_field twin drift between "
            "hooks/shared/session_resume.py and "
            "skills/pact-memory/scripts/working_memory.py — update both "
            "in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )


class TestAtomicWriteTwinCopyDrift:
    """Drift detection for the _atomic_write_text twin (#1247).

    _atomic_write_text is twin-copied from hooks/shared/claude_md_manager into
    skills/pact-memory/scripts/working_memory (skills/ cannot import from
    hooks/shared/). Since #1247 it carries the CONTAINMENT SECURITY CHECK, so
    the function BODY must stay byte-identical across the two copies -- a
    silent divergence in the security check between the hook and skill copies
    is the #1118-class hazard this gate exists to prevent. The docstring may
    differ (each copy points at the other); the executable logic may not.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body: skip leading decorators + the def line,
        strip a leading docstring, dedent, normalize. Same extractor as
        TestFileLockTwinCopyDrift (docstring-tolerant, logic-pinning)."""
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    def test_atomic_write_text_bodies_are_identical(self):
        """The _atomic_write_text body MUST be byte-identical across the twins.

        The body carries the #1247 containment check (kernel object ancestry on
        a pinned directory descriptor, fail-closed); a divergence would let the
        hook and skill write paths enforce different containment, silently
        defeating the guard on one side. Compares logic only -- docstrings are
        allowed to differ.

        THIS GATE PROVES BYTE-IDENTITY, NOT THAT EITHER COPY IS EXERCISED. It
        goes red whenever the bodies diverge for ANY reason, including a
        deliberate single-twin experiment -- so a red here is not by itself
        evidence of a behavioural regression, and counting it as one inflates
        the apparent coverage of whichever twin was left untouched. The
        behavioural coverage of each copy lives in
        test_containment_certification.py, which drives both independently.
        """
        from shared.claude_md_manager import _atomic_write_text as canonical
        from scripts.working_memory import _atomic_write_text as twin

        canonical_body = self._extract_body(inspect.getsource(canonical))
        twin_body = self._extract_body(inspect.getsource(twin))

        assert canonical_body == twin_body, (
            "_atomic_write_text twin drift between "
            "hooks/shared/claude_md_manager.py and "
            "skills/pact-memory/scripts/working_memory.py — the #1247 "
            "containment check must stay identical; update both in the SAME "
            "commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )


class TestLineEndingHelperTwinCopyDrift:
    """Drift detection for the two line-ending helpers.

    THIS GATE ESTABLISHES THE IDENTITY RATHER THAN PRESERVING IT, AND THAT IS
    WHY IT IS NOT OPTIONAL. Its siblings guard functions that were COPIED, so
    the two sides started identical and the gate reports a later divergence.
    `_detect_line_ending` was REWRITTEN when the restore moved into the write
    seam: it takes a directory descriptor and a leaf name now, because a read by
    NAME inside `_atomic_write_text` would reintroduce the race that function's
    descriptor design removes. A rewrite has no shared ancestor, so nothing
    except this gate makes the two copies agree at all.

    WHY THE TWO COPIES ARE THERE. `skills/pact-memory/scripts/` cannot import
    from `hooks/shared/`, the same package boundary that produced the `file_lock`
    and `_atomic_write_text` twins.

    WHAT DIVERGENCE COSTS. The two helpers decide the BYTES that land in a
    user's CLAUDE.md. A hook copy and a skill copy that disagree write the same
    document with different line endings, so two writers fight over the file and
    each one reports a whole-file change to the user. The docstrings may differ,
    because each copy points at the other. The logic may not.
    """

    @staticmethod
    def _extract_body(source: str) -> str:
        """Return the executable body: skip leading decorators + the def line,
        strip a leading docstring, dedent, normalize. Same extractor as
        TestAtomicWriteTwinCopyDrift (docstring-tolerant, logic-pinning)."""
        lines = source.split("\n")
        idx = 0
        while idx < len(lines) and lines[idx].lstrip().startswith("@"):
            idx += 1
        body_lines = lines[idx + 1:]
        body_text = textwrap.dedent("\n".join(body_lines)).strip()
        for quote in ['"""', "'''"]:
            if body_text.startswith(quote):
                end_idx = body_text.find(quote, len(quote))
                if end_idx != -1:
                    body_text = body_text[end_idx + len(quote):].strip()
                break
        return body_text

    @pytest.mark.parametrize(
        "helper", ["_detect_line_ending",]
    )
    def test_line_ending_helper_bodies_are_identical(self, helper):
        """Each helper's body MUST be byte-identical across the twins."""
        import shared.claude_md_manager as canonical_mod
        from scripts import working_memory as twin_mod

        canonical_body = self._extract_body(
            inspect.getsource(getattr(canonical_mod, helper))
        )
        twin_body = self._extract_body(
            inspect.getsource(getattr(twin_mod, helper))
        )

        assert canonical_body == twin_body, (
            f"{helper} twin drift between "
            f"hooks/shared/claude_md_manager.py and "
            f"skills/pact-memory/scripts/working_memory.py. The two decide the "
            f"bytes that land in a user's CLAUDE.md, so a divergence makes the "
            f"hook and the skill write one file with different line endings. "
            f"Update both in the SAME commit.\n"
            f"canonical body:\n{canonical_body}\n\n"
            f"twin body:\n{twin_body}"
        )


class TestRestoreHasOneOwnerPerTwin:
    """The line-ending restore has ONE owner for each twin, and it is the seam.

    WHAT THIS CONVERTS. Before the restore moved into `_atomic_write_text`, one
    call site did it for itself and the other nine did not. A branch written
    against that shape carries its own call-site restore, so a later merge can
    land the seam restore AND a call-site restore in one tree. The substitution
    then runs two times.

    WHY THAT IS A CORRUPTION AND NOT AN INEFFICIENCY. `_restore_line_ending`
    normalises before it applies, so a second application is a no-op TODAY. That
    makes the two-owner state SILENT, which is the reason it wants a mechanical
    gate rather than a note: nothing in the output reports it, and the next
    editor of the helper can remove the normalise step without knowing a second
    owner is relying on it.

    FAILURE DIRECTION, and it is fail-safe. This arm reddens when a SECOND owner
    appears anywhere in the shipped tree. A future writer with a legitimate
    reason to restore for itself turns it red and must state the reason in the
    same commit. That is the intended cost.
    """

    # COUNT RULE, STATED BESIDE THE NUMBER: one entry for each CALL EXPRESSION
    # of the restore helper found by an AST walk of every `.py` file under
    # `pact-plugin/hooks/` and `pact-plugin/skills/`. Tests are not in that
    # population, and a mention inside a comment or a string is not a call.
    _SEARCH_ROOTS = ("hooks", "skills")
    _RESTORE = "_restore_line_ending"

    @staticmethod
    def _calls_with_enclosing_function(tree, name):
        """Return the enclosing function name for each call of `name`.

        Attributes a call to its INNERMOST enclosing function, and to None at
        module level, so a restore moved out of a function reddens rather than
        being credited to whatever function sits above it.
        """
        found = []

        def visit(node, enclosing):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name)
                    continue
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == name
                ):
                    found.append(enclosing)
                visit(child, enclosing)

        visit(tree, None)
        return found

    def _sites(self):
        """DISCOVER the call sites. Do NOT enumerate them.

        A guard whose target list is written by hand passes green for anything
        it forgot to name, which is the failure this walk exists to avoid.
        """
        plugin_root = Path(__file__).parent.parent
        sites = {}
        scanned = 0
        for root in self._SEARCH_ROOTS:
            for path in sorted((plugin_root / root).rglob("*.py")):
                scanned += 1
                tree = ast.parse(path.read_text(encoding="utf-8"))
                enclosing = self._calls_with_enclosing_function(
                    tree, self._RESTORE
                )
                if enclosing:
                    sites[path.relative_to(plugin_root).as_posix()] = enclosing
        return sites, scanned

    def test_the_only_owner_of_the_restore_is_the_write_seam(self):
        sites, scanned = self._sites()

        # NON-VACUITY FIRST, AND IT IS REQUIRED. A walk that found no call at
        # all satisfies an "every call is in the seam" assertion perfectly, so
        # the assertion below is evidence only once the population is known to
        # be non-empty. A broken path or a renamed helper lands here.
        assert scanned > 0, "the walk found no Python file at all"
        assert sites, (
            f"no call of {self._RESTORE!r} was found in "
            f"{list(self._SEARCH_ROOTS)} across {scanned} files. Either the "
            f"seam no longer restores the line ending, or the helper was "
            f"renamed and this guard is now measuring nothing"
        )

        owners = {name for names in sites.values() for name in names}
        assert owners == {"_atomic_write_text"}, (
            f"the line-ending restore has more than one owner: {sorted(sites.items())}. "
            f"The seam applies the ending for every caller, so a second site "
            f"runs the substitution twice on the same document. Remove the "
            f"call-site restore, or state in this commit why this caller must "
            f"own the ending itself"
        )


class TestRestoreNormalisesBeforeItApplies:
    """`_restore_line_ending` converts CRLF to LF BEFORE it applies the ending.

    WHY THE TWO GATES ABOVE CANNOT REACH THIS, AND IT IS THE WHOLE REASON THIS
    CLASS IS HERE. A removal of the normalise step applied to the TWO twins
    alike is invisible to each of them, for a different reason:
      - TestLineEndingHelperTwinCopyDrift compares the two twins AGAINST EACH
        OTHER, so a symmetric removal keeps them identical and that gate stays
        silent BY CONSTRUCTION.
      - TestRestoreHasOneOwnerPerTwin counts CALL SITES, and a step removed
        INSIDE the helper changes no count, so that gate stays silent too.
    A symmetric removal sits in the blind spot of the two. The merge-guard
    docstring above names this hazard in prose. Prose that names a hazard is
    not a guard against it.

    WHAT A REMOVAL COSTS, MEASURED RATHER THAN FEARED. Content that already
    carries CRLF meets a plain `.replace("\\n", line_ending)` and each ending
    becomes "\\r\\r\\n". That is corruption of the user's own text, and
    CLAUDE.md is frequently gitignored, so no commit brings the original back.

    THE STEP IS REACHABLE IN THE SHIPPED TREE, AND THE TRIGGER IS A
    CONJUNCTION. The target must be CRLF-dominant, AND the content must carry a
    carriage return. The content does NOT get one from the document: each of
    the seam call sites reads the target with `read_text(encoding="utf-8")`,
    which translates. It gets one by COMPOSITION, because a caller interpolates
    an external payload into the text it hands the seam. So this arm guards a
    live path rather than a hypothetical future one.
    """

    # THE TWO TWINS, as import targets. Parametrized rather than looped so a
    # failure names WHICH copy broke.
    _TWINS = ("shared.claude_md_manager", "scripts.working_memory")

    @staticmethod
    def _restore(module_name):
        import importlib

        return importlib.import_module(module_name)._restore_line_ending

    @pytest.mark.parametrize("twin", list(_TWINS)[:1])
    def test_crlf_content_does_not_gain_a_doubled_carriage_return(self, twin):
        """THIS IS THE DISCRIMINATING ARM. Remove the normalise step and it
        reddens in EACH twin.

        Input carries CRLF and the target ending is CRLF, so the correct output
        is the input unchanged. Without the normalise step each "\\n" is
        rewritten while its leading "\\r" survives, giving "\\r\\r\\n".
        """
        restore = self._restore(twin)

        result = restore("a\r\nb\r\n", "\r\n")

        assert "\r\r\n" not in result, (
            f"{twin}: the restore produced a DOUBLED carriage return "
            f"{result!r}. It applied the ending without normalising first, so "
            f"content that already carries CRLF is corrupted. Restore the "
            f'.replace("\\r\\n", "\\n") step before the ending is applied'
        )
        assert result == "a\r\nb\r\n", (
            f"{twin}: expected the CRLF input back unchanged, got {result!r}"
        )


