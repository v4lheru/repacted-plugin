"""
Location: pact-plugin/tests/merge_guard_baseline_loader.py
Summary: Loud-fail loaders for the merge-guard certification bases. A base is a past
         commit's hooks/shared/merge_guard_common.py; a certification file compares that
         base's verdicts with the live module's. The bases are no longer vendored as
         3.4 MB of Python: each is a CAPTURED DECISION TABLE,
         tests/fixtures/shell/decisions/base_<sha8>.jsonl, holding every call the
         certification suites made on the base module (function, arguments, result)
         plus every attribute they read, behind the same API the certs always used:
         `load_vendored(sha8).is_dangerous_command(cmd)`. A row the table does not hold
         is a pytest.fail naming the capture tool, never a skip. The header row carries
         the base commit, the git blob id of the bytes the table was captured from (so
         provenance is not lost), the plugin commit of the capture and the sha256 of the
         body, which the loader checks before serving a row.

         Three modes, chosen by environment:
           default                   serve rows from the table (no Python base is loaded);
           PACT_CAPTURE_DECISIONS=1  load the vendored base module, record every call and
                                     attribute read, write the tables at exit
                                     (tests/tools/capture_decisions.py drives this);
           PACT_DECISIONS_VERIFY=1   load both; every lookup is compared with the module
                                     and a difference is a pytest.fail (the proof that
                                     ran before the vendored bytes were deleted).
         The two recording modes need the vendored bytes under
         tests/fixtures/merge_guard_baseline/ (deleted from the tree; `git show` the
         blob ids in _VENDORED to restore them).

         The three named loaders (load_baseline, load_baseline_172a77dd,
         load_baseline_5017d1f2) keep their discriminator rows: the base MUST still
         exhibit the bug its cert fixes, so a table captured from post-fix bytes fails
         the same way post-fix bytes used to.
Used by: the tests/test_merge_guard_*_mechanism.py files that still compare a base view,
         test_merge_guard_1136_canonical_join_ssot.py,
         test_merge_guard_vendored_baselines.py (the pins), tests/tools/capture_decisions.py.
Map: _VENDORED, encode, decode, _TableFunction, _TableProxy, _RecordingProxy,
     _load_module, _load_table, load_vendored, load_baseline, load_baseline_172a77dd,
     load_baseline_5017d1f2.
"""
import atexit
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

_VENDORED_DIR = Path(__file__).parent / "fixtures" / "merge_guard_baseline"
_DECISIONS_DIR = Path(__file__).parent / "fixtures" / "shell" / "decisions"
_CAPTURE_TOOL = "tests/tools/capture_decisions.py"

# commit sha8 -> (vendored file name, git blob id of the bytes the table was captured from)
_VENDORED = {
    "b4041ccf": ("merge_guard_common_b4041ccf.py", "48baf9e2df6bb1a214f276c70ffdaf49740db7a3"),
    "172a77dd": ("merge_guard_common_172a77dd.py", "52cb9bd84b8d5d986a4e61eab48252895e0183fe"),
    "5017d1f2": ("merge_guard_common_5017d1f2.py", "c00c377defa538a7c967c7bd910b183da125be17"),
    "c5e9b324": ("merge_guard_common_c5e9b324.py", "57eee4410e0a6ef2dae3e9021bdbb32bb7ce7d69"),
    "38f76965": ("merge_guard_common_38f76965.py", "745cbfe8adb4c303e83402fa5e2093fd51d718ed"),
    "023ee2c3": ("merge_guard_common_023ee2c3.py", "9f7f155bdac0012858701bd24e8f638d06bdadbb"),
    "6f404f2e": ("merge_guard_common_6f404f2e.py", "4c39097504d008ebe4d4ec61bc2e0d843afbaff4"),
    "51e6c5a5": ("merge_guard_common_51e6c5a5.py", "9a351309358c3f196995a106f1216b437bd151f1"),
    "72bacaf8": ("merge_guard_common_72bacaf8.py", "86f3d93d6f20757ed781bba519d781869a2246fb"),
    "b313ecaa": ("merge_guard_common_b313ecaa.py", "93e6f56d905b6b0326940ccabe5eaac775691d98"),
    "f6e3639a": ("merge_guard_common_f6e3639a.py", "cb24d0b883f092126e2714865c1b1d8ce89e9572"),
    "2d7fcd07": ("merge_guard_common_2d7fcd07.py", "b07f06c0c57ad9c966d637e4ac50e8beed7609ca"),
    "b6418727": ("merge_guard_common_b6418727.py", "f080051af6f9c6e9427d4dbaad886203b1276a5c"),
    "3972bb5f": ("merge_guard_common_3972bb5f.py", "2c29f088ff5fc03626a7ad5e31c976cc15d8012a"),
    "bf7c8786": ("merge_guard_common_bf7c8786.py", "66e1a48e33604ff98903d1624914af3cace24f7a"),
    "a62703f1": ("merge_guard_common_a62703f1.py", "8f67b82deb254e2ceff9532ab639f5012fadcf9f"),
    "a542e21b": ("merge_guard_common_a542e21b.py", "0d4be274af969a69f7053d343b8a81813115920a"),
    "89061755": ("merge_guard_common_89061755.py", "c0ea2ed08980ce3bde81c160bf94d7023b7e5b71"),
}

