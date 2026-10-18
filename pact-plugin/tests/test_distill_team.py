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


@pytest.mark.parametrize("content", [None, "", "# only a comment\n"])
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


def test_only_added_lesson_files_and_clean_messages_may_leave(world, monkeypatch):
    record(world)
    staged(world, monkeypatch, ID_A)
    (world.clone / "notes.txt").write_text("internal\n")
    git("add", "notes.txt", cwd=world.clone)
    git("commit", "-q", "-m", f"Notes, the staging password={SECRET}", cwd=world.clone)
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    text = out.getvalue()
    assert "adds notes.txt (A)" in text and "message (" in text and SECRET not in text


# --- the remote and the terminal proof -------------------------------------------------------------

CREDENTIAL_URLS = [f"https://bob:{SECRET}@example.com/team/kb.git",
                   f"deploy:{SECRET}@example.com:team/kb.git",
                   f"https://example.com/team/kb.git?private_token={SECRET}",
                   f"https:/deploy:{SECRET}@example.com/team/kb.git", ""]


@pytest.mark.parametrize("url", CREDENTIAL_URLS)
def test_carries_credential(url):
    assert T.carries_credential(url)


@pytest.mark.parametrize("url", ["git@example.com:team/kb.git", "ssh://git@example.com:2222/kb.git",
                                 "https://example.com/team/kb.git", "/srv/git/kb.git",
                                 "file:///srv/git/kb.git"])
def test_plain_urls_carry_no_credential(url):
    assert not T.carries_credential(url)


@pytest.mark.parametrize("where", ["url", "pushurl"])
def test_a_remote_url_with_a_credential_refuses_even_with_i_reviewed(world, monkeypatch, where):
    record(world)
    staged(world, monkeypatch, ID_A)
    if where == "url":
        git("remote", "set-url", "origin", CREDENTIAL_URLS[0], cwd=world.clone)
    else:
        git("config", "remote.origin.pushurl", CREDENTIAL_URLS[1], cwd=world.clone)
    sent = []
    with pytest.raises(T.Refused) as caught:
        push(world, lines=f"{head(world)[:12]}\n", reviewed="alice", send=lambda *a: sent.append(a))
    assert "credential" in caught.value.what and SECRET not in T.render_refusal(caught.value)
    assert sent == [] and remote_files(world) == ["README.md"]


def test_a_remote_that_is_not_a_local_path_needs_i_reviewed(world, monkeypatch):
    record(world)
    staged(world, monkeypatch, ID_A)
    git("remote", "set-url", "origin", "git@example.com:team/kb.git", cwd=world.clone)
    sent = []
    send = lambda root, remote, refspec: sent.append((remote, refspec)) or 0  # noqa: E731
    with pytest.raises(T.Refused) as caught:
        push(world, lines=f"{head(world)[:12]}\n", send=send)
    assert "--i-reviewed" in caught.value.instead and sent == []
    base = git("rev-parse", "origin/main", cwd=world.clone).strip()
    code, out = push(world, lines=f"{head(world)[:12]}\n", reviewed="alice", send=send,
                     tip=lambda root, url, dest: base)
    assert code == 0 and sent == [("git@example.com:team/kb.git", f"{head(world)}:refs/heads/main")]


@pytest.mark.parametrize("stdin_tty, stderr_tty", [(False, True), (True, False)])
def test_a_push_without_the_terminal_proof_is_refused(world, monkeypatch, stdin_tty, stderr_tty):
    record(world)
    staged(world, monkeypatch, ID_A)
    terminal(monkeypatch, on=stderr_tty)
    stdin = Tty(f"{head(world)[:12]}\n") if stdin_tty else io.StringIO(f"{head(world)[:12]}\n")
    with pytest.raises(T.Refused) as caught:
        push(world, stdin=stdin)
    assert "interactive terminal" in caught.value.what and "--dry-run" in caught.value.instead
    assert remote_files(world) == ["README.md"]


def test_a_stage_without_the_terminal_proof_writes_nothing(world, monkeypatch):
    record(world)
    terminal(monkeypatch, on=False)
    with pytest.raises(T.Refused):
        stage(world, lines=f"{ID_A}\n")
    assert not (world.clone / "lessons").exists()


