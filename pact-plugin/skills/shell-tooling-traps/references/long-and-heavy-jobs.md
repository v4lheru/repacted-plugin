# Long and heavy jobs

Context: how to run work that outlives a turn (backfills, scrapes, crawls) and work that loads the
machine (mutation runs, repeated suites, benchmarks, vault or corpus scans). Read this before you
start anything expected to take more than about ten minutes, or anything that would use more than
two CPU workers.

## Canonical rules
1. By default heavy local compute is capped at 2 parallel workers and `nice -n 19`: `pytest -n 2`, `--maxWorkers=2`, `NW=2`. Six parallel pytest workers overheated a laptop.
2. tcount runs every command at niceness 19 and caps vitest, jest and dotnet at 2 workers (pytest -n above 2 becomes 2); REPACT_TCOUNT_NO_CAP=1 turns both off.
3. A job longer than a few minutes must run in its own session: a launcher doing `subprocess.Popen([...], start_new_session=True)` that exits at once.
4. `nohup ... & disown` is reaped with the launching Bash call's process group, and macOS has no `setsid`. Harness `run_in_background` was reaped at the turn boundary for jobs over about 2 minutes.
5. Launch long jobs from the LEAD's session. Jobs started from a teammate session die at the teammate's turn boundary, even detached ones. Teammates hand the launch to the lead.
6. A detached child never notifies anyone when it ends. Give it an append-only, fsynced on-disk ledger so a kill is harmless and a re-run skips finished work.
7. Watch the ledger or log as the health signal, and re-check state yourself; a quiet phase (fetching, no output yet) is not a stall.
8. Two jobs sharing one API token share its rate limit and halve throughput: serialize them or accept it.
9. Mutate code in a throwaway copy (`git archive` into a scratch dir, `node_modules` symlinked), never in a shared tree: peers see the mutant as a real defect.
10. A harness that cannot construct its scenario must exit with its own code and a one-line reason, never share exit 1 with "the check failed". Derive paths from the script, not ambient env.
11. A test run is heavy when repeated: loop-running a suite, mutation matrices and flaky-test hunts all take the default 2-worker, `nice -n 19` cap.

## Examples
- 3: a ~3,000-document backfill died 15-20 s in under `nohup`; a `start_new_session=True` launcher finished it (53 min).
- 5: two 15-25 min collectors launched from a teammate were reaped; the same launch from the lead completed.
- 9: a mutant moving a call after a rethrow was read by a peer as a defect in committed code.
- 10: a mutation test read an unset env var and died with exit 1 after printing "mutation applied".
