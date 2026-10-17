"""What a HANDOFF item claims: the count and test-run wording, the gated fields, the
per-claim check against a run, the claim keys and the uncertainty items. Pure: no I/O.

Location: pact-plugin/hooks/shared/evidence/claims.py, split out of the package's
__init__, which re-exports every name here; callers import shared.evidence.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import re
from typing import Optional

DEFAULT_PRIORITY_FIELDS = ("produced", "verification", "integration", "summary")
DEFAULT_EXCLUDED_FIELDS = ("open_questions", "uncertainty")
LAST_FIELDS = ("reasoning_chain",)
ITEM_MIN_CHARS = 12

# An exact count: plain digits, or digits in groups of three after a comma or a space
# ("19,246", "19 246"; J1: the "246" of "19,246" read as an enumeration, so no count parsed).
# ASCII digits only: a count in any other digits is UNREADABLE (FOREIGN_DIGITS), never parsed.
NUM = r"(?<![\d.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+|[0-9]+)(?!\d|[.,]\d)"
# {K}: the word is not the key of the NEXT number. In "total=8 passed=1 failed=7" the 8 is the
# total and the 1 is what passed; read as "8 passed" and "1 failed" it returned two correct TRX
# citations (1.3.4 field report, B). A key is told by its sign touching the digit: "36 passed: 2
# skipped" and "36 passed = 100%" end a count, and read as a key they returned four run-backed
# phrasings (review of 96be605d, finding 1).
_NOT_A_KEY = r"(?![=:]\d)"
# The other reading, for a chain written with spaces ("total: 8 passed: 1 failed: 7"): the sign
# need not touch. A clause the two patterns read differently keeps BOTH readings (claim_readings)
# and matches when either does, when it holds a keyed chain (KEYED_CHAIN): the number in front of
# the word is itself the value of a count key. "40 passed: 36 unit" and "tests: 36 passed: 0
# failed" hold none, and their second reading passed a wrong total (probe rerun, fix 2).
_NOT_A_SPACED_KEY = r"(?!\s*[=:]\s*\d)"
_COUNT_KEY = r"(?:total|executed|collected|passed|failed|skipped|errors?)"
# "45 fail rows, 11 pass controls": rows of a table written in a test file. The
# number counts the noun right after the word, so it is no pass count; "11 pass, 45 fail" is.
_TABLE_NOUN = r"(?!\s+(?:rows?|controls?)\b)"
_COUNT_SHAPES = (
    ("passed", r"\b{N}\s*(?:/\s*{N}\s*)?(?:tests?\s+|checks?\s+)?(?:passed|passing|pass|green)\b"
               + _TABLE_NOUN + "{K}"),
    ("passed", r"\bpassed[:=\s]+{N}"),
    ("failed", r"\b{N}\s+(?:tests?\s+)?(?:failed|failing|failures?)\b{K}"),
    ("failed", r"\bfailed[:=\s]+{N}"),
    ("collected", r"\bcollected[:=\s]+{N}|\b{N}\s+collected\b{K}"),
    ("skipped", r"\b{N}\s+skipped\b{K}|\bskipped[:=\s]+{N}"),
    ("errors", r"\b{N}\s+errors?\b{K}|\berrors[:=\s]+{N}"),
)
COUNT_PATTERNS = tuple((key, re.compile(pattern.replace("{N}", NUM).replace("{K}", _NOT_A_KEY), re.I))
                       for key, pattern in _COUNT_SHAPES)
KEYED_PATTERNS = tuple((key, re.compile(pattern.replace("{N}", NUM).replace("{K}", _NOT_A_SPACED_KEY), re.I))
                       for key, pattern in _COUNT_SHAPES)
KEYED_CHAIN = re.compile(rf"\b{_COUNT_KEY}\s*[:=]\s*{NUM}\s+{_COUNT_KEY}\s*[:=]\s*\d", re.I)
# The same chain with no sign at all, as tcount's line reads once its dots are dropped:
# "collected 655 passed 654 failed 0" (read number-before-word it is 655 passed and
# 654 failed). Its second reading takes each number from the key in front of it (SPACED_PAIR).
SPACED_CHAIN = re.compile(rf"\b{_COUNT_KEY}\s+{NUM}\s+{_COUNT_KEY}\s+\d", re.I)
SPACED_PAIR = re.compile(rf"\b(collected|passed|failed|skipped|errors)\s+{NUM}", re.I)
# A ratio whose label, right in front of it, is a check tally: "CI 5/5 green", "checks: 5/5"
# (field report, finding 35). "5/5 checks green" and "pytest 5/5 green" are test counts.
CHECK_TALLY = re.compile(r"\b(?:CI|checks?|pipelines?|jobs?)\s*:?\s*$", re.I)
# A ratio a neighbouring clause attributes to tcount ("Live matrix 205/205, tcount node 206/206"):
# the tcount ratio is the run's count, and a labelled ratio beside it whose own
# clause names no test, runner or log is the item's own tally (_beside_tcount_ratio).
TCOUNT_RATIO = re.compile(r"\btcount\b(?:\s+[\w.-]+){0,2}\s+[0-9]+/[0-9]+", re.I)
RATIO_LABEL = re.compile(r"[A-Za-z][\w-]*\s*:?\s*$")
# A pass, fail or collected count the parser cannot read exactly: in words ("nineteen
# thousand-odd"), rounded ("19k", "19,000+", "12-odd") or dot-grouped ("19.246"). It is never
# matched against a run: run_check UNREADABLE, which flags like a MISMATCH (J1). "pass",
# "passing" and "failing" count only after "tests" ("in one pass", "one failing URL" are prose).
_WORDS = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|(?:thir|four|fif|six|seven|"
          r"eigh|nine)teen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|"
          r"millions?|dozens?)")
UNREADABLE_COUNT = re.compile(
    rf"(?:\b{_WORDS}(?:[\s-]+(?:and\s+)?(?:{_WORDS}|odd|ish)\b)*"
    r"|(?<![\w.,])\d+(?:[.,]\d+)*(?:\s?[km]\b|\+|-?odd\b|-?ish\b)"
    r"|(?<![\w.,])\d{1,3}(?:\.\d{3})+(?!\d))"
    r"\s*(?:(?:(?:tests?|checks?)\s+)?(?:passed|failed|collected)|(?:tests?|checks?)\s+(?:pass|passing|failing))\b",
    re.I)
# A count written in digits that are not ASCII ("passed=\u0666", "\uff16 passed"): int() would read
# its value, but the reader of the HANDOFF may not, so it is never matched against a run.
_FOREIGN = r"\d*(?![0-9])\d\d*"
FOREIGN_DIGITS = re.compile(
    rf"\b(?:passed|failed|collected|skipped|errors)[:=\s]+{_FOREIGN}"
    rf"|(?<![\w.,]){_FOREIGN}\s*(?:/\s*\d+\s*)?(?:tests?\s+|checks?\s+)?"
    r"(?:passed|passing|pass|green|failed|failing|failures?|collected|skipped|errors?)\b", re.I)
# What makes a lone failure or error count a TEST count. "3 Failed/Abandoned ones" and "one failed
# window" (1.3.4 field report: a policy line and a review finding) name things that failed, not
# tests: a clause that states only failures or errors is a count claim when it names a test, a
# runner, a log or a result file, and prose otherwise.
TEST_CONTEXT = re.compile(r"\b(?:tests?|suites?|specs?|pytest|vitest|jest|mocha|playwright|tcount|rtk|dotnet|"
                          r"cargo|counters|trx|logs?)\b|\d{6}Z-\d", re.I)
# A bare "N/N" (e.g. "tsc clean, 638/638") is a full-pass count: passed = collected = N. Only
# when both sides are equal (so "page 2/3" and "24/7" never count), the ratio is not part of
# a longer slash or number chain (dates, versions), and it is not a split, odds or vision.
# "fails 2/2" and "4/4 fail" say that many FAILED: never a pass count. A full stop that ends the
# sentence (before whitespace, a closing quote or bracket, or the end) ends the ratio too: "tcount
# pytest 610/610." is a count, "3/3.5" and "1/1.0.1" are not (it read as none and failed open).
BARE_RATIO = re.compile(r"(?<![\w./-])(?<!page )(?<!p\. )(?<!fail )(?<!fails )(?<!failed )(?<!failing )"
                        r"([0-9]+)/([0-9]+)(?![\w/-]|\.(?![\s\"'\u201d\u2019)\]]|$))"
                        r"(?!\s*(?:split|chance|odds|vision|shares?|mix|fail(?:s|ed|ing|ures?)?)\b)", re.I)
# A bare "N/N" that counts something else: "1000/1000 compactions in the probe" (field report,
# finding 15: read as 1000 tests passed, a MISMATCH against the task's real runs). The ratio is
# no test count when the word right after it is a noun (anything but RATIO_FOLLOWER: a function
# word, as in "638/638 with ORACLE_REQUIRED=1" or "72/72 by tcount") and its clause names no
# test, runner, log or pass/fail word (TEST_CONTEXT, RATIO_TEST_WORDS): "72/72 unit tests",
# "vitest 72/72 files" and "72/72 cases green" stay counts.
RATIO_NEXT_WORD = re.compile(r"\s+([A-Za-z][A-Za-z-]*)")
RATIO_FOLLOWER = re.compile(r"(?:with|by|in|on|under|after|before|at|from|for|via|across|as|and|or|is|are|was|were|"
                            r"now|still|again|total|overall|all|locally|there|here|exit|when|while|once|then|"
                            r"since|per|the|this|that|it|both|each|too|also|so|but|not|no)", re.I)
RATIO_TEST_WORDS = re.compile(r"\b(?:pass(?:ed|es|ing)?|green|fail(?:ed|s|ing|ures?)?|unit|integration|e2e|smoke|"
                              r"regression|assertions?|cases?|checks?|gates?|scenarios?|examples?)\b", re.I)
# A clause that reports a run that is RED BY DESIGN: a revert check, a mutation or sabotage run,
# the regression test before its fix (field report, findings 6 and 13: its count was matched
# like the final count and no standing run has it). See claim_marks and item_evidence.
RED_BY_DESIGN = re.compile(
    r"\b(?:revert(?:ed|ing|s)?|red[- ]by[- ]design|red (?:first|commit|run|step|phase)|mutat(?:ed|ions?|ants?)|"
    r"sabotaged?|counter[- ]test|(?:without|before) the fix|fix (?:removed|reverted|stashed)|"
    r"fail(?:s|ed|ing)? as expected|expected(?:ly)? (?:red|fail(?:s|ed|ing|ures?)?)|negative control)\b", re.I)
# Wording that puts two counts in a sequence: "275 passed after the merge, then 285 passed",
# "275 passed before the fix. Final: 285 passed" (finding 27). LATER marks the count that came
# last, EARLIER the one before it; "after" alone orders nothing ("290 passed after the fix; 285
# passed after the merge" does not say which is final). See claim_marks and item_evidence.
LATER = re.compile(r"\b(?:then|final(?:ly)?|now|later|latest|re-?run|re-?ran)\b|->|\u2192", re.I)
EARLIER = re.compile(r"\b(?:before|earlier|previous(?:ly)?|initial(?:ly)?|first|at first|originally|intermediate|"
                     r"baseline)\b", re.I)
# An item that REPORTS a test run. With no parseable count it gets run_check NO-COUNT: the
# D7 trap ("rtk pytest ... exit 0") is a run claim with nothing to verify against the ledger.
TEST_RUN = re.compile(r"\b(pytest|vitest|jest|mocha|playwright|tcount|rtk|go test|cargo test|"
                      r"(?:npm|pnpm|yarn)(?: run)? test|node --test|test suite|"
                      r"(?:test suite|tests?|suite) (?:pass(?:ed|es)?|ran|run|green|succeed(?:ed|s)?)|"
                      r"all tests|exit(?:ed)?(?: with)?(?: code| status)? 0)\b", re.I)
# A clause that states only the EXIT CODE of a command tcount cannot count ("tsc -b exit 0",
# "eslint . exit 0", "npm run build exit 0") is not a run claim: tcount records test counts, so
# its sender has no run to cite (1.3.5 field report: every frontend HANDOFF tripped on it). Only
# such a clause: it NAMES a tool that is no test and says nothing else that claims a run. It
# stays a claim when it names a test or a test runner (the D7 trap, "rtk pytest ... exit 0"),
# states a count, or holds, beside the tool and the exit code, a verify word or gate, check,
# script, e2e, suite or run wording: "Full gate: exit 0", "make check exited with code 0" and
# "Verified the migration applies cleanly with exit 0" name no such tool and were dropped
# unasked (review of 69d9de3a, EV1; lead ruling).
# The tool: a compiler, linter, formatter or type checker by its name (NAMED_TOOL), anywhere in
# the clause, or a build, lint, format, compile or type-check word (TOOL_WORD) that OPENS the
# clause: "Cypress against the production build exit 0" and "Health probe after docker build
# exit 0" name a test, not a build (review of fb0be49a, finding 4).
# The label: the words up to the last colon before the tool ("Gates on 044830d: frontend: pnpm
# tsc -b exit 0", the field shape). Gate and check wording in it is the label's own; a verify
# word, or script, e2e, suite or run wording in it keeps the claim ("e2e: next build exit 0").
# The words between the label and the tool are read like the rest of the clause (findings 3
# and 4). See exit_only_clause.
EXIT_CODE = re.compile(r"\bexit(?:ed|s)?(?: with)?(?: code| status)?[\s:=]+\d+\b", re.I)
_TOOL_CHECK = r"(?: (?:--)?checks?)?"       # "ruff check", "prettier --check", "type check": the tool's own word
_NAMED_TOOL = (rf"(?:tsc|eslint|prettier|stylelint|biome|ruff|flake8|pylint|mypy|pyright|clippy|gofmt){_TOOL_CHECK}"
               r"|cargo (?:check|clippy)|go vet")
_TOOL_WORD = (r"(?:(?:npm|pnpm|yarn|bun)(?: run)? |dotnet |cargo |go |make |docker |next |vite )?"
              rf"(?:(?:build|lint|format|fmt|typecheck|type[- ]check|compile)(?:s|ed|er|ing)?|black){_TOOL_CHECK}")
NAMED_TOOL = re.compile(rf"\b(?:{_NAMED_TOOL})\b", re.I)
TOOL_WORD = re.compile(rf"\b(?:{_TOOL_WORD})\b", re.I)
NON_TEST_TOOL = re.compile(rf"\b(?:{_NAMED_TOOL}|{_TOOL_WORD})\b", re.I)
# What may stand between the start of the clause (or its label) and a TOOL_WORD that opens it:
# the separator the clause was cut at, a list number, a quote, `cd web &&`, `npx`.
OPENS = re.compile(r"""[\s,;.()\d`'"]*(?:and\s+)?(?:cd\s+\S+\s*&&\s*)?(?:npx\s+|pnpm\s+(?:exec|dlx)\s+)?""", re.I)
LABEL_END = re.compile(r""":(?=[\s`'"]|$)""")
GATE_WORDING = re.compile(r"\b(?:gates?|checks?|checked|checking)\b", re.I)
RUN_WORDING = re.compile(r"\b(?:scripts?|e2e|suites?|runs|running)\b|\.sh\b", re.I)
VERIFY_WORDS = re.compile(
    r"\b(pass(ed|es|ing)?|fail(ed|s|ing|ures?)?|green|verif(y|ied|ies)|tested|tests?|ran|run|"
    r"exit(ed)?|smoke|works?|working|deployed|confirmed|measured|collected|succeed(ed|s)?|"
    r"reproduc(ed|es)|ok)\b", re.I)
COUNTABLE = re.compile(r"\b(?:pytest|vitest|jest|mocha|playwright|tcount|go test|cargo (?:test|nextest)|"
                       r"dotnet (?:test|vstest)|(?:npm|pnpm|yarn|bun)(?: run)? test|node --test|tests?|suites?|"
                       r"specs?)\b", re.I)
# "and" between two numbers ("the route 6 and 7 tests") joins an enumeration, not two clauses.
CLAUSE_SPLIT = re.compile(r"[;\n]|,\s+(?=[a-z])|\band\b(?!(?<=\d\sand)\s\d)", re.I)
# A sentence end inside a clause. The counts of two sentences are two claims: "passed 427
# (203919Z-26754). Full Tenancy: collected 260" read as one, 427 passed of 260 collected, which no
# run has (1.3.5 field report: an item citing six real logs by their short stamps was returned for
# it). Only the counts written with a word are read per sentence (claim_readings): a bare "N/N" is
# still looked for once per clause, and only in a clause that states no other count, so the cut
# reads no ratio the check did not read before ("back 200. Leader same 200/200" are HTTP statuses).
_SENTENCE = r"(?<=[\w)\]'\"`])\.\s+(?=[A-Z0-9(\[`])"
SENTENCE_END = re.compile(_SENTENCE)
EXIT_CLAUSE_SPLIT = re.compile(f"{CLAUSE_SPLIT.pattern}|{_SENTENCE}", re.I)
# A number that ends an enumeration ("6 and 7 tests pass", "tests 3, 4 pass") names tests, it
# is not a count (e1baab9f #13: "the route 6 and 7 tests pass by construction" read as 7 passed).
ENUMERATED = re.compile(r"\d\s*(?:,|&|\band|\bor)\s*$", re.I)
# An exit code in that place is not one: "build exit 0, 36 passed" states that 36 passed.
EXIT_BEFORE = re.compile(EXIT_CODE.pattern + r"\s*(?:,|&|\band|\bor)\s*$", re.I)
SEVERITY = {"MISMATCH": 4, "UNREADABLE": 3, "EXIT": 3, "NO-FILE": 3, "NO-RUN": 2, "NO-COUNT": 2, "UNDECIDED": 1,
            "NO-LEDGER": 1, "MATCH": 0, "n/a": 0}


def field_policy() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(priority fields, excluded fields) of a HANDOFF."""
    return DEFAULT_PRIORITY_FIELDS, DEFAULT_EXCLUDED_FIELDS


def gated_fields(handoff: dict) -> tuple[str, ...]:
    """The handoff's scored fields, in judging order: the canonical ones, then every other
    string or list field in the HANDOFF's own order, then reasoning_chain. Excluded fields
    never count; dict values count only for the canonical fields (as before)."""
    priority, excluded = field_policy()

    def scorable(key: str) -> bool:
        value = handoff.get(key)
        return key not in excluded and (isinstance(value, (str, list, tuple))
                                         or (key in priority and isinstance(value, dict)))

    middle = [key for key in handoff if key not in priority and key not in LAST_FIELDS]
    ordered = [*[key for key in priority if key in handoff], *middle,
               *[key for key in LAST_FIELDS if key in handoff]]
    return tuple(key for key in ordered if scorable(key))


# ---------------------------------------------------------------------------
# HANDOFF items and claimed counts
# ---------------------------------------------------------------------------

def _flatten(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [f"{k}: {t}" for k, v in value.items() for t in _flatten(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _flatten(v)]
    return [str(value)] if value is not None else []


def handoff_items(handoff: dict, fields: tuple[str, ...]) -> list[dict]:
    """One item per list element, bullet or line of the given fields, in field order."""
    items = []
    for field in fields:
        for chunk in _flatten(handoff.get(field)):
            for piece in re.split(r"\n\s*(?:[-*•]|\d+[.)])\s+|\n{2,}", chunk):
                text = " ".join(piece.split())
                if len(text) >= ITEM_MIN_CHARS:
                    items.append({"field": field, "idx": len([i for i in items if i["field"] == field]),
                                  "text": text})
    return items


def _count(number: str) -> int:
    return int(re.sub(r"\D", "", number))


def unreadable_count(text: str) -> bool:
    """The item states a count the parser cannot read exactly: in words or rounded
    (UNREADABLE_COUNT), or in digits that are not ASCII (FOREIGN_DIGITS). A failure count in
    words is one only in a clause with a test in it (TEST_CONTEXT): "one failed window" is prose.
    The clause cuts are found once, not per match (152 KB of such prose took 10 s)."""
    if FOREIGN_DIGITS.search(text):
        return True
    cuts: Optional[tuple[list[int], list[int]]] = None      # (start, end) of each CLAUSE_SPLIT, in order
    for match in UNREADABLE_COUNT.finditer(text):
        if not match.group(0).lower().endswith(("failed", "failing")):
            return True
        if cuts is None:
            found = [cut.span() for cut in CLAUSE_SPLIT.finditer(text)]
            cuts = [start for start, _ in found], [end for _, end in found]
        before = bisect.bisect_right(cuts[1], match.start())          # cuts that end at or before the match
        after = bisect.bisect_left(cuts[0], match.end())              # the first cut at or after its end
        left = cuts[1][before - 1] if before else 0
        right = cuts[0][after] if after < len(cuts[0]) else len(text)
        if TEST_CONTEXT.search(text, left, right):
            return True
    return False


def _clause_counts(clause: str, patterns: tuple) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, pattern in patterns:
        for match in pattern.finditer(clause):
            numbers = [g for g in match.groups() if g is not None]
            before = clause[:match.start()]
            if not numbers or key in counts or (ENUMERATED.search(before) and not EXIT_BEFORE.search(before)):
                continue
            if key == "passed" and len(numbers) > 1 and CHECK_TALLY.search(before):
                continue        # "CI 5/5 green": a tally of checks, not of tests
            counts[key] = _count(numbers[0])
            if key == "passed" and len(numbers) > 1:
                counts.setdefault("collected", _count(numbers[1]))
    return counts


def _counts_something_else(clause: str, end: int) -> bool:
    """A bare ratio ending at `end` is followed by a noun and its clause names no test
    (RATIO_NEXT_WORD, RATIO_FOLLOWER, RATIO_TEST_WORDS)."""
    following = RATIO_NEXT_WORD.match(clause, end)
    return bool(following) and not RATIO_FOLLOWER.fullmatch(following.group(1)) \
        and not TEST_CONTEXT.search(clause) and not RATIO_TEST_WORDS.search(clause)


def _beside_tcount_ratio(clauses: list, number: int, start: int) -> bool:
    """The bare ratio at `start` of clause `number` has a label in front of it, its clause names
    no test, runner, log or pass/fail word, and the clause before or after it gives tcount's own
    ratio (TCOUNT_RATIO): it is the item's tally, and tcount's ratio is the claim."""
    clause = clauses[number] or ""
    if not RATIO_LABEL.search(clause[:start]) or TEST_CONTEXT.search(clause) or RATIO_TEST_WORDS.search(clause):
        return False
    return any(TCOUNT_RATIO.search(clauses[at] or "") for at in (number - 1, number + 1) if 0 <= at < len(clauses))


def _spaced_reading(part: str) -> dict:
    """The word-before-number reading of a chain written with spaces only (SPACED_CHAIN). None
    when a number stands in front of the chain: "8 collected 8 passed 0 failed" reads one way."""
    chain = SPACED_CHAIN.search(part)
    if not chain or re.search(r"\d\s*$", part[:chain.start()]):
        return {}
    reading: dict[str, int] = {}
    for key, number in SPACED_PAIR.findall(part, chain.start()):
        reading.setdefault(key.lower(), _count(number))
    return reading


def claim_readings(text: str) -> list[tuple[dict, ...]]:
    """The readings of each count claim in an item (_claims), in the item's order."""
    return [readings for readings, _part, _number in _claims(text)]


def claim_marks(text: str) -> list[dict]:
    """One {"red", "earlier", "later"} per claim of claim_readings, from the wording of its own sentence.
    red: the claim reports a run that is red by design: its sentence says so (RED_BY_DESIGN) and
    the claim states a failure or an error. A clean count never is, whatever its sentence says,
    with one exception: "revert check: 74 passed and 2 failed" is cut at "and", so the pass
    count in front of a failures-only clause, and the failures-only clause behind a worded one,
    are the same red run. earlier, later: its sentence says it came before, or after, another
    count (EARLIER, LATER)."""
    claims = _claims(text)

    def fails(counts: dict) -> bool:
        return bool(counts.get("failed") or counts.get("errors"))

    marks = []
    for at, (readings, part, number) in enumerate(claims):
        counts, worded = readings[0], bool(RED_BY_DESIGN.search(part))
        before = claims[at - 1] if at else None
        after = claims[at + 1] if at + 1 < len(claims) else None
        only_fails = fails(counts) and set(counts) <= {"failed", "errors"}
        red = (worded and fails(counts)) \
            or bool(only_fails and before and before[2] == number - 1 and RED_BY_DESIGN.search(before[1])) \
            or bool(worded and after and after[2] == number + 1 and fails(after[0][0])
                    and set(after[0][0]) <= {"failed", "errors"})
        marks.append({"red": red, "earlier": bool(EARLIER.search(part)), "later": bool(LATER.search(part))})
    return marks


def _claims(text: str) -> list[tuple[tuple[dict, ...], str, int]]:
    """(readings, the sentence, the clause number) of each count claim in an item.

    Count claims in an item, one per clause (per sentence of it, SENTENCE_END, when the clause
    states a count with a word), each as its readings: (counts,), or (counts,
    keyed) for a keyed chain (KEYED_CHAIN) that reads differently number-before-word and
    word-before-number ("total: 8 passed: 1 failed: 7" is 8 passed, 1 failed, or 1 passed, 7
    failed). The first is
    the claim (claimed_counts); item_evidence matches a claim when either reading has a run or a
    result file, and returns it only when both mismatch (lead ruling, amend_1 on the review fixes).
    A clause that states only failures or errors, names no test (TEST_CONTEXT) and stands beside no
    pass count is prose, when some count in it is not zero: "0 failed, 0 errors" is a clean claim."""
    found: list[tuple[int, str, str, dict, dict]] = []  # (clause number, the clause, a sentence, its counts, keyed)
    clauses = CLAUSE_SPLIT.split(text)
    for number, clause in enumerate(clauses):
        clause = clause or ""
        sentences = [(part, _clause_counts(part, COUNT_PATTERNS)) for part in SENTENCE_END.split(clause)]
        if not any(counts for _, counts in sentences):
            counts = {}
            for match in BARE_RATIO.finditer(clause):
                passed, total = int(match.group(1)), int(match.group(2))
                if passed == total and passed > 0 and not _counts_something_else(clause, match.end()) \
                        and not CHECK_TALLY.search(clause[:match.start()]) \
                        and not _beside_tcount_ratio(clauses, number, match.start()):
                    counts = {"passed": passed, "collected": total}
                    break
            sentences = [(clause, counts)]
        for part, counts in sentences:
            keyed = _clause_counts(part, KEYED_PATTERNS) if counts and KEYED_CHAIN.search(part) else \
                _spaced_reading(part) if counts else {}
            found.append((number, clause, part, counts, keyed))
    claims = []
    for number, clause, part, counts, keyed in found:
        if not counts:
            continue
        if set(counts) <= {"failed", "errors"} and any(counts.values()) and not TEST_CONTEXT.search(clause):
            beside = [other for at, _, _, other, _ in found if abs(at - number) <= 1]
            if not any("passed" in other or "collected" in other for other in beside):
                continue        # failures alone, naming no test and beside no pass count, are prose
        claims.append(((counts, keyed) if keyed and keyed != counts else (counts,), part, number))
    return claims


def exit_only_clause(clause: str) -> bool:
    """The clause states the exit code of a tool that is no test and nothing else that claims a
    run: no test or test runner (COUNTABLE), no count, read or unreadable, and, once the label,
    the tool and the exit code are taken out, no verify word (VERIFY_WORDS) and no gate, check,
    script, e2e, suite or run wording. The tool is the first NAMED_TOOL, or the first TOOL_WORD
    that opens the clause after its label; the label is what stands before the last colon in
    front of the tool, and it keeps the claim when it holds a verify word or script, e2e, suite
    or run wording."""
    if not EXIT_CODE.search(clause) or COUNTABLE.search(clause) or claim_readings(clause) or unreadable_count(clause):
        return False
    for tool in sorted([*NAMED_TOOL.finditer(clause), *TOOL_WORD.finditer(clause)], key=lambda found: found.start()):
        colon = max((found.end() for found in LABEL_END.finditer(clause, 0, tool.start())), default=0)
        if tool.re is TOOL_WORD and not OPENS.fullmatch(clause, colon, tool.start()):
            continue
        label = clause[:colon]
        rest = EXIT_CODE.sub(" ", NON_TEST_TOOL.sub(" ", clause[colon:]))
        return not (VERIFY_WORDS.search(label) or RUN_WORDING.search(label) or VERIFY_WORDS.search(rest)
                    or RUN_WORDING.search(rest) or GATE_WORDING.search(rest))
    return False

def without_exit_codes(text: str) -> str:
    """The item without its clauses that state only the exit code of a command tcount cannot
    count (exit_only_clause): what is left is what can claim a run. The text as given when no
    clause is one."""
    if not EXIT_CODE.search(text):
        return text
    kept, last = [], 0
    for cut in [*EXIT_CLAUSE_SPLIT.finditer(text), None]:
        end = cut.start() if cut else len(text)
        if not exit_only_clause(text[last:end]):
            kept.append(text[last:cut.end() if cut else end])
        last = cut.end() if cut else end
    return "".join(kept)


def reports_test_run(text: str) -> bool:
    """The item reports a test run (TEST_RUN) in a clause that is not only the exit code of a
    command tcount cannot count."""
    return bool(TEST_RUN.search(without_exit_codes(text)))


def every_count(text: str) -> list[dict]:
    """Every pass and fail count in an item, one {key: n} each, wherever it stands. Only the stale
    rule of item_evidence reads them: "went from 36 passed to 37 passed" and a before/after report
    in two sentences hold the latest run's count in a clause whose claim is the older run's."""
    return [{key: _count(match.group(1))} for key, pattern in COUNT_PATTERNS if key in ("passed", "failed")
            for match in pattern.finditer(text)]


def claimed_counts(text: str) -> list[dict]:
    """Count claims in an item, one dict per clause, e.g. [{"passed": 12, "collected": 12}]."""
    return [readings[0] for readings in claim_readings(text)]


def _runner_label(run: dict) -> str:
    """'dotnet', or 'dotnet (exit 1)' when the run exited non-zero."""
    code = run.get("exit_code")
    return str(run.get("runner") or "runner unknown") + (f" (exit {code})" if _nonzero(code) else "")


def _nonzero(code) -> bool:
    return isinstance(code, int) and not isinstance(code, bool) and code != 0


def _has_counts(run: dict, claim: dict) -> bool:
    return all(run.get(key) == value for key, value in claim.items())


def check_claim(claim: dict, runs: list[dict], ledger_exists: bool) -> str:
    if not ledger_exists:
        return "NO-LEDGER"
    if not runs:
        return "NO-RUN"
    if any(_has_counts(run, claim) for run in runs):
        return "MATCH"
    return "MISMATCH"


def failed_exits(claim: dict, runs: list[dict]) -> list[int]:
    """The exit codes of the runs that have a CLEAN claim's counts, when every one of them exited
    non-zero: the claim says nothing failed and the run says something did. [] when the claim
    states a failure or an error (it says what failed), when it states no pass and no zero
    failures, or when a matching run exited 0 or recorded no exit code. The fact is the
    ledger's exit_code, so it holds for every runner."""
    says_clean = "passed" in claim or claim.get("failed") == 0 or claim.get("errors") == 0
    if not says_clean or claim.get("failed") or claim.get("errors"):
        return []
    codes = [run.get("exit_code") for run in runs if _has_counts(run, claim)]
    return sorted(set(codes)) if codes and all(_nonzero(code) for code in codes) else []


def unparsed_only(item: dict) -> bool:
    """The item's ONLY problem is a run recorded with no parsed count: it states a count, this
    task's runs exist and none has a parsed count, and it cites nothing the ledger lacks. A
    MISMATCH, an unreadable count or a missing citation is never this (run_check is the worst)."""
    return bool(item.get("unparsed")) and bool(item.get("counts")) and item.get("run_check") == "NO-RUN" \
        and not item.get("missing")


def run_check(text: str, runs: list[dict], ledger_exists: bool) -> str:
    """Worst outcome over the item's count claims. With no count: 'NO-COUNT' when the item
    reports a test run (nothing to check it against), else 'n/a'."""
    outcomes = [check_claim(claim, runs, ledger_exists) for claim in claimed_counts(text)]
    if unreadable_count(text):
        outcomes.append("UNREADABLE")
    if outcomes:
        return max(outcomes, key=SEVERITY.get)
    return "NO-COUNT" if reports_test_run(text) else "n/a"


def flag_item(item: dict) -> bool:
    """Facts only: a MISMATCH, a count it cannot read exactly, a cited run the ledger lacks, or
    a count that only a count-less probe or another agent's cited run 'backs' (J2, J3) flags."""
    if item["run_check"] in ("MISMATCH", "UNREADABLE", "EXIT", "NO-FILE", "UNDECIDED") or item.get("missing"):
        return True             # UNDECIDED is a line for the lead: acceptance never returns anything
    return bool(item.get("counts") and item["run_check"] == "NO-RUN" and item.get("ran"))


def claim_key(text: str) -> str:
    """The return-once key of an item: its text with case folded and runs of whitespace
    collapsed, plus the counts the gate reads in it. Nothing else is normalised and nothing is
    dropped, so a claim whose numbers changed, or that swaps in a look-alike character, is a NEW
    claim, while the same claim at another index or in another field is not (the
    old key was the list index, so a shifted item was returned again and a changed claim at the
    same index passed as a repeat)."""
    material = json.dumps([" ".join(text.casefold().split()), claimed_counts(text)], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def counts_sig(text: str) -> str:
    """The counts the gate reads in an item, as one short key; "" when it reads none."""
    counts = claimed_counts(text)
    return hashlib.sha256(json.dumps(counts, sort_keys=True).encode("utf-8")).hexdigest()[:12] if counts else ""


# The uncertainty_disposition gate: every handoff.uncertainty item opens with its disposition,
# after an optional bullet and [HIGH]/[MEDIUM]/[LOW] tag. A bare "none" is no item. The label
# counts only there: one inside the item ("... is not fixed: ...") disposes of nothing.
DISPOSITIONS = ("fixed", "known-limit", "not-applicable")
DISPOSITION = re.compile(r"^(?:[-*\u2022]\s*)?(?:\[[a-z]+\]\s*)?(fixed|known[- ]limit|not[- ]applicable)\s*:", re.I)
NO_UNCERTAINTY = re.compile(r"^(?:none|n/?a|no (?:areas of )?uncertaint(?:y|ies)(?: flagged)?)\W*$", re.I)


def uncertainty_items(handoff: dict) -> list[str]:
    """One item per element and per line of handoff.uncertainty (a dict flattens to 'k: v').
    Every line is its own item (aud-bugs #20): splitting only at bullets and blank lines let
    'fixed: a\\nThe cache path is untested' pass as one disposed item."""
    items = []
    for chunk in _flatten(handoff.get("uncertainty")):
        for piece in re.split(r"[\r\n\u0085\u2028\u2029]+", chunk):
            text = re.sub(r"^\d+[.)]\s*", "", " ".join(piece.split()))
            if text and not NO_UNCERTAINTY.match(text):
                items.append(text)
    return items
