"""Coupling arms for the two entry ceilings, driven through the shipped path.

SIBLING OF `test_token_budget.py`. That file holds the primary arms for each function with tight fire-count assertions. This file holds the parametrized
worst-case sweeps, which are kept apart so that their setup noise does not
sit beside those counts.

WHAT THESE ARMS ADD THAT THE PRIMARY FILE DOES NOT HOLD.

The primary file DERIVES the ceiling for each entry with the same expression the
source uses, then checks one entry against it. That check cannot fail on a
change to `COMPRESSED_ENTRY_TOKEN_CEILING`, because the fixture moves with
the constant. THE IDENTITY `ceiling + (MAX - 1) * compressed == budget` IS
TRUE BY CONSTRUCTION AND ASSERTING IT PROVES NOTHING: the ceiling is DEFINED
as that subtraction. So these arms do not assert the identity. They MEASURE
the quantity the identity is a proxy for, which is the token cost of the
assembled section, and compare it against the budget.

THE COUPLING THESE ARMS EXIST TO CATCH. `COMPRESSED_ENTRY_TOKEN_CEILING` is
an estimate, and its first premise is `_REFRESH_IDENTIFIER_TRUNCATION_LIMIT`.
A later author can raise the identifier limit without seeing that a token
constant depends on it. No arm in the primary file goes red on that change.
These do, because they drive the shipped formatter at the limit and compare
the result against the constant.

MEASURED HEADROOM, and the counting rule for it. Each number below counts
ESTIMATED TOKENS from `_estimate_tokens`, which is `int(words * 1.3)` across
`str.split()` of the ASSEMBLED string. Two separately rounded parts do not
sum to the rounded whole, so each number comes from one call on one joined
string.
"""

import re
from pathlib import Path

import pytest


def _densest(nchars):
    """Return the densest string of `nchars` that `str.split()` can meet.

    One character for each word plus one space is the smallest a word can be,
    so n words occupy 2n-1 characters. THE DENSITY IS THE VARIABLE AND THE
    LENGTH IS NOT: a character bound cannot enforce a token budget, because
    the producer of the value controls the ratio.
    """
    n = (nchars + 1) // 2
    s = " ".join(["a"] * n)
    if len(s) < nchars:
        s = s + "b" * (nchars - len(s))
    return s[:nchars]


EXEMPT_DERIVATION_CEILING = 0


def _exempt_lines(entry):
    """Return the lines `_apply_entry_token_ceiling` refuses to drop.

    DERIVED BY RUNNING THE SHIPPED CUT, NOT BY RESTATING ITS RULE. At a
    ceiling of 0 no line has a word budget, so what comes back is what the
    function refused to drop, which is the exempt set BY DEFINITION rather
    than by agreement with a copy of the rule.

    WHY THIS IS NOT A STYLE PREFERENCE. The earlier version of this helper
    selected index 0 plus each `**Memory ID**` line by hand, and its own
    docstring claimed it mirrored the source. It did not. A change to the
    exempt set in the source left this helper stating the OLD rule, and the
    arms built on it kept measuring a set the shipped cut no longer used,
    with nothing red.

    THE SHAPE OF THE DERIVED SET IS NOT ASSERTED HERE. It is asserted by
    `TestTheExemptSetIsTheHeaderAndThePointer`, which is what turns a widened
    exemption into a red rather than into a quietly different number.
    """
    from scripts.working_memory import _apply_entry_token_ceiling

    return _apply_entry_token_ceiling(entry, EXEMPT_DERIVATION_CEILING)


def _worst_case_memory():
    from scripts.working_memory import _REFRESH_FIELD_TRUNCATION_LIMIT

    dense = _densest(_REFRESH_FIELD_TRUNCATION_LIMIT)
    return {
        "context": dense,
        "goal": dense,
        "decisions": dense,
        "lessons_learned": dense,
        "reasoning_chains": dense,
        "agreements_reached": dense,
        "disagreements_resolved": dense,
    }