def test_the_cli_refuses_with_no_team_repository_and_exits_3(world, capsys):
    record(world)
    args = CLI.build_parser().parse_args(["team", "push", "--dry-run"])
    assert T.cli(args, lambda: world.kb, profile={}) == 3
    assert "repact-distill team: refused: no team repository" in capsys.readouterr().err


# --- what was checked is what is sent (each reproduced red before its fix) -------------------------

def commit_on(world, name, text, message="Add a note."):
    (world.clone / name).write_text(text)
    git("add", name, cwd=world.clone)
    git("commit", "-q", "-m", message, cwd=world.clone)
    return head(world)


def real_push(world, **kw):
    return push(world, lines=f"{head(world)[:12]}\n", **kw)


def test_a_moved_tracking_ref_cannot_hide_a_commit_from_the_check(world, monkeypatch):
    """F1: base came from the local refs/remotes ref, which an agent can move over its own commit."""
    record(world)
    hidden = commit_on(world, "notes.txt", "internal\n")
    staged(world, monkeypatch, ID_A)
    git("update-ref", "refs/remotes/origin/main", hidden, cwd=world.clone)
    with pytest.raises(T.Refused) as caught:
        real_push(world)
    assert "remote branch" in caught.value.what and remote_files(world) == ["README.md"]
    code, out = push(world, dry_run=True)
    assert code == 0 and "remote tip: not checked" in out


def test_a_replace_ref_cannot_show_the_check_another_commit(world, monkeypatch):
    """F2: with replace refs on, the reads saw the good commit while push sent the bad one."""
    record(world)
    staged(world, monkeypatch, ID_A)
    good = head(world)
    (world.clone / "notes.txt").write_text("internal\n")
    git("add", "notes.txt", cwd=world.clone)
    tree = git("write-tree", cwd=world.clone).strip()
    bad = git("commit-tree", tree, "-p", f"{good}~1", "-m", "Add 1 reviewed lesson.", cwd=world.clone).strip()
    git("replace", bad, good, cwd=world.clone)
    git("update-ref", "refs/heads/main", bad, cwd=world.clone)
    with pytest.raises(T.Refused):
        real_push(world)
    assert remote_files(world) == ["README.md"]


@pytest.mark.parametrize("where", ["hooks-dir", "hooks-path"])
def test_no_clone_hook_runs_during_the_push(world, monkeypatch, where):
    """F3: a pre-push or reference-transaction hook the agent planted ran in the user's push."""
    record(world)
    staged(world, monkeypatch, ID_A)
    marker = world.tmp / "hook-ran"
    hooks = world.clone / ".git" / "hooks" if where == "hooks-dir" else world.tmp / "planted"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-push", "reference-transaction"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        (hooks / name).chmod(0o755)
    if where == "hooks-path":
        git("config", "core.hooksPath", str(hooks), cwd=world.clone)
    code, out = real_push(world)
    assert code == 0 and "pushed" in out and not marker.exists()


CLONE_CONFIG = [("core.sshCommand", "ssh -o ProxyCommand=x"), ("core.askpass", "/tmp/x"),
                ("remote.origin.receivepack", "git-receive-pack"), ("remote.origin.uploadpack", "x"),
                ("remote.origin.vcs", "x"), ("remote.origin.mirror", "true"),
                ("remote.origin.proxy", "http://127.0.0.1:9"), ("push.pushOption", "Globex"),
                ("url./elsewhere.git.insteadOf", "/nowhere"), ("url./elsewhere.git.pushInsteadOf", "/no"),
                ("protocol.foo.allow", "always"), ("credential.helper", "!x"),
                ("http.sslVerify", "false"), ("include.path", "<include>")]


@pytest.mark.parametrize("key, value", CLONE_CONFIG, ids=[k for k, _ in CLONE_CONFIG])
def test_clone_config_that_runs_code_or_moves_the_push_is_refused(world, monkeypatch, key, value):
    """F3: config in the clone (or a file it includes) can run code or change where the push goes."""
    record(world)
    staged(world, monkeypatch, ID_A)
    if value == "<include>":
        included = world.tmp / "included.cfg"
        included.write_text("[core]\n\tsshCommand = ssh -o ProxyCommand=x\n")
        value = str(included)
    git("config", key, value, cwd=world.clone)
    sent = []
    with pytest.raises(T.Refused):
        real_push(world, send=lambda *a: sent.append(a) or 0)
    assert sent == []


