"""The Anthropic API backend for repact-distill: the Messages API, or one Message Batch per run.

Location: pact-plugin/bin/_distill/backend_api.py. bin/repact-distill loads it instead
of backend.py when distill.backend is "api" (or --backend api), through the same seam
`make_backend(settings) -> Backend`; batch mode (distill.batch / --batch) is driven by
_distill/batchrun.py through submit(), status() and results().

Same contract as backend.py: the input is the already-redacted chunk in the prompts frame, and
the reply goes through the same schema and intake path. The request carries no tools: the
reply is forced by output_config.format (a JSON schema), because forced tool_choice is refused
by current models. A reply with any tool call, more or less than one text block, a stop reason
other than end_turn, or usage that is missing, NaN or negative raises BackendError.

  - the `anthropic` SDK is optional (`uv pip install anthropic`); without it, or without
    ANTHROPIC_API_KEY in the environment, make_backend raises Unavailable and the run refuses.
  - the key is read from the environment when the client is built and handed to the SDK; it is
    never stored on this object, logged, or put in any error text (errors carry type names).

Settings (profile `distill` block, plus `model` from the CLI): model (default DEFAULT_MODEL; a
claude -p alias such as "sonnet" refuses), effort (optional), max_output_tokens (default 16000), timeout_s (default 300), batch (default false),
batch_poll_s (default 60), batch_max_wait_s (default 3600; a batch still running then is
collected by the next run).
"""
from __future__ import annotations

import json
import os
from typing import Callable, Iterator, Optional

from _distill import estimate as E
from _distill import prompts, schema
from _distill.backend import MODEL_RE, BackendError

NAME = "api"
DEFAULT_MODEL = "claude-opus-5-5"
INSTALL = "uv pip install anthropic"
KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MAX_OUTPUT = 16000
MAX_OUTPUT = 128000
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 3600.0
DEFAULT_POLL_S = 60.0
DEFAULT_MAX_WAIT_S = 3600.0
MAX_WAIT_S = 24 * 3600.0
MAX_TEXT_BYTES = 4 * 1024 * 1024
BATCH_PRICE_FACTOR = 0.5          # the Message Batches API bills half the standard token price
INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
OPTIONAL_FIELDS = INPUT_FIELDS[1:]
REPLY_BLOCKS = ("text", "thinking", "redacted_thinking")    # anything else is refused
# Structured outputs reject these keywords; schema.validate_output still enforces them locally.
UNSUPPORTED_KEYWORDS = ("minLength", "maxLength", "minItems", "maxItems", "pattern")
CLI_ALIASES = ("sonnet", "opus", "haiku")     # claude -p names; the API needs a full model id
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class Unavailable(RuntimeError):
    """The backend cannot start; `refusal` is the one line the run prints (never a secret)."""

    def __init__(self, refusal: str):
        super().__init__(refusal)
        self.refusal = refusal


def _number(settings: dict, key: str, default: float, high: float, whole: bool = False):
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= high \
            or (whole and int(value) != value):
        kind = "a whole number" if whole else "a number"
        raise ValueError(f"distill.{key} must be {kind}, 0 < n <= {high:g}")
    return int(value) if whole else float(value)


def api_schema(node=schema.OUTPUT_SCHEMA):
    """OUTPUT_SCHEMA without the keywords structured outputs refuses (a copy; the original stays)."""
    if isinstance(node, dict):
        return {k: api_schema(v) for k, v in node.items() if k not in UNSUPPORTED_KEYWORDS}
    if isinstance(node, list):
        return [api_schema(v) for v in node]
    return node


def sdk_client(timeout: float):
    """An anthropic.Anthropic client, or Unavailable. The key goes straight to the SDK."""
    try:
        import anthropic
    except ImportError:
        raise Unavailable(f"the api backend needs the anthropic SDK: run `{INSTALL}`") from None
    key = os.environ.get(KEY_ENV)
    if not key:
        raise Unavailable(f"the api backend needs {KEY_ENV} set in the environment") from None
    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=2)


def as_dict(value) -> dict:
    """An SDK response object (pydantic) or a plain dict, as a dict; anything else is {}."""
    if isinstance(value, dict):
        return value
    for method in ("to_dict", "model_dump"):
        if callable(getattr(value, method, None)):
            try:
                out = getattr(value, method)()
            except Exception:
                return {}
            return out if isinstance(out, dict) else {}
    return {}


def _usage(message: dict) -> tuple:
    usage = message.get("usage")
    if not isinstance(usage, dict) or not all(f in usage for f in ("input_tokens", "output_tokens")):
        raise BackendError("bad usage")
    counts = [usage[f] for f in INPUT_FIELDS + ("output_tokens",)
              if f not in OPTIONAL_FIELDS or usage.get(f) is not None]
    problem = E.usage_problem(None, *counts)
    if problem:
        raise BackendError(problem)
    tokens_in = sum(usage[f] for f in INPUT_FIELDS if usage.get(f) is not None)
    return tokens_in, usage["output_tokens"]