class TestTheExemptSetIsTheHeaderAndThePointer:
    """The shipped cut holds out the date header and the pointer, and no more.

    THIS IS THE ARM THAT MAKES A WIDENED EXEMPTION LOUD. `_exempt_lines`
    above DERIVES the exempt slice by running the cut, so a source change
    that holds out a third kind of line changes that slice with no edit
    here. The derivation alone would then feed a DIFFERENT number to the
    margin arms and stay green, which trades a stale rule for a silent one.
    This class asserts the SHAPE of the derived set, so the change reaches a
    person.

    THE TWO EXEMPTIONS CARRY THEIR CAUSES, taken from the source. The date
    header is what makes the text parse as an entry. The `**Memory ID**`
    line is the pointer to the durable record, and the design accepts
    truncation rather than refusal only while that pointer survives the cut.
    A third exemption is not free: each exempt line spends the per-entry
    budget that the droppable lines compete for.
    """

    def _entry(self):
        from scripts.working_memory import (
            _format_retrieved_entry,
            _REFRESH_IDENTIFIER_TRUNCATION_LIMIT,
        )

        return _format_retrieved_entry(
            {"context": "c"}, query="q",
            memory_id=_densest(_REFRESH_IDENTIFIER_TRUNCATION_LIMIT),
        )

    def test_the_derived_exempt_set_is_the_header_and_the_pointer(self):
        from scripts.working_memory import _MEMORY_ID_LABEL

        entry = self._entry()
        entry_lines = entry.split("\n")
        exempt = _exempt_lines(entry).split("\n")

        # NON-VACUITY, TWO LEGS. An empty slice, or a slice equal to the whole
        # entry, would make the membership test below pass for the wrong
        # cause: the first has nothing to check, and the second means the cut
        # dropped nothing and the derivation measured the identity.
        assert exempt, "the derived exempt slice is empty"
        assert len(exempt) < len(entry_lines), (
            f"the derivation dropped no line: {len(entry_lines)} in, "
            f"{len(exempt)} out. The cut did not run, so the slice is not "
            "evidence about what is exempt."
        )

        assert exempt[0] == entry_lines[0], (
            f"the date header is not the first exempt line: {exempt[0]!r}"
        )
        for line in exempt[1:]:
            assert line.startswith(_MEMORY_ID_LABEL), (
                f"the shipped cut now holds out a line that is neither the "
                f"date header nor the recovery pointer: {line!r}\n"
                "A THIRD EXEMPTION SPENDS THE PER-ENTRY BUDGET THAT THE "
                "DROPPABLE LINES COMPETE FOR, and it narrows the word budget "
                "the margin arms measure. Decide it here rather than let the "
                "numbers move quietly."
            )


