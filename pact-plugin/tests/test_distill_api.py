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
    ({}, [], "claude-cli", "sonnet"),
    ({"backend": "api"}, [], "api", A.DEFAULT_MODEL),
    ({"backend": "claude-cli"}, ["--backend", "api"], "api", A.DEFAULT_MODEL),
    ({"backend": "api", "model": "claude-haiku-5-5"}, [], "api", "claude-haiku-5-5"),
])
def test_the_backend_comes_from_the_profile_or_the_flag(profile, argv, backend, model):
    settings = CLI.load_settings(CLI.build_parser().parse_args(["run", *argv]), {"distill": profile})
    assert (settings.backend, settings.model) == (backend, model)
    assert CLI.load_seams(backend)[1].__name__ == CLI.BACKENDS[backend][0]


@pytest.mark.parametrize("profile,argv", [({"backend": "openai"}, []), ({}, ["--batch"]),
                                          ({"backend": "api", "batch": "yes"}, [])])
def test_a_bad_backend_or_batch_setting_is_a_usage_error(profile, argv):
    with pytest.raises(CLI.UsageError):
        CLI.load_settings(CLI.build_parser().parse_args(["run", *argv]), {"distill": profile})


@pytest.mark.parametrize("sdk,key,argv,needle", [
    (None, KEY, [], A.INSTALL), ("fake", None, [], A.KEY_ENV),
    ("fake", KEY, ["--model", "sonnet"], "full model id")])
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


