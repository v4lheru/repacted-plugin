"""Contract 4, ONE HOME PER RULE: no run of >= 25 identical words shared by two loaded surfaces.

repacted-fork.md §8.4. PACT deliberately mirrored rules across surfaces; rePACTed gives each rule
one home and points to it from everywhere else. The detector below finds every shared run of
RUN_WORDS or more identical (whitespace- and backtick-normalised) words between two different
loaded surfaces.

ENFORCED: any undeclared pair fails. Two ways text may legitimately repeat, both narrow:
- a PINNED REGION: a marker-delimited copy that another check keeps byte-identical to its one
  source. The detector drops the region before comparing, so prose around it is still checked.
- a declared EXCEPTION: one named pair of surfaces, with the reason the copy cannot be a pointer.
Never declare an exception to hide a rule that could have one home.

Run `python3 -m tests.contract.test_one_home` from pact-plugin/ for the full pair list.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from tests.contract._surfaces import loaded_surfaces, read, rel
from tests.test_claudemd_write_prohibition_guard import TEMPLATE_END_ANCHOR, TEMPLATE_START_ANCHOR

RUN_WORDS = 25
# Built, not written out: tests/test_store_access_bar_presence.py treats any file holding the literal
# begin marker as a carrier of the bar.
STORE_BAR = "<!-- PACT_STORE_BAR_{} -->"
# Delimited regions whose copies another check pins to one source or to each other.
PINNED_REGIONS = (
    # the store-access bar; tests/test_store_access_bar_presence.py holds every copy to the source
    re.compile(re.escape(STORE_BAR.format("BEGIN")) + ".*?" + re.escape(STORE_BAR.format("END")), re.S),
    # protocol views written by dev/protocol/render.py; render.py --check and
    # tests/contract/test_protocol_rendered.py hold them to the descriptor
    re.compile(r"<!-- protocol:[\w-]+ -->.*?<!-- /protocol -->", re.S),
    # the CLAUDE.md rule each dispatch template emits into a task description, for a teammate that
    # never loads the teammate skill; tests/test_claudemd_write_prohibition_guard.py (W4) holds the
    # template copies byte-equal, and a marker there would ship inside every task description
    re.compile(re.escape(TEMPLATE_START_ANCHOR) + ".*?" + re.escape(TEMPLATE_END_ANCHOR), re.S),
)
# Declared exceptions: {(file_a, file_b) sorted: why the shared text cannot be a pointer}.
EXCEPTIONS: dict[tuple[str, str], str] = {}


def words(text: str) -> list[str]:
    for region in PINNED_REGIONS:
        text = region.sub(" ", text)
    return text.replace("`", "").split()


def shared_runs(texts: dict[str, str], run: int = RUN_WORDS) -> dict[tuple[str, str], int]:
    """{(file_a, file_b): shared word-shingles} for every pair sharing at least one run of `run` words."""
    owners: dict[int, set[str]] = defaultdict(set)
    for name, text in texts.items():
        seq = words(text)
        for i in range(len(seq) - run + 1):
            owners[hash(" ".join(seq[i:i + run]))].add(name)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for names in owners.values():
        if len(names) > 1:
            ordered = sorted(names)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    pairs[(a, b)] += 1
    return dict(pairs)


def surface_texts() -> dict[str, str]:
    return {rel(p): read(p) for p in loaded_surfaces()}


def report() -> tuple[dict[tuple[str, str], int], str]:
    pairs = {k: v for k, v in shared_runs(surface_texts()).items() if k not in EXCEPTIONS}
    lines = [f"{n:6d} shared {RUN_WORDS}-word runs: {a} <-> {b}"
             for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])]
    summary = (f"one-home: {len(pairs)} surface pairs share >= {RUN_WORDS}-word runs "
               f"({sum(pairs.values())} shingles)")
    return pairs, "\n".join([summary, *lines])


def test_detector_finds_a_planted_mirror():
    rule = " ".join(f"w{i}" for i in range(RUN_WORDS))
    texts = {"a.md": f"intro {rule} outro", "b.md": f"other `{rule}` end", "c.md": "unrelated text"}
    assert shared_runs(texts) == {("a.md", "b.md"): 1}


def test_detector_ignores_a_run_one_word_short():
    near = " ".join(f"w{i}" for i in range(RUN_WORDS - 1))
    assert shared_runs({"a.md": near + " x", "b.md": near + " y"}) == {}


def test_detector_skips_a_pinned_region_but_not_the_prose_around_it():
    rule = " ".join(f"w{i}" for i in range(RUN_WORDS))
    bar = f"{STORE_BAR.format('BEGIN')} {rule} {STORE_BAR.format('END')}"
    view = f"<!-- protocol:wait --> {rule} <!-- /protocol -->"
    claudemd = f"{TEMPLATE_START_ANCHOR} {rule} {TEMPLATE_END_ANCHOR}"
    assert shared_runs({"a.md": f"x {bar} {view} {claudemd} y", "b.md": f"p {bar} {view} {claudemd} q"}) == {}
    assert shared_runs({"a.md": f"{bar} {rule}", "b.md": f"{bar} {rule}"}) == {("a.md", "b.md"): 1}


def test_detector_does_not_skip_an_unclosed_marker():
    rule = " ".join(f"w{i}" for i in range(RUN_WORDS))
    texts = {"a.md": f"{STORE_BAR.format('BEGIN')} {rule}", "b.md": rule}
    assert shared_runs(texts) == {("a.md", "b.md"): 1}


def test_every_exception_names_a_pair_that_still_shares_text():
    live = shared_runs(surface_texts())
    stale = [pair for pair in EXCEPTIONS if pair not in live]
    assert not stale, f"declared exceptions with nothing left to excuse: {stale}"


def test_one_home_across_loaded_surfaces():
    pairs, text = report()
    assert not pairs, text


if __name__ == "__main__":
    print(report()[1])
    sys.exit(0)
