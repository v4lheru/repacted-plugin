"""Token and cost estimates, the price table, the caps, and the backend contract.

Location: pact-plugin/bin/_distill/estimate.py. Used by bin/repact-distill for the dry
run and for the cap check before each chunk of a real run.

No price is written in code (ruling R11). Cost comes only from a price table file the user
configures (profile distill.pricing_source or --pricing), shaped like
templates/distill-pricing.example.json:
  {"asof": "YYYY-MM-DD", "currency": "USD", "output_ratio": <number|null>,
   "models": {"<model>": {"input_per_mtok": <number|null>, "output_per_mtok": <number|null>}}}
A missing as-of date, a missing model or a null price means "cost: not computed", never a guess.
Token counts are ESTIMATES: (bytes / bytes_per_token plus a fixed per-call overhead) times the
input multiplier of a --json-schema call.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Protocol

# Estimates measured by the U5.1 spike (the design notes), not prices:
DEFAULT_BYTES_PER_TOKEN = 2.3    # pooled mixed text; prose ran higher, code and JSON lower
CALL_OVERHEAD_TOKENS = 1000      # fixed input per call with a JSON schema
DEFAULT_OUTPUT_RATIO = 0.1       # output tokens per input token, design note §5.4 (believed)
# Input tokens a --json-schema call really bills per estimated token: 97,576 actual vs 50,694
# estimated in the one live U5.4 call (MEASURED on n=1; profile distill.schema_input_multiplier
# or --schema-input-multiplier overrides it).
SCHEMA_INPUT_MULTIPLIER = round(97_576 / 50_694, 3)
TOKENS_PER_MTOK = 1_000_000
ASOF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CAP_NAMES = ("usd", "tokens", "sessions")


class PriceTableError(ValueError):
    """The price table file cannot be used; the message names the field and the fix."""


@dataclass(frozen=True)
class PriceTable:
    path: str
    asof: Optional[str]
    currency: str
    output_ratio: Optional[float]
    models: dict


@dataclass(frozen=True)
class Estimate:
    chunks: int
    tokens_in: int
    tokens_out: int
    cost: Optional[float]
    currency: str
    price_asof: Optional[str]
    model: str
    note: str            # why the cost is not computed, or "" when it is


@dataclass(frozen=True)
class ChunkResult:
    """What a backend returns per chunk (U5.4 implements it)."""
    chunk_key: str
    candidates: list
    tokens_in: int
    tokens_out: int
    cost: Optional[float]


def usage_problem(cost, *counts) -> Optional[str]:
    """Why a backend's cost or token counts cannot be trusted, or None. A NaN or negative value
    would disable or rewind CapMeter, so the chunk fails instead: cost None (unpriced) is fine;
    a count must be an int >= 0 (bool is not one); a cost a finite number >= 0."""
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))
                             or not math.isfinite(cost) or cost < 0):
        return "bad cost"
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts):
        return "bad usage"
    return None


class Backend(Protocol):
    """The model backend contract (KB lite key decision 12). U5.4 provides bin/_distill/backend.py
    with make_backend(settings: dict) -> Backend."""
    name: str

    def estimate(self, chunks: list) -> Estimate: ...

    def extract(self, chunks: list) -> Iterator[ChunkResult]: ...


def _number_or_null(value, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 \
            or not math.isfinite(value):
        raise PriceTableError(f"{where} must be a non-negative number or null")
    return float(value)


def _reject_constant(name: str):
    raise PriceTableError(f"{name} is not a number; use a number or null")


def load_price_table(path: Path) -> PriceTable:
    """Parse and check the table. Raises PriceTableError with the field and the way to fix it."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as exc:
        raise PriceTableError(f"cannot read the price table ({type(exc).__name__})") from None
    except ValueError as exc:
        raise PriceTableError(f"the price table is not valid JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise PriceTableError("the price table needs a \"models\" object")
    asof = data.get("asof")
    if asof is not None and not (isinstance(asof, str) and ASOF_RE.match(asof)):
        raise PriceTableError("\"asof\" must be a YYYY-MM-DD date or null")
    models = {}
    for name, prices in data["models"].items():
        if not isinstance(prices, dict):
            raise PriceTableError(f"models.{name} must be an object")
        models[name] = {key: _number_or_null(prices.get(key), f"models.{name}.{key}")
                        for key in ("input_per_mtok", "output_per_mtok")}
    currency = data.get("currency") if isinstance(data.get("currency"), str) else "USD"
    return PriceTable(str(path), asof, currency,
                      _number_or_null(data.get("output_ratio"), "output_ratio"), models)


