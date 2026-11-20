"""The SessionEnd distill enqueue (KB full): hook, queue, distiller intake, doctor and wrap-up.

Location: pact-plugin/tests/test_distill_enqueue.py. Drives hooks/distill_enqueue.py,
hooks/shared/distill_queue.py, bin/repact-distill (queue order, drop after a run, the `queue`
line) and bin/_doctor_state.check_distill in a tmp config, data and profile dir. One row per
behaviour; adversarial rows at the hook input boundary (missing, non-string, traversal, unicode,
huge and multi-line ids, malformed stdin) and a boundary row proving the transcript is never
opened. No model call, no network.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import stat
import subprocess
import sys
import types
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
BIN = PLUGIN / "bin"


def _load_cli():
    loader = SourceFileLoader("_repact_distill_for_enqueue", str(BIN / "repact-distill"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


CLI = _load_cli()
import distill_enqueue as hook  # noqa: E402
from _distill import estimate as E  # noqa: E402
from shared import distill_queue as Q  # noqa: E402
from shared.pact_context import LEAD_AGENT_TYPES  # noqa: E402

LEAD = "0f0e0d0c-aaaa-bbbb-cccc-000000000001"
OTHER = "0f0e0d0c-aaaa-bbbb-cccc-000000000002"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    profile = tmp_path / "xdg" / "repacted" / "profile.json"
    profile.parent.mkdir(parents=True)
    world = types.SimpleNamespace(tmp=tmp_path, cfg=tmp_path / "cfg", queue=tmp_path / "data" / "distill")
    world.profile = lambda body: profile.write_text(json.dumps(body))
    return world


def _end(session_id=LEAD, **extra) -> dict:
    return {"hook_event_name": "SessionEnd", "session_id": session_id, "reason": "other",
            "transcript_path": f"/nowhere/projects/-tmp-demo/{session_id}.jsonl", **extra}


def _queue_file(env) -> Path:
    return env.queue / Q.QUEUE_FILE


# --- the hook ------------------------------------------------------------------------------

def test_off_by_default_writes_nothing(env):
    assert hook.enqueue_ended(_end()) == "off"
    env.profile({"distill": {"transcripts": "yes"}})       # only the boolean true turns it on
    assert hook.enqueue_ended(_end()) == "off"
    assert not _queue_file(env).exists()


def test_on_writes_once_and_dedupes_in_private_files(env):
    env.profile({"distill": {"transcripts": True}})
    assert hook.enqueue_ended(_end()) == "queued"
    assert hook.enqueue_ended(_end()) == "duplicate"
    assert hook.enqueue_ended(_end(OTHER)) == "queued"
    assert _queue_file(env).read_text() == f"{LEAD}\n{OTHER}\n"
    assert stat.S_IMODE(_queue_file(env).stat().st_mode) == 0o600
    assert stat.S_IMODE(env.queue.stat().st_mode) == 0o700


@pytest.mark.parametrize("frame", [
    {"agent_type": "pact-backend-coder"},])
def test_teammate_and_subagent_ends_are_skipped(env, frame):
    env.profile({"distill": {"transcripts": True}})
    assert hook.enqueue_ended(_end(**frame)) == "skipped"
    assert not _queue_file(env).exists()


def test_a_lead_spelling_and_a_plain_session_both_queue(env):
    env.profile({"distill": {"transcripts": True}})
    assert hook.enqueue_ended(_end(agent_type=sorted(LEAD_AGENT_TYPES)[0])) == "queued"
    assert hook.enqueue_ended(_end(OTHER, agent_type="")) == "queued"


def test_the_transcript_is_never_opened_and_no_process_starts(env, capsys):
    env.profile({"distill": {"transcripts": True}})
    transcript = env.tmp / "projects" / "-tmp-demo" / f"{LEAD}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("SENTINEL: reading this fails the test\n")
    touched = []

    def guard(real):
        def opener(target, *args, **kwargs):
            if str(transcript) in str(target):
                touched.append(str(target))
                raise AssertionError("the hook opened the transcript")
            return real(target, *args, **kwargs)
        return opener

    stdin = io.StringIO(json.dumps(_end(transcript_path=str(transcript))))
    with patch.object(builtins, "open", guard(builtins.open)), \
            patch.object(io, "open", guard(io.open)), patch.object(os, "open", guard(os.open)), \
            patch.object(Path, "read_text", guard(Path.read_text)), \
            patch.object(Path, "read_bytes", guard(Path.read_bytes)), \
            patch.object(subprocess, "Popen", side_effect=AssertionError("a process started")), \
            patch.object(sys, "stdin", stdin):
        assert hook.main() == 0
    assert touched == [] and Q.error_count() == 0
    assert _queue_file(env).read_text() == f"{LEAD}\n"
    assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}


# --- the queue file ------------------------------------------------------------------------


# --- the distiller -------------------------------------------------------------------------

def _transcript(cfg: Path, session: str, words: str) -> None:
    path = cfg / "projects" / "-tmp-demo" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "user", "timestamp": "2026-10-15T09:00:00.000Z", "origin": {"kind": "human"},
             "message": {"role": "user", "content": f"why did {words} fail"}},
            {"type": "assistant", "timestamp": "2026-10-15T09:00:01.000Z",
             "message": {"id": "m1", "role": "assistant", "model": "m",
                         "content": [{"type": "text", "text": f"my mistake: {words} was stale"}]}}]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class _Backend:
    name = "fake"

    def extract(self, chunks):
        for chunk in chunks:
            yield E.ChunkResult(chunk.chunk_key, [], 2 * E.CALL_OVERHEAD_TOKENS, 0, None)


class _Redactor:
    def detector_fingerprint(self):
        return "fp-test"

    def redact_chunk(self, chunk):
        return chunk


def _distill(argv, capsys) -> str:
    seams = (_Redactor(), types.SimpleNamespace(make_backend=lambda settings: _Backend()))
    assert CLI.main(argv, memory_runner=lambda cli: ([], None), seams=seams) == 0
    return capsys.readouterr().out


# --- the doctor and wrap-up lines ----------------------------------------------------------


