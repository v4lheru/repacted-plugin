"""The Anthropic API and Message Batches backend for repact-distill.

Location: pact-plugin/tests/test_distill_api.py. No network and no real key: a fake
client is injected at the client_factory seam (or a fake `anthropic` module, for the key path),
and the run loop runs through CLI.main with the fixtures of tests/test_distill_backend.py.
Adversarial rows sit at the response boundary: tool calls, shapes, sizes, unicode, usage.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from tests.test_distill_backend import (  # noqa: F401  # notes and stranger are fixtures
    CLI, FakeRedactor, candidate, notes, stranger)
from _distill import backend_api as A  # noqa: E402
from _distill import batchrun as BR  # noqa: E402
from _distill import ledger as L  # noqa: E402
from _distill import schema as SC  # noqa: E402
from _distill.backend import BackendError  # noqa: E402

KEY = "fake-test-key-QX7vT2mL9pR4wZ"     # not key-shaped; the scan below looks for it verbatim
pytestmark = pytest.mark.usefixtures("notes")   # a config dir with notes, distill enabled


def reply(text='{"candidates": []}', *, blocks=None, stop="end_turn", usage=None) -> dict:
    usage = {"input_tokens": 1000, "output_tokens": 100} if usage is None else usage
    return {"content": blocks if blocks is not None else [{"type": "text", "text": text}],
            "stop_reason": stop, "usage": usage}


class Crash(BaseException):
    """A run killed mid-wait (Ctrl-C, a reboot): not an Exception, so nothing catches it."""


class FakeClient:
    """messages.create and messages.batches.{create,retrieve,results}; records every call."""

    def __init__(self, answer=lambda params: reply(), statuses=("ended",), results=None):
        self.answer, self.statuses, self.results_for = answer, list(statuses), results
        self.calls, self.batches_created, self.polls = [], [], 0
        self.on_poll = None
        self.messages = types.SimpleNamespace(create=self._create, batches=types.SimpleNamespace(
            create=self._batch_create, retrieve=self._retrieve, results=self._results))

    def _create(self, **params):
        self.calls.append(params)
        return self.answer(params)

    def _batch_create(self, requests):
        self.batches_created.append(requests)
        return {"id": f"msgbatch_{len(self.batches_created)}", "processing_status": "in_progress"}

    def _retrieve(self, batch_id):
        self.polls += 1
        if self.on_poll:
            self.on_poll(batch_id)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"id": batch_id, "processing_status": status}

    def _results(self, batch_id):
        requests = self.batches_created[-1] if self.batches_created else []
        if self.results_for is not None:
            return iter(self.results_for(requests))
        return iter({"custom_id": r["custom_id"], "result": {"type": "succeeded", "message": reply()}}
                    for r in requests)


def seams(client):
    return FakeRedactor(), types.SimpleNamespace(
        make_backend=lambda settings: A.make_backend(settings, client_factory=lambda timeout: client))


def run(argv, capsys, client, state):
    code = CLI.main(argv, memory_runner=lambda cli: ([], None), seams=seams(client), state_dir=state)
    out = capsys.readouterr()
    return code, json.loads(out.out) if "--json" in argv and code == 0 else out.out, out.err


def events(state) -> list:
    path = state / BR.BATCH_FILE
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(BR.time, "sleep", lambda s: None)


# --- selection and setup -------------------------------------------------------------------------

@pytest.mark.parametrize("profile,argv,backend,model", [
    ({}, [], "claude-cli", "sonnet"),])
def test_the_backend_comes_from_the_profile_or_the_flag(profile, argv, backend, model):
    settings = CLI.load_settings(CLI.build_parser().parse_args(["run", *argv]), {"distill": profile})
    assert (settings.backend, settings.model) == (backend, model)
    assert CLI.load_seams(backend)[1].__name__ == CLI.BACKENDS[backend][0]


@pytest.mark.parametrize("profile,argv", [({"backend": "openai"}, []),])
def test_a_bad_backend_or_batch_setting_is_a_usage_error(profile, argv):
    with pytest.raises(CLI.UsageError):
        CLI.load_settings(CLI.build_parser().parse_args(["run", *argv]), {"distill": profile})


@pytest.mark.parametrize("sdk,key,argv,needle", [
    (None, KEY, [], A.INSTALL),])
def test_without_the_sdk_the_key_or_a_full_model_id_the_api_run_refuses_in_one_line(
        capsys, monkeypatch, sdk, key, argv, needle):
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic") if sdk else None)
    monkeypatch.delenv(A.KEY_ENV, raising=False)
    if key:
        monkeypatch.setenv(A.KEY_ENV, key)
    code = CLI.main(["run", "--backend", "api", *argv], memory_runner=lambda cli: ([], None))
    err = capsys.readouterr().err
    assert code == CLI.EXIT_REFUSED and needle in err.splitlines()[0]


# --- one direct call -----------------------------------------------------------------------------

def test_a_request_has_no_tools_and_forces_the_schema_without_the_refused_keywords():
    params = A.make_backend({"effort": "low"}, client_factory=lambda t: FakeClient()).params("n")
    assert "tools" not in params and "tool_choice" not in params
    assert params["output_config"]["effort"] == "low"
    def keys(node):
        if isinstance(node, dict):
            yield from node
            for value in node.values():
                yield from keys(value)
        elif isinstance(node, list):
            for value in node:
                yield from keys(value)
    sent = set(keys(params["output_config"]["format"]["schema"]))
    assert "candidates" in sent and not sent & set(A.UNSUPPORTED_KEYWORDS)
    assert "maxLength" in set(keys(SC.OUTPUT_SCHEMA))      # the local schema keeps its limits


def test_the_cost_comes_from_api_usage_and_the_price_table(capsys, tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"asof": "2026-10-06", "models": {A.DEFAULT_MODEL: {
        "input_per_mtok": 4.0, "output_per_mtok": 20.0}}}))
    usage = {"input_tokens": 900, "cache_read_input_tokens": 100, "output_tokens": 100}
    client = FakeClient(answer=lambda params: reply(usage=usage))
    code, _, _ = run(["run", "--backend", "api", "--pricing", str(prices), "--json"], capsys,
                     client, tmp_path / "state")
    rows = L.read_rows(tmp_path / "state")
    assert code == 0 and client.calls and {r["status"] for r in rows} == {"done"}
    assert all(r["cost_estimate"] == pytest.approx((1000 * 4 + 100 * 20) / 1e6) for r in rows)


HUGE = '{"candidates": [], "pad": "' + "x" * (A.MAX_TEXT_BYTES + 1) + '"}'


# --- batch mode ----------------------------------------------------------------------------------


# --- the key -------------------------------------------------------------------------------------

