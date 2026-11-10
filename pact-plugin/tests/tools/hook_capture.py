"""
Location: pact-plugin/tests/tools/hook_capture.py
Summary: Records the hook-level decisions of the guards over every payload the PRE-CUT
         suite fed them, into tests/fixtures/shell/decisions/hooks/<check>.jsonl, replayed
         by tests/test_hook_decision_tables.py. Two steps:
           1. capture: a pytest plugin, run inside a checkout of the pre-cut tree (commit
              7b893da9, which holds the full guard suites; the guard source there equals
              this tree's). It wraps each guard's entries (main, decide/check, the
              command-string readers, the dispatcher, a hook run as a subprocess) and, per
              outermost call, writes one record to $GT_OUT: the payload, the files the test
              had built under its tmp dir (git repos as a recipe), the env it set, the cwd,
              the verdict the real hook entry (dispatch.run_check) gives under the test's
              live state, and the direct call's own return mapped to a verdict.
                cd <pre-cut>/repacted/pact-plugin && GT_OUT=/abs/cap.jsonl \\
                  PYTHONPATH=<this tree>/repacted/pact-plugin/tests/tools \\
                  python -m pytest -q -p no:cacheprovider -p hook_capture tests/<file>.py
           2. build: `python tests/tools/hook_capture.py build <precut plugin dir> cap*.jsonl`
              (from this tree) turns machine paths and secret-shaped values into
              placeholders (tests/tools/hook_replay.py), replays every unique row on this
              tree's source in its rebuilt world and keeps the rows whose outcome equals
              the one seen at capture; the rest are state the world cannot carry (a
              monkeypatched internal, a clock, a session-wide dir) and are counted per
              check. A capture must use the pre-cut suite's inputs, never the cut suite's.
Used by: the maintainer at a re-capture; tests/test_hook_decision_tables.py reads its output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_replay import real_streams, verdict_of  # noqa: E402

OUT = os.environ.get("GT_OUT", "/dev/null")
BASE_ENV = dict(os.environ)
_state = threading.local()
_current = {"node": None, "basetemp": None}
_records: list = []
_REAL_RUN = subprocess.run

MAX_RAW = 4000          # a longer payload is a timing input, not a route (tests/slow/ keeps those)
MAX_FILES = 400
MAX_FILE = 65536

PAYLOAD_FNS = {
    "env_guard": ("decide",),
    "egress_guard": ("decide",),
    "process_table_guard": ("decide",),
    "settings_guard": ("decide",),
    "ops_frame_guard": ("decide", "dispatch_check"),
    "task_write_guard": ("decide", "dispatch_check"),
    "unattended_gate": ("decide",),
    "merge_question_gate": ("decide",),
    "docker_lock_gate": ("check", "dispatch_check"),
    "background_test_gate": ("check", "dispatch_check"),
    "output_secret_scan": ("check",),
}
COMMAND_FNS = {
    "merge_guard_pre": ("check_merge_authorization",),
    "process_table_guard": ("command_is_listing", "wraps_human_gate"),
    "docker_lock_gate": ("detect",),
    "background_test_gate": ("runs_tests", "backgrounded"),
    "ops_frame_guard": ("lead_op_of",),
    "git_commit_check": ("find_commit", "read_commit"),
    "env_guard": ("bash_verdict",),
    "egress_uploads": ("upload_paths",),
}
MAIN_MODULES = ("bootstrap_gate", "git_commit_check", "merge_guard_pre", "env_guard",
                "egress_guard", "process_table_guard", "dispatch_gate", "worktree_guard",
                "write_checks", "unattended_gate", "merge_question_gate", "wait_filler_gate",
                "output_secret_scan", "docker_lock_gate", "background_test_gate",
                "task_write_guard", "ops_frame_guard", "merge_guard_post")
POST = {"output_secret_scan", "merge_guard_post"}
OWNER = {"egress_uploads": "egress_guard"}
HOOK_RE = re.compile(r"/hooks/([a-z_]+)\.py$")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _depth() -> int:
    return getattr(_state, "depth", 0)


class _Deeper:
    def __enter__(self):
        _state.depth = _depth() + 1

    def __exit__(self, *exc):
        _state.depth = _depth() - 1


# ─── outcome and verdict ──────────────────────────────────────────────────────
def mapped(ret):
    """The verdict a direct call's return carries, when it has a hook-output shape."""
    if ret is None:
        return "none"
    if isinstance(ret, dict):
        hso = ret.get("hookSpecificOutput")
        if isinstance(hso, dict) and hso.get("permissionDecision"):
            return hso["permissionDecision"]
        if ret.get("decision") == "block":
            return "block"
        return "dict"
    if isinstance(ret, str):
        return "str"
    return type(ret).__name__


