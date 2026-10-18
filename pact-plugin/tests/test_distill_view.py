"""KB full raw view: the R0 transcript view, segmenting, pre-score and the transcript source.

Location: pact-plugin/tests/test_distill_view.py. Drives bin/_distill/view.py,
segment.py and the transcript kind in discover.py and bin/repact-distill against synthetic
transcripts in a tmp config dir (alice, example.com). Adversarial at the boundary: malformed,
huge and non-finite lines, lone surrogates, unknown and future types, tool results dressed as
text, nested content arrays, a secret in a prompt. Secret-shaped values are assembled at run time.
No model call, no network.
"""
from __future__ import annotations

import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load_cli():
    loader = SourceFileLoader("_repact_distill_for_view", str(BIN / "repact-distill"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


CLI = _load_cli()
from _distill import discover as D  # noqa: E402  # bin/ is on sys.path once the CLI loaded
from _distill import redact as R  # noqa: E402
from _distill import segment as G  # noqa: E402
from _distill import sources as S  # noqa: E402
from _distill import view as V  # noqa: E402
from shared import scrub  # noqa: E402

SESSION = "0f0e0d0c-aaaa-bbbb-cccc-000000000001"
TS = "2026-10-15T09:00:00.000Z"


def user(content, **extra) -> dict:
    return {"type": "user", "timestamp": TS, "message": {"role": "user", "content": content}, **extra}


def assistant(*blocks, mid="m1", **extra) -> dict:
    return {"type": "assistant", "timestamp": TS,
            "message": {"id": mid, "role": "assistant", "model": "m", "content": list(blocks)}, **extra}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def write(path: Path, *lines) -> Path:
    """Each line a dict (dumped) or raw bytes (written as they are)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        for line in lines:
            handle.write(line if isinstance(line, bytes) else json.dumps(line).encode("utf-8"))
            handle.write(b"\n")
    return path


def view_of(tmp_path, *lines) -> V.View:
    return V.read_view(write(tmp_path / "t.jsonl", *lines))


def joined(view: V.View) -> str:
    return "\n".join(turn.text for turn in view.turns)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.delenv(scrub.ENV_DENYLIST, raising=False)
    monkeypatch.delenv(scrub.ENV_DENYLIST_WORDS, raising=False)
    return root


# --- R0: what enters the view --------------------------------------------------------------

def test_r0_keeps_the_humans_words_and_the_prose_and_nothing_else(tmp_path):
    view = view_of(
        tmp_path,
        user("retry the upload once\n<system-reminder>REMINDER-MARK</system-reminder>",
             origin={"kind": "human"}),
        assistant({"type": "thinking", "thinking": "THINK-MARK"},
                  {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "TOOL-IN"}},
                  text("the root cause was the clock")),
        user([{"type": "tool_result", "tool_use_id": "t1", "content": "TOOL-OUT"}],
             toolUseResult={"stdout": "TOOL-OUT"}),
        user("META-MARK", isMeta=True),
        user("SUMMARY-MARK", isCompactSummary=True),
        user("NOTIFY-MARK", origin={"kind": "task-notification"}),
        user('<teammate-message teammate_id="bob">PEER-MARK</teammate-message>\nBOILER-MARK'),
        user('<cross-session-message from="worker">XSESS-MARK</cross-session-message>'),  # a cross-session message is dropped
        user("<command-name>/x</command-name><command-args>ARGS-MARK</command-args>"),
        user([text("look at this"), {"type": "image", "source": {"data": "IMG-MARK"}}]),
        assistant(text("SYNTH-MARK"), **{"isApiErrorMessage": True}),
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "ATTACH-MARK"}},
        {"type": "system", "content": "SYSTEM-MARK"},
    )
    assert [(t.role, t.text) for t in view.turns] == [
        ("user", "retry the upload once"), ("assistant", "the root cause was the clock")]
    assert not view.counts, "every entry here is named by R0: nothing unknown"
    assert view.excluded["user harness delivery"] == 3 and view.excluded["block image"] == 1


def test_unknown_and_future_types_are_counted_and_never_included(tmp_path):
    view = view_of(
        tmp_path,
        {"type": "hologram", "message": {"role": "user", "content": "FUTURE-MARK"}},
        {"type": "Weird Type!\u202e", "message": {"content": "ODD-MARK"}},
        {"message": {"role": "user", "content": "NOTYPE-MARK"}},
        user([text("ok"), {"type": "voice_note", "text": "VOICE-MARK"}]),
        assistant(text("kept prose"), {"type": "citation_card", "text": "CARD-MARK"}),
    )
    assert joined(view) == "kept prose"
    assert view.counts == {"unknown entry type hologram": 1, "unknown entry type other": 2,
                           "unknown block type voice_note": 1, "unknown block type citation_card": 1}


# --- reading: fails closed, line by line ---------------------------------------------------

def test_bad_lines_are_skipped_and_counted_and_reading_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "LINE_MAX_BYTES", 300_000)
    big = json.dumps(user("HUGE-MARK " + "x" * 400_000, origin={"kind": "human"})).encode()
    view = view_of(
        tmp_path,
        b"{not json",
        b'{"type": "user", "n": NaN, "message": {"role": "user", "content": "NAN-MARK"}}',
        b'{"type": "user", "n": -Infinity, "message": {"role": "user", "content": "INF-MARK"}}',
        b'{"type": "user", "n": ' + b"9" * 5000 + b', "message": {"role": "user", "content": "BIG"}}',
        b"[" * 100_000 + b"]" * 100_000,
        b'{"type": "user", "message": {"role": "user", "content": "\xff\xfe BAD-UTF8"}}',
        b'["a list, not an entry"]',
        big,
        user("still read after all that"),
    )
    assert joined(view) == "still read after all that"
    assert view.counts == {"malformed line": 7, "line over the size cap": 1}


def test_a_lone_surrogate_drops_the_text_and_is_counted(tmp_path):
    view = view_of(tmp_path, b'{"type": "user", "message": {"role": "user", "content": "a \\udcff b"}}',
                   assistant(text("prose \ud800 here"), mid="m2"), assistant(text("clean prose"), mid="m3"))
    assert joined(view) == "clean prose" and view.counts == {"text is not valid unicode": 2}


def test_tool_results_dressed_as_text_never_enter(tmp_path):
    view = view_of(
        tmp_path,
        user([text("DISGUISE-1")], toolUseResult=""),                  # present, however empty
        user("DISGUISE-2", sourceToolUseID="t9"),
        user([{"type": "text", "text": "DISGUISE-3", "tool_use_id": "t1"}]),
        user([text("DISGUISE-4"), {"type": "tool_result", "tool_use_id": "t1", "content": "x"}]),
        assistant({"type": "text", "text": "DISGUISE-5", "content": [text("y")]}),
    )
    assert view.turns == [] and view.counts == {"tool result disguised as text": 2}
    assert "DISGUISE" not in repr(view.counts)


def test_nested_content_arrays_are_malformed_not_text(tmp_path):
    view = view_of(tmp_path, user([[text("NEST-1")]]), user([{"type": "text", "text": ["NEST-2"]}]),
                   assistant({"type": "text", "text": {"deep": [text("NEST-3")]}}),
                   user({"type": "text", "text": "NEST-4"}))
    assert view.turns == [] and view.counts == {"malformed content block": 4}


def test_a_secret_in_a_prompt_is_redacted_or_the_chunk_skipped(cfg, tmp_path):
    key = "sk-" + "ant-" + "api03-" + "Xq7Lm2Pz9Rt4Vw8Nb3Hj6Kd1Fg5"
    lead = write(cfg / "projects" / "-tmp-demo" / f"{SESSION}.jsonl",
                 user(f"the deploy failed, use {key} for now", origin={"kind": "human"}),
                 assistant(text("fixed by rotating the key")))
    source = D.Source(D.KIND_TRANSCRIPT, lead, "demo", SESSION, 0.0, lead.stat().st_size)
    segments, _ = G.read_transcript(source)
    [chunk] = S.build_chunks(segments, 100_000)
    assert key in chunk.text, "positive control: the view itself does not redact"
    R._SKIPS.clear()
    out = R.redact_chunk(chunk)
    assert out is None or (key not in out.text and R.MARKER in out.text)


# --- segments and pre-score ----------------------------------------------------------------

def test_turns_split_on_each_prompt_and_score_their_cues(tmp_path):
    view = view_of(tmp_path, assistant(text("hello"), mid="m0"),
                   user("add the retry"), assistant(text("added it"), mid="m1"),
                   user("no, that's not what I asked"),
                   assistant(text("the root cause was a stale cache; fixed by clearing it"), mid="m2"))
    turns = G.split_turns(view.turns)
    assert [[t.role for t in seg] for seg in turns] == [["assistant"], ["user", "assistant"],
                                                         ["user", "assistant"]]
    scores = [G.pre_score(seg) for seg in turns]
    assert scores[0] == scores[1] == 0 and scores[2] >= G.CORRECTION_POINTS + 2 * G.LESSON_POINTS


def test_transcript_chunks_run_best_score_first_and_lite_keys_do_not_move():
    lite = [S.Segment("memory_inbox", "memory:a/MEMORY-inbox.md#1", "2026-10-11", "", "a",
                      "2026-10-11 | retry once | x"),
            S.Segment("compact_summary", "session:s1/compact-summary.txt", "2026-10-15T00:00:00Z",
                      "demo", "s1", "bob seeded the clock"),
            S.Segment("handoff", "task:7@s1", "2026-10-14T10:00:00+00:00", "demo", "s1",
                      "decisions: use a queue"),
            S.Segment("memory_inbox", "memory:b/MEMORY-inbox.md#2", "2026-10-13", "", "b",
                      "2026-10-13 | quote paths | y")]
    keys = [c.chunk_key[:16] for c in S.build_chunks(lite, 100_000)]
    assert keys == ["43abb336c5ce0530", "43cf2faba83943d8", "90c888e46114df4a", "fe3f400a1fece218"], \
        "keys and order measured before the score field was added"
    raw = [S.Segment(D.KIND_TRANSCRIPT, f"session:{sid}/r0-1#t1", ts, "demo", sid, "x", score)
           for sid, ts, score in (("old", "2026-10-01", 9), ("new", "2026-10-16", 1))]
    order = [c.session for c in S.build_chunks(lite + raw, 100_000)]
    assert order[-2:] == ["old", "new"], "transcripts after lite, the better score first"


# --- the source kind: off by default, leads only -------------------------------------------

def _world(cfg):
    lead = write(cfg / "projects" / "-tmp-demo" / f"{SESSION}.jsonl",
                 user("why did you skip the test", origin={"kind": "human"}),
                 assistant(text("my mistake: the fixture was stale")),
                 {"type": "hologram"})
    write(cfg / "projects" / "-tmp-demo" / SESSION / "subagents" / "agent-abob-0123456789abcdef.jsonl",
          user("TEAMMATE-MARK"))
    write(cfg / "projects" / "-tmp-other" / "0f0e0d0c-aaaa-bbbb-cccc-000000000002.jsonl", user("x"))
    write(cfg / "projects" / "-tmp-repact-distill-ab12" / "s9.jsonl", user("FEEDBACK-MARK"))
    (cfg / "pact-sessions" / "demo-project" / SESSION).mkdir(parents=True)
    return lead


def test_discovery_reads_no_transcript_unless_asked_and_then_leads_only(cfg):
    _world(cfg)
    assert not [s for s in D.discover(cfg).sources if s.kind == D.KIND_TRANSCRIPT]
    found = D.discover(cfg, include=["demo-*"], transcripts=True)
    assert [(s.project, s.session) for s in found.sources if s.kind == D.KIND_TRANSCRIPT] == \
        [("demo-project", SESSION)]
    assert found.skipped["transcript: teammate and subagent transcripts not read (R0)"] == 1
    assert found.skipped["transcript: project unknown"] == 1
    assert found.skipped["feedback: a distiller backend session"] == 1


def test_dry_run_reports_counts_only_and_refuses_a_non_bool_switch(cfg, tmp_path, capsys):
    _world(cfg)
    profile = tmp_path / "xdg" / "repacted" / "profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"distill": {"transcripts": True}}))
    code = CLI.main(["dry-run", "--json"], memory_runner=lambda cli: ([], None))
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == 0 and report["segments"]["transcript"] == 2   # demo and the unmapped one
    assert report["skipped"]["transcript: unknown entry type hologram"] == 1
    assert "mistake" not in out and "TEAMMATE-MARK" not in out and "FEEDBACK-MARK" not in out
    profile.write_text(json.dumps({"distill": {"transcripts": "yes"}}))
    assert CLI.main(["dry-run"], memory_runner=lambda cli: ([], None)) == CLI.EXIT_USAGE
