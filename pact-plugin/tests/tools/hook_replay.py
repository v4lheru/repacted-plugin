"""
Location: pact-plugin/tests/tools/hook_replay.py
Summary: Replays one recorded hook decision (tests/fixtures/shell/decisions/hooks/) through
         the real hook entry: the dispatcher's in-process run of that one check
         (dispatch.run_check), as `python3 hooks/dispatch.py <event>` runs it. A row holds
         the payload, the env the test set, the process cwd and the id of the file world
         the test had built (worlds.jsonl); placeholders stand in for machine paths and for
         secret-shaped values:
           @@root<n>@@   the n-th world directory, rebuilt fresh under a scratch dir per row
           @@plugin@@    this plugin's root
           @@fake<n>@@   tests/tools/hook_fakes.FAKES[n], a fake credential built from parts
                         in code, so no table line is secret-shaped for the commit check
         outcome() turns the check's exit code and output into the recorded shape:
         {"v": deny|ask|allow|none|block|context|notice|exit<n>, "rule": <[rule: id]>,
          "why": <reason head when no rule id>, "upd": <updatedInput>,
          "out": <updatedToolOutput>}. The check runs with sys.stdout and sys.stderr on
         fds 1 and 2 (real_streams), as in a hook process: under pytest they are capture
         objects that bypass the fds, and the check's output would be lost (an ask, a
         context or a rewrite would read as none, and no rule id would be seen).
Used by: tests/test_hook_decision_tables.py (replay), tests/tools/hook_capture.py (record).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
TABLES = Path(os.environ.get("HOOK_TABLES_DIR")
              or PLUGIN / "tests" / "fixtures" / "shell" / "decisions" / "hooks")
WORLDS = TABLES / "worlds.jsonl"
MARK = re.compile(r"@@(root\d+|plugin|fake\d+)@@")
RULE_RE = re.compile(r"\[rule: ([^\]\s]+)\]")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def fakes() -> dict:
    sys.path.insert(0, str(PLUGIN / "tests" / "tools"))
    try:
        from hook_fakes import FAKES
    finally:
        sys.path.remove(str(PLUGIN / "tests" / "tools"))
    return FAKES


def expand(value, places: dict):
    """Every placeholder in value (str, list or dict, recursively) spelled out."""
    if isinstance(value, str):
        return MARK.sub(lambda m: places[m.group(1)], value)
    if isinstance(value, list):
        return [expand(v, places) for v in value]
    if isinstance(value, dict):
        return {expand(k, places): expand(v, places) for k, v in value.items()}
    return value


@contextlib.contextmanager
def real_streams():
    """sys.stdout and sys.stderr written to fds 1 and 2 while a check runs, as in a hook
    process, so dispatch.run_check's fd capture sees what the check printed."""
    saved = sys.stdout, sys.stderr
    out = open(1, "w", encoding="utf-8", closefd=False)
    err = open(2, "w", encoding="utf-8", errors="backslashreplace", closefd=False)
    sys.stdout, sys.stderr = out, err
    try:
        yield
    finally:
        for stream in (out, err):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass
        sys.stdout, sys.stderr = saved


