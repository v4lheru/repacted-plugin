"""
Location: pact-plugin/tests/tools/live_capture.py
Summary: Records the LIVE classifiers' decisions over the command universe the lane's
         suites feed them, into tests/fixtures/shell/decisions/live.jsonl. Installed by
         tests/conftest.py when PACT_CAPTURE_LIVE=1: the pure, string-in DECISION
         functions of shared.merge_guard_common and env_guard (verdicts, operation
         types, contexts, extracted targets: not the intermediate views, which the
         suites pin directly) are wrapped in place before any test module binds them;
         every outermost call with scalar arguments under ARG_CAP characters is
         recorded (nested calls from inside a recorded call are not: the table holds
         decisions, not traces), and the table is written at exit.
         Format, one JSON array per line after the header:
           [command, {"merge.is_dangerous_command": true, ...}]   one-string-argument calls
           ["env.dumps_environment", [args], result]             every other call
         tests/test_shell_decision_tables.py replays the table against the live module
         on every run: a decision that changes without a declared flip fails.
Used by: tests/conftest.py (install), the maintainer at a milestone:
           PACT_CAPTURE_LIVE=1 python3 -m pytest -q tests/test_merge_guard*.py \\
               tests/test_env_guard*.py tests/test_shell_words.py
         After a capture, `python3 tests/tools/live_capture.py wash` drops the rows a
         clean process does not reproduce (state a test had monkeypatched), the rows
         naming a scratch path or a personal word, and the rows the purity scanner
         would flag. A row the commit-time secret detector flags never reaches the
         file at all (screened at capture and at wash): a merge scans every commit's
         own patch, so a secret-shaped row refuses the merge for as long as any commit
         carried it.
Map: RECORDED, ARG_CAP, install, rows_of, secret_row, _write, _replay_keys, _screen, wash.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
TABLE = PLUGIN / "tests" / "fixtures" / "shell" / "decisions" / "live.jsonl"

# Rows naming a test's temporary directory are washed out: machine-specific commands.
SCRATCH_PATH_MARKS = ("/pytest-of-", "/var/folders/", "/tmp/tmp")

# A call whose arguments exceed this many characters is not recorded (the perf suite
# feeds 100 KB-1 MB commands; those stay in tests/slow/).
ARG_CAP = 300

# module alias -> (import name, decision functions whose result is a pure function of
# their string arguments). State-dependent functions (cwd, git, disk) are not here.
RECORDED = {
    "merge": ("shared.merge_guard_common", (
        "is_dangerous_command", "detect_command_operation_type", "extract_command_context",
        "is_compound_destructive_command", "unmintable_danger", "flag_flood",
        "implicit_push_to_main", "push_option_merge", "extract_privileged_flags",
        "_single_destructive_leg", "_single_detectable_leg", "_has_pipe_to_shell",
        "_has_process_substitution_to_shell", "_has_eval_or_source", "_has_eval_with_heredoc",
        "_extract_pr_number", "_extract_close_target", "_extract_force_push_target_ref",
    )),
    "env": ("env_guard", (
        "is_protected_name", "mentions_class", "is_template_name", "dumps_environment",
        "normalize_env_get", "expand_literal_variables",
    )),
}

_by_command: dict[str, dict[str, object]] = defaultdict(dict)
_other: dict[str, object] = {}
_depth = 0
_skipped = 0


def _scalar(value) -> bool:
    return value is None or isinstance(value, (bool, int, str))


def _recordable(args) -> bool:
    if not all(_scalar(a) or (isinstance(a, list) and all(_scalar(x) for x in a)) for a in args):
        return False
    return sum(len(a) if isinstance(a, str) else len(json.dumps(a)) for a in args) <= ARG_CAP


def _store(fn: str, args, outcome) -> None:
    if len(args) == 1 and isinstance(args[0], str):
        _by_command[args[0]][fn] = outcome
    else:
        _other[json.dumps([fn, list(args)], ensure_ascii=False)] = outcome


def _wrap(alias, name, fn, encode):
    label = f"{alias}.{name}"

    def recorded(*args, **kwargs):
        global _depth, _skipped
        if _depth or kwargs or not _recordable(args):
            return fn(*args, **kwargs)
        _depth += 1
        try:
            try:
                result = fn(*args)
            except Exception as error:  # noqa: BLE001 — the raise is the recorded decision
                _store(label, args, encode(error))
                raise
            try:
                outcome = encode(result, f"{label} result")
            except TypeError:
                _skipped += 1
                return result
            _store(label, args, outcome)
            return result
        finally:
            _depth -= 1
    recorded.__name__ = fn.__name__
    recorded.__doc__ = fn.__doc__
    recorded.__wrapped__ = fn
    return recorded


def install() -> None:
    """Wrap the recorded functions in place and register the writer."""
    import importlib

    from merge_guard_baseline_loader import encode  # tests/ is on sys.path

    for alias, (module_name, names) in RECORDED.items():
        module = importlib.import_module(module_name)
        for name in names:
            setattr(module, name, _wrap(alias, name, getattr(module, name), encode))
    atexit.register(_write)


def rows_of(text: str):
    """Yield (fn, args, result) from a table body (the lines after the header)."""
    for line in text.splitlines():
        row = json.loads(line)
        if len(row) == 3:
            yield row[0], row[1], row[2]
        else:
            for fn, result in row[1].items():
                yield fn, [row[0]], result


_secret_kind = None


def secret_row(line: str) -> bool:
    """True when the commit-time secret detector (hooks/shared/scrub.secret_kind, the one
    git_commit_check runs over every added line) flags this table line. The same function,
    imported, so the table and the commit check never disagree on what a secret line is."""
    global _secret_kind
    if _secret_kind is None:
        sys.path.insert(0, str(PLUGIN / "hooks"))
        from shared.scrub import secret_kind
        _secret_kind = secret_kind
    return bool(_secret_kind(line))


def _counts(rows) -> tuple[int, int]:
    """(rows, commands) of a list of decoded table rows."""
    return (sum(len(r[1]) if len(r) == 2 else 1 for r in rows),
            sum(1 for r in rows if len(r) == 2))


def _write() -> None:
    lines = [json.dumps([cmd, dict(sorted(fns.items()))], ensure_ascii=False,
                        separators=(",", ":"))
             for cmd, fns in sorted(_by_command.items())]
    lines += [json.dumps([*json.loads(k), v], ensure_ascii=False, separators=(",", ":"))
              for k, v in sorted(_other.items())]
    kept = [line for line in lines if not secret_row(line)]
    body = "\n".join(kept) + "\n"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PLUGIN,
                            capture_output=True, text=True, check=False).stdout.strip()
    rows, commands = _counts([json.loads(line) for line in kept])
    header = {"captured_from": commit or "unknown", "commands": commands,
              "rows": rows, "skipped_unencodable": _skipped,
              "screened_secret_shaped": len(lines) - len(kept),
              "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.write_text(json.dumps(header, sort_keys=True) + "\n" + body, encoding="utf-8")
    print(f"live decisions: {rows} rows over {commands} commands "
          f"({_skipped} unencodable skipped, {len(lines) - len(kept)} secret-shaped screened)"
          f" -> {TABLE}", file=sys.stderr)


# ─── wash: keep only the rows a clean process reproduces ─────────────────────────
# A test may monkeypatch state (the invocation cwd, git remotes, a flag set) around a
# call, and the capture records the decision under that state. Replaying the table in
# two fresh processes with different working directories finds every such row; the
# wash drops them (their own tests keep them) and records how many in the header.

def _replay_keys(body: str):
    """Keys of the rows whose live decision differs from the table, in THIS process."""
    import importlib

    sys.path.insert(0, str(PLUGIN / "tests"))
    sys.path.insert(0, str(PLUGIN / "hooks"))
    from merge_guard_baseline_loader import encode

    modules = {alias: importlib.import_module(name) for alias, (name, _) in RECORDED.items()}
    differing = []
    for fn, args, result in rows_of(body):
        alias, name = fn.split(".", 1)
        try:
            got = encode(getattr(modules[alias], name)(*args), fn)
        except Exception as error:  # noqa: BLE001 — a raise is a decision too
            got = encode(error)
        if got != result:
            differing.append(json.dumps([fn, args], ensure_ascii=False))
    return differing


def _screen():
    """(personal words, purity scanner) for the wash. Personal words: the purity scan's
    personal literals (tests/contract/_purity.py) plus PACT_WASH_WORDS (comma-separated),
    lower-cased. The scanner: a row the purity detectors would flag (an email-shaped
    host, a password-shaped value) never enters the committed table either, so the
    purity baseline cannot grow by a capture; the originating test still pins the
    decision."""
    sys.path.insert(0, str(PLUGIN))
    from tests.contract import _purity

    words = [w.strip().lower() for w in os.environ.get("PACT_WASH_WORDS", "").split(",") if w.strip()]
    try:
        ctx = _purity.context_from_env()
    except _purity.FailClosed as error:
        print(f"wash: purity context unavailable ({error}); only PACT_WASH_WORDS screened",
              file=sys.stderr)
        return words, (lambda line: False)
    words += [w.lower() for w in _purity._personal_literals(ctx) if isinstance(w, str) and len(w) >= 3]
    return words, (lambda line: bool(_purity.scan_file(TABLE.name, line, ctx)))


def wash() -> int:
    import tempfile

    header_line, body = TABLE.read_text(encoding="utf-8").split("\n", 1)
    header = json.loads(header_line)
    unstable: set[str] = set()
    with tempfile.TemporaryDirectory() as scratch:
        for cwd in (str(PLUGIN), scratch):
            out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "replay"],
                                 cwd=cwd, capture_output=True, text=True, check=True)
            unstable |= set(json.loads(out.stdout))
    kept = []
    scratch_rows = secret_rows = 0
    personal, flagged = _screen()
    for line in body.splitlines():
        row = json.loads(line)
        if secret_row(line):
            secret_rows += 1        # the commit check would refuse the line: never a universe row
            continue
        if any(mark in line for mark in SCRATCH_PATH_MARKS) or any(
                word in line.lower() for word in personal) or flagged(line):
            scratch_rows += 1       # a tmp path, a personal word or a purity hit: never a universe row
            continue
        if len(row) == 3:
            if json.dumps([row[0], row[1]], ensure_ascii=False) not in unstable:
                kept.append(row)
            continue
        fns = {fn: r for fn, r in row[1].items()
               if json.dumps([fn, [row[0]]], ensure_ascii=False) not in unstable}
        if fns:
            kept.append([row[0], fns])
    new_body = "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in kept) + "\n"
    header["washed_state_dependent"] = header.get("washed_state_dependent", 0) + len(unstable)
    header["washed_scratch_or_personal"] = header.get("washed_scratch_paths", 0) + scratch_rows
    header["screened_secret_shaped"] = header.get("screened_secret_shaped", 0) + secret_rows
    header["rows"], header["commands"] = _counts(kept)
    header["sha256"] = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    TABLE.write_text(json.dumps(header, sort_keys=True) + "\n" + new_body, encoding="utf-8")
    print(f"washed {len(unstable)} state-dependent rows, {scratch_rows} scratch-path or personal "
          f"rows and {secret_rows} secret-shaped rows; {header['rows']} rows remain", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["replay"]:
        print(json.dumps(_replay_keys(TABLE.read_text(encoding="utf-8").split("\n", 1)[1])))
    elif sys.argv[1:] == ["wash"]:
        sys.exit(wash())
    else:
        print("usage: live_capture.py wash | replay", file=sys.stderr)
        sys.exit(64)