# ─── world snapshot ───────────────────────────────────────────────────────────
def _roots_in(*texts) -> list[str]:
    base = _current["basetemp"]
    if not base:
        return []
    found = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        for b in {base, os.path.realpath(base)}:
            for m in re.finditer(re.escape(b) + r"/([^/\s\"'\\]+)", text):
                found.add(b + "/" + m.group(1))
    return sorted(found)


def _git_recipe(repo: Path) -> dict:
    def git(*args):
        r = _REAL_RUN(["git", "-C", str(repo), *args], capture_output=True, timeout=20)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    recipe = {"branch": (git("symbolic-ref", "--short", "-q", "HEAD") or "").strip(),
              "head": {}, "index": {}, "config": []}
    files = git("ls-tree", "-r", "--name-only", "HEAD")
    for name in (files or "").splitlines()[:MAX_FILES]:
        body = git("show", f"HEAD:{name}")
        if body is not None and len(body) <= MAX_FILE:
            recipe["head"][name] = body
    staged = git("ls-files", "-s")
    for line in (staged or "").splitlines()[:MAX_FILES]:
        name = line.split("\t", 1)[-1]
        body = git("show", f":{name}")
        if body is not None and len(body) <= MAX_FILE and recipe["head"].get(name) != body:
            recipe["index"][name] = body
    recipe["commits"] = len((git("rev-list", "--all") or "").split())
    cfg = git("config", "--local", "--list") or ""
    recipe["config"] = [l for l in cfg.splitlines()
                        if l.startswith(("remote.", "branch.", "alias.", "user.", "core.hooksPath"))]
    return recipe


_SNAPSHOTS: dict = {}


def _fingerprint(root: str) -> tuple:
    marks = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "objects":
            dirnames[:] = []
        for name in filenames + dirnames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            marks.append((dirpath, name, st.st_mtime_ns, st.st_size, st.st_mode))
        if len(marks) > 5000:
            break
    return tuple(marks)


def snapshot(root: str) -> dict:
    """The world at root, reused while nothing under it changed (a test that calls a guard
    a thousand times over one tree pays for one walk and one git recipe)."""
    mark = _fingerprint(root)
    cached = _SNAPSHOTS.get(root)
    if cached and cached[0] == mark:
        return json.loads(cached[1])
    world = _snapshot(root)
    _SNAPSHOTS[root] = (mark, json.dumps(world))
    return world


def _snapshot(root: str) -> dict:
    files, links, modes, dirs, repos = {}, {}, {}, [], {}
    status = "ok"
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if ".git" in dirnames:
            repos[rel_dir] = _git_recipe(Path(dirpath))
            dirnames.remove(".git")
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".pytest_cache")]
        if not filenames and not dirnames and rel_dir != ".":
            dirs.append(rel_dir)
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                links[os.path.normpath(os.path.join(rel_dir, d))] = os.readlink(full)
                dirnames.remove(d)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name))
            count += 1
            if count > MAX_FILES:
                return {"status": "too-big"}
            if os.path.islink(full):
                links[rel] = os.readlink(full)
                continue
            try:
                st = os.stat(full)
                if st.st_size > MAX_FILE:
                    status = "partial"
                    continue
                with open(full, "rb") as fh:
                    body = fh.read()
                files[rel] = body.decode("utf-8")
            except UnicodeDecodeError:
                status = "partial"
                continue
            except OSError:
                modes[rel] = "unreadable"
                continue
            mode = st.st_mode & 0o777
            if mode not in (0o644, 0o600, 0o664):
                modes[rel] = oct(mode)
    world = {"status": status, "files": files}
    if links:
        world["links"] = links
    if modes:
        world["modes"] = modes
    if dirs:
        world["dirs"] = dirs
    if repos:
        world["repos"] = repos
    return world


