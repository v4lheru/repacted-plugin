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
import _doctor_state as DS  # noqa: E402  # bin/ is on sys.path once the CLI loaded
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
    {"agent_type": "pact-backend-coder"},                        # a separate-process teammate
    {"agent_type": "alice-coder"},                               # an in-process member name
    {"agent_type": 7},
    {"transcript_path": f"/x/projects/-tmp/{LEAD}/subagents/agent-abob-0123456789abcdef.jsonl"},
])
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


@pytest.mark.parametrize("session_id", [
    None, 123, ["a"], {"id": LEAD}, "", "../../etc/passwd", "a/b", ".hidden", "a\nb", "a b",
    "séssion", "ａｂｃ", "\ud800", "a" * (Q.MAX_ID_CHARS + 1), "a" * 100_000,
])
def test_a_hostile_session_id_writes_nothing(env, session_id):
    env.profile({"distill": {"transcripts": True}})
    frame = _end()
    frame["session_id"] = session_id
    assert hook.enqueue_ended(frame) == "invalid"
    frame.pop("session_id")
    assert hook.enqueue_ended(frame) == "invalid"
    assert not _queue_file(env).exists() and not list(env.tmp.rglob("passwd"))


@pytest.mark.parametrize("stdin", ["{not json", "[1, 2]", "", "\"a string\"", "null", "{\"session_id\":"])
def test_malformed_stdin_exits_zero_and_writes_nothing(env, capsys, stdin):
    env.profile({"distill": {"transcripts": True}})
    with patch.object(sys, "stdin", io.StringIO(stdin)):
        assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}
    assert not _queue_file(env).exists() and Q.error_count() == 0


def test_an_unwritable_queue_is_counted_and_never_raises(env):
    env.profile({"distill": {"transcripts": True}})
    env.queue.mkdir(parents=True)
    os.symlink(env.tmp / "elsewhere", env.queue / Q.QUEUE_FILE)     # O_NOFOLLOW refuses it
    assert hook.dispatch_check(_end()) is None
    assert hook.dispatch_check(_end()) is None
    assert Q.error_count() == 2
    assert not (env.tmp / "elsewhere").exists()


# --- the queue file ------------------------------------------------------------------------

def test_a_corrupt_queue_keeps_its_valid_ids_and_drops_the_rest(env):
    env.queue.mkdir(parents=True)
    _queue_file(env).write_bytes(f"{LEAD}\n../x\n\xff\xfe\n{LEAD}\nnot an id\n".encode("latin-1"))
    assert Q.enqueue(OTHER) is True
    assert _queue_file(env).read_text() == f"{LEAD}\n{OTHER}\n"


def test_an_oversize_queue_is_read_to_its_cap_and_rewritten_bounded(env):
    env.queue.mkdir(parents=True)
    ids = [f"s{n:05d}" for n in range(Q.MAX_IDS)]
    _queue_file(env).write_text("".join(f"{i}\n" for i in ids) + "x" * (Q.MAX_BYTES * 2))
    assert Q.queued() == ids
    assert Q.enqueue(LEAD) is True
    kept = Q.queued()
    assert len(kept) == Q.MAX_IDS and kept[0] == ids[1] and kept[-1] == LEAD   # the oldest left
    assert _queue_file(env).stat().st_size <= Q.MAX_BYTES


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


def test_queued_sessions_go_first_and_leave_the_queue_after_a_run(env, capsys):
    env.profile({"distill": {"enabled": True, "transcripts": True}})
    _transcript(env.cfg, LEAD, "the cache")
    _transcript(env.cfg, OTHER, "the fixture")
    for session in (LEAD, OTHER):
        (env.cfg / "pact-sessions" / "demo-project" / session).mkdir(parents=True)
    Q.enqueue(OTHER)
    Q.enqueue("0f0e0d0c-gone")                 # no transcript: nothing to send, so it leaves too
    settings = CLI.load_settings(CLI.build_parser().parse_args(["dry-run"]), CLI.common.profile())
    chunks = CLI.collect(settings, env.cfg, CLI.R.RunSummary(mode="dry-run"), lambda cli: ([], None))
    assert [c.session for c in chunks if c.source == "transcript"] == [OTHER, LEAD]
    _distill(["dry-run"], capsys)
    assert Q.queued() == [OTHER, "0f0e0d0c-gone"], "a dry-run changes nothing"
    _distill(["run", "--no-propose"], capsys)
    assert Q.queued() == []


def test_a_capped_run_keeps_the_unsent_session_queued(env, capsys):
    env.profile({"distill": {"enabled": True, "transcripts": True}})
    _transcript(env.cfg, LEAD, "the cache")
    Q.enqueue(LEAD)
    _distill(["run", "--no-propose", "--max-tokens", "1"], capsys)
    assert Q.queued() == [LEAD]


# --- the doctor and wrap-up lines ----------------------------------------------------------

def test_the_doctor_line_reports_the_switch_the_count_and_swallowed_errors(env):
    result = DS.check_distill(None)
    assert (result.state, result.summary) == ("green", "transcript distill off; 0 ended session(s) queued")
    env.profile({"distill": {"transcripts": True}})
    Q.enqueue(LEAD)
    result = DS.check_distill(None)
    assert result.state == "green" and result.summary.startswith("transcript distill on; 1 ended")
    Q.note_error("TimeoutError")
    result = DS.check_distill(None)
    assert result.state == "amber" and "1 enqueue error(s)" in result.summary


def test_the_queue_line_and_the_wrap_up_step_that_relays_it(env, capsys):
    env.profile({"distill": {"transcripts": True}})
    Q.enqueue(LEAD)
    Q.enqueue(OTHER)
    line = _distill(["queue"], capsys).strip()
    assert line.startswith("2 ended session(s) queued for distilling, transcript distill on")
    assert line.endswith('bin/repact-distill" run') and "\n" not in line
    wrap_up = (PLUGIN / "commands" / "wrap-up.md").read_text(encoding="utf-8")
    step = wrap_up[wrap_up.index("## 7b."):wrap_up.index("## 8.")]
    assert "repact-distill queue" in step
    reference = (PLUGIN / "reference" / "wrap-up" / "kb-queue.md").read_text(encoding="utf-8")
    assert 'bin/repact-distill" queue' in reference.split("## 6.")[1]
