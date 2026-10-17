"""KB lite distiller core (U5.2): dry-run estimate, ledger resume, caps, and the refusal seam.

Location: pact-plugin/tests/test_distill_core.py. Drives bin/repact-distill in-process
against a synthetic config dir (alice/bob, example.com); no model call, no network.
"""
from __future__ import annotations

import json
import sys
import types
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"
PROFILE_BASE = {"distill": {"enabled": True, "model": "sonnet"}}


def _load_cli():
    loader = SourceFileLoader("_repact_distill_under_test", str(BIN / "repact-distill"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module   # dataclasses resolve their module by name
    loader.exec_module(module)
    return module


CLI = _load_cli()
from _distill import estimate as E  # noqa: E402  # bin/ is on sys.path once the CLI loaded
from _distill import ledger as L  # noqa: E402
from _distill import sources as S  # noqa: E402

MEMORY_ROWS = [{"id": "m1", "lessons_learned": ["alice: pin the CLI version in CI"],
                "decisions": [{"decision": "keep the queue file append-only"}],
                "project_id": "demo-project", "session_id": "s-mem", "created_at": "2026-10-12"},
               {"id": "m2", "withheld": "memory m2 - holds credentials"}]


def _fake_memory(cli):
    return MEMORY_ROWS, None


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg, xdg, data = tmp_path / "cfg", tmp_path / "xdg", tmp_path / "data"
    inbox = cfg / "agent-memory" / "alice-coder" / "MEMORY-inbox.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("# Inbox\n2026-10-11 | retry the upload once on a 502 | notes.md\n"
                     "2026-10-13 | quote paths with spaces | run.log\nnot a lesson\n")
    session = cfg / "pact-sessions" / "demo-project" / "sess-1"
    session.mkdir(parents=True)
    event = {"type": "agent_handoff", "ts": "2026-10-14T10:00:00+00:00", "agent": "bob",
             "task_id": "7", "task_subject": "build", "handoff": {
                 "decisions": ["use a queue"], "uncertainty": ["known-limit: one writer"],
                 "produced": ["PRODUCED-MARKER"]}}
    (session / "session-journal.jsonl").write_text(json.dumps(event) + "\n")
    (session / "compact-summary.txt").write_text("bob fixed the flaky test by seeding the clock")
    (session / "compact-summary.head.txt").write_text("HEAD-MARKER")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    world = types.SimpleNamespace(tmp=tmp_path, xdg=xdg, data=data / "distill")
    world.profile = lambda body: _write_json(xdg / "repacted" / "profile.json", body)
    world.profile(PROFILE_BASE)
    return world


def _write_json(path: Path, body: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return path


def _prices(tmp: Path, sonnet_in, asof="2026-10-01") -> Path:
    return _write_json(tmp / "prices.json", {"asof": asof, "currency": "USD", "output_ratio": 0.1,
                                             "models": {"sonnet": {"input_per_mtok": sonnet_in,
                                                                   "output_per_mtok": 5}}})


class FakeRedactor:
    def detector_fingerprint(self):
        return "fp-test"

    def redact_chunk(self, chunk):
        return chunk


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.sent = []

    def extract(self, chunks):
        for chunk in chunks:
            self.sent.append(chunk.chunk_key)
            yield E.ChunkResult(chunk.chunk_key, [{"kind": "gotcha"}], 2 * E.CALL_OVERHEAD_TOKENS,
                                0, None)


def _seams(backend):
    return FakeRedactor(), types.SimpleNamespace(make_backend=lambda settings: backend)


def _run(argv, capsys, **kw):
    code = CLI.main(argv, memory_runner=_fake_memory, **kw)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_dry_run_prints_sources_tokens_chunks_and_cost_from_the_price_table(world, capsys):
    prices = _prices(world.tmp, 1)
    code, out, _ = _run(["dry-run", "--pricing", str(prices), "--json"], capsys,
                        seams=lambda: pytest.fail("a dry run must not load the seams"))
    report = json.loads(out)
    assert code == 0 and report["status"] == "ok"
    assert report["segments"] == {"memory_inbox": 2, "handoff": 1, "pact_memory": 1,
                                  "compact_summary": 1}
    assert report["skipped"] == {"pact_memory: record withheld by the CLI": 1}
    assert report["chunks"] == 4 and report["projects"] == {"demo-project": 3}
    est = report["estimate"]
    assert est["tokens_in"] > 4 * E.CALL_OVERHEAD_TOKENS and est["price_asof"] == "2026-10-01"
    assert est["cost"] == pytest.approx((est["tokens_in"] * 1 + est["tokens_out"] * 5) / 1e6)
    assert not world.data.exists(), "a dry run writes nothing"
    _, text, _ = _run(["dry-run", "--pricing", str(prices)], capsys)
    assert "prices as of 2026-10-01" in text and "PRODUCED-MARKER" not in text
    chunks = CLI.collect(CLI.load_settings(CLI.build_parser().parse_args(["dry-run"]), {}),
                         world.tmp / "cfg", CLI.R.RunSummary("dry-run"), _fake_memory)
    texts = "".join(chunk.text for chunk in chunks)
    assert "PRODUCED-MARKER" not in texts and "HEAD-MARKER" not in texts


def test_a_null_price_means_cost_not_computed(world, capsys):
    code, out, _ = _run(["dry-run", "--pricing", str(_prices(world.tmp, None))], capsys)
    assert code == 0 and "cost: not computed (models.sonnet.input_per_mtok is null" in out
    template = BIN.parent / "templates" / "distill-pricing.example.json"
    table = E.load_price_table(template)
    assert table.asof is None and E.why_no_cost("sonnet", table)


def test_ledger_resume_skips_done_chunks(world, capsys):
    first = FakeBackend()
    code, out, _ = _run(["run", "--json"], capsys, seams=_seams(first))
    assert code == 0 and json.loads(out)["statuses"] == {"done": 4}
    assert len(L.done_keys(world.data)) == 4
    second = FakeBackend()
    code, out, _ = _run(["run", "--json"], capsys, seams=_seams(second))
    assert code == 0 and second.sent == [] and json.loads(out)["chunks_done_before"] == 4
    # the fake candidate is not schema-valid (U5.4): counted as invalid, never spooled
    spooled = (world.data / "runs").rglob("candidates.jsonl")
    assert sum(len(p.read_text().splitlines()) for p in spooled) == 0


def test_a_cap_stops_the_run_with_capped_and_the_rest_resume(world, capsys):
    backend = FakeBackend()
    one_chunk = 3 * E.CALL_OVERHEAD_TOKENS     # the first send uses 2/3 of it
    code, out, _ = _run(["run", "--max-tokens", str(one_chunk), "--json"], capsys,
                        seams=_seams(backend))
    report = json.loads(out)
    assert code == 0 and report["status"] == "capped" and report["cap_hit"] == "tokens"
    assert len(backend.sent) == 1 and report["chunks_capped"] == 3
    statuses = [row["status"] for row in L.read_rows(world.data)]
    assert statuses == ["done", "capped"]
    resumed = FakeBackend()
    _run(["run"], capsys, seams=_seams(resumed))
    assert len(resumed.sent) == 3 and backend.sent[0] not in resumed.sent


def test_a_usd_cap_without_prices_stops_before_sending(world, capsys):
    backend = FakeBackend()
    code, out, _ = _run(["run", "--max-usd", "5", "--json"], capsys, seams=_seams(backend))
    assert code == 0 and json.loads(out)["cap_hit"] == "usd" and backend.sent == []


def test_a_real_run_without_redaction_and_backend_refuses(world, capsys, monkeypatch):
    real_import = CLI.importlib.import_module

    def without_backend(name, *args):     # backend.py ships since U5.4: simulate its absence
        if name == "_distill.backend":
            raise ImportError(name)
        return real_import(name, *args)
    monkeypatch.setattr(CLI.importlib, "import_module", without_backend)
    code, out, err = _run(["run"], capsys)
    assert code == CLI.EXIT_REFUSED and out == ""
    assert "model backend" in err and "is not installed" in err and "dry-run" in err
    assert not world.data.exists()
    world.profile({"distill": {"enabled": False}})
    code, _, err = _run(["run"], capsys, seams=_seams(FakeBackend()))
    assert code == CLI.EXIT_REFUSED and "distill.enabled" in err


def test_a_failing_memory_cli_skips_the_source_without_opening_the_store(tmp_path):
    fake_cli = tmp_path / "cli.py"
    fake_cli.write_text("import sys\nprint('not json')\nsys.exit(1)\n")
    rows, reason = S.run_memory_cli(fake_cli)
    assert rows == [] and reason == "pact_memory: the CLI printed no JSON (exit 1)"


def test_the_estimate_applies_the_schema_input_multiplier_measured_once(world, capsys):
    assert E.SCHEMA_INPUT_MULTIPLIER == round(97_576 / 50_694, 3)
    reports = {}
    for label, flags in (("one", ["--schema-input-multiplier", "1"]), ("default", []),
                         ("two", ["--schema-input-multiplier", "2"])):
        code, out, _ = _run(["dry-run", "--json", *flags], capsys)
        assert code == 0, label
        reports[label] = json.loads(out)
    base = reports["one"]["estimate"]["tokens_in"]
    assert reports["default"]["input_multiplier"] == E.SCHEMA_INPUT_MULTIPLIER
    assert base * 1.9 < reports["default"]["estimate"]["tokens_in"] <= base * 1.925 + 4
    assert reports["two"]["estimate"]["tokens_in"] == 2 * base
    world.profile(dict(PROFILE_BASE, distill={**PROFILE_BASE["distill"], "schema_input_multiplier": 2}))
    assert json.loads(_run(["dry-run", "--json"], capsys)[1])["estimate"]["tokens_in"] == 2 * base


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_a_bad_schema_input_multiplier_is_a_usage_error(world, capsys, value):
    code, _, err = _run(["dry-run", "--schema-input-multiplier", value], capsys)
    assert code == 2 and "schema_input_multiplier must be a number above 0" in err