# ─── recording ────────────────────────────────────────────────────────────────
def _env_diff(env: dict) -> dict:
    diff = {}
    for k, v in env.items():
        if BASE_ENV.get(k) != v and not k.startswith(("PYTEST_", "COV_", "GT_")):
            base = BASE_ENV.get(k)
            if k == "PATH" and base and v.endswith(base):
                v = v[: len(v) - len(base)] + "@@PATH@@"
            diff[k] = v
    for k in BASE_ENV:
        if k not in env and not k.startswith(("PYTEST_", "GT_")):
            diff[k] = None
    return diff


def _seen(check_name: str, event: str, raw: bytes, env=None, cwd=None) -> dict:
    import dispatch_registry as registry
    checks = {c.name: c for c in registry.REGISTRY.get(event, ())}
    check = checks.get(check_name)
    if check is None:
        return {"v": "no-check"}
    try:
        payload = json.loads(raw)
        tool = payload.get("tool_name") if isinstance(payload, dict) else ""
    except ValueError:
        tool = ""
    saved_env, saved_cwd = None, None
    if env is not None:
        saved_env = dict(os.environ)
        os.environ.clear()
        os.environ.update({k: v for k, v in env.items() if isinstance(v, str)})
    if cwd is not None:
        saved_cwd = os.getcwd()
        try:
            os.chdir(cwd)
        except OSError:
            saved_cwd = None
    try:
        with _Deeper(), real_streams():
            outcome = _ORIG_RUN_CHECK(check, raw, event, tool if isinstance(tool, str) else "")
        return verdict_of(outcome.exit_code, outcome.stdout, outcome.stderr)
    except BaseException as error:  # noqa: BLE001
        return {"v": "seen-error", "why": type(error).__name__}
    finally:
        if saved_env is not None:
            os.environ.clear()
            os.environ.update(saved_env)
        if saved_cwd is not None:
            os.chdir(saved_cwd)


def _seen_on_copies(check: str, event: str, raw: bytes, env, cwd, roots: list) -> dict:
    """The live verdict, run over a copy of the test's dirs: a hook's side effects (a
    consumed approval, a journal row, a minted token) never reach the test's own state."""
    import shutil
    import tempfile
    from pathlib import Path as _P
    scratch = tempfile.mkdtemp(prefix="gtseen")
    pairs = []
    for n, root in enumerate(roots):
        copy = os.path.join(scratch, f"c{n}")
        try:
            shutil.copytree(root, copy, symlinks=True)
        except (OSError, shutil.Error):
            os.makedirs(copy, exist_ok=True)
        for variant in sorted({root, os.path.realpath(root),
                               root.replace("/private/var/", "/var/", 1)}, key=len, reverse=True):
            pairs.append((variant, copy))

    def sub(text):
        if not isinstance(text, str):
            return text
        for a, b in pairs:
            text = text.replace(a, b)
        return text

    def back(value):
        if isinstance(value, str):
            for a, b in pairs:
                value = value.replace(b, a)
            return value
        if isinstance(value, list):
            return [back(v) for v in value]
        if isinstance(value, dict):
            return {k: back(v) for k, v in value.items()}
        return value

    real_home = _P.home
    raw_home = _P.__dict__.get("home")
    run_env = {k: sub(v) for k, v in (env if env is not None else dict(os.environ)).items()}
    try:
        home = str(real_home())
        _P.home = classmethod(lambda cls: _P(sub(home)))
        out = _seen(check, event, sub(raw.decode("utf-8", "surrogatepass")).encode("utf-8", "surrogatepass"),
                    run_env, sub(cwd) if cwd else None)
    finally:
        if raw_home is None:
            del _P.home
        else:
            setattr(_P, "home", raw_home)
        for dirpath, dirnames, filenames in os.walk(scratch):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700, follow_symlinks=False)
                except (OSError, NotImplementedError):
                    pass
        shutil.rmtree(scratch, ignore_errors=True)
    return back(out)