@pytest.mark.parametrize("url", ["ext::sh -c x", "foo::bar", "git://example.com/kb.git",
                                 "http://example.com/kb.git"])
def test_a_remote_transport_outside_file_ssh_and_https_is_refused(world, monkeypatch, url):
    record(world)
    staged(world, monkeypatch, ID_A)
    git("config", "remote.origin.pushurl", url, cwd=world.clone)
    sent = []
    with pytest.raises(T.Refused):
        real_push(world, reviewed="alice", send=lambda *a: sent.append(a) or 0)
    assert sent == []


def rewrite_head(world, edit):
    raw = git("cat-file", "commit", "HEAD", cwd=world.clone)
    done = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin", "--literally"],
                          cwd=world.clone, input=edit(raw), capture_output=True, text=True, check=True)
    git("update-ref", "refs/heads/main", done.stdout.strip(), cwd=world.clone)


@pytest.mark.parametrize("edit", [
    lambda raw: raw.replace("\n\n", "\nx-note Globex shipped it\n\n", 1),
    lambda raw: raw.replace("author Alice <", "author Globex <", 1),
    lambda raw: raw.replace("committer Alice <", f"committer password={SECRET} <", 1),
    # Regression: a second tree header, after committer or before author, went unscanned.
    lambda raw: raw.replace("\n\n", f"\ntree Globex password={SECRET}\n\n", 1),
    lambda raw: raw.replace("\nauthor ", f"\ntree Globex password={SECRET}\nauthor ", 1),
], ids=["extra-header", "author-name", "committer-secret", "tree-after-committer",
        "tree-before-author"])
def test_every_commit_header_is_checked(world, monkeypatch, edit):
    """F4: only the message was scanned; the agent controls the idents and can add headers."""
    record(world)
    staged(world, monkeypatch, ID_A)
    rewrite_head(world, edit)
    out = io.StringIO()
    with pytest.raises(T.Refused):
        T.push(world.kb, world.clone, world.profile, dry_run=True, stdin=io.StringIO(), out=out)
    assert "Globex" not in out.getvalue() and SECRET not in out.getvalue()


# --- the send uses what was checked (each reproduced red before its fix) --------------------------

class RacingTty(Tty):
    """A terminal whose retype read runs `change` first: config moved while the user confirms."""

    def __init__(self, text, change):
        super().__init__(text)
        self.change = change

    def readline(self, *args):
        self.change()
        return super().readline(*args)


@pytest.mark.parametrize("what", ["push-url", "receivepack"])
def test_a_remote_or_config_changed_during_the_retype_refuses_the_push(world, monkeypatch, what):
    """N2: the check read the URLs and config before the retype, the push re-read them after."""
    record(world)
    staged(world, monkeypatch, ID_A)
    evil, marker = world.tmp / "evil.git", world.tmp / "receivepack-ran"
    git("init", "-q", "--bare", "-b", "main", str(evil))
    git("--git-dir", str(evil), "fetch", "-q", str(world.bare), "main:main")    # same tip as team
    script = world.tmp / "receivepack.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\nexec git-receive-pack \"$@\"\n")
    script.chmod(0o755)
    change = {"push-url": ("remote", "set-url", "--push", "origin", str(evil)),
              "receivepack": ("config", "remote.origin.receivepack", str(script))}[what]
    stdin = RacingTty(f"{head(world)[:12]}\n", lambda: git(*change, cwd=world.clone))
    with pytest.raises(T.Refused) as caught:
        push(world, stdin=stdin)
    assert "changed" in caught.value.what and not marker.exists()
    assert remote_files(world) == ["README.md"]
    assert git("--git-dir", str(evil), "rev-parse", "main").strip() != head(world)


def test_no_clone_fsmonitor_program_runs_and_one_refuses_the_push(world, monkeypatch):
    """N3: a clone core.fsmonitor program ran during the push's diff-tree read."""
    record(world)
    staged(world, monkeypatch, ID_A)
    marker, program = world.tmp / "fsmonitor-ran", world.tmp / "fsmonitor.sh"
    program.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    program.chmod(0o755)
    git("config", "core.fsmonitor", str(program), cwd=world.clone)
    with pytest.raises(T.Refused):
        real_push(world)
    assert not marker.exists() and remote_files(world) == ["README.md"]
