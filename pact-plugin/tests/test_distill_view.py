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

@pytest.fixture
def no_interpreter_cap():
    """Turn off the interpreter's own int-digit cap (3.11+, some 3.9/3.10 builds), as on a build
    without it: the view's cap must hold on its own."""
    previous = getattr(sys, "get_int_max_str_digits", lambda: None)()
    if previous is not None:
        sys.set_int_max_str_digits(0)
    yield
    if previous is not None:
        sys.set_int_max_str_digits(previous)


def test_bad_lines_are_skipped_and_counted_and_reading_goes_on(tmp_path, monkeypatch,
                                                               no_interpreter_cap):
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


def _number_line(literal: bytes, mark: str) -> bytes:
    return b'{"type": "user", "n": ' + literal + b', "message": {"role": "user", "content": "' \
        + mark.encode() + b'"}}'


def test_integer_digit_cap_is_the_views_own_not_the_interpreters(tmp_path, no_interpreter_cap):
    cap = V.INT_MAX_DIGITS
    deep = b'{"a": ' * 50 + b"7" * (cap + 1) + b"}" * 50
    view = view_of(
        tmp_path,
        _number_line(b"9" * cap, "AT-CAP"),
        _number_line(b"-" + b"9" * cap, "NEG-AT-CAP"),
        _number_line(b"9" * (cap + 1), "OVER-CAP"),
        _number_line(b"-" + b"9" * (cap + 1), "NEG-OVER-CAP"),
        _number_line(b"0" * 1, "ZERO"),
        _number_line(deep, "DEEP-OVER-CAP"),
        _number_line(b"[1, 2, {\"k\": [" + b"8" * (cap + 1) + b"]}]", "LIST-OVER-CAP"),
    )
    assert [t.text for t in view.turns] == ["AT-CAP", "NEG-AT-CAP", "ZERO"]
    assert view.counts == {"malformed line": 4}


def test_floats_that_overflow_to_infinity_are_refused_like_infinity(tmp_path, no_interpreter_cap):
    view = view_of(
        tmp_path,
        _number_line(b"1e999", "EXP-INF"),
        _number_line(b"-1e999", "NEG-EXP-INF"),
        _number_line(b"9" * 5000 + b".0", "MANTISSA-INF"),
        _number_line(b"0." + b"9" * 5000, "LONG-FRACTION"),      # finite: kept
        _number_line(b"1e-999", "UNDERFLOW"),                    # 0.0, finite: kept
        _number_line(b"1.5e308", "LARGE-FINITE"),
    )
    assert [t.text for t in view.turns] == ["LONG-FRACTION", "UNDERFLOW", "LARGE-FINITE"]
    assert view.counts == {"malformed line": 3}


# --- segments and pre-score ----------------------------------------------------------------


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


