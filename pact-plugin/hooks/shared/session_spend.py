"""
Location: pact-plugin/hooks/shared/session_spend.py
Summary: What a session has spent so far, in US dollars, estimated from the token counts its
         transcripts record. Every assistant message in the lead transcript
         (<config>/projects/<slug>/<session>.jsonl) and in each teammate or subagent transcript
         under <session>/subagents/ carries the API's own usage (input, cache reads, 5-minute
         and 1-hour cache writes, output, web searches) and the model; this module prices
         those counts at Anthropic's list API prices. The figure is an ESTIMATE AT LIST API
         PRICES, NOT BILLING: a subscription plan is not billed per token, discounts and
         credits are unknown here, and calls outside the session's transcripts (a `claude -p`
         run, another session) are not counted.
Used by: shared/cost_cap.py (the session cap in hooks/dispatch_gate.py) and bin/repact-cost
         --preview (the one-line preview before /repacted:orchestrate and /repacted:comPACT).
Doc: reference/config.md, section "Session cost cap".

Reading is incremental: a small cache in the session dir (cost-spend.json) keeps, per
transcript, the byte offset read so far, the last message ids and the running sums, so a
check reads only what was appended since the last one. A run reads at most `byte_budget`
bytes; what it could not reach makes the result `partial`, which is then a LOWER bound. The
cache is written by the session's own agents' processes and is trusted as a budget record,
not as a security boundary: an agent that rewrites it can lower its own figure.

Usage is counted once per message id: a streamed message is written as several entries that
repeat its usage (hooks/shared/transcripts.py measured the overcount at about 2.7x).

Example:
    spend = session_spend(Path(lead), cache_path=Path(session_dir) / CACHE_FILE)
    print(f"${spend.usd:.2f} over {spend.files} transcripts ({LABEL})")
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─── prices ────────────────────────────────────────────────────────────────
# Source: Anthropic's published pricing page, read on the as-of date below. When it changes,
# change this table and AS_OF together (reference/config.md quotes both).
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
AS_OF = "2026-10-17"
LABEL = "estimate at list API prices, not billing"

# USD per million tokens: (input, 5m cache write, 1h cache write, cache read, output).
_FABLE_51 = (10.0, 12.5, 20.0, 0.25, 50.0)
_FABLE_5 = (10.0, 12.5, 20.0, 1.0, 50.0)
_OPUS_45_TO_5 = (5.0, 6.25, 10.0, 0.5, 25.0)
_OPUS_4 = (15.0, 18.75, 30.0, 1.5, 75.0)
_SONNET_4 = (3.0, 3.75, 6.0, 0.3, 15.0)
PRICES: Dict[str, Tuple[float, float, float, float, float]] = {
    "fable-5-1": _FABLE_51, "mythos-5-1": _FABLE_51,
    "fable-5": _FABLE_5, "mythos-5": _FABLE_5,
    "opus-5-5": (4.0, 5.0, 8.0, 0.2, 20.0),
    "opus-5": _OPUS_45_TO_5, "opus-4-8": _OPUS_45_TO_5, "opus-4-7": _OPUS_45_TO_5,
    "opus-4-6": _OPUS_45_TO_5, "opus-4-5": _OPUS_45_TO_5,
    "opus-4-1": _OPUS_4, "opus-4": _OPUS_4,
    "sonnet-5-5": (2.0, 2.5, 4.0, 0.1, 10.0),
    "sonnet-5": (2.0, 2.5, 4.0, 0.2, 10.0),
    "sonnet-4-6": _SONNET_4, "sonnet-4-5": _SONNET_4, "sonnet-4": _SONNET_4,
    "haiku-5-5": (0.1, 0.125, 0.2, 0.01, 0.5),
    "haiku-4-5": (1.0, 1.25, 2.0, 0.1, 5.0),
    "haiku-3-5": (0.8, 1.0, 1.6, 0.08, 4.0),
}
# A prompt over the threshold (all input tokens, cache reads and writes included) pays these.
LONG_PROMPT = {"haiku-5-5": (100_000, (0.5, 0.625, 1.0, 0.05, 2.5))}
# Fast mode (usage.speed == "fast"): $8/$40 over $4/$20 for Opus 5.5, $10/$50 over $5/$25 for
# Opus 5 and 4.8; the cache multipliers apply on top, so every class scales by the same factor.
FAST_FACTOR = {"opus-5-5": 2.0, "opus-5": 2.0, "opus-4-8": 2.0}
# US-only inference (usage.inference_geo == "us") costs 1.1x on Claude 4.6 and later models.
US_GEO_FACTOR = 1.1
US_GEO_MODELS = frozenset(k for k in PRICES if k not in ("opus-4-5", "opus-4-1", "opus-4",
                                                         "sonnet-4-5", "sonnet-4", "haiku-4-5",
                                                         "haiku-3-5"))
WEB_SEARCH_USD = 10.0 / 1000           # $10 per 1,000 searches
# A model this table does not name is priced at the highest rate of each class, and named.
HIGHEST = tuple(max(rates[i] for rates in [*PRICES.values(), *(r for _, r in LONG_PROMPT.values())])
                for i in range(5))

_MODEL = re.compile(r"(fable|mythos|opus|sonnet|haiku)-(\d{1,2})(?:-(\d)(?!\d))?")

# ─── reading ───────────────────────────────────────────────────────────────
CACHE_FILE = "cost-spend.json"
CACHE_VERSION = 1
# 256 MB: about 0.35 s on a 2024 laptop (MEASURED 2026-10-17: a session of 111 transcripts,
# ~830 MB, read whole in 1.2 s; lines without usage are skipped before parsing).
DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024
MAX_TOKENS = 10 ** 12                    # a larger value is not a token count
RECENT_IDS = 32                          # message ids remembered per transcript
MAX_UNKNOWN = 10                         # unknown model names kept per transcript
TEAMMATE_FILE = re.compile(r"^agent-a(?P<name>.+)-[0-9a-f]{16}\.jsonl$")


class SpendUnreadable(Exception):
    """The lead transcript cannot be found or read; the message says why."""


@dataclass
class Spend:
    """A session's estimated spend. `partial` means the read stopped at its budget or a
    transcript could not be read, so `usd` is a lower bound; `unreadable` counts the latter."""
    usd: float = 0.0
    tokens: int = 0
    files: int = 0
    partial: bool = False
    unreadable: int = 0
    read: int = 0                                                # bytes read by this call
    unknown_models: List[str] = field(default_factory=list)
    teammates: Dict[str, float] = field(default_factory=dict)   # transcript name -> usd


def price_key(model) -> Optional[str]:
    """The PRICES key of a model id ("claude-opus-5-5", "claude-sonnet-4-5-20250929",
    "us.anthropic.claude-haiku-4-5@..." all resolve), or None."""
    if not isinstance(model, str):
        return None
    match = _MODEL.search(model.lower())
    if match is None:
        return None
    family, major, minor = match.groups()
    key = f"{family}-{major}" + (f"-{minor}" if minor else "")
    return key if key in PRICES else None


def _count(value) -> int:
    return value if type(value) is int and 0 <= value < MAX_TOKENS else 0


def message_cost(model, usage: dict) -> Tuple[float, int, bool]:
    """(usd, tokens, whether the model is priced) for one message's usage. A cache write with no
    5m/1h split is priced as a 1-hour write, which is what Claude Code asks for."""
    key = price_key(model)
    rates = PRICES[key] if key else HIGHEST
    fresh, read = _count(usage.get("input_tokens")), _count(usage.get("cache_read_input_tokens"))
    output = _count(usage.get("output_tokens"))
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        write_5m = _count(split.get("ephemeral_5m_input_tokens"))
        write_1h = _count(split.get("ephemeral_1h_input_tokens"))
    else:
        write_5m, write_1h = 0, _count(usage.get("cache_creation_input_tokens"))
    prompt = fresh + read + write_5m + write_1h
    if key in LONG_PROMPT and prompt > LONG_PROMPT[key][0]:
        rates = LONG_PROMPT[key][1]
    usd = (fresh * rates[0] + write_5m * rates[1] + write_1h * rates[2] + read * rates[3]
           + output * rates[4]) / 1e6
    if usage.get("speed") == "fast" and key in FAST_FACTOR:
        usd *= FAST_FACTOR[key]
    if usage.get("inference_geo") == "us" and key in US_GEO_MODELS:
        usd *= US_GEO_FACTOR
    tools = usage.get("server_tool_use")
    if isinstance(tools, dict):
        usd += _count(tools.get("web_search_requests")) * WEB_SEARCH_USD
    return usd, prompt + output, key is not None


def _fresh_entry() -> dict:
    """One transcript's running record; `done` says the last read reached its end."""
    return {"offset": 0, "ids": [], "usd": 0.0, "tokens": 0, "unknown": [], "done": False}


