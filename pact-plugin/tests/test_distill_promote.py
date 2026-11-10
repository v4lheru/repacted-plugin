"""Per-repo promote: 100% human review at a terminal, the shared-remote rule, the origin
mark, idempotence, the dry run, and the boundary cases (a lesson that steers an agent, traversal,
symlinked folders, remote URL spellings, unicode, huge input).

Location: pact-plugin/tests/test_distill_promote.py. The KB decided records are sealed
with the gate's own key in a tmp plugin data dir; git is faked except in the one real-git row.
Names are synthetic (alice, bob, example.com).
"""
from __future__ import annotations

import io
import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load_cli():
    loader = SourceFileLoader("_repact_distill_for_promote", str(BIN / "repact-distill"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


CLI = _load_cli()
from _distill import promote as P  # noqa: E402  # bin/ is on sys.path once the CLI loaded
from _kb import gate as G  # noqa: E402

ID_A, ID_B, ID_C = "20261015T100000Z-0000000a", "20261015T100000Z-0000000b", "20261015T100000Z-0000000c"
LESSON = {"title": "Upload API returns 502 under burst load",
          "claim": "The upload API returns a 502 under burst load and succeeds on one retry.",
          "why": "the gateway sheds load before the backend sees the request",
          "apply": "any client that uploads in bursts",
          "evidence": {"class": "observed", "how": "pointers: task:7@sess-1, memory:m1"},
          "ctx": "distill", "date": "2026-10-14"}


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "data").mkdir()
    kb, repo = tmp_path / "kb", tmp_path / "repo"
    repo.mkdir()
    return type("World", (), {"kb": kb, "repo": repo, "remotes": {}, "tmp": tmp_path})


def record(world, item_id=ID_A, decision="accepted", by="alice", **lesson_over):
    lesson = dict(LESSON, **lesson_over)
    body = {"id": item_id, "lesson": lesson, "page": {"summary": "The upload API sheds burst load."},
            "proposer": {"id": "distill", "session": "s1", "agent": None}, "action": "new_topic",
            "target": "x.md", "decision": {"decision": decision, "by": by, "at": "2026-10-15T11:00:00Z"}}
    folder = G.decided_dir(world.kb) / "2026-10"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{item_id}.json"
    path.write_text(json.dumps(G.seal_record(body)))
    return path


def fake_git(world):
    def git(root, *args):
        if args[0] == "rev-parse":
            return f"{world.repo}\n"
        if args == ("remote",):
            return "".join(f"{name}\n" for name in world.remotes)
        return "".join(f"{url}\n" for url in world.remotes[args[-1]])
    return git


def promote(world, *, dry_run=False, lines="", **kw):
    out, err = io.StringIO(), io.StringIO()
    stdin = kw.pop("stdin", None) or Tty(lines)
    code = P.run(world.kb, world.repo, dry_run=dry_run, stdin=stdin, out=out, err=err,
                 git=fake_git(world), by=kw.pop("by", "alice"), **kw)
    return code, out.getvalue(), err.getvalue()


def landed(world):
    folder = world.repo / ".repacted" / "kb"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- acceptance: review, dry run, idempotence, marking -------------------------------------------

def test_dry_run_shows_path_and_exact_text_and_writes_nothing(world):
    record(world)
    code, out, _ = promote(world, dry_run=True, stdin=io.StringIO())   # no terminal needed
    assert code == 0 and f"would write .repacted/kb/{ID_A}.md" in out
    assert "origin: model-written, human-reviewed" in out and "kb-decision: accepted by alice" in out
    assert not (world.repo / ".repacted").exists()


def test_real_run_without_a_terminal_is_refused_and_names_the_command(world):
    record(world)
    with pytest.raises(P.Refused) as caught:
        promote(world, stdin=io.StringIO(f"{ID_A}\n"))
    assert "interactive terminal" in caught.value.what and "promote --repo" in caught.value.instead
    assert landed(world) == []


def test_each_item_needs_its_id_retyped_and_a_rerun_promotes_nothing_twice(world):
    record(world, ID_A)
    record(world, ID_B, claim="The upload API returns a 502 under burst load; retry once with jitter.")
    code, out, _ = promote(world, lines=f"{ID_A}\n\n")          # A confirmed, B skipped
    assert code == 0 and landed(world) == [f"{ID_A}.md"] and f"{ID_B}: skipped" in out
    text = (world.repo / ".repacted" / "kb" / f"{ID_A}.md").read_text()
    assert text.startswith("---\norigin: model-written, human-reviewed\n")
    assert f"kb-gate-id: {ID_A}" in text and "promoted-by: alice" in text
    assert "task:7@sess-1" not in text                          # private evidence pointers dropped
    record(world, ID_C)                                         # the same lesson under a new id
    _, out, err = promote(world, lines=f"{ID_B}\n{ID_C}\n")
    assert f"{ID_A}: promoted" in out and f"{ID_C}: promoted" in out
    assert landed(world) == [f"{ID_A}.md", f"{ID_B}.md"] and ID_C not in err


def test_a_lesson_appended_to_an_existing_page_has_no_summary_and_still_promotes(world):
    path = record(world)
    body = json.loads(path.read_text())
    body["page"]["summary"] = ""                                # a real shape
    path.write_text(json.dumps(G.seal_record({k: v for k, v in body.items() if k != "seal"})))
    _, out, _ = promote(world, dry_run=True)
    assert "1 new" in out


def test_only_sealed_human_decisions_are_eligible(world):
    record(world, ID_A, decision="auto", by=G.AUTO_DECIDER)
    record(world, ID_B, decision="rejected")
    tampered = record(world, ID_C)
    tampered.write_text(tampered.read_text().replace("burst load", "any load"))
    _, out, _ = promote(world, dry_run=True)
    assert "0 new" in out and "1 decided files skipped" in out


def test_an_existing_file_with_other_content_is_never_overwritten(world):
    record(world)
    folder = world.repo / ".repacted" / "kb"
    folder.mkdir(parents=True)
    (folder / f"{ID_A}.md").write_text("a teammate's edit\n")
    _, out, _ = promote(world, lines=f"{ID_A}\n")
    assert f"{ID_A}: differs" in out and (folder / f"{ID_A}.md").read_text() == "a teammate's edit\n"


# --- the shared-remote rule ----------------------------------------------------------------------

SHARED = ["https://bob:hunter2-not-a-secret@example.com/team/app.git", "http://example.com/app",  # pragma: allowlist secret
          "ssh://git@example.com:2222/team/app.git", "git@example.com:team/app.git",
          "git://example.com/app", "ext::sh -c true", "bob@exämple.com:team/äpp.git", ""]
LOCAL = ["/srv/git/app.git", "../app.git", "./mirror", "file:///srv/git/app.git", "C/app"]


@pytest.mark.parametrize("url", SHARED)
def test_a_remote_that_is_not_a_local_path_needs_i_reviewed(world, url):
    assert not P.is_local_url(url)
    record(world)
    world.remotes = {"origin": [url]}
    with pytest.raises(P.Refused) as caught:
        promote(world, lines=f"{ID_A}\n")
    assert "--i-reviewed" in caught.value.instead and "hunter2" not in caught.value.render()
    assert landed(world) == []


@pytest.mark.parametrize("url", LOCAL)
def test_local_path_remotes_do_not_need_i_reviewed(world, url):
    assert P.is_local_url(url)
    record(world)
    world.remotes = {"backup": [url]}
    assert promote(world, lines=f"{ID_A}\n")[0] == 0 and landed(world) == [f"{ID_A}.md"]


TOKEN = "Q7xW3zP9" + "rT2mV5nB"          # a password-shaped value, fake  # pragma: allowlist secret


# --- boundary: hostile lessons, names and folders ------------------------------------------------


# --- credentials in a lesson, forged record headers, odd remote URLs ---------------------------

# A made-up Slack incoming-webhook URL, assembled so no source line holds one.
SLACK_HOOK = ("https://hooks.slack" + ".com/services/T" + "Q4ZK7WMR" + "/B" + "8NXD2PLH" + "/"
              + "Vq3mR8tLx2" + "Kp9Wz4Nc7Yb5Hd")


def reseal(path, **decision_over):
    """Rewrite a decided record with other decision values and a valid seal: what a process
    running as the user can do with the key."""
    body = json.loads(path.read_text())
    body.pop("seal")
    body["decision"].update(decision_over)
    path.write_text(json.dumps(G.seal_record(body)))