def _holds_approval(roots: list) -> bool:
    """True when a merge approval token exists (in the token dir the test set, or under its
    dirs): the merge hook would consume it, and a table cannot carry its clock and session."""
    dirs = list(roots)
    for name in ("merge_guard_pre", "shared.merge_guard_common"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "TOKEN_DIR", None):
            dirs.append(str(module.TOKEN_DIR))
    prefix = getattr(sys.modules.get("shared.merge_guard_common"), "TOKEN_PREFIX", "merge-authorized")
    for d in dirs:
        for _, _, files in os.walk(d):
            if any(f.startswith(prefix) for f in files):
                return True
    return False


def record(check: str, event: str, raw, via: str, ret=None, env=None, cwd=None, seen=None):
    if check == "merge_guard_post":
        return                      # it mints approvals: a replay would change the test's state
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "surrogatepass")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RAW:
        return
    raw = bytes(raw)
    text = raw.decode("utf-8", "replace")
    effective_env = env if env is not None else dict(os.environ)
    effective_cwd = cwd or os.getcwd()
    roots = _roots_in(text, effective_cwd, *[v for v in effective_env.values() if isinstance(v, str)])
    if check == "merge_guard_pre" and _holds_approval([r for r in roots if os.path.isdir(r)]):
        return
    worlds = {r: snapshot(r) for r in roots if os.path.isdir(r)}
    if seen is None:
        seen = _seen_on_copies(check, event, raw, env, effective_cwd, list(worlds))
    rec = {"node": _current["node"], "check": check, "event": event, "raw": text,
           "via": via, "seen": seen, "env": _env_diff(effective_env), "cwd": effective_cwd,
           "roots": list(worlds), "worlds": worlds, "basetemp": _current["basetemp"]}
    if ret is not None:
        rec["ret"] = ret
    _records.append(rec)


def _owner(module: str) -> str:
    return OWNER.get(module, module)


def _wrap_payload(module, mod_name: str, fn_name: str):
    fn = getattr(module, fn_name)

    def wrapped(*args, **kwargs):
        if _depth():
            return fn(*args, **kwargs)
        payload = args[0] if args else kwargs.get("payload")
        with _Deeper():
            ret = fn(*args, **kwargs)
        try:
            if isinstance(payload, dict):
                check = _owner(mod_name)
                if mod_name == "settings_guard":
                    check = "worktree_guard" if payload.get("tool_name") in EDIT_TOOLS else "env_guard"
                if mod_name == "egress_guard" and (args[1:2] == (True,) or kwargs.get("ws_only")):
                    check = "egress_ws"
                event = "PostToolUse" if check in POST else "PreToolUse"
                record(check, event, json.dumps(payload), f"{mod_name}.{fn_name}",
                       ret=mapped(ret))
        except Exception:  # noqa: BLE001
            pass
        return ret
    wrapped.__wrapped__ = fn
    setattr(module, fn_name, wrapped)


def _wrap_command(module, mod_name: str, fn_name: str):
    fn = getattr(module, fn_name)

    def wrapped(*args, **kwargs):
        if _depth():
            return fn(*args, **kwargs)
        with _Deeper():
            ret = fn(*args, **kwargs)
        try:
            command = args[0] if args else kwargs.get("command")
            if isinstance(command, str) and command.strip():
                cwd = kwargs.get("cwd") or (args[1] if len(args) > 1 and isinstance(args[1], str)
                                            and mod_name in ("git_commit_check", "env_guard",
                                                             "egress_uploads") else None)
                if mod_name == "merge_guard_pre":
                    cwd = kwargs.get("cwd") or (args[2] if len(args) > 2 else None)
                payload = {"tool_name": "Bash", "tool_input": {"command": command},
                           "cwd": cwd or os.getcwd()}
                record(_owner(mod_name), "PreToolUse", json.dumps(payload),
                       f"{mod_name}.{fn_name}", ret=mapped(ret) if not isinstance(ret, tuple) else "tuple")
        except Exception:  # noqa: BLE001
            pass
        return ret
    wrapped.__wrapped__ = fn
    setattr(module, fn_name, wrapped)


