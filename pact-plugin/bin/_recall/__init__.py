"""The recall package (D9b): read-only retrievers over every knowledge store, one merge.

Location: pact-plugin/bin/_recall/ (work unit D9b; design the design notes D9).
Used by: dispatch.handle (the one dispatcher, shadow only, unregistered) and bin/recall (replay,
signals).

Every retriever returns at most MAX_CANDIDATES candidates shaped
{store, id, key, title, summary, score}, where `key` is `<store>:<id>` (the id form of D9a's
frozen labels). Every candidate is built through `make_candidate`, the D9s S3 gate
(D9.12 rules 2-3, D25): an item whose path or text carries a credential is WITHHELD (never a
candidate), and every text field that leaves is passed through `scrub_text`. There is no
second pattern list: both helpers come from `_repact_common`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

BIN_DIR = Path(__file__).resolve().parent.parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from _repact_common import credential_reason, scrub_text  # noqa: E402

MAX_CANDIDATES = 5
TITLE_MAX = 120
SUMMARY_MAX = 160

STOP_WORDS = frozenset("""
a an and are as at be but by can do does for from has have how i if in into is it its me my
no not of on or our so than that the their them then there these they this to up us was we
were what when where which while who why will with you your via per use using get set new
""".split())
# Path parts that name no subject: they would match every index line.
GENERIC_PATH_PARTS = frozenset("""
users documents src lib app apps pkg packages test tests spec docs doc bin scripts script tmp
private var folders index main utils util components component public static assets build dist
node_modules claude projects personal md py ts tsx js jsx json yaml yml txt sh toml cfg lock
www com net org io api http https
""".split())
TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase alphanumeric runs, stop words dropped, a plural s stripped."""
    out = []
    for raw in TOKEN.findall(text.lower()):
        if len(raw) < 3 or raw in STOP_WORDS:
            continue
        if len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        out.append(raw)
    return out


def cut(text: str, limit: int) -> str:
    """Whitespace-collapsed and cut AFTER redaction (a cut could split a secret)."""
    text = scrub_text(" ".join(str(text or "").split()))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def make_candidate(store: str, item_id: str, title: str, summary: str, score: float,
                   source_text: str, source_path: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]:
    """(candidate, None), or (None, reason) when the item is withheld for a credential.

    `source_text` is the item's FULL text (not the summary): exclusion is per item, at
    retrieval time, so a credential anywhere in it withholds the whole item.
    """
    reason = credential_reason(source_path or f"{store}/{item_id}", source_text or "")
    if reason:
        return None, reason
    return {"store": store, "id": item_id, "key": f"{store}:{item_id}", "title": cut(title, TITLE_MAX),
            "summary": cut(summary, SUMMARY_MAX), "score": round(float(score), 3)}, None


def first_sentence(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-*#> ").strip()
        if len(line) > 12 and not line.startswith(("---", "|", "```")) and ":" not in line[:15]:
            match = re.match(r"(.+?[.!?])(\s|$)", line)
            return (match.group(1) if match else line).strip()
    return ""