def tokens_for(nbytes: int, bytes_per_token: float, multiplier: float = 1.0) -> int:
    return math.ceil((math.ceil(nbytes / bytes_per_token) + CALL_OVERHEAD_TOKENS) * multiplier)


def output_tokens(tokens_in: int, table: Optional[PriceTable]) -> int:
    ratio = table.output_ratio if table and table.output_ratio is not None else DEFAULT_OUTPUT_RATIO
    return math.ceil(tokens_in * ratio)


def why_no_cost(model: str, table: Optional[PriceTable]) -> str:
    """"" when `table` prices `model`, else the reason the cost cannot be computed."""
    if table is None:
        return "no price table configured; set distill.pricing_source in profile.json"
    if not table.asof:
        return f"the price table has no as-of date ({table.path})"
    prices = table.models.get(model)
    if prices is None:
        return f"model {model!r} is not in the price table ({table.path})"
    for key in ("input_per_mtok", "output_per_mtok"):
        if prices[key] is None:
            return f"models.{model}.{key} is null in {table.path}"
    return ""


def cost_of(tokens_in: int, tokens_out: int, model: str,
            table: Optional[PriceTable]) -> Optional[float]:
    if why_no_cost(model, table):
        return None
    prices = table.models[model]
    return (tokens_in * prices["input_per_mtok"]
            + tokens_out * prices["output_per_mtok"]) / TOKENS_PER_MTOK


def chunk_estimate(chunk, model: str, table: Optional[PriceTable], bytes_per_token: float,
                   multiplier: float = SCHEMA_INPUT_MULTIPLIER) -> tuple[int, int, Optional[float]]:
    """(tokens_in, tokens_out, cost or None) for one chunk."""
    tokens_in = tokens_for(chunk.size, bytes_per_token, multiplier)
    tokens_out = output_tokens(tokens_in, table)
    return tokens_in, tokens_out, cost_of(tokens_in, tokens_out, model, table)


def estimate_chunks(chunks: list, model: str, table: Optional[PriceTable], bytes_per_token: float,
                    multiplier: float = SCHEMA_INPUT_MULTIPLIER) -> Estimate:
    tokens_in = tokens_out = 0
    for chunk in chunks:
        chunk_in, chunk_out, _ = chunk_estimate(chunk, model, table, bytes_per_token, multiplier)
        tokens_in, tokens_out = tokens_in + chunk_in, tokens_out + chunk_out
    note = why_no_cost(model, table)
    return Estimate(len(chunks), tokens_in, tokens_out,
                    None if note else cost_of(tokens_in, tokens_out, model, table),
                    table.currency if table else "USD", table.asof if table else None,
                    model, note)


@dataclass(frozen=True)
class Caps:
    usd: Optional[float] = None
    tokens: Optional[int] = None
    sessions: Optional[int] = None


class CapMeter:
    """Running totals for one run. would_exceed() asks before a chunk is sent; add() records
    what it cost (an estimate in a dry run, the backend's usage in a real run)."""

    def __init__(self, caps: Caps):
        self.caps = caps
        self.tokens = 0
        self.usd = 0.0
        self.sessions: set = set()

    def would_exceed(self, tokens: int, cost: Optional[float], session: str) -> Optional[str]:
        """The cap name this chunk would break, or None. A usd cap with no computable cost
        stops the run: an unpriced run cannot honour a dollar limit."""
        caps = self.caps
        if caps.tokens is not None and self.tokens + tokens > caps.tokens:
            return "tokens"
        if caps.usd is not None and (cost is None or self.usd + cost > caps.usd):
            return "usd"
        if caps.sessions is not None and session not in self.sessions \
                and len(self.sessions) + 1 > caps.sessions:
            return "sessions"
        return None

    def add(self, tokens: int, cost: Optional[float], session: str) -> None:
        self.tokens += tokens
        self.usd += cost or 0.0
        self.sessions.add(session)