def _wrap_main(module, mod_name: str):
    fn = getattr(module, "main", None)
    if fn is None:
        return

    def wrapped(*args, **kwargs):
        if _depth():
            return fn(*args, **kwargs)
        raw = None
        try:
            import io
            raw = sys.stdin.read()
            sys.stdin = io.StringIO(raw)
        except Exception:  # noqa: BLE001
            raw = None
        if raw is not None:
            try:
                check = mod_name
                if mod_name == "egress_guard" and "--ws" in sys.argv[1:]:
                    check = "egress_ws"
                event = "PostToolUse" if check in POST else "PreToolUse"
                record(check, event, raw, f"{mod_name}.main")
            except Exception:  # noqa: BLE001
                pass
        with _Deeper():
            return fn(*args, **kwargs)
    wrapped.__wrapped__ = fn
    module.main = wrapped


def _hook_of(argv) -> tuple | None:
    if isinstance(argv, (str, bytes)):
        return None
    try:
        words = [str(a) for a in argv]
    except TypeError:
        return None
    for i, w in enumerate(words):
        m = HOOK_RE.search(w)
        if m:
            return m.group(1), words[i + 1:]
    return None


def _subprocess_record(argv, kwargs):
    hit = _hook_of(argv)
    if not hit or _depth():
        return
    name, rest = hit
    raw = kwargs.get("input")
    if raw is None:
        return
    env = kwargs.get("env")
    env = dict(env) if env is not None else None
    cwd = kwargs.get("cwd")
    cwd = str(cwd) if cwd is not None else None
    if name == "dispatch":
        import dispatch
        import dispatch_registry as registry
        event = rest[0] if rest else "PreToolUse"
        group = rest[1] if len(rest) > 1 else None
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        try:
            selected = dispatch.select(event, registry.checks_for(event), payload if isinstance(payload, dict) else {}, group)
        except Exception:  # noqa: BLE001
            selected = []
        for check in selected:
            record(check.name, event, raw, "subprocess.dispatch", env=env, cwd=cwd)
        return
    check = "egress_ws" if name == "egress_guard" and "--ws" in rest else name
    event = "PostToolUse" if check in POST else "PreToolUse"
    record(check, event, raw, "subprocess", env=env, cwd=cwd)


def _patched_run(*args, **kwargs):
    argv = args[0] if args else kwargs.get("args")
    try:
        _subprocess_record(argv, kwargs)
    except Exception:  # noqa: BLE001
        pass
    with _Deeper():
        return _REAL_RUN(*args, **kwargs)


_ORIG_RUN_CHECK = None


def install():
    global _ORIG_RUN_CHECK
    import importlib
    import dispatch
    _ORIG_RUN_CHECK = dispatch.run_check
    for mod_name, fns in PAYLOAD_FNS.items():
        mod = importlib.import_module(mod_name)
        for fn in fns:
            if hasattr(mod, fn):
                _wrap_payload(mod, mod_name, fn)
    for mod_name, fns in COMMAND_FNS.items():
        mod = importlib.import_module(mod_name)
        for fn in fns:
            if hasattr(mod, fn):
                _wrap_command(mod, mod_name, fn)
    for mod_name in MAIN_MODULES:
        _wrap_main(importlib.import_module(mod_name), mod_name)

    def run_check(check, raw, event, tool_name=""):
        if not _depth():
            try:
                record(check.name, event, raw, "dispatch.run_check")
            except Exception:  # noqa: BLE001
                pass
        with _Deeper():
            return _ORIG_RUN_CHECK(check, raw, event, tool_name)
    dispatch.run_check = run_check
    subprocess.run = _patched_run


def pytest_configure(config):
    install()


def pytest_runtest_setup(item):
    _records.clear()
    _current["node"] = item.nodeid
    try:
        _current["basetemp"] = str(item.config._tmp_path_factory.getbasetemp())
    except Exception:  # noqa: BLE001
        _current["basetemp"] = None


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        outcome = report.outcome
        with open(OUT, "a", encoding="utf-8") as fh:
            for rec in _records:
                rec["outcome"] = outcome
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _records.clear()