class TestCompressedEntryCeilingCoupling:
    """`COMPRESSED_ENTRY_TOKEN_CEILING` against the shipped compressed form."""

    @pytest.mark.parametrize(
        "density_label, make_id",
        [
            # THE ADVERSARIAL SHAPE, AND IT IS THE WORST CASE. At the bound,
            # one character for each word. No emitted pointer can cost more,
            # because the formatter REFUSES an id longer than the bound and
            # emits no line at all for it. A row that fed an id past the bound
            # would therefore measure a CHEAPER entry than this one, and its
            # own non-vacuity check on the pointer line would go red.
            ("dense", lambda limit: _densest(limit)),],
    )
    def test_compressed_cost_stays_within_the_ceiling(self, density_label, make_id):
        """The compressed cost holds at the identifier bound at each density.

        SWEEP THE DENSITY AXIS AND NOT ONLY THE LENGTH AXIS. A bound that
        holds for the friendly shape and fails for the dense one is the
        defect this branch repairs, and the two shapes have the SAME
        character count.

        THIS ARM GOES RED IF `_REFRESH_IDENTIFIER_TRUNCATION_LIMIT` RISES
        AND `COMPRESSED_ENTRY_TOKEN_CEILING` DOES NOT FOLLOW. That coupling
        is stated in a comment at the constant and enforced by nothing else.
        """
        from scripts.working_memory import (
            _compress_memory_entry,
            _estimate_tokens,
            _format_memory_entry,
            COMPRESSED_ENTRY_TOKEN_CEILING,
            _REFRESH_IDENTIFIER_TRUNCATION_LIMIT,
        )

        entry = _format_memory_entry(
            _worst_case_memory(),
            memory_id=make_id(_REFRESH_IDENTIFIER_TRUNCATION_LIMIT),
        )
        compressed = _compress_memory_entry(entry)

        # NON-VACUITY: compression must have run. Without this, an input that
        # was small before compression passes the bound below untouched.
        assert _estimate_tokens(entry) > _estimate_tokens(compressed), (
            "the fixture did not exceed the compressed form, so the bound "
            "below is not exercised"
        )
        # NON-VACUITY: the recovery pointer must be present, because it is
        # the line that carries the identifier this arm is about.
        assert "**Memory ID**" in compressed

        assert _estimate_tokens(compressed) <= COMPRESSED_ENTRY_TOKEN_CEILING, (
            f"compressed cost at density {density_label} is "
            f"{_estimate_tokens(compressed)} tokens, above the ceiling of "
            f"{COMPRESSED_ENTRY_TOKEN_CEILING}. The ceiling rests on "
            f"_REFRESH_IDENTIFIER_TRUNCATION_LIMIT="
            f"{_REFRESH_IDENTIFIER_TRUNCATION_LIMIT}; if that limit moved, "
            f"move the ceiling in the same commit."
        )


class TestWorkingMemorySectionBudget:
    """Site A: the assembled section against `WORKING_MEMORY_TOKEN_BUDGET`."""

    def test_worst_case_section_stays_within_the_budget(self):
        """The section fits the budget at the worst input the formatter emits.

        THIS IS THE PROPERTY THE CEILING IDENTITY IS A PROXY FOR. The
        identity is true by construction and cannot fail. The measured total
        can, and it is what a reader of the constants cares about.
        """
        from scripts.working_memory import (
            _apply_token_budget,
            _estimate_tokens,
            _format_memory_entry,
            COMPRESSED_ENTRY_TOKEN_CEILING,
            MAX_WORKING_MEMORIES,
            WORKING_MEMORY_TOKEN_BUDGET,
            _REFRESH_IDENTIFIER_TRUNCATION_LIMIT,
        )

        entries = [
            _format_memory_entry(
                _worst_case_memory(),
                memory_id=_densest(_REFRESH_IDENTIFIER_TRUNCATION_LIMIT),
            )
            for _ in range(MAX_WORKING_MEMORIES)
        ]
        # NON-VACUITY: the raw input must exceed the budget, or the function
        # returns it untouched and the total below proves nothing.
        assert sum(_estimate_tokens(e) for e in entries) > WORKING_MEMORY_TOKEN_BUDGET

        result = _apply_token_budget(entries, WORKING_MEMORY_TOKEN_BUDGET)

        assert sum(_estimate_tokens(e) for e in result) <= WORKING_MEMORY_TOKEN_BUDGET
        for neighbour in result[1:]:
            assert _estimate_tokens(neighbour) <= COMPRESSED_ENTRY_TOKEN_CEILING