@pytest.mark.parametrize("message,reason", [
    (reply(blocks=[{"type": "tool_use", "name": "bash", "input": {}}], stop="tool_use"), "isolation"),
    (reply(blocks=[{"type": "text", "text": "{}"}, {"type": "server_tool_use", "name": "web"}]),
     "isolation"),
    (reply(blocks=[{"type": "text", "text": '{"candidates": []}'}] * 2), "exactly one text"),
    (reply(blocks=[{"type": "thinking", "thinking": ""}]), "exactly one text"),
    (reply(stop="max_tokens"), "stop reason"),
    (reply(stop="refusal"), "stop reason"),
    (reply("[]"), "output shape"),
    (reply('{"candidates": [], "extra": 1}'), "output shape"),
    (reply('{"candidates": {}}'), "output shape"),
    (reply("not json"), "not JSON"),
    (reply(HUGE), "too large"),
    ({"content": [{"type": "text", "text": '{"candidates": []}'}], "stop_reason": "end_turn"},
     "bad usage"),
    (reply(usage={"input_tokens": -1, "output_tokens": 1}), "bad usage"),
    (reply(usage={"input_tokens": float("nan"), "output_tokens": 1}), "bad usage"),
    (reply(usage={"input_tokens": True, "output_tokens": 1}), "bad usage"),
    (reply(usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": -5}),
     "bad usage"),
    ("a string", "no content"),
])
def test_a_reply_of_the_wrong_shape_or_with_a_tool_call_is_refused(message, reason):
    with pytest.raises(BackendError, match=reason):
        A.parse_message(message)


def test_unicode_and_thinking_blocks_parse_and_null_cache_counts_are_zero():
    text = json.dumps({"candidates": [candidate(title="Ünïcødé titel — ok 日本語")]}, ensure_ascii=False)
    usage = {"input_tokens": 5, "output_tokens": 7, "cache_creation_input_tokens": None}
    message = reply(blocks=[{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
                    usage=usage)
    candidates, tokens_in, tokens_out = A.parse_message(message)
    assert candidates[0]["title"].startswith("Ünïcødé") and (tokens_in, tokens_out) == (5, 7)


# --- batch mode ----------------------------------------------------------------------------------

def test_a_batch_is_recorded_before_the_first_poll_and_the_report_names_the_cap_rule(
        capsys, tmp_path):
    state, client, seen = tmp_path / "state", FakeClient(), []
    client.on_poll = lambda batch_id: seen.append(events(state))
    code, report, _ = run(["run", "--backend", "api", "--batch", "--json"], capsys, client, state)
    ids = [r["custom_id"] for r in client.batches_created[0]]
    assert code == 0 and len(client.batches_created) == 1 and not client.calls
    first = seen[0]
    assert [e["event"] for e in first] == ["submitted"] and first[0]["batch_id"] == "msgbatch_1"
    assert first[0]["custom_ids"] == ids
    assert BR.CAPS_NOTE in report["notes"] and [e["event"] for e in events(state)][-1] == "closed"
    assert {r["status"] for r in L.read_rows(state)} == {"done"}


def test_a_run_that_crashed_after_submit_collects_the_batch_instead_of_resubmitting(
        capsys, tmp_path):
    state, first = tmp_path / "state", FakeClient()
    first.on_poll = lambda batch_id: (_ for _ in ()).throw(Crash())
    with pytest.raises(Crash):
        run(["run", "--backend", "api", "--batch"], capsys, first, state)
    sent = {r["custom_id"]: r["params"]["messages"][0]["content"] for r in first.batches_created[0]}
    assert L.read_rows(state) == [] and events(state)[0]["custom_ids"] == list(sent)

    def cite(requests):   # the old batch's results, answered from the text that was sent
        for custom_id, text in sent.items():
            ref = next(iter(SC.chunk_refs(text)))
            body = json.dumps({"candidates": [candidate(
                evidence=[{"type": ref.split(":", 1)[0], "ref": ref, "ts": ""}])]})
            yield {"custom_id": custom_id, "result": {"type": "succeeded", "message": reply(body)}}

    second = FakeClient(results=cite)
    second.messages.batches.create = lambda **kw: pytest.fail("a recorded batch was resubmitted")
    code, report, _ = run(["run", "--backend", "api", "--batch", "--json"], capsys, second, state)
    rows = L.read_rows(state)
    assert code == 0 and {r["chunk_key"] for r in rows} == set(sent)
    assert {r["status"] for r in rows} == {"done"} and report["candidates"]["valid"] == len(sent)
    assert [e["event"] for e in events(state)] == ["submitted", "closed"]


def test_an_expired_or_errored_item_goes_back_to_pending_and_the_next_run_resends_it(
        capsys, tmp_path):
    state = tmp_path / "state"
    kinds = iter(["expired", "errored", "canceled"])
    first = FakeClient(results=lambda requests: [
        {"custom_id": r["custom_id"], "result": {"type": next(kinds)}} for r in requests])
    run(["run", "--backend", "api", "--batch"], capsys, first, state)
    keys = [r["custom_id"] for r in first.batches_created[0]]
    assert {r["status"] for r in L.read_rows(state)} == {"failed"} and not L.done_keys(state)
    second = FakeClient()
    run(["run", "--backend", "api", "--batch"], capsys, second, state)
    assert [r["custom_id"] for r in second.batches_created[0]] == keys
    assert L.done_keys(state) == set(keys)


def test_a_foreign_or_duplicate_custom_id_is_refused_and_a_missing_one_fails(
        capsys, tmp_path):
    def answer(requests):
        first = requests[0]["custom_id"]
        good = {"custom_id": first, "result": {"type": "succeeded", "message": reply()}}
        hostile = {"custom_id": "not-in-this-batch", "result": {"type": "succeeded",
                                                               "message": reply()}}
        return [good, dict(good, result={"type": "succeeded", "message": reply("[]")}), hostile]
    state, client = tmp_path / "state", FakeClient(results=answer)
    _, report, _ = run(["run", "--backend", "api", "--batch", "--json"], capsys, client, state)
    first = client.batches_created[0][0]["custom_id"]
    assert report["failures"]["batch: a duplicate result"] == 1
    assert report["failures"]["batch: a result for a chunk not in this batch"] == 1
    rows = {r["chunk_key"]: r["status"] for r in L.read_rows(state)}
    assert rows.pop(first) == "done" and set(rows.values()) <= {"failed"}
    assert "not-in-this-batch" not in rows


def test_a_batch_still_running_at_the_wait_limit_stays_open_for_the_next_run(
        capsys, tmp_path, monkeypatch):
    clock = iter(range(0, 10_000, 1000))
    monkeypatch.setattr(BR.time, "monotonic", lambda: next(clock))
    state, client = tmp_path / "state", FakeClient(statuses=("in_progress",))
    _, report, _ = run(["run", "--backend", "api", "--batch", "--json"], capsys, client, state)
    assert any("still processing" in note for note in report["notes"])
    assert BR.open_batches(state) and L.read_rows(state) == []


# --- the key -------------------------------------------------------------------------------------

def test_the_key_is_read_from_the_environment_and_never_written_or_printed(
        capsys, tmp_path, monkeypatch):
    built = []

    class Anthropic:                      # the SDK's message can quote what it was given
        def __init__(self, api_key, **kw):
            built.append(api_key)
            boom = lambda **k: (_ for _ in ()).throw(RuntimeError(f"401 for key {api_key}"))
            self.messages = types.SimpleNamespace(create=boom, batches=types.SimpleNamespace(
                create=boom, retrieve=boom, results=boom))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setenv(A.KEY_ENV, KEY)
    state, printed = tmp_path / "state", []
    for argv in (["run", "--backend", "api"], ["run", "--backend", "api", "--batch", "--json"]):
        code = CLI.main(argv, memory_runner=lambda cli: ([], None), state_dir=state,
                        seams=lambda name: (FakeRedactor(), CLI.load_seams(name)[1]))
        out = capsys.readouterr()
        printed += [out.out, out.err]
        assert code == 0
    assert built == [KEY, KEY] and L.read_rows(state)
    assert not any(KEY in text for text in printed)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files and not any(KEY.encode() in p.read_bytes() for p in files)