def verdict_of(exit_code, stdout: bytes, stderr: bytes, head: int | None = 90) -> dict:
    text = stdout.decode("utf-8", "replace").strip()
    err = stderr.decode("utf-8", "replace")
    obj = None
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
    v, reason, upd, rewritten = "none", "", None, None
    if isinstance(obj, dict):
        hso = obj.get("hookSpecificOutput") or {}
        if isinstance(hso, dict):
            if hso.get("permissionDecision"):
                v = hso["permissionDecision"]
                reason = str(hso.get("permissionDecisionReason") or "")
            if "updatedInput" in hso:
                upd = hso["updatedInput"]
            if "updatedToolOutput" in hso:
                rewritten = hso["updatedToolOutput"]
            if hso.get("additionalContext") and v == "none":
                v, reason = "context", str(hso["additionalContext"])
        if obj.get("decision") == "block":
            v, reason = "block", str(obj.get("reason") or "")
        if obj.get("systemMessage") and v == "none":
            v, reason = "notice", str(obj["systemMessage"])
    if exit_code == 2:
        v, reason = "deny", reason or err
    elif exit_code not in (0, None) and v == "none":
        v, reason = f"exit{exit_code}", err
    rules = RULE_RE.findall(reason)
    out = {"v": v, "rule": rules[-1] if rules else None}
    if upd is not None:
        out["upd"] = upd
    if rewritten is not None:
        out["out"] = rewritten
    if v != "none" and not rules:
        out["why"] = reason.strip().split("\n", 1)[0][:head]
    return out


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV}, timeout=30)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def build_world(world: dict, root: Path, places: dict) -> None:
    """Rebuild a recorded world at root: git repos from their recipe first (HEAD, then the
    index), then the working files, links, empty dirs and modes."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, recipe in sorted((world.get("repos") or {}).items()):
        repo = root / rel
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q", "-b", recipe.get("branch") or "main")
        for line in recipe.get("config", []):
            key, _, val = line.partition("=")
            _git(repo, "config", key, expand(val, places))
        head = recipe.get("head") or {}
        for name, body in head.items():
            _write(repo / name, expand(body, places))
        if head:
            _git(repo, "add", "-f", "--", *head)
        if head or recipe.get("commits"):
            _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "w")
        index = recipe.get("index") or {}
        for name, body in index.items():
            _write(repo / name, expand(body, places))
        if index:
            _git(repo, "add", "-f", "--", *index)
    for rel, body in (world.get("files") or {}).items():
        _write(root / rel, expand(body, places))
    for rel in world.get("dirs") or ():
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel, target in (world.get("links") or {}).items():
        link = root / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.is_symlink():
            os.symlink(expand(target, places), link)
    for rel, mode in (world.get("modes") or {}).items():
        path = root / rel
        if mode == "unreadable":
            if not path.exists():
                _write(path, "")
            path.chmod(0)
        elif path.exists():
            path.chmod(int(mode, 8))


def _unlock(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o700, follow_symlinks=False)
            except (OSError, NotImplementedError):
                pass


def load_worlds(path: Path = WORLDS) -> dict:
    worlds = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            entry = json.loads(line) if line.strip() else {}
            if "id" in entry:
                worlds[entry["id"]] = entry["world"]
    return worlds


_BUILT: dict = {}


def _cacheable(worlds: dict, ids: list) -> bool:
    return not any("unreadable" in (worlds[w].get("modes") or {}).values() for w in ids)


def _fingerprint(root: Path) -> tuple:
    seen = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            seen.append((dirpath, name, st.st_size, st.st_mtime_ns, st.st_mode))
    return tuple(sorted(seen))


def _build(worlds: dict, world_id: str, root: Path, places: dict, cache: Path | None) -> None:
    """A world built at root; one that names no machine path is built once per cache and
    copied (git repos hold no absolute path of their own)."""
    world = worlds[world_id]
    if cache is None or "@@root" in json.dumps(world):
        build_world(world, root, places)
        return
    pristine = cache / "worlds" / hashlib.sha1(world_id.encode("utf-8")).hexdigest()[:12]
    if not pristine.exists():
        build_world(world, pristine, places)
    shutil.copytree(pristine, root, symlinks=True)


def replay(row: dict, scratch: Path, worlds: dict, fake_values: dict,
           cache: Path | None = None) -> dict:
    """The outcome the real hook entry gives for one row, in a world rebuilt at scratch.
    With cache, a context's worlds are built once at one fixed path per context (so the
    paths inside them hold) and reused by its later rows while nothing in them changed;
    a row that changed a file gets the next row a fresh build."""
    import dispatch
    import dispatch_registry as registry

    places = {"plugin": str(PLUGIN)}
    places.update({f"fake{n}": v for n, v in fake_values.items()})
    context = worlds[row["ctx"]]
    ids = context.get("worlds") or []
    cached = cache is not None and _cacheable(worlds, ids)
    if cached:
        scratch = cache / hashlib.sha1(row["ctx"].encode("utf-8")).hexdigest()[:12]
    for n, _ in enumerate(ids):
        places[f"root{n}"] = str(scratch / f"r{n}")
    if not (cached and str(scratch) in _BUILT):
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        for n, world_id in enumerate(ids):
            _build(worlds, world_id, scratch / f"r{n}", places, cache if cached else None)
        if cached:
            _BUILT[str(scratch)] = _fingerprint(scratch)
    row = dict(row, env=context.get("env") or {}, cwd=context.get("cwd") or "")
    raw = expand(row["raw"], places).encode("utf-8", "surrogatepass")
    check = {c.name: c for c in registry.REGISTRY[row["event"]]}[row["check"]]
    try:
        tool = json.loads(raw).get("tool_name")
    except (ValueError, AttributeError):
        tool = ""
    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    saved_home = Path.home    # the suite's conftest points Path.home at its tmp dir; a hook
    Path.home = classmethod(lambda cls: cls(os.environ["HOME"]))  # reads $HOME, as in a session
    home = scratch.parent / "home"          # a row that set no HOME gets a scratch one, so a
    home.mkdir(parents=True, exist_ok=True)  # check that writes state never reaches the real one
    try:
        os.environ["HOME"] = str(home)
        for name, value in (row.get("env") or {}).items():
            if value is None:
                os.environ.pop(name, None)
            elif name == "PATH" and "@@PATH@@" in value:
                os.environ[name] = expand(value, places).replace("@@PATH@@", saved_env.get("PATH", ""))
            else:
                os.environ[name] = expand(value, places)
        cwd = expand(row.get("cwd") or "", places)
        target = Path(cwd) if cwd and Path(cwd).is_dir() else scratch
        os.chdir(target)
        with real_streams():
            outcome = dispatch.run_check(check, raw, row["event"], tool if isinstance(tool, str) else "")
        result = verdict_of(outcome.exit_code, outcome.stdout, outcome.stderr, head=None)
    finally:
        Path.home = saved_home
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        if not cached:
            _unlock(scratch)
            shutil.rmtree(scratch, ignore_errors=True)
        elif _fingerprint(scratch) != _BUILT[str(scratch)]:
            del _BUILT[str(scratch)]
    result = normalise(result, places)
    if "why" in result:     # cut after the paths in it are placeholders, so the head is stable
        result["why"] = result["why"][:90]
    return result


def normalise(result: dict, places: dict) -> dict:
    """Machine paths in an outcome turned back into their placeholders."""
    pairs = []
    for name, text in places.items():
        pairs.append((text, name))
        if not name.startswith("fake") and os.path.realpath(text) != text:
            pairs.append((os.path.realpath(text), name))
    pairs.sort(key=lambda p: -len(p[0]))

    def back(value):
        if isinstance(value, str):
            for text, name in pairs:
                if text:
                    value = value.replace(text, f"@@{name}@@")
            return value
        if isinstance(value, list):
            return [back(v) for v in value]
        if isinstance(value, dict):
            return {k: back(v) for k, v in value.items()}
        return value
    return back(result)