# ─── build: captures -> candidate tables (pruned by the replay test) ──────────
SESSION_TEMP = ("/pytest-of-", "/var/folders/", "/tmp/", "pact-scratch-suite")
GUARD_PROBE_FIELDS = ("raw", "cwd", "env", "worlds", "seen", "node")


def _variants(path: str) -> list[str]:
    out = {path, os.path.realpath(path)}
    for p in list(out):
        if p.startswith("/private/var/"):
            out.add(p[len("/private"):])
        elif p.startswith("/var/"):
            out.add("/private" + p)
    return sorted(out, key=len, reverse=True)


def _marker(pairs):
    def mark(value):
        if isinstance(value, str):
            for text, name in pairs:
                value = value.replace(text, f"@@{name}@@")
            return value
        if isinstance(value, list):
            return [mark(v) for v in value]
        if isinstance(value, dict):
            return {mark(k): mark(v) for k, v in value.items()}
        return value
    return mark


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)


class Faker:
    """Secret-shaped values -> @@fake<n>@@, numbered once across every table."""

    def __init__(self, plugin: Path):
        sys.path.insert(0, str(plugin / "hooks"))
        sys.path.insert(0, str(plugin))
        from shared import scrub
        from tests.contract import _purity
        self.spans = scrub._secret_value_spans
        self.kind = scrub.secret_kind
        self.patterns, self.secret_kinds, self.hit = scrub.SCRUB_PATTERNS, scrub.SECRET_ONLY_KINDS, scrub._hit
        self.purity = _purity
        try:
            self.ctx = _purity.context_from_env()
        except _purity.FailClosed:
            self.ctx = None
        self.values: dict[str, int] = {}

    def _id(self, value: str) -> str:
        if value not in self.values:
            self.values[value] = len(self.values) + 1
        return f"@@fake{self.values[value]}@@"

    def flagged_values(self, text: str) -> list[str]:
        found = [text[a:b] for a, b in self.spans(text) if b > a]
        if self.ctx is not None:
            found += [hit.value for hit in self.purity.scan_file("hooks.jsonl", text, self.ctx)
                      if isinstance(hit.value, str) and hit.value and hit.cls != "personal"]
        return sorted(set(found), key=len, reverse=True)

    def scrub(self, value):
        if isinstance(value, str):
            for _ in range(4):
                found = self.flagged_values(value)
                if not found:
                    break
                for secret in found:
                    value = value.replace(secret, self._id(secret))
            for _ in range(6):
                # A context class (KEY=value, password: x) matches its placeholder too, and a
                # match may show only in the JSON-escaped line: replace the whole match.
                whole = self.whole_matches(value)
                if not whole:
                    break
                for secret in whole:
                    value = value.replace(secret, self._id(secret))
            return value
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if isinstance(value, dict):
            return {self.scrub(k): self.scrub(v) for k, v in value.items()}
        return value

    def whole_matches(self, value: str) -> list[str]:
        """Full matched text of every secret class (and purity secret hit) in value or in its
        JSON-escaped spelling, mapped back to value's own text."""
        found = []
        escaped = json.dumps(value, ensure_ascii=False)[1:-1]
        for text, is_escaped in ((value, False), (escaped, True)):
            for pattern in self.patterns:
                if pattern.kind not in self.secret_kinds:
                    continue
                for match in pattern.regex.finditer(text):
                    if self.hit(pattern, match):
                        found.append((match.group(0), is_escaped))
            if self.ctx is not None:
                for hit in self.purity.scan_file("hooks.jsonl", text, self.ctx):
                    if hit.cls != "personal":
                        line = text.split("\n")[hit.line - 1] if "\n" in text else text
                        found.append((hit.value or line, is_escaped))
        out = []
        for text, is_escaped in found:
            if is_escaped:
                try:
                    text = json.loads(f'"{text}"')
                except ValueError:
                    text = None
            if text and text in value:
                out.append(text)
            elif text is None or text not in value:
                out.append(value)       # cannot map it back: the whole string is the fake
        return sorted(set(out), key=len, reverse=True)

    def why(self, line: str) -> str:
        kind = self.kind(line)
        hits = [] if self.ctx is None else self.purity.scan_file("hooks.jsonl", line, self.ctx)
        return f"{kind} {[h.cls for h in hits][:3]}"

    def line_clean(self, line: str) -> bool:
        if self.kind(line):
            return False
        return self.ctx is None or not self.purity.scan_file("hooks.jsonl", line, self.ctx)


