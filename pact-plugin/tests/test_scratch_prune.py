"""bin/_scratch_prune.py (repact-scratch-prune) and its doctor_brief wiring: rebuildable build
output goes from stale Claude Code session temp folders; everything else stays, and nothing
outside the root is reached.

Every test builds its own root under tmp_path; nothing reads or writes the real temp root.
Staleness is tested by moving the module's clock forward (ctime cannot be set back).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MODULE = PLUGIN_ROOT / "bin" / "_scratch_prune.py"
CLI = PLUGIN_ROOT / "bin" / "repact-scratch-prune"
HOOK = PLUGIN_ROOT / "hooks" / "doctor_brief.py"
SID = "0f81448b-5d15-4895-b0a3-b6774ac3287b"
SID2 = "262bb221-f22a-4e85-9936-b46b9fa6f3d9"
DAY = 86400


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


sp = load("scratch_prune_under_test", MODULE)


def put(path: Path, text: str = "x" * 5000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    r = tmp_path / "claude-root"
    r.mkdir(mode=0o700)
    r.chmod(0o700)
    return r


def session(root: Path, name: str = SID, slug: str = "-Users-me-proj") -> Path:
    path = root / slug / name
    path.mkdir(parents=True)
    put(path / "scratchpad" / "notes.md", "keep me")
    return path


def run(root: Path, monkeypatch, apply=True, days_ahead=4, skip=()):
    monkeypatch.setattr(sp, "clock", lambda: __import__("time").time() + days_ahead * DAY)
    return sp.prune(root, apply=apply, days=sp.STALE_DAYS, skip=set(skip))


def tree(path: Path) -> set:
    return {str(p.relative_to(path)) for p in path.rglob("*")}


def test_rebuildable_output_goes_and_every_other_file_stays(root, monkeypatch):
    s = session(root)
    put(s / "tasks" / "b1.output", "log")
    put(s / "scratchpad" / "report.json", "{}")
    put(s / "app" / "package.json", "{}")
    put(s / "app" / "node_modules" / "lib" / "index.js")
    put(s / "app" / "dist" / "bundle.js")
    put(s / "app" / ".next" / "cache" / "c")
    put(s / "api" / "Api.csproj", "<Project/>")
    put(s / "api" / "bin" / "Debug" / "Api.dll")
    put(s / "api" / "obj" / "project.assets.json")
    put(s / "repo" / ".git" / "objects" / "ab" / "cd")
    put(s / "repo" / "src" / "main.py", "print()")
    put(s / "repo" / "src" / "__pycache__" / "main.cpython-312.pyc")
    put(s / "repo" / "pyproject.toml", "[project]")
    put(s / "repo" / "build" / "lib" / "m.py")
    put(s / "sdk" / "dotnet", "elf")
    put(s / "sdk" / "sdk" / "8.0" / "x.dll")
    put(s / "sdk" / "shared" / "y.dll")
    put(s / "py" / ".venv" / "pyvenv.cfg", "home=/usr")
    put(s / "py" / ".venv" / "lib" / "site.py")
    put(s / "results" / "build" / "summary.txt", "a report folder named build, no project file beside it")
    put(s / "results" / "bin" / "tool", "bin with no .NET project beside it")
    sessions, refused = run(root, monkeypatch)
    assert refused == "" and sessions[0].freed > 0 and sessions[0].errors == 0
    assert tree(s) == {
        "scratchpad", "scratchpad/notes.md", "scratchpad/report.json", "tasks", "tasks/b1.output",
        "app", "app/package.json", "api", "api/Api.csproj",
        "repo", "repo/.git", "repo/.git/objects", "repo/.git/objects/ab", "repo/.git/objects/ab/cd",
        "repo/src", "repo/src/main.py", "repo/pyproject.toml", "py",
        "results", "results/build", "results/build/summary.txt", "results/bin", "results/bin/tool",
    }


def test_a_target_holding_a_git_entry_at_any_depth_stays(root, tmp_path, monkeypatch):
    """Second review F3: a dist/ clone with an unpushed commit went with its .git. A .git of
    any kind (directory, worktree file, symlink, empty, deep, nested) keeps the whole target."""
    outside = tmp_path / "elsewhere"
    put(outside / "HEAD", "ref: refs/heads/main")
    s = session(root)
    put(s / "a" / "package.json", "{}")
    put(s / "a" / "dist" / ".git" / "objects" / "ab" / "cd")                # a clone
    put(s / "a" / "dist" / "index.html")
    put(s / "b" / "package.json", "{}")
    put(s / "b" / "dist" / ".git", "gitdir: /elsewhere/.git/worktrees/b")   # a worktree
    put(s / "c" / "node_modules" / "pkg" / "sub" / "deep" / "x.js")
    (s / "c" / "node_modules" / "pkg" / "sub" / ".git").symlink_to(outside)  # a symlinked .git
    (s / "d" / "node_modules" / "vendored" / ".git").mkdir(parents=True)   # empty, nested
    put(s / "d" / "node_modules" / "vendored" / "y.js")
    put(s / "e" / "x\n y" / "node_modules" / ".git" / "HEAD")               # odd parent name
    put(s / "f" / "node_modules" / "z.js")                                   # control: goes
    sessions, _ = run(root, monkeypatch)
    assert sorted(sessions[0].targets) == ["./f/node_modules"]
    for kept in ("a/dist/.git/objects/ab/cd", "a/dist/index.html", "b/dist/.git",
                 "c/node_modules/pkg/sub/deep/x.js", "d/node_modules/vendored/y.js",
                 "e/x\n y/node_modules/.git/HEAD"):
        assert os.path.lexists(s / kept), kept
    assert (s / "c" / "node_modules" / "pkg" / "sub" / ".git").is_symlink()
    assert not (s / "f" / "node_modules").exists() and (outside / "HEAD").exists()


def test_only_tool_output_goes_never_hand_written_files_or_tool_config(root, monkeypatch):
    """Second review F4: a hand-written build/ beside package.json, .cargo/config.toml beside
    its registry/, and user code in a GOPATH-shaped folder went too."""
    s = session(root)
    put(s / "vue" / "package.json", "{}")
    put(s / "vue" / "build" / "webpack.base.conf.js", "hand written")
    put(s / "vue" / "build" / "build.js", "hand written")
    put(s / "cra" / "package.json", "{}")
    put(s / "cra" / "build" / "index.html")
    put(s / "cra" / "build" / "static" / "js" / "main.js")
    put(s / "py" / "pyproject.toml", "[project]")
    put(s / "py" / "build" / "lib" / "m.py")
    put(s / "py" / "build" / "bdist.macosx-14-arm64" / "x")
    put(s / "empty" / "package.json", "{}")
    (s / "empty" / "build").mkdir()
    put(s / "rs" / ".cargo" / "config.toml", "[alias]")
    put(s / "rs" / ".cargo" / "credentials.toml", "token")
    put(s / "rs" / ".cargo" / "registry" / "cache" / "crate.crate")
    put(s / "rs" / ".cargo" / "git" / "db" / "x")
    put(s / "rs" / ".cargo" / "bin" / "cargo-tool")
    put(s / "nu" / ".nuget" / "NuGet" / "NuGet.Config", "<configuration/>")
    put(s / "nu" / ".nuget" / "packages" / "pkg" / "1.0" / "pkg.nupkg")
    put(s / "gopath" / "VERSION", "go1.22")
    put(s / "gopath" / "bin" / "go", "bin")
    put(s / "gopath" / "src" / "mine" / "main.go", "user code")
    (s / "gopath" / "pkg").mkdir()
    put(s / "goroot" / "VERSION", "go1.22")
    put(s / "goroot" / "bin" / "go", "bin")
    put(s / "goroot" / "src" / "runtime" / "proc.go")
    put(s / "goroot" / "pkg" / "tool" / "darwin_arm64" / "compile")
    sessions, _ = run(root, monkeypatch)
    assert sorted(sessions[0].targets) == [
        "./cra/build", "./goroot", "./nu/.nuget/packages", "./py/build",
        "./rs/.cargo/git", "./rs/.cargo/registry"]
    for kept in ("vue/build/webpack.base.conf.js", "vue/build/build.js", "rs/.cargo/config.toml",
                 "rs/.cargo/credentials.toml", "rs/.cargo/bin/cargo-tool",
                 "nu/.nuget/NuGet/NuGet.Config", "gopath/src/mine/main.go"):
        assert (s / kept).exists(), kept
    assert not (s / "rs" / ".cargo" / "registry").exists() and not (s / "goroot").exists()


def test_a_dry_run_deletes_nothing_and_reports_the_bytes(root, monkeypatch, capsys):
    s = session(root)
    put(s / "w" / "node_modules" / "a.js")
    before = tree(s)
    sessions, _ = run(root, monkeypatch, apply=False)
    assert tree(s) == before and sessions[0].size > 0 and sessions[0].freed == 0
    monkeypatch.setattr(sp, "clock", lambda: __import__("time").time() + 4 * DAY)
    assert sp.main(["--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "dry run: would free" in out and "node_modules" in out and tree(s) == before


def test_symlinks_inside_scratch_are_never_followed(root, tmp_path, monkeypatch):
    outside = tmp_path / "home"
    put(outside / "node_modules" / "precious.js")
    put(outside / "repo" / "package.json", "{}")
    put(outside / "repo" / "dist" / "precious.js")
    s = session(root)
    (s / "node_modules").symlink_to(outside / "node_modules")
    (s / "repo").symlink_to(outside / "repo")
    (s / "w").mkdir()
    (s / "w" / "package.json").symlink_to(outside / "repo" / "package.json")
    (s / "w" / "dist").symlink_to(outside / "repo" / "dist")
    (s / "loop").symlink_to(s)
    sessions, _ = run(root, monkeypatch)
    assert sessions[0].skipped == "" and sessions[0].targets == {}
    assert (outside / "node_modules" / "precious.js").exists()
    assert (outside / "repo" / "dist" / "precious.js").exists()
    assert (s / "node_modules").is_symlink() and (s / "repo").is_symlink()


def test_a_symlinked_root_slug_or_session_is_never_entered(root, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere"
    put(outside / SID / "node_modules" / "precious.js")
    (root / "-linked-slug").symlink_to(outside)
    (root / "-slug").mkdir()
    (root / "-slug" / SID2).symlink_to(outside / SID)
    sessions, _ = run(root, monkeypatch)
    assert sessions == []
    link_root = tmp_path / "root-link"
    link_root.symlink_to(root)
    assert run(link_root, monkeypatch) == ([], "not a real directory")
    assert (outside / SID / "node_modules" / "precious.js").exists()


def test_dot_dot_and_unicode_names(root, monkeypatch, capsys):
    s = session(root, slug="-Users-me-проект-漢字")
    for name in ("...", "..x", "ünï cödé\nline"):   # a real '..' entry cannot exist; remove() refuses the part
        put(s / name / "node_modules" / "a.js")
        put(s / name / "keep.txt", "keep")
    sessions, _ = run(root, monkeypatch)
    for name in ("...", "..x", "ünï cödé\nline"):
        assert not (s / name / "node_modules").exists() and (s / name / "keep.txt").exists()
    session_fd = os.open(s, os.O_RDONLY)
    try:
        assert sp.remove(session_fd, "./../" + SID, os.getuid()) == (1, False)
        assert sp.remove(session_fd, "./x/../..", os.getuid()) == (1, False)
    finally:
        os.close(session_fd)
    assert s.exists() and (s / "scratchpad" / "notes.md").exists()
    monkeypatch.setattr(sp, "clock", lambda: __import__("time").time() + 4 * DAY)
    put(s / "ünï cödé\nline" / "node_modules" / "b.js")
    assert sp.main(["--root", str(root)]) == 0
    assert "would free" in capsys.readouterr().out


def test_a_missing_shared_or_foreign_root_is_left_alone(root, tmp_path, monkeypatch, capsys):
    assert run(tmp_path / "nope", monkeypatch) == ([], "missing")
    assert sp.main(["--root", str(tmp_path / "nope")]) == 1
    assert "left alone: missing" in capsys.readouterr().out
    put(session(root) / "w" / "node_modules" / "a.js")
    root.chmod(0o777)
    assert run(root, monkeypatch) == ([], "writable by other users")
    root.chmod(0o700)
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    assert run(root, monkeypatch) == ([], "not owned by this user")
    monkeypatch.setattr(os, "getuid", lambda: real_uid)
    assert (root / "-Users-me-proj" / SID / "w" / "node_modules" / "a.js").exists()


def test_the_pre_3_11_delete_walk_never_follows_a_link_or_a_swapped_directory(tmp_path):
    """_rmtree_at is the delete before Python 3.11 (no rmtree dir_fd); it runs here on every
    interpreter. A symlink inside the target goes as a link, its target stays; a directory whose
    identity no longer matches the surveyed one is counted and left whole."""
    outside = put(tmp_path / "home" / "precious.js").parent
    target = tmp_path / "s" / "node_modules"
    put(target / "a" / "b" / "c.js")
    (target / "a" / "out").symlink_to(outside)
    (target / "link.js").symlink_to(outside / "precious.js")
    parent = os.open(tmp_path / "s", sp.DIR_FLAGS)
    try:
        failures = []
        sp._rmtree_at(parent, "node_modules", os.lstat(target), failures)
        assert failures == [] and not target.exists() and (outside / "precious.js").exists()
        put(target / "kept.js")
        failures = []
        sp._rmtree_at(parent, "node_modules", os.lstat(outside), failures)
        assert len(failures) == 1 and (target / "kept.js").exists()
    finally:
        os.close(parent)


def test_permission_errors_are_counted_never_raised(root, monkeypatch):
    s = session(root)
    put(s / "a" / "node_modules" / "locked" / "f.js")
    put(s / "a" / "node_modules" / "free.js")
    locked = s / "a" / "node_modules" / "locked"
    locked.chmod(0o500)                       # readable, but its file cannot be unlinked
    try:
        sessions, _ = run(root, monkeypatch)
        assert sessions[0].errors >= 1 and not (s / "a" / "node_modules" / "free.js").exists()
        assert (locked / "f.js").exists() and 0 < sessions[0].freed < sessions[0].size
    finally:
        locked.chmod(0o700)
    s2 = session(root, SID2)
    put(s2 / "b" / "node_modules" / "x.js")
    put(s2 / "b" / "secret" / "y")
    (s2 / "b" / "secret").chmod(0o000)        # its activity cannot be read: the session is skipped
    try:
        sessions, _ = run(root, monkeypatch)
        by_name = {x.name: x for x in sessions}
        assert by_name[SID2].skipped.startswith("unreadable") and (s2 / "b" / "node_modules" / "x.js").exists()
    finally:
        (s2 / "b" / "secret").chmod(0o700)


def test_a_current_running_or_recently_touched_session_is_left_alone(root, tmp_path, monkeypatch):
    current, running, touched, old = (session(root, n) for n in (
        SID, SID2, "7b6dab27-16de-4696-9601-6b79b980a79c", "86e5911e-649f-4e3b-86e6-9747fbe6c1f9"))
    for s in (current, running, touched, old):
        put(s / "w" / "node_modules" / "a.js")
    sessions_dir = tmp_path / "config" / "sessions"
    put(sessions_dir / "4242.json", json.dumps({"pid": 4242, "sessionId": SID2}))
    future = __import__("time").time() + 5 * DAY
    os.utime(put(touched / "scratchpad" / "fresh.md"), (future, future))
    sessions, _ = run(root, monkeypatch, skip=sp.running_sessions() | {SID})
    by_name = {s.name: s for s in sessions}
    assert by_name[SID].skipped == by_name[SID2].skipped == "current or running"
    assert by_name["7b6dab27-16de-4696-9601-6b79b980a79c"].skipped == "active"
    for s in (current, running, touched):
        assert (s / "w" / "node_modules" / "a.js").exists()
    assert not (old / "w" / "node_modules").exists()


def test_a_fresh_copy_with_old_mtimes_counts_as_active(root, monkeypatch):
    """cp -a, rsync -a and tar x set old mtimes on new files; their ctime is still new."""
    s = session(root)
    put(s / "copy" / "node_modules" / "a.js")
    long_ago = __import__("time").time() - 30 * DAY
    for path in [s, *s.rglob("*")]:
        os.utime(path, (long_ago, long_ago), follow_symlinks=False)
    sessions, _ = run(root, monkeypatch, days_ahead=0)
    assert sessions[0].skipped == "active" and (s / "copy" / "node_modules" / "a.js").exists()


def test_folders_that_are_not_claude_sessions_are_never_read(root, monkeypatch):
    for path in (root / "-Users-me-proj" / "notes", root / "project" / SID,
                 root / "-Users-me-proj" / SID.upper(), root / "-Users-me-proj" / (SID + "x")):
        put(path / "node_modules" / "a.js")
    put(root / "-Users-me-proj" / "loose-file.txt")
    put(root / "node_modules" / "a.js")
    sessions, _ = run(root, monkeypatch)
    assert sessions == [] and len([p for p in root.rglob("a.js")]) == 5


def test_auto_runs_once_a_day_and_reports_only_a_large_free(root, tmp_path, monkeypatch):
    state = tmp_path / "data"
    s = session(root)
    put(s / "w" / "node_modules" / "a.js")
    monkeypatch.setattr(sp, "clock", lambda: __import__("time").time() + 4 * DAY)
    assert sp.auto(state, root, set()) == 0
    result = json.loads((state / sp.STATE_NAME).read_text())
    assert result["freed"] > 0 and result["sessions"] == 1 and not (state / sp.REPORT_NAME).exists()
    put(s / "v" / "node_modules" / "b.js")
    assert sp.auto(state, root, set()) == 0               # within a day: nothing
    assert (s / "v" / "node_modules" / "b.js").exists()
    (state / sp.STATE_NAME).unlink()
    monkeypatch.setattr(sp, "REPORT_MIN", 1)
    assert sp.auto(state, root, set()) == 0
    assert json.loads((state / sp.REPORT_NAME).read_text())["sessions"] == 1


def test_the_hook_and_the_pruner_share_their_file_names():
    brief = load("doctor_brief_scratch_pin", HOOK)
    assert (brief.SCRATCH_STATE, brief.SCRATCH_REPORT) == (sp.STATE_NAME, sp.REPORT_NAME)
    assert brief.SCRATCH_REPORT_MIN == sp.REPORT_MIN and brief.SCRATCH_EVERY == sp.RUN_EVERY


def brief_env(tmp_path, **extra):
    (tmp_path / "data").mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "home" / ".claude"), "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "REPACT_DOCTOR_REPO": str(tmp_path / "no-repo"),
            "REPACT_DOCTOR_NO_REFRESH": "1", "REPACT_SCRATCH_ROOT": str(tmp_path / "scratch-root"), **extra}


def brief(env, **event):
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"source": "startup", **event}),
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_session_start_shows_a_large_free_once(tmp_path):
    env = brief_env(tmp_path)
    report = tmp_path / "data" / sp.REPORT_NAME
    report.write_text(json.dumps({"freed": 13 * 2**30, "sessions": 3}))
    assert brief(env)["systemMessage"] == (
        "scratch: freed 13.0 GB of build output from 3 stale session folder(s) (repact-scratch-prune)")
    assert brief(env) == {"suppressOutput": True}
    report.write_text(json.dumps({"freed": 2**29, "sessions": 3}))
    assert brief(env) == {"suppressOutput": True}
    report.write_text(json.dumps({"freed": 13 * 2**30, "sessions": 3}))
    assert brief(env, agent_type="repacted:pact-backend-coder") == {"suppressOutput": True}
    assert report.exists(), "a teammate's start leaves the report for the lead"


def test_session_start_runs_the_pruner_detached_once_a_day(tmp_path):
    import time
    env = brief_env(tmp_path, REPACT_DOCTOR_NO_REFRESH="")
    state = tmp_path / "data" / sp.STATE_NAME
    started = time.monotonic()
    brief(env, session_id=SID)
    assert time.monotonic() - started < 5, "the hook must not wait for the pruner"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (state.exists() and "refused" in state.read_text()):
        time.sleep(0.2)
    assert json.loads(state.read_text())["refused"] == "missing"   # it ran, on the test's own root
    before = state.stat().st_mtime_ns
    brief(env, session_id=SID)
    time.sleep(2)
    assert state.stat().st_mtime_ns == before
    state.unlink()
    brief({**env, "REPACT_SCRATCH_PRUNE_OFF": "1"})
    time.sleep(2)
    assert not state.exists()


def test_the_cli_is_a_dry_run_by_default(root, tmp_path):
    s = session(root)
    put(s / "w" / "node_modules" / "a.js")
    proc = subprocess.run([sys.executable, str(CLI), "--root", str(root), "--days", "1"],
                          env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "dry run" in proc.stdout
    assert (s / "w" / "node_modules" / "a.js").exists()
    bad = subprocess.run([sys.executable, str(CLI), "--days", "0"], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2
