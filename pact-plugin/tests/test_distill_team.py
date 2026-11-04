"""Team audience: stage reviewed lessons in a clone of the team repository, then push only
after every outgoing commit is re-checked and the user confirms at their own terminal.

Location: pact-plugin/tests/test_distill_team.py. Real git throughout, against a local
bare repository in tmp_path (never a real remote); a remote that is not a local path is only set
as a URL, and its push goes to a fake sender. KB decided records are sealed with the gate's key in
a tmp plugin data dir. Names are synthetic (alice, Globex, Initech, example.com).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load_cli():
    loader = SourceFileLoader("_repact_distill_for_team", str(BIN / "repact-distill"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


CLI = _load_cli()
from _distill import promote as P  # noqa: E402  # bin/ is on sys.path once the CLI loaded
from _distill import team as T  # noqa: E402
from _kb import gate as G  # noqa: E402

ID_A, ID_B = "20261015T100000Z-0000000a", "20261015T100000Z-0000000b"
LESSON = {"title": "Upload API returns 502 under burst load",
          "claim": "The upload API returns a 502 under burst load and succeeds on one retry.",
          "why": "the gateway sheds load before the backend sees the request",
          "apply": "any client that uploads in bursts",
          "evidence": {"class": "observed", "how": "pointers: task:7@sess-1"},
          "ctx": "distill", "date": "2026-10-14"}
SECRET = "hunt" + "er2" + "Zq9vXw4Lm"           # a made-up password, assembled  # pragma: allowlist secret


class Tty(io.StringIO):
    def isatty(self):
        return True


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "data").mkdir()
    personal = tmp_path / "personal-denylist.txt"
    personal.write_text("Initech\n")
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST", str(personal))
    monkeypatch.delenv("REPACT_SCRUB_DENYLIST_WORDS", raising=False)
    (tmp_path / "xdg" / "repacted").mkdir(parents=True)
    team_list = tmp_path / "xdg" / "repacted" / "team-denylist.txt"
    team_list.write_text("# names that never reach the team repository\nGlobex\n")
    bare, clone = tmp_path / "team.git", tmp_path / "clone"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("clone", "-q", str(bare), str(clone))
    for key, value in (("user.name", "Alice"), ("user.email", "alice@example.com"),
                       ("commit.gpgsign", "false")):
        git("config", key, value, cwd=clone)
    (clone / "README.md").write_text("team lessons\n")
    git("add", "README.md", cwd=clone)
    git("commit", "-q", "-m", "Start the team repository.", cwd=clone)
    git("push", "-q", "-u", "origin", "HEAD:main", cwd=clone)
    return type("World", (), {"kb": tmp_path / "kb", "clone": clone, "bare": bare, "tmp": tmp_path,
                              "team_list": team_list, "profile": {}})


def record(world, item_id=ID_A, decision="accepted", by="alice", **lesson_over):
    body = {"id": item_id, "lesson": dict(LESSON, **lesson_over),
            "page": {"summary": "The upload API sheds burst load."},
            "proposer": {"id": "distill", "session": "s1", "agent": None}, "action": "new_topic",
            "target": "x.md", "decision": {"decision": decision, "by": by, "at": "2026-10-15T11:00:00Z"}}
    folder = G.decided_dir(world.kb) / "2026-10"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{item_id}.json"
    path.write_text(json.dumps(G.seal_record(body)))
    return path


def reseal(path, **lesson_over):
    body = json.loads(path.read_text())
    body.pop("seal")
    body["lesson"].update(lesson_over)
    path.write_text(json.dumps(G.seal_record(body)))


def terminal(monkeypatch, on=True):
    monkeypatch.setattr(sys, "stderr", Tty() if on else io.StringIO())


def stage(world, lines="", dry_run=False, **kw):
    out = io.StringIO()
    stdin = kw.pop("stdin", None) or Tty(lines)
    code = T.stage(world.kb, world.clone, world.profile, dry_run=dry_run, by="alice", stdin=stdin,
                   out=out, **kw)
    return code, out.getvalue()


def push(world, lines="", dry_run=False, **kw):
    out = io.StringIO()
    stdin = kw.pop("stdin", None) or Tty(lines)
    code = T.push(world.kb, world.clone, world.profile, dry_run=dry_run, by="alice", stdin=stdin,
                  out=out, **kw)
    return code, out.getvalue()


def head(world):
    return git("rev-parse", "HEAD", cwd=world.clone).strip()


def remote_files(world):
    return git("--git-dir", str(world.bare), "ls-tree", "-r", "--name-only", "main").split()


def staged(world, monkeypatch, *ids):
    """Stage and commit lessons through the real path (terminal on, each id retyped)."""
    terminal(monkeypatch)
    assert stage(world, lines="".join(f"{i}\n" for i in ids))[0] == 0


# --- acceptance: review, commit, push --------------------------------------------------------------

def test_dry_runs_need_no_terminal_and_write_and_send_nothing(world):
    record(world)
    code, out = stage(world, dry_run=True, stdin=io.StringIO())
    assert code == 0 and f"would write lessons/{ID_A}.md" in out and "kb-decision: accepted by alice" in out
    assert not (world.clone / "lessons").exists()
    code, out = push(world, dry_run=True, stdin=io.StringIO())
    assert code == 0 and "nothing to push" in out


def test_stage_commits_each_confirmed_lesson_and_push_sends_exactly_it(world, monkeypatch):
    record(world, ID_A)
    record(world, ID_B, claim="The upload API returns a 502 under burst load; retry with jitter.")
    terminal(monkeypatch)
    code, out = stage(world, lines=f"{ID_A}\n\n")
    assert code == 0 and f"{ID_B}: skipped" in out and "committed 1 lessons" in out
    assert git("status", "--porcelain", cwd=world.clone) == ""
    _, out = push(world, dry_run=True)
    assert "outgoing: 1 commit(s), 1 lesson file(s)" in out and "nothing was sent" in out
    assert f"remote: origin ({world.bare}) -> refs/heads/main" in out      # a local path as it is
    assert remote_files(world) == ["README.md"]
    code, out = push(world, lines=f"{head(world)[:12]}\n")
    assert code == 0 and "pushed" in out
    assert remote_files(world) == ["README.md", f"lessons/{ID_A}.md"]
    assert git("--git-dir", str(world.bare), "rev-parse", "main").strip() == head(world)
    assert git("rev-parse", "origin/main", cwd=world.clone).strip() == head(world)  # tracking moved


def test_a_wrong_retype_cancels_the_push(world, monkeypatch):
    record(world)
    staged(world, monkeypatch, ID_A)
    code, out = push(world, lines="yes\n")
    assert code == 0 and "push cancelled; nothing was sent" in out and remote_files(world) == ["README.md"]


# --- the boundary: unreviewed, edited after review, team-only names ---------------------------------

def test_unreviewed_items_are_never_staged_or_pushed(world, monkeypatch):
    record(world, ID_A, decision="auto", by=G.AUTO_DECIDER)
    record(world, ID_B, decision="rejected")
    _, out = stage(world, dry_run=True)
    assert "0 new" in out
    lessons = world.clone / "lessons"
    lessons.mkdir()
    (lessons / f"{ID_A}.md").write_text("---\nkb-gate-id: x\n---\nhand-written\n")
    git("add", "lessons", cwd=world.clone)
    git("commit", "-q", "-m", "Add a lesson.", cwd=world.clone)
    terminal(monkeypatch)
    with pytest.raises(T.Refused):
        push(world, lines=f"{head(world)[:12]}\n")
    assert remote_files(world) == ["README.md"]


def test_a_file_edited_after_review_refuses_the_push(world, monkeypatch):
    record(world)
    staged(world, monkeypatch, ID_A)
    path = world.clone / "lessons" / f"{ID_A}.md"
    path.write_text(path.read_text().replace("succeeds on one retry", "succeeds after sudo"))
    git("commit", "-q", "--amend", "--no-edit", "-a", cwd=world.clone)     # still one added file
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    assert f"{ID_A}: edited after review" in out.getvalue() and remote_files(world) == ["README.md"]


def test_a_record_edited_after_review_or_a_later_change_refuses_the_push(world, monkeypatch):
    path = record(world)
    staged(world, monkeypatch, ID_A)
    reseal(path, claim="The upload API returns a 502 under burst load and needs three retries.")
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    assert f"{ID_A}: edited after review" in out.getvalue()
    reseal(path, claim=LESSON["claim"])
    (world.clone / "lessons" / f"{ID_A}.md").write_text("changed\n")
    git("commit", "-q", "-am", "Change it.", cwd=world.clone)
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    assert f"changes lessons/{ID_A}.md (M)" in out.getvalue()


def test_a_name_only_the_team_list_holds_refuses_for_the_team_but_not_for_promote(world, monkeypatch):
    record(world, claim="The Globex upload API returns a 502 under burst load and then succeeds.")
    personal, _ = P.plan(world.kb, world.clone, (), "alice", None, "2026-10-15T12:00:00Z")
    assert personal[0].status == "new"                       # the personal lists do not know it
    _, out = stage(world, dry_run=True)
    assert f"{ID_A}: refused (denylist)" in out and "Globex" not in out.split("\n- ")[0]


def test_a_name_added_to_the_team_list_after_staging_refuses_the_push(world, monkeypatch):
    record(world, claim="The Umbrella upload API returns a 502 under burst load, then succeeds.")
    staged(world, monkeypatch, ID_A)
    world.team_list.write_text("Globex\nUmbrella\n")
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    assert f"{ID_A}: denylist" in out.getvalue()


@pytest.mark.parametrize("content", [None,])
def test_a_missing_or_empty_team_list_refuses_stage_and_push(world, content):
    record(world)
    if content is None:
        world.team_list.unlink()
    else:
        world.team_list.write_text(content)
    with pytest.raises(T.Refused) as caught:
        stage(world, dry_run=True)
    assert "team list" in caught.value.what and "team-denylist.txt" in caught.value.instead
    with pytest.raises(T.Refused):
        push(world, dry_run=True)


# --- the remote and the terminal proof -------------------------------------------------------------

CREDENTIAL_URLS = [f"https://bob:{SECRET}@example.com/team/kb.git",
                   f"deploy:{SECRET}@example.com:team/kb.git",
                   f"https://example.com/team/kb.git?private_token={SECRET}",
                   f"https:/deploy:{SECRET}@example.com/team/kb.git", ""]


# --- what was checked is what is sent (each reproduced red before its fix) -------------------------

def commit_on(world, name, text, message="Add a note."):
    (world.clone / name).write_text(text)
    git("add", name, cwd=world.clone)
    git("commit", "-q", "-m", message, cwd=world.clone)
    return head(world)


def real_push(world, **kw):
    return push(world, lines=f"{head(world)[:12]}\n", **kw)


CLONE_CONFIG = [("core.sshCommand", "ssh -o ProxyCommand=x"), ("core.askpass", "/tmp/x"),
                ("remote.origin.receivepack", "git-receive-pack"), ("remote.origin.uploadpack", "x"),
                ("remote.origin.vcs", "x"), ("remote.origin.mirror", "true"),
                ("remote.origin.proxy", "http://127.0.0.1:9"), ("push.pushOption", "Globex"),
                ("url./elsewhere.git.insteadOf", "/nowhere"), ("url./elsewhere.git.pushInsteadOf", "/no"),
                ("protocol.foo.allow", "always"), ("credential.helper", "!x"),
                ("http.sslVerify", "false"), ("include.path", "<include>")]


def rewrite_head(world, edit):
    raw = git("cat-file", "commit", "HEAD", cwd=world.clone)
    done = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin", "--literally"],
                          cwd=world.clone, input=edit(raw), capture_output=True, text=True, check=True)
    git("update-ref", "refs/heads/main", done.stdout.strip(), cwd=world.clone)


# --- the send uses what was checked (each reproduced red before its fix) --------------------------

class RacingTty(Tty):
    """A terminal whose retype read runs `change` first: config moved while the user confirms."""

    def __init__(self, text, change):
        super().__init__(text)
        self.change = change

    def readline(self, *args):
        self.change()
        return super().readline(*args)