def _digest(value) -> str:
    import hashlib
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def _split_literal(value: str, size: int = 4) -> str:
    parts = [value[i:i + size] for i in range(0, len(value), size)] or [""]
    return " ".join(json.dumps(p, ensure_ascii=True) for p in parts)


def build(precut: str, captures: list[str]) -> int:
    plugin = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(plugin / "tests" / "tools"))
    from hook_replay import TABLES
    faker = Faker(plugin)
    rows: dict[str, dict] = {}
    contexts: dict[str, dict] = {}
    report = {"records": 0, "not_passed": 0, "too_big": 0, "unclean": 0, "crosscheck": [],
              "by_check": {}}
    for path in captures:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                report["records"] += 1
                if rec.get("outcome") != "passed":
                    report["not_passed"] += 1
                    continue
                if any(w.get("status") == "too-big" for w in rec["worlds"].values()):
                    report["too_big"] += 1
                    continue
                pairs = [(v, "plugin") for v in _variants(precut)]
                for n, root in enumerate(rec["roots"]):
                    pairs += [(v, f"root{n}") for v in _variants(root)]
                pairs.sort(key=lambda p: -len(p[0]))
                mark = _marker(pairs)
                env = {}
                for key, value in mark(rec["env"]).items():
                    if isinstance(value, str) and any(m in value for m in SESSION_TEMP):
                        continue            # a session-wide temp dir: the replay's own conftest sets it
                    env[key] = value
                cwd = mark(rec["cwd"])
                if any(m in cwd for m in SESSION_TEMP):
                    cwd = ""
                worlds = [mark(rec["worlds"][r]) for r in rec["roots"]]
                for w in worlds:
                    w.pop("status", None)
                ctx = faker.scrub({"env": env, "cwd": cwd, "worlds": worlds})
                raw = faker.scrub(mark(rec["raw"]))
                seen = faker.scrub(mark(rec["seen"]))
                node = faker.scrub(mark(rec["node"] or ""))
                if any(m in json.dumps([raw, seen]) for m in SESSION_TEMP):
                    report["unclean"] += 1
                    continue
                ctx_world_ids = []
                for w in ctx["worlds"]:
                    wid = _digest(w)
                    contexts.setdefault("w" + wid, w)
                    ctx_world_ids.append("w" + wid)
                context = {"env": ctx["env"], "cwd": ctx["cwd"], "worlds": ctx_world_ids}
                cid = "c" + _digest(context)
                contexts.setdefault(cid, context)
                key = json.dumps([rec["check"], rec["event"], raw, cid], ensure_ascii=False)
                row = rows.get(key)
                if row is None:
                    row = rows[key] = {"id": _digest(key)[:10], "check": rec["check"],
                                       "event": rec["event"], "raw": raw, "ctx": cid,
                                       "want": seen, "src": []}
                elif row["want"] != seen:
                    row["unstable"] = True
                if node not in row["src"]:
                    row["src"].append(node)
                ret = rec.get("ret")
                if ret in ("none", "deny", "ask", "allow", "block") and ret != seen.get("v"):
                    report["crosscheck"].append({"check": rec["check"], "node": node,
                                                 "via": rec["via"], "ret": ret, "seen": seen,
                                                 "raw": raw[:200]})
    by_check: dict[str, list] = {}
    for row in rows.values():
        if row.pop("unstable", False):
            report.setdefault("unstable", 0)
            report["unstable"] = report["unstable"] + 1
            continue
        by_check.setdefault(row["check"], []).append(row)
    TABLES.mkdir(parents=True, exist_ok=True)
    used_contexts: set[str] = set()
    sidecar = {}
    clean: dict[str, bool] = {}

    def context_clean(cid: str) -> bool:
        if cid not in clean:
            line = json.dumps({"id": cid, "world": contexts[cid]}, ensure_ascii=False, sort_keys=True)
            clean[cid] = faker.line_clean(line)
            if not clean[cid]:
                report.setdefault("unclean_samples", []).append(faker.why(line))
        return clean[cid]

    for check, items in sorted(by_check.items()):
        lines = []
        for row in sorted(items, key=lambda r: r["id"]):
            sidecar[row["id"]] = row["src"]
            row = dict(row, src=row["src"][:3], srcs=len(row["src"]))
            line = json.dumps(row, ensure_ascii=False, sort_keys=True)
            ids = [row["ctx"], *contexts[row["ctx"]]["worlds"]]
            if not faker.line_clean(line) or not all(context_clean(c) for c in ids):
                report["unclean"] += 1
                if not faker.line_clean(line):
                    report.setdefault("unclean_samples", []).append(faker.why(line))
                continue
            lines.append(line)
            used_contexts.update(ids)
        _write_table(TABLES / f"{check}.jsonl", lines, {"check": check, "captured_from": "7b893da9"})
        report["by_check"][check] = len(lines)
    ctx_lines = [json.dumps({"id": cid, "world": contexts[cid]}, ensure_ascii=False, sort_keys=True)
                 for cid in sorted(used_contexts)]
    _write_table(TABLES / "worlds.jsonl", ctx_lines, {"captured_from": "7b893da9"})
    _write_fakes(plugin / "tests" / "tools" / "hook_fakes.py", faker.values, faker.line_clean)
    out = Path(os.environ.get("GT_REPORT", "/dev/null"))
    if str(out) != "/dev/null":
        out.write_text(json.dumps({"report": report, "src": sidecar}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "crosscheck"}, indent=1))
    print(f"crosscheck mismatches: {len(report['crosscheck'])}")
    return 0