class TestRetrievedContextThinMargin:
    """Site B: the exempt lines against the share each retrieved entry gets."""

    def test_the_exempt_lines_leave_the_ceiling_binding(self):
        """The lines that cannot be dropped cost less than the ceiling.

        WHERE THE EXEMPT LINES COST AS MUCH AS THE CEILING, NOTHING IN THE
        ENTRY IS DROPPABLE BELOW IT. The ceiling then stops binding in
        silence: no exception, no red, and a section above its budget.

        THIS IS WRITTEN AS A PROPERTY AND NOT AS A NUMBER, because a number
        rots when a constant moves. It goes red if the identifier limit
        rises, if `MAX_RETRIEVED_MEMORIES` rises, or if the budget falls.
        Each of those is a change a later author can make without seeing
        this coupling, and no single file names the two constants together.

        WHAT THIS ARM DOES NOT CATCH, CORRECTED AGAINST A MEASUREMENT. An
        earlier wording of this docstring also promised a red when the
        exempt set WIDENS. It does not have that red and it never did.
        With the shipped cut replaced by one that holds out EVERY line, the
        shape arms in `TestTheExemptSetIsTheHeaderAndThePointer` went red
        and THIS ARM STAYED GREEN: the slice became the whole entry, and a
        bound against a constant passed anyway because the entry is small.
        THE SET AXIS IS ARMED THERE AND NOT HERE. The leg below closes the
        remaining half, which is that the cost measured here must be the
        cost of a PROPER slice.
        """
        from scripts.working_memory import (
            _estimate_tokens,
            _format_retrieved_entry,
            MAX_RETRIEVED_MEMORIES,
            RETRIEVED_CONTEXT_TOKEN_BUDGET,
            _REFRESH_IDENTIFIER_TRUNCATION_LIMIT,
        )

        share = RETRIEVED_CONTEXT_TOKEN_BUDGET // MAX_RETRIEVED_MEMORIES
        entry = _format_retrieved_entry(
            {"context": "c", "goal": "g"},
            query="q",
            score=0.5,
            memory_id=_densest(_REFRESH_IDENTIFIER_TRUNCATION_LIMIT),
        )
        exempt = _exempt_lines(entry)
        # NON-VACUITY: the exempt slice must carry the identifier line, or
        # the cost below is the date header alone and the bound is trivial.
        assert "**Memory ID**" in exempt

        # NON-VACUITY, THE OTHER DIRECTION, AND THE ONE THAT WAS MISSING.
        # The leg above bounds the slice from BELOW. Nothing bounded it from
        # ABOVE, so a cut that dropped NOTHING handed this arm the cost of
        # the WHOLE entry and the comparison below passed regardless. The
        # sibling class asserts the SHAPE of the derived set; this arm takes
        # a NUMBER from that same derivation and had no statement about what
        # the number describes. A cost is only evidence about the ceiling
        # while it is the cost of the lines the cut REFUSED to drop.
        assert len(exempt.split("\n")) < len(entry.split("\n")), (
            f"the derivation dropped no line: {len(entry.split(chr(10)))} in, "
            f"{len(exempt.split(chr(10)))} out. The slice is the whole entry, "
            f"so the cost below is not the cost of the exempt lines and the "
            f"bound says nothing about the ceiling."
        )

        cost = _estimate_tokens(exempt)
        margin = share - cost
        assert cost < share, (
            f"the exempt lines cost {cost} tokens against a share of "
            f"{share}, a margin of {margin}. At or above the share the "
            f"ceiling stops binding and the section can exceed its budget "
            f"in silence."
        )


# THE SHAPES FOR THE LIVE-CEILING SWEEP, IN ONE PLACE. The absence arm below
# and the reached-the-cut arm beside it both run THIS list. Two lists would
# let the reached count describe a population the absence arm does not sweep,
# and the count is the whole evidence that the absence means anything.
_LIVE_CEILING_SHAPES = [
    ("one_context_field_only", {"context": "A save with one field."}, False),
    ("one_context_field_with_id", {"context": "A save with one field."}, True),
    ("two_fields", {"context": "Ctx " * 40, "goal": "Goal " * 40}, True),
    ("many_fields", {"context": "Ctx " * 200, "goal": "Goal " * 200,
                     "decisions": ["D " * 100],
                     "lessons_learned": ["L " * 100]}, True),
]


def _fold_constant(node):
    """Return the value of a constant expression, or None where it is not one.

    KEPT NARROW ON PURPOSE. It resolves a `Constant` and an arithmetic
    `BinOp` over constants, which is the split-spelling shape. It does not
    evaluate calls, names or attributes, so it cannot run module code while
    a test collects.
    """
    import ast

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        left = _fold_constant(node.left)
        right = _fold_constant(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
        except Exception:
            return None
    return None