def _valid_entry(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    offset, usd, tokens = entry.get("offset"), entry.get("usd"), entry.get("tokens")
    ids, unknown = entry.get("ids"), entry.get("unknown")
    return (type(offset) is int and offset >= 0
            and type(usd) in (int, float) and math.isfinite(usd) and usd >= 0
            and type(tokens) is int and tokens >= 0
            and isinstance(ids, list) and len(ids) <= RECENT_IDS and all(isinstance(i, str) for i in ids)
            and isinstance(unknown, list) and all(isinstance(m, str) for m in unknown)
            and type(entry.get("done", False)) is bool)


def _scan(path: Path, entry: dict, budget: int) -> Tuple[int, bool]:
    """Read `path` from entry["offset"], adding to the entry; (bytes read, reached the end).
    Only complete lines are read: a line still being written is read next time."""
    used = 0
    with open(path, "rb") as handle:
        if os.fstat(handle.fileno()).st_size < entry["offset"]:
            entry.update(_fresh_entry())          # the file was replaced: read it again
        handle.seek(entry["offset"])
        for line in handle:
            if not line.endswith(b"\n"):
                return used, True
            if used >= budget:
                return used, False
            used += len(line)
            entry["offset"] += len(line)
            if b'"usage"' not in line or b'"assistant"' not in line:
                continue
            _add_line(line, entry)
    return used, True


def _add_line(line: bytes, entry: dict) -> None:
    try:
        record = json.loads(line)
    except ValueError:
        return
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return
    message = record.get("message")
    if not isinstance(message, dict):
        return
    usage, mid = message.get("usage"), message.get("id")
    if not isinstance(usage, dict) or not usage:
        return
    if isinstance(mid, str):
        if mid in entry["ids"]:
            return
        entry["ids"] = (entry["ids"] + [mid])[-RECENT_IDS:]
    usd, tokens, priced = message_cost(message.get("model"), usage)
    entry["usd"] += usd
    entry["tokens"] += tokens
    model = str(message.get("model"))[:80]
    if not priced and usage and model not in entry["unknown"] and len(entry["unknown"]) < MAX_UNKNOWN:
        entry["unknown"].append(model)


def _transcripts(lead: Path) -> List[Path]:
    """The lead, then every transcript under <session>/subagents/."""
    folder = lead.with_suffix("") / "subagents"
    try:
        others = sorted(p for p in folder.glob("*.jsonl") if p.is_file())
    except OSError:
        others = []
    return [lead, *others]


def load_cache(path: Optional[Path]) -> Dict[str, dict]:
    """The cached per-transcript entries; {} when the file is absent, unreadable or not ours."""
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) and data.get("v") == CACHE_VERSION else None
    if not isinstance(files, dict):
        return {}
    return {name: entry for name, entry in files.items() if isinstance(name, str) and _valid_entry(entry)}