_CAPTURE = os.environ.get("PACT_CAPTURE_DECISIONS") == "1"
_VERIFY = os.environ.get("PACT_DECISIONS_VERIFY") == "1"

_cached_vendored = {}


# ─── value encoding: what a row stores and what a cert gets back ───────────────
# JSON scalars, lists and str-keyed dicts pass through. Tuples, sets, frozensets,
# compiled patterns, matches and exceptions are tagged objects. Anything else is a
# TypeError at capture time naming the call, so the table never holds a value the
# replay could not rebuild.

def encode(value, where=""):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [encode(v, where) for v in value]
    if isinstance(value, tuple):
        return {"__tuple__": [encode(v, where) for v in value]}
    if isinstance(value, (set, frozenset)):
        items = sorted((encode(v, where) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
        return {"__set__": items, "frozen": isinstance(value, frozenset)}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and not any(k.startswith("__") for k in value):
            return {k: encode(v, where) for k, v in value.items()}
        return {"__dict__": [[encode(k, where), encode(v, where)] for k, v in value.items()]}
    if isinstance(value, re.Pattern):
        return {"__pattern__": value.pattern, "flags": value.flags}
    if isinstance(value, re.Match):
        return {"__match__": value.group(0), "span": list(value.span()),
                "groups": [encode(g, where) for g in value.groups()],
                "groupdict": {k: encode(v, where) for k, v in value.groupdict().items()}}
    if isinstance(value, BaseException):
        return {"__raises__": type(value).__name__, "text": str(value)}
    raise TypeError("decision table cannot hold a %s (%s)" % (type(value).__name__, where))


class _MatchStandIn:
    """What a replayed `re.Match` row returns: truthy, with the groups the capture saw."""

    def __init__(self, row):
        self._whole, self._span = row["__match__"], tuple(row["span"])
        self._groups = tuple(decode(g) for g in row["groups"])
        self._groupdict = {k: decode(v) for k, v in row["groupdict"].items()}

    def group(self, *keys):
        if not keys or keys == (0,):
            return self._whole
        got = tuple(self._groupdict[k] if isinstance(k, str) else
                    (self._whole if k == 0 else self._groups[k - 1]) for k in keys)
        return got[0] if len(got) == 1 else got

    def groups(self):
        return self._groups

    def groupdict(self):
        return dict(self._groupdict)

    def span(self):
        return self._span

    def start(self):
        return self._span[0]

    def end(self):
        return self._span[1]


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "__tuple__" in value:
        return tuple(decode(v) for v in value["__tuple__"])
    if "__set__" in value:
        items = [decode(v) for v in value["__set__"]]
        return frozenset(items) if value["frozen"] else set(items)
    if "__dict__" in value:
        return {decode(k): decode(v) for k, v in value["__dict__"]}
    if "__pattern__" in value:
        return re.compile(value["__pattern__"], value["flags"])
    if "__match__" in value:
        return _MatchStandIn(value)
    if "__raises__" in value:
        return _ReplayedError(value["__raises__"], value["text"])
    return {k: decode(v) for k, v in value.items()}


class _ReplayedError(Exception):
    """A row that recorded a raise: re-raised with the captured type name and text."""

    def __init__(self, type_name, text):
        super().__init__(text)
        self.type_name = type_name

    def __str__(self):
        return "%s: %s" % (self.type_name, super().__str__())


def _call_key(name, args, kwargs):
    return json.dumps([name, encode(list(args), name), encode(kwargs, name)],
                      sort_keys=True, ensure_ascii=False)


# ─── the table proxy: the cert's view of a base after the vendored bytes are gone ──

class _TableFunction:
    def __init__(self, proxy, name):
        self._proxy, self._name = proxy, name

    def __call__(self, *args, **kwargs):
        return self._proxy._call(self._name, args, kwargs)

    def __repr__(self):
        return "<decision-table function %s@%s>" % (self._name, self._proxy._sha8)


class _TableProxy:
    """Serves a base's rows. `_attrs` holds attribute reads, `_calls` holds call rows;
    both keyed as the recorder keyed them. A missing row fails loudly."""

    def __init__(self, sha8, table):
        self._sha8 = sha8
        self._attrs, self._calls = table["attrs"], table["calls"]

    def _missing(self, what):
        pytest.fail("row not captured for %s: %s. Restore the vendored bytes and run "
                    "%s" % (self._sha8, what, _CAPTURE_TOOL))

    def __getattr__(self, name):
        if name.startswith("_"):
            if name not in self._attrs:
                raise AttributeError(name)
        elif name not in self._attrs:
            self._missing("attribute %s" % name)
        row = self._attrs[name]
        if row.get("missing"):
            raise AttributeError(name)
        if row.get("callable"):
            return _TableFunction(self, name)
        return decode(row["value"])

    def _call(self, name, args, kwargs):
        key = _call_key(name, args, kwargs)
        if key not in self._calls:
            self._missing("call %s%r" % (name, tuple(args)))
        result = decode(self._calls[key])
        if isinstance(result, _ReplayedError):
            raise result
        return result


# ─── the recording and verifying proxies: need the vendored module ──────────────

class _RecordingProxy:
    """Wraps a loaded base module; records every attribute read and every call.
    With `verify` set, each lookup is also checked against the committed table."""

    def __init__(self, sha8, module, table=None):
        self._sha8, self._module, self._table = sha8, module, table
        self._attrs, self._calls = {}, {}
        _recordings[sha8] = self

    def __getattr__(self, name):
        try:
            value = getattr(self._module, name)
        except AttributeError:
            self._attrs[name] = {"missing": True}
            self._check_attr(name)
            raise
        if callable(value) and not isinstance(value, type):
            self._attrs[name] = {"callable": True}
            self._check_attr(name)
            return _RecordedFunction(self, name, value)
        self._attrs[name] = {"value": encode(value, "attribute %s" % name)}
        self._check_attr(name)
        return value

    def _check_attr(self, name):
        if self._table is None:
            return
        if self._table["attrs"].get(name) != self._attrs[name]:
            pytest.fail("decision table %s: attribute %s differs from the module: table %r, "
                        "module %r" % (self._sha8, name, self._table["attrs"].get(name),
                                       self._attrs[name]))

    def _record(self, name, args, kwargs, outcome):
        key = _call_key(name, args, kwargs)
        self._calls[key] = outcome
        if self._table is None:
            return
        if key not in self._table["calls"]:
            pytest.fail("decision table %s: no row for %s%r" % (self._sha8, name, tuple(args)))
        if self._table["calls"][key] != outcome:
            pytest.fail("decision table %s: %s%r differs: table %r, module %r"
                        % (self._sha8, name, tuple(args), self._table["calls"][key], outcome))


class _RecordedFunction:
    def __init__(self, proxy, name, fn):
        self._proxy, self._name, self._fn = proxy, name, fn

    def __call__(self, *args, **kwargs):
        try:
            result = self._fn(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 — the raise is the recorded outcome
            self._proxy._record(self._name, args, kwargs, encode(error))
            raise
        self._proxy._record(self._name, args, kwargs, encode(result, "%s result" % self._name))
        return result


_recordings = {}


def _write_recordings():
    """At exit of a capture run: one table per base, rows sorted, header with digests."""
    _DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    for sha8, proxy in sorted(_recordings.items()):
        body = [json.dumps({"attr": k, **v}, sort_keys=True, ensure_ascii=False)
                for k, v in sorted(proxy._attrs.items()) if not k.startswith("__")]
        body += [json.dumps({"call": json.loads(k), "result": v}, sort_keys=True, ensure_ascii=False)
                 for k, v in sorted(proxy._calls.items())]
        text = "\n".join(body) + "\n"
        header = {
            "base": sha8,
            "blob_id": _VENDORED[sha8][1],
            "captured_from": os.environ.get("PACT_CAPTURE_COMMIT", "unknown"),
            "rows": len(body),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        (_DECISIONS_DIR / ("base_%s.jsonl" % sha8)).write_text(
            json.dumps(header, sort_keys=True) + "\n" + text, encoding="utf-8")


if _CAPTURE:
    atexit.register(_write_recordings)


# ─── loading ───────────────────────────────────────────────────────────────────

def _git_blob_id(data):
    """The id git gives these bytes as a blob: sha1 over a `blob <len>\\0` header."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _load_module(sha8):
    """The vendored Python base (capture and verify modes only), checked against its
    blob id and imported under the `shared` package so its relative imports resolve."""
    name, blob_id = _VENDORED[sha8]
    path = _VENDORED_DIR / name
    if not path.is_file():
        pytest.fail("vendored base %s is not in the tree (%s): `git show %s` restores it for "
                    "a capture or verify run" % (sha8, path, blob_id))
    got = _git_blob_id(path.read_bytes())
    if got != blob_id:
        pytest.fail("vendored fixture %s git blob id mismatch: got %s, pinned %s"
                    % (name, got, blob_id))
    spec = importlib.util.spec_from_file_location("shared._merge_guard_baseline_%s" % sha8, path)
    if spec is None or spec.loader is None:
        pytest.fail("vendored fixture spec unloadable: %s" % path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "shared"
    spec.loader.exec_module(module)
    return module


def _load_table(sha8):
    """The committed decision table, its body checked against the header's sha256."""
    path = _DECISIONS_DIR / ("base_%s.jsonl" % sha8)
    if not path.is_file():
        pytest.fail("decision table missing for %s (%s): run %s" % (sha8, path, _CAPTURE_TOOL))
    header_line, _, body = path.read_text(encoding="utf-8").partition("\n")
    header = json.loads(header_line)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != header["sha256"]:
        pytest.fail("decision table %s drifted: body sha256 %s, header %s. Re-capture with %s "
                    "or restore the file" % (sha8, digest, header["sha256"], _CAPTURE_TOOL))
    if header["blob_id"] != _VENDORED[sha8][1]:
        pytest.fail("decision table %s was captured from blob %s, the loader pins %s"
                    % (sha8, header["blob_id"], _VENDORED[sha8][1]))
    attrs, calls = {}, {}
    for line in body.splitlines():
        row = json.loads(line)
        if "attr" in row:
            name = row.pop("attr")
            attrs[name] = row
        else:
            calls[json.dumps(row["call"], sort_keys=True, ensure_ascii=False)] = row["result"]
    return {"header": header, "attrs": attrs, "calls": calls}


def load_vendored(commit_sha8):
    """The base at `commit_sha8` (cached per SHA), as the certs have always called it:
    `load_vendored("c5e9b324").is_dangerous_command(cmd)`. Fails loudly, never skips:
    an unknown SHA, a missing or drifted table, or a row the table does not hold."""
    if commit_sha8 in _cached_vendored:
        return _cached_vendored[commit_sha8]
    if commit_sha8 not in _VENDORED:
        pytest.fail("no decision table for commit %s: add its _VENDORED row and capture it "
                    "with %s" % (commit_sha8, _CAPTURE_TOOL))
    if _CAPTURE:
        base = _RecordingProxy(commit_sha8, _load_module(commit_sha8))
    elif _VERIFY:
        base = _RecordingProxy(commit_sha8, _load_module(commit_sha8), _load_table(commit_sha8))
    else:
        base = _TableProxy(commit_sha8, _load_table(commit_sha8))
    _cached_vendored[commit_sha8] = base
    return base


# ─── the three named bases and their discriminator rows ────────────────────────
# Danger literals are assembled at runtime so this file carries no raw destructive
# literal and stays inert to the live guard.

_DISCRIMINATOR_OVER_BLOCK = "git log --grep '" + "gh " + "pr " + "merge 5 --admin" + "'"
_DISCRIMINATOR_BITE = (
    "gh pr close 5 --comment 'weighed "
    + "gh "
    + "pr "
    + "merge 5 --admin but closing instead'"
)
_DISCRIMINATOR_1148 = (
    'git commit -m "note: ' + "gh " + "pr " + 'merge 5 --admin later" '
    "# reviewed, do not pipe | sh"
)


def load_baseline():
    """The b4041ccf base (pre OBS-A→I; the over-block-cluster certs' base). The base
    MUST still exhibit the three bugs this arc fixes; a table captured from post-fix
    bytes fails here."""
    module = load_vendored("b4041ccf")
    if module.is_dangerous_command(_DISCRIMINATOR_OVER_BLOCK) is not True:
        pytest.fail(
            "vendored baseline does not exhibit the #1181 read-verb over-block — "
            "post-fix bytes were vendored"
        )
    if module.detect_command_operation_type(_DISCRIMINATOR_BITE) != "merge":
        pytest.fail(
            "vendored baseline does not exhibit the #1155 cross-auth recognition "
            "bug — post-fix bytes were vendored"
        )
    if (
        module._has_pipe_to_shell(module._executed_surface_view(_DISCRIMINATOR_1148))
        is not True
    ):
        pytest.fail(
            "vendored baseline does not exhibit the #1148 comment-survives-view "
            "bug — post-fix bytes were vendored"
        )
    return module


_DISCRIMINATOR_1134_UNDERBLOCK = "cd /repo && " + "git " + "push " + "origin --delete feature"
_DISCRIMINATOR_1134_CLOSE_ASYM = "cd /repo && " + "gh " + "pr " + "close 5 -d"


def load_baseline_172a77dd():
    """The 172a77dd base (post OBS-A→I / pre #1134; the three-dimension cert's base).
    Discriminators: `cd … && git push origin --delete feature` runs UNGATED, and
    `cd … && gh pr close 5 -d` is CLASSIFIED yet UNGATED (the pre-fix mint==read
    asymmetry on the close family)."""
    module = load_vendored("172a77dd")
    if module.is_dangerous_command(_DISCRIMINATOR_1134_UNDERBLOCK) is not False:
        pytest.fail(
            "vendored 172a77dd baseline does not exhibit the #1134 non-first-leg "
            "under-block (delete-in-leg-2 already gates) — post-fix bytes were vendored"
        )
    if module.detect_command_operation_type(_DISCRIMINATOR_1134_CLOSE_ASYM) != "close":
        pytest.fail(
            "vendored 172a77dd baseline does not classify the leg-2 close form — the "
            "detect/gate asymmetry the cert pins is absent; wrong bytes were vendored"
        )
    if module.is_dangerous_command(_DISCRIMINATOR_1134_CLOSE_ASYM) is not False:
        pytest.fail(
            "vendored 172a77dd baseline already GATES the leg-2 close form — the pre-fix "
            "mint==read asymmetry is absent; post-fix bytes were vendored"
        )
    return module


_MINT_KEYS_1203 = (
    "pr_number", "branch", "branch_set", "target_ref", "push_set",
    "force_push_set", "force_push_implicit", "merge_implicit",
    "mass_target", "protected_branch",
)
_DISCRIMINATOR_1203_FP = "git " + "push " + "--force"           # SET A implicit force-push
_DISCRIMINATOR_1203_MERGE = "gh " + "pr " + "merge"             # bare gh pr merge
_DISCRIMINATOR_1203_INERT = "gh " + "pr " + "merge --help"      # inert help over-block


def load_baseline_5017d1f2():
    """The v4.6.10 (5017d1f2) base, the PARENT of the #1203 commits. Discriminators:
    `git push --force` and bare `gh pr merge` are DANGEROUS yet bind no mint key
    (gated-but-unmintable), and `gh pr merge --help` is DANGEROUS (no inert-help
    recognizer yet)."""
    module = load_vendored("5017d1f2")

    def _binds_no_mint_key(cmd):
        ctx = module.extract_command_context(cmd)
        return not any(ctx.get(k) for k in _MINT_KEYS_1203)

    if not (
        module.is_dangerous_command(_DISCRIMINATOR_1203_FP) is True
        and _binds_no_mint_key(_DISCRIMINATOR_1203_FP)
    ):
        pytest.fail(
            "vendored 5017d1f2 baseline does not exhibit the implicit force-push "
            "gated-but-unmintable over-block — post-fix bytes were vendored"
        )
    if not (
        module.is_dangerous_command(_DISCRIMINATOR_1203_MERGE) is True
        and _binds_no_mint_key(_DISCRIMINATOR_1203_MERGE)
    ):
        pytest.fail(
            "vendored 5017d1f2 baseline does not exhibit the bare-merge "
            "gated-but-unmintable over-block — post-fix bytes were vendored"
        )
    if module.is_dangerous_command(_DISCRIMINATOR_1203_INERT) is not True:
        pytest.fail(
            "vendored 5017d1f2 baseline does not gate the inert `gh pr merge --help` "
            "form — the inert-help recognizer is already present; post-fix bytes were "
            "vendored"
        )
    return module
