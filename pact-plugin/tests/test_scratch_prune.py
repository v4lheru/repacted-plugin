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