def parse_message(message) -> tuple:
    """(candidates list, tokens_in, tokens_out) from one Messages API reply. Fails closed."""
    message = as_dict(message)
    blocks = message.get("content")
    if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
        raise BackendError("no content")
    if any(b.get("type") not in REPLY_BLOCKS for b in blocks):
        raise BackendError("isolation broken: the reply carries a tool call or an unknown block")
    if message.get("stop_reason") != "end_turn":
        raise BackendError("stop reason")
    texts = [b.get("text") for b in blocks if b.get("type") == "text"]
    if len(texts) != 1 or not isinstance(texts[0], str):
        raise BackendError("not exactly one text block")
    if len(texts[0].encode("utf-8", "surrogatepass")) > MAX_TEXT_BYTES:
        raise BackendError("output too large")
    tokens_in, tokens_out = _usage(message)
    try:
        output = json.loads(texts[0])
    except ValueError:
        raise BackendError("output is not JSON") from None
    if not isinstance(output, dict) or set(output) != {"candidates"} \
            or not isinstance(output["candidates"], list):
        raise BackendError("output shape")
    return output["candidates"], tokens_in, tokens_out


class ApiBackend:
    """estimate.Backend over the Messages API; with `batch`, batchrun.py drives submit/collect."""

    name = NAME

    def __init__(self, settings: dict, client_factory: Optional[Callable] = None):
        model = settings.get("model") or DEFAULT_MODEL
        if not isinstance(model, str) or not MODEL_RE.match(model):
            raise ValueError("distill.model must be a model name (letters, digits, . _ : - [ ])")
        if model in CLI_ALIASES:
            raise Unavailable(f"the api backend needs a full model id in distill.model or --model "
                              f"(for example {DEFAULT_MODEL}), not the claude -p alias {model!r}")
        effort = settings.get("effort")
        if effort is not None and effort not in EFFORTS:
            raise ValueError("distill.effort must be one of: " + ", ".join(EFFORTS))
        self.model, self.effort = model, effort
        self.max_tokens = _number(settings, "max_output_tokens", DEFAULT_MAX_OUTPUT, MAX_OUTPUT, True)
        self.timeout = _number(settings, "timeout_s", DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S)
        self.poll_s = _number(settings, "batch_poll_s", DEFAULT_POLL_S, MAX_WAIT_S)
        self.max_wait_s = _number(settings, "batch_max_wait_s", DEFAULT_MAX_WAIT_S, MAX_WAIT_S)
        batch = settings.get("batch", False)
        if not isinstance(batch, bool):
            raise ValueError("distill.batch must be true or false")
        self.batch = batch
        self.price_factor = BATCH_PRICE_FACTOR if batch else 1.0
        self._client = (client_factory or sdk_client)(self.timeout)

    def params(self, text: str) -> dict:
        """One request: the system prompt, the framed chunk and the forced JSON schema; no tools."""
        output = {"format": {"type": "json_schema", "schema": api_schema()}}
        if self.effort:
            output["effort"] = self.effort
        return {"model": self.model, "max_tokens": self.max_tokens,
                "system": prompts.SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompts.build_input(text)}],
                "output_config": output}

    def _call(self, what: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BackendError:
            raise
        except Exception as exc:   # the SDK's message can quote the request: keep the type only
            raise BackendError(f"api {what}: {type(exc).__name__}") from None

    def estimate(self, chunks: list) -> E.Estimate:
        return E.estimate_chunks(chunks, self.model, None, E.DEFAULT_BYTES_PER_TOKEN)

    def extract(self, chunks: list) -> Iterator[E.ChunkResult]:
        for chunk in chunks:
            reply = self._call("call", self._client.messages.create, **self.params(chunk.text))
            candidates, tokens_in, tokens_out = parse_message(reply)
            yield E.ChunkResult(chunk.chunk_key, candidates, tokens_in, tokens_out, None)

    def submit(self, items: list) -> str:
        """One batch of (custom_id, redacted text); returns the batch id."""
        requests = [{"custom_id": cid, "params": self.params(text)} for cid, text in items]
        batch = as_dict(self._call("batch create", self._client.messages.batches.create,
                                   requests=requests))
        batch_id = batch.get("id")
        if not isinstance(batch_id, str) or not batch_id:
            raise BackendError("api batch create: no batch id")
        return batch_id

    def status(self, batch_id: str) -> str:
        batch = as_dict(self._call("batch status", self._client.messages.batches.retrieve, batch_id))
        return str(batch.get("processing_status"))

    def results(self, batch_id: str) -> Iterator[tuple]:
        """(custom_id, result type, message dict or None) per result, in the API's order."""
        items = self._call("batch results", self._client.messages.batches.results, batch_id)
        try:
            for item in items:
                item = as_dict(item)
                result = as_dict(item.get("result"))
                yield item.get("custom_id"), result.get("type"), result.get("message")
        except Exception as exc:   # a stream that breaks mid-way: the type only, as in _call
            raise BackendError(f"api batch results: {type(exc).__name__}") from None


def make_backend(settings: dict, client_factory: Optional[Callable] = None) -> ApiBackend:
    """The seam bin/repact-distill loads. ValueError on a bad setting; Unavailable without the
    SDK or the key (the run prints its refusal line)."""
    return ApiBackend(settings if isinstance(settings, dict) else {}, client_factory)
