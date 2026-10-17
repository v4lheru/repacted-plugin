"""vault-find's output: the text lines and the JSON, both under one byte cap.

Location: pact-plugin/bin/_kb/findview.py. Split from bin/vault-find (its byte ceiling);
vault-find imports render_text, render_json and page_origin. Text lines print human KB text as
written and distill-origin text inside the data wrapper (_kb/datawrap.py), as recall does.
"""
from __future__ import annotations

import json
from pathlib import Path

from _kb import datawrap as W

WITHHELD_SHOWN = 3
OUTPUT_CAP = 2000               # hard cap on everything printed to stdout, in bytes


def page_origin(page: dict) -> str:
    """`distill` when repact-distill wrote the page: its first lesson with a provenance is a
    distill one, so the summary, the MOC line and every untagged paragraph are model text too.
    Else `kb`. A doubtful page reads as distill (wrapped), never the other way round."""
    first = next((les["ctx"] for les in page.get("lessons") or [] if les.get("ctx")), None)
    return W.DISTILL if first == W.DISTILL else "kb"


def format_line(hit: dict) -> str:
    """One hit. A human page or lesson prints as written; distill text (a distill page's summary,
    a distill lesson's title) prints inside the data wrapper with the [distill] mark."""
    tags = f"  [{', '.join(hit['tags'])}]" if hit["tags"] else ""
    lesson, rel = hit.get("lesson"), hit["relpath"]
    summary, distill_page = hit["summary"], hit.get("origin") == W.DISTILL
    if distill_page:
        summary = f"{W.DISTILL_MARK} {W.element(f'vault/{rel}', rel, W.DISTILL, summary)}"
    where = ""
    if lesson:
        title = lesson["title"]
        if distill_page or lesson.get("ctx") == W.DISTILL:
            title = f"{W.DISTILL_MARK} {W.element(f'vault/{rel}', lesson['anchor'], W.DISTILL, title)}"
        where = f"  ¶ L{lesson['line']} {title}"
    return f"{hit['score']:.1f}  {rel}  — {summary}{tags}{where}"


def withheld_line(withheld: list[dict]) -> str:
    """One line naming withheld notes by PATH only, never content."""
    shown = [item["path"] for item in withheld[:WITHHELD_SHOWN]]
    more = f" (+{len(withheld) - len(shown)} more)" if len(withheld) > len(shown) else ""
    noun, verb = ("note", "holds") if len(withheld) == 1 else ("notes", "hold")
    return (f"{len(withheld)} {noun} withheld: {', '.join(shown)}{more} — {verb} credentials "
            "(move the key out to restore recall)")


def render_text(hits: list[dict], vault: Path, withheld: list[dict] | None = None,
                cap: int = OUTPUT_CAP) -> str:
    """Lines that fit the byte cap, then the withheld line, then `root=`. The last two are
    always kept."""
    tail = ([withheld_line(withheld)] if withheld else []) + [f"root={vault}"]
    budget = cap - sum(len(line.encode()) + 1 for line in tail)
    lines = []
    for hit in hits:
        line = format_line(hit)
        size = len(line.encode()) + 1
        if size > budget:
            break
        lines.append(line)
        budget -= size
    return "\n".join(lines + tail)


def render_json(hits: list[dict], vault: Path, terms: int, withheld: list[dict] | None = None,
                cap: int = OUTPUT_CAP) -> str:
    """JSON under the same byte cap: trailing hits are dropped until it fits.

    `terms` is the number of distinct query terms, so a caller can normalise scores per
    term without re-implementing the tokeniser.
    """
    kept = list(hits)
    while True:
        text = json.dumps({"root": str(vault), "terms": terms, "hits": kept,
                           "withheld": (withheld or [])[:WITHHELD_SHOWN]}, ensure_ascii=False)
        if len(text.encode()) <= cap or not kept:
            return text
        kept.pop()