def _write_table(path: Path, lines: list[str], header: dict) -> None:
    import hashlib
    body = "".join(line + "\n" for line in lines)
    header = dict(header, rows=len(lines), sha256=hashlib.sha256(body.encode("utf-8")).hexdigest())
    path.write_text(json.dumps(header, sort_keys=True) + "\n" + body, encoding="utf-8")


def _fake_line(n: int, value: str, clean) -> str:
    """One FAKES entry, in the first piece size whose line the purity scan finds clean (a
    cut can line up two pieces into a word the scan refuses)."""
    for size in (4, 3, 5, 6, 7):
        line = f"    {n}: ({_split_literal(value, size)}),"
        if clean is None or clean(line):
            return line
    raise ValueError(f"FAKES[{n}]: no piece size gives a clean line")


def _write_fakes(path: Path, values: dict, clean=None) -> None:
    out = ['"""',
           "Location: pact-plugin/tests/tools/hook_fakes.py",
           "Summary: The fake credentials the pre-cut guard suites built at run time, each spelled",
           "         here in four-character pieces so no line of this file or of the hook tables",
           "         (tests/fixtures/shell/decisions/hooks/, where @@fake<n>@@ stands for FAKES[n])",
           "         is secret-shaped. Generated by tests/tools/hook_capture.py build.",
           "Used by: tests/tools/hook_replay.py.",
           '"""',
           "FAKES = {"]
    for value, n in sorted(values.items(), key=lambda kv: kv[1]):
        out.append(_fake_line(n, value, clean))
    out.append("}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "fakes":     # rewrite hook_fakes.py in place
        plugin = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(plugin / "tests" / "tools"))
        from hook_fakes import FAKES
        _write_fakes(plugin / "tests" / "tools" / "hook_fakes.py",
                     {v: n for n, v in FAKES.items()}, Faker(plugin).line_clean)
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == "build":
        sys.exit(build(sys.argv[2], sys.argv[3:]))
    print("usage: hook_capture.py build <pre-cut plugin dir> <capture.jsonl>...", file=sys.stderr)
    sys.exit(64)
