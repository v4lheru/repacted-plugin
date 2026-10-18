#!/usr/bin/env python3
"""
Location: pact-plugin/tests/perf/hook_latency.py
Summary: The hook latency budget. Runs hooks/dispatch.py on a fixed synthetic workload (the
    hooks.json entry the host would pick for each case, in a throwaway HOME, config dir and
    project), reads each check's handler time from the hook-latency.ndjson the dispatcher
    writes, and fails when a check's p95 or a case's process wall p95 passes its budget.
Used by: the hook-latency job of .github/workflows/tests.yml (ubuntu, Python 3.14), and by
    hand for the README's number. Run as a script, not under pytest: a long pytest process
    carries a large heap whose full GC passes (40-90 ms) land inside timing windows.

Usage: python3 tests/perf/hook_latency.py [--runs N] [--baseline PATH] [--record] [--json]
Exit codes: 0 within budget (or baseline recorded); 1 over budget on both trials;
    2 usage error or unreadable baseline; 3 a run wrote no latency line (the record is broken).

What is measured, per case: the process wall from spawn to exit (what the user waits for,
interpreter start included) and, from the dispatcher's own line, each check's handler ms.

Budget, made not to flake on runners of unequal speed:
  - CALIBRATION: before each trial, the median time of a fixed pure-Python loop. Every time
    is divided by (calibration now / calibration in the baseline), so a runner twice as slow
    as the recording machine doubles nothing.
  - CHECK BUDGET (the gate): a check's normalised p95 must stay within
    max(CHECK_FACTOR x baseline p95, baseline p95 + CHECK_SLACK_MS); a check the baseline
    lacks gets NEW_CHECK_MS.
  - WALL CEILING (loose): a case's normalised process-wall p95 within
    max(WALL_FACTOR x baseline p95, baseline p95 + WALL_SLACK_MS). Interpreter start varies
    2-5x across runners, so this catches only gross regressions.
  - TWO TRIALS: a second trial runs only after a miss, and only a budget missed in both fails.
--record writes the baseline from one trial on this machine (commit it with the change that
moved the numbers).
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PLUGIN_ROOT / "hooks"
DEFAULT_BASELINE = Path(__file__).resolve().parent / "hook_latency_baseline.json"
CHECK_FACTOR, CHECK_SLACK_MS, NEW_CHECK_MS = 3.0, 25.0, 50.0
WALL_FACTOR, WALL_SLACK_MS = 5.0, 250.0
WARMUP_RUNS = 2
RUN_TIMEOUT_S = 120
SESSION, WARM_SESSION = "latency-run", "latency-warm"

# (case id, event, tool or None, payload fields). {proj} becomes the throwaway project dir.
CASES = (
    ("pre-bash", "PreToolUse", "Bash", {"tool_input": {"command": "git status"}}),
    ("pre-read", "PreToolUse", "Read", {"tool_input": {"file_path": "{proj}/README.md"}}),
    ("pre-edit", "PreToolUse", "Edit", {"tool_input": {"file_path": "{proj}/a.py",
                                                       "old_string": "a = 1", "new_string": "a = 2"}}),
    ("pre-write", "PreToolUse", "Write", {"tool_input": {"file_path": "{proj}/b.py", "content": "b = 1\n"}}),
    ("pre-taskupdate", "PreToolUse", "TaskUpdate", {"tool_input": {"taskId": "1", "status": "in_progress"}}),
    ("post-bash", "PostToolUse", "Bash", {"tool_input": {"command": "git status"},
                                          "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False}}),
    ("post-edit", "PostToolUse", "Edit", {"tool_input": {"file_path": "{proj}/a.py"}, "tool_response": {}}),
    ("prompt", "UserPromptSubmit", None, {"prompt": "hello"}),
    ("stop", "Stop", None, {"stop_hook_active": False}),
)


def reference_ms() -> float:
    """One pass of a fixed pure-Python loop, in ms: the runner's speed, for normalising."""
    started = time.perf_counter()
    total = 0
    for index in range(200_000):
        total += (index * index) % 7
    json.loads(json.dumps({str(i): [i, str(i)] for i in range(2_000)}))
    return (time.perf_counter() - started) * 1000.0 + (total & 0)