def _save_cache(path: Path, files: Dict[str, dict]) -> None:
    """Write the cache whole, atomically; a failed write is dropped (the next run reads again)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".cost-spend.", suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"v": CACHE_VERSION, "files": files}, handle)
        os.replace(temp, path)
    except (OSError, ValueError, TypeError):
        try:
            os.unlink(temp)
        except OSError:
            pass


def session_spend(lead: Path, cache_path: Optional[Path] = None,
                  byte_budget: int = DEFAULT_BYTE_BUDGET) -> Spend:
    """The session's spend from its lead transcript and subagents dir, reading at most
    `byte_budget` new bytes. Raises SpendUnreadable when the lead cannot be read."""
    if not lead.is_file():
        raise SpendUnreadable("no lead transcript at the session's transcript path")
    cached = load_cache(cache_path)
    files: Dict[str, dict] = {}
    spend = Spend()
    left = max(0, int(byte_budget))
    for index, path in enumerate(_transcripts(lead)):
        name = "lead" if index == 0 else path.name
        entry = dict(cached.get(name) or _fresh_entry())
        try:
            used, done = _scan(path, entry, left)
        except OSError:
            if index == 0:
                raise SpendUnreadable("the lead transcript cannot be read") from None
            spend.unreadable += 1
            spend.partial = True
            files[name] = entry
            spend.usd += entry["usd"]          # what an earlier read counted still stands
            spend.tokens += entry["tokens"]
            continue
        left -= used
        spend.read += used
        spend.partial = spend.partial or not done
        entry["done"] = done
        files[name] = entry
        spend.usd += entry["usd"]
        spend.tokens += entry["tokens"]
        spend.files += 1
        for model in entry["unknown"]:
            if model not in spend.unknown_models:
                spend.unknown_models.append(model)
        if TEAMMATE_FILE.match(name) and done:
            spend.teammates[name] = entry["usd"]
    if cache_path is not None:
        _save_cache(cache_path, files)
    return spend
