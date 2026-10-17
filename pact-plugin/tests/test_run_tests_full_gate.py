"""scripts/run-tests --full is the merge gate: it runs the plugin suite, reports its count
and reads the verdict against the known-failures baseline.

The child is a stub "python" that records its cwd, its last argument and the manifest it was
given, prints a tcount-style counts line and exits with the code the test chose for that suite,
so no real suite runs. Every test points REPACTED_LOCK_DIR at tmp_path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PLUGIN_ROOT.parent / "scripts" / "run-tests"
REPO = PLUGIN_ROOT.parent.parent
LOCKF = "/usr/bin/lockf"
MANIFEST_REL = Path("docs") / "architecture" / "source-manifest.csv"

pytestmark = pytest.mark.skipif(not Path(LOCKF).exists(), reason="macOS lockf(1) not present")

# Invoked as `stub <tcount> -- stub -m pytest -q -p no:cacheprovider [args]`.
STUB = """#!/bin/sh
# run-tests asks its interpreter `-c "import site; ..."` for the user base (user_base); a
# python answers that without running a suite, so the stub does too, recording nothing.
case "$1" in -c) exit 0 ;; esac
for last; do :; done
here=$(pwd -P)
deny="$HOME/.config/repacted/scrub-denylist.txt"
if [ -L "$deny" ]; then deny=$(readlink "$deny"); elif [ -e "$deny" ]; then deny="<file>"; else deny="<none>"; fi
printf '%s|%s|%s|%s|%s\\n' "$here" "$last" "${REPACT_SOURCE_MANIFEST-<unset>}" "$deny" \
  "${REPACT_PURITY_DENYLIST_DIR-<unset>}" >> "$CALLS_FILE"
case "$last|$here" in
  tools/public-export/tests\|*) code="${EXPORT_EXIT:-0}"; fails=""; extra=0; n=7 ;;
  *\|*/pact-plugin) code="${PLUGIN_EXIT:-0}"; fails="$PLUGIN_FAILS"; extra="${PLUGIN_EXTRA_FAILED:-0}"; n=3
                 [ -n "$PLUGIN_SLEEP" ] && sleep "$PLUGIN_SLEEP" ;;
  *) code="${ROOT_EXIT:-0}"; fails="$ROOT_FAILS"; extra=0; n=5 ;;