def calibrate() -> float:
    gc.collect()
    return statistics.median(reference_ms() for _ in range(7))


def _latency():
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    from shared import hook_latency
    return hook_latency


def entry_argv(event: str, tool):
    """The arguments of the one synchronous dispatcher entry the host matches, or None."""
    _latency()
    import dispatch_registry as registry

    config = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    hits = [hook["command"].split('dispatch.py" ', 1)[1].split()
            for entry in config["hooks"].get(event, []) for hook in entry["hooks"]
            if not hook.get("async") and "dispatch.py" in hook["command"]
            and (tool is None or registry.host_matches(entry.get("matcher"), tool))]
    return hits[0] if hits else None


def sandbox(root: Path) -> dict:
    """A throwaway HOME, config dir and project; the env every run gets."""
    project = root / "proj"
    project.mkdir(parents=True)
    (project / "README.md").write_text("readme\n", encoding="utf-8")
    (project / "a.py").write_text("a = 1\n", encoding="utf-8")
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
            "CLAUDE_CONFIG_DIR": str(root / ".claude"), "CLAUDE_PROJECT_DIR": str(project),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT), "REPACT_DOCTOR_NO_REFRESH": "1"}


def session_dir(env: dict, session: str) -> Path:
    """The session dir the dispatcher resolves for `session` under env, created."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from shared.pact_context import reconstruct_session_dir as r; "
            "print(r(sys.argv[2], sys.argv[3]))")
    done = subprocess.run([sys.executable, "-c", code, str(HOOKS_DIR), env["CLAUDE_PROJECT_DIR"], session],
                          capture_output=True, text=True, env=env, check=True)
    folder = Path(done.stdout.strip())
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def stdin_for(event: str, tool, fields: dict, env: dict, session: str) -> bytes:
    payload = {"session_id": session, "cwd": env["CLAUDE_PROJECT_DIR"], "hook_event_name": event,
               **({"tool_name": tool} if tool else {}), **fields}
    return json.dumps(payload).replace("{proj}", env["CLAUDE_PROJECT_DIR"]).encode("utf-8")


def run_once(argv: list, stdin: bytes, env: dict) -> float:
    started = time.perf_counter()
    subprocess.run([sys.executable, str(HOOKS_DIR / "dispatch.py"), *argv], input=stdin,
                   capture_output=True, env=env, cwd=env["CLAUDE_PROJECT_DIR"], timeout=RUN_TIMEOUT_S)
    return (time.perf_counter() - started) * 1000.0


def trial(runs: int) -> dict:
    """One pass over every case: calibration, process walls, and per-check handler times."""
    latency = _latency()
    calibration = calibrate()
    with tempfile.TemporaryDirectory(prefix="hook-latency-") as tmp:
        env = sandbox(Path(tmp))
        session_dir(env, WARM_SESSION)
        folder = session_dir(env, SESSION)
        walls, lines_by_case = {}, {}
        for case, event, tool, fields in CASES:
            argv = entry_argv(event, tool)
            if argv is None:
                continue
            for _ in range(WARMUP_RUNS):
                run_once(argv, stdin_for(event, tool, fields, env, WARM_SESSION), env)
            before = len(latency.read_rows(folder))
            gc.collect()
            stdin = stdin_for(event, tool, fields, env, SESSION)
            walls[case] = [run_once(argv, stdin, env) for _ in range(runs)]
            lines_by_case[case] = latency.read_rows(folder)[before:]
    checks: dict = {}
    for case, rows in lines_by_case.items():
        for row in rows:
            for name, value in (row.get("checks") or {}).items():
                checks.setdefault(f"{row.get('event')}:{name}", []).append(float(value))
    missing = sorted(case for case, rows in lines_by_case.items() if len(rows) < runs)
    return {"calibration_ms": round(calibration, 2), "missing": missing,
            "walls": {case: summary(values, latency) for case, values in walls.items()},
            "checks": {name: summary(values, latency) for name, values in sorted(checks.items())}}


def summary(values: list, latency) -> dict:
    """runs, p50 and p95 of a list of ms."""
    return {"runs": len(values), "p50_ms": round(latency.percentile(values, 50), 2),
            "p95_ms": round(latency.percentile(values, 95), 2)}


def over_budget(result: dict, baseline: dict) -> list:
    """(kind, name, normalised p95, budget) for every budget this trial missed."""
    scale = result["calibration_ms"] / baseline["calibration_ms"]
    misses = []
    for name, row in result["checks"].items():
        base = baseline["checks"].get(name, {}).get("p95_ms")
        budget = NEW_CHECK_MS if base is None else max(CHECK_FACTOR * base, base + CHECK_SLACK_MS)
        if row["p95_ms"] / scale > budget:
            misses.append(("check", name, round(row["p95_ms"] / scale, 2), round(budget, 2)))
    for case, row in result["walls"].items():
        base = baseline["walls"].get(case, {}).get("p95_ms")
        if base is not None and row["p95_ms"] / scale > max(WALL_FACTOR * base, base + WALL_SLACK_MS):
            misses.append(("wall", case, round(row["p95_ms"] / scale, 2),
                           round(max(WALL_FACTOR * base, base + WALL_SLACK_MS), 2)))
    return misses


def render(result: dict, misses: list) -> list:
    lines = [f"calibration {result['calibration_ms']} ms; process wall per case (spawn to exit):"]
    for case, row in result["walls"].items():
        lines.append(f"  {case:<18} p50 {row['p50_ms']:>8.1f}  p95 {row['p95_ms']:>8.1f} ms  ({row['runs']} runs)")
    slowest = sorted(result["checks"].items(), key=lambda kv: -kv[1]["p95_ms"])[:8]
    lines.append("slowest checks by handler p95:")
    lines += [f"  {name:<44} p50 {row['p50_ms']:>7.2f}  p95 {row['p95_ms']:>7.2f} ms" for name, row in slowest]
    lines += [f"OVER BUDGET {kind} {name}: {value} ms normalised > {budget} ms" for kind, name, value, budget in misses]
    return lines


def load_baseline(path: Path):
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(baseline, dict) and isinstance(baseline.get("calibration_ms"), (int, float)) \
        and baseline["calibration_ms"] > 0 and isinstance(baseline.get("checks"), dict) \
        and isinstance(baseline.get("walls"), dict)
    return baseline if ok else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    parser.add_argument("--runs", type=int, default=30, help="timed runs per case (default 30)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--record", action="store_true", help="write the baseline from one trial")
    parser.add_argument("--json", action="store_true", help="machine output")
    args = parser.parse_args(argv)
    if args.runs < 5:
        parser.error("--runs needs at least 5 runs for a p95")
    first = trial(args.runs)
    if first["missing"]:
        print(f"hook latency: no latency line for {', '.join(first['missing'])}", file=sys.stderr)
        return 3
    if args.record:
        baseline = {"v": 1, "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "python": platform.python_version(), "machine": f"{platform.system()} {platform.machine()}",
                    "runs": args.runs, **{k: first[k] for k in ("calibration_ms", "walls", "checks")}}
        args.baseline.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("\n".join(render(first, [])) + f"\nbaseline written: {args.baseline}")
        return 0
    baseline = load_baseline(args.baseline)
    if baseline is None:
        print(f"hook latency: no usable baseline at {args.baseline} (run with --record)", file=sys.stderr)
        return 2
    misses = over_budget(first, baseline)
    trials = [first]
    if misses:
        missed_first = {(kind, name) for kind, name, _, _ in misses}
        trials.append(trial(args.runs))
        misses = [miss for miss in over_budget(trials[-1], baseline) if miss[:2] in missed_first]
    if args.json:
        print(json.dumps({"trials": trials, "over_budget": misses}, indent=1, sort_keys=True))
    else:
        print("\n".join(render(trials[-1], misses)) + f"\ntrials: {len(trials)}; "
              + ("within budget" if not misses else f"{len(misses)} over budget in both trials"))
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
