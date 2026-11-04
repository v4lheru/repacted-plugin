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
printf '%s|%s|%s|%s|%s|%s\\n' "$here" "$last" "${REPACT_SOURCE_MANIFEST-<unset>}" "$deny" \
  "${REPACT_PURITY_DENYLIST_DIR-<unset>}" "$*" >> "$CALLS_FILE"
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




# --- amend_1 S2: the root suite reads the real scrub denylists, read-only, named on stderr -------

def load_wrapper():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader("run_tests_wrapper", SourceFileLoader("run_tests_wrapper", str(WRAPPER)))
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    return wrapper




# --- amend_1 S3: a narrowed --full says it is not the merge gate --------------------------------


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