esac
failed=0
for id in $(printf '%s' "$fails" | tr ',' ' '); do failed=$((failed + 1)); done
[ "$failed" = 0 ] && [ "$code" != 0 ] && failed=1
failed=$((failed + extra)); [ "$failed" != 0 ] && [ "$code" = 0 ] && code=1
echo "pytest: collected $n · passed $((n - failed)) · failed $failed · errors 0 · skipped 0 · exit $code"
for id in $(printf '%s' "$fails" | tr ',' ' '); do echo "FAILED $id"; done
exit "$code"
"""


@pytest.fixture
def rig(tmp_path):
    stub = tmp_path / "stub-python"
    stub.write_text(STUB)
    stub.chmod(0o755)
    locks = tmp_path / "locks"
    locks.mkdir()
    calls = tmp_path / "calls.txt"
    manifest = tmp_path / "given-manifest.csv"
    manifest.write_text("dest_kind\n")
    baseline = tmp_path / "known-failures.txt"
    baseline.write_text("# no known failures\n")
    env = {**os.environ, "REPACTED_LOCK_DIR": str(locks), "CALLS_FILE": str(calls),
           "REPACT_SOURCE_MANIFEST": str(manifest), "REPACTED_KNOWN_FAILURES": str(baseline),
           "REPACTED_DENYLIST_DIR": str(tmp_path / "no-denylists")}
    for key in ("PLUGIN_EXIT", "ROOT_EXIT", "EXPORT_EXIT", "PLUGIN_SLEEP", "PLUGIN_FAILS", "ROOT_FAILS",
                "PLUGIN_EXTRA_FAILED", "REPACT_PURITY_DENYLIST_DIR"):
        env.pop(key, None)
    return {"stub": stub, "calls": calls, "env": env, "tmp": tmp_path, "manifest": manifest,
            "baseline": baseline}


def run(rig, *extra, script=WRAPPER, **env_extra):
    env = {**rig["env"], **env_extra}
    return subprocess.run([sys.executable, str(script), "--full", "--pkg", "t", "--wait", "30",
                           "--python", str(rig["stub"]), *extra],
                          env=env, capture_output=True, text=True, timeout=120)


def calls(rig) -> list[tuple[Path, str, str]]:
    """(cwd, last argument, manifest) per stub run; denylists() gives the fourth field."""
    return [(Path(row[0]), row[1], row[2]) for row in _rows(rig)]


def denylists(rig) -> list[str]:
    return [row[3] for row in _rows(rig)]


def _rows(rig) -> list[list[str]]:
    if not rig["calls"].exists():
        return []
    return [line.split("|") for line in rig["calls"].read_text().splitlines()]




def test_full_with_pytest_args_runs_only_those_args(rig):
    """A narrowed --full (the suite-queue tests use this shape) runs exactly what it names."""
    result = run(rig, "--", "tests/contract")
    assert [(cwd, last) for cwd, last, _ in calls(rig)] == [(PLUGIN_ROOT.resolve(), "tests/contract")], \
        result.stdout + result.stderr




# --- amend_1 S1: the verdict reads against a versioned known-failures baseline ------------------

P_KNOWN = "tests/test_p.py::test_known"
R_KNOWN = "tests/user_layer/test_r.py::test_known[x]"


def baseline(rig, *entries):
    rig["baseline"].write_text("# suite node-id # reason\n" + "".join(
        f"{suite} {node} # {reason}\n" for suite, node, reason in entries))


def verdict(result) -> str:
    return "\n".join(line for line in result.stdout.splitlines() if line.startswith("run-tests: gate"))


def test_a_known_plugin_failure_alone_passes_the_gate(rig):
    baseline(rig, ("plugin", P_KNOWN, "flaky on this Mac"))
    result = run(rig, PLUGIN_FAILS=P_KNOWN)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed (1 known)" in verdict(result)


def test_a_new_failure_fails_the_gate_and_is_named(rig):
    baseline(rig, ("plugin", P_KNOWN, "flaky"))
    new = "tests/test_p.py::test_new"
    result = run(rig, PLUGIN_FAILS=f"{P_KNOWN},{new}")
    assert result.returncode != 0
    assert "NEW failures" in verdict(result) and new in verdict(result)


def test_a_known_plugin_failure_that_now_passes_is_baseline_drift(rig):
    baseline(rig, ("plugin", P_KNOWN, "flaky"))
    result = run(rig)
    assert result.returncode != 0
    assert "baseline drift" in verdict(result) and P_KNOWN in verdict(result)




def test_a_truncated_failure_list_cannot_pass(rig):
    """tcount lists at most 20 ids: more failures than listed ids can hide a new one."""
    baseline(rig, ("plugin", P_KNOWN, "flaky"))
    result = run(rig, PLUGIN_FAILS=P_KNOWN, PLUGIN_EXTRA_FAILED="1")
    assert result.returncode != 0
    assert "not listed" in verdict(result)


def test_a_missing_baseline_fails_closed(rig):
    rig["baseline"].unlink()
    result = run(rig)
    assert result.returncode != 0 and "baseline" in verdict(result)


def test_the_shipped_baseline_is_well_formed_and_holds_no_manifest_rows():
    """An empty baseline (no known failures) is the desired state; any rows present must be well formed."""
    path = PLUGIN_ROOT.parent / "scripts" / "known-failures.txt"
    entries = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    for line in entries:
        head, _, reason = line.partition(" # ")
        suite, _, node = head.partition(" ")
        assert suite in {"plugin"} and "::" in node and reason.strip(), line
    assert not [line for line in entries if "test_manifest_coverage" in line], \
        "the MEMORY-inbox reds are fixed on #177, never baselined"


# --- amend_1 S2: the root suite reads the real scrub denylists, read-only, named on stderr -------

def load_wrapper():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader("run_tests_wrapper", SourceFileLoader("run_tests_wrapper", str(WRAPPER)))
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    return wrapper


def test_an_optional_denylist_tells_the_purity_gate_to_run_without_one_only_when_absent(tmp_path, monkeypatch):
    """With DENYLIST_REQUIRED off, no denylist file means REPACT_PURITY_NO_DENYLIST=1; one file
    present means the gate is pointed at it, exactly as when it is required."""
    wrapper = load_wrapper()
    wrapper.DENYLIST_REQUIRED = False
    source = tmp_path / "denylists"
    source.mkdir()
    for key in ("REPACT_PURITY_NO_DENYLIST", "REPACT_PURITY_DENYLIST_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REPACTED_DENYLIST_DIR", str(source))
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "base"))
    env = wrapper.child_env(tmp_path / "home", through_tcount=False)
    assert env.get("REPACT_PURITY_NO_DENYLIST") == "1" and "REPACT_PURITY_DENYLIST_DIR" not in env
    (source / "scrub-denylist.txt").write_text("codename\n")
    env = wrapper.child_env(tmp_path / "home", through_tcount=False)
    assert env["REPACT_PURITY_DENYLIST_DIR"] == str(source) and "REPACT_PURITY_NO_DENYLIST" not in env




# --- amend_1 S3: a narrowed --full says it is not the merge gate --------------------------------

def test_a_narrowed_full_says_it_is_not_the_merge_gate(rig):
    result = run(rig, "--", "tests/contract")
    assert "narrowed --full: only the named tests run (not the merge gate)" in result.stdout + result.stderr


def test_the_merge_gate_does_not_claim_to_be_narrowed(rig):
    result = run(rig)
    assert "narrowed --full" not in result.stdout + result.stderr


# Writes a tcount row to the CHILD's ledger and prints tcount's counts and log lines.
LEDGER_STUB = """#!/bin/sh
# run-tests asks its interpreter `-c "import site; ..."` for the user base (user_base); a
# python answers that without running a suite, so the stub does too, recording nothing.
case "$1" in -c) exit 0 ;; esac
data="$CLAUDE_PLUGIN_DATA/runs"; mkdir -p "$data"
log="$data/2026-09-30T010000Z-$$-pytest.log"
printf '{"v":1,"ts":"2026-09-30T01:00:00+00:00","session_id":"s-1","runner":"pytest","collected":3,"passed":3,"failed":0,"errors":0,"skipped":0,"exit_code":0,"count_parsed":true,"source":"tcount","log_path":"%s","cwd":"%s"}\\n' "$log" "$(pwd -P)" >> "$data/ledger.jsonl"
echo "pytest: collected 3 · passed 3 · failed 0 · errors 0 · skipped 0 · exit 0"
echo "log: $log"
"""


def test_each_suite_run_reaches_the_live_ledger_and_the_child_keeps_its_scratch_one(rig):
    """e1baab9f/#2: full-gate counts sat only in $TMPDIR/repacted-test-runs, so a HANDOFF citing
    them read as MISMATCH against the live ledger. The wrapper copies each suite's row across."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("shared.runcounts", PLUGIN_ROOT / "hooks" / "shared" / "runcounts.py")
    runcounts = sys.modules.setdefault("shared.runcounts", importlib.util.module_from_spec(spec))
    if not hasattr(runcounts, "detect_runner"):
        spec.loader.exec_module(runcounts)
    detect_runner = runcounts.detect_runner

    rig["stub"].write_text(LEDGER_STUB)
    tmp = rig["tmp"]
    (tmp / "t").mkdir()
    env = {key: value for key, value in rig["env"].items() if key != "CLAUDE_PLUGIN_DATA"}
    env.update(TMPDIR=str(tmp / "t"), CLAUDE_CONFIG_DIR=str(tmp / "live"))
    result = subprocess.run([sys.executable, str(WRAPPER), "--full", "--pkg", "t", "--wait", "30",
                             "--python", str(rig["stub"])], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    live = tmp / "live" / "plugins" / "data" / "repacted-repacted-local" / "runs" / "ledger.jsonl"
    child = [json.loads(line) for line in (tmp / "t" / "repacted-test-runs" / "runs" / "ledger.jsonl").read_text().splitlines()]
    copied = [json.loads(line) for line in live.read_text().splitlines()]
    assert [row["log_path"] for row in copied] == [row["log_path"] for row in child] and len(copied) == 1
    assert all(row["source"] == "tcount" and row["passed"] == 3 for row in copied)
    assert detect_runner("scripts/run-tests --full 2>&1 | tail") == "tcount"   # the hook attributes it


# As LEDGER_STUB, and the log holds a pytest durations block; the stub records the flags it got.
SLOW_STUB = LEDGER_STUB.replace('echo "pytest: collected 3', """printf '%s\\n' "$*" >> "$CALLS_FILE"
here=$(basename "$(pwd -P)")
{ echo "..."; echo "=============== slowest durations ==============="
  echo "2.50s call     tests/test_b_$here.py::T::test_x[a b::c]"
  echo "0.40s setup    tests/test_a_$here.py::test_y"
  echo "1.00s call     tests/test_b_$here.py::test_z"
  echo "0.30s teardown tests/test_a_$here.py::test_y"
  echo ""; echo "(12 durations < 0.1s hidden.  Use -vv to show these durations.)"
  echo "3 passed in 4.20s"; } > "$log"
echo "pytest: collected 3""")


def test_full_prints_the_slowest_files_after_the_counts_and_ledgers_them(rig):
    """T4 (refactor step 19): --full asks pytest for every phase's wall, sums it per file from
    tcount's log, prints the slowest files after the counts lines and carries them in the
    suite's row of the live ledger."""
    import json
    rig["stub"].write_text(SLOW_STUB)
    tmp = rig["tmp"]
    (tmp / "t").mkdir()
    env = {key: value for key, value in rig["env"].items() if key != "CLAUDE_PLUGIN_DATA"}
    env.update(TMPDIR=str(tmp / "t"), CLAUDE_CONFIG_DIR=str(tmp / "live"))
    result = subprocess.run([sys.executable, str(WRAPPER), "--full", "--pkg", "t", "--wait", "30",
                             "--python", str(rig["stub"])], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all("--durations=0 --durations-min=0.1" in line for line in rig["calls"].read_text().splitlines())
    out = result.stdout.splitlines()
    counts = max(i for i, line in enumerate(out) if "collected 3" in line and line.startswith("run-tests: "))
    assert out[counts + 1].startswith("run-tests: plugin") and "slowest 2 test files" in out[counts + 1]
    assert out[counts + 2].split() == ["3.50", "tests/test_b_pact-plugin.py"]
    assert out[counts + 3].split() == ["0.70", "tests/test_a_pact-plugin.py"]
    live = tmp / "live" / "plugins" / "data" / "repacted-repacted-local" / "runs" / "ledger.jsonl"
    rows = [json.loads(line) for line in live.read_text().splitlines()]
    assert rows[0]["slowest"] == [["tests/test_b_pact-plugin.py", 3.5], ["tests/test_a_pact-plugin.py", 0.7]]
    assert len(rows) == 1


def test_slowest_files_reads_only_the_durations_block():
    """Adversarial: a duration-shaped line outside the block, a node id with `::` and blanks in
    its parameter, more files than the top and output with no block at all."""
    wrapper = load_wrapper()
    before = ["9.99s call     tests/test_noise.py::test_outside"]
    block = ["==== slowest durations ===="] + [f"{n}.00s call     tests/test_{n}.py::t[x ::y]" for n in range(1, 13)]
    ranked = wrapper.slowest_files(before + block + ["", "==== 12 passed in 80s ====", "5.00s call t.py::late"])
    assert ranked[0] == ["tests/test_12.py", 12.0] and len(ranked) == 10 and ranked[-1] == ["tests/test_3.py", 3.0]
    assert wrapper.slowest_files(["12 passed in 1.0s"]) == [] and wrapper.log_slowest(None) == []
