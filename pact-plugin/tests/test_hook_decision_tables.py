"""
Location: pact-plugin/tests/test_hook_decision_tables.py
Summary: Replays the recorded hook-level guard decisions (tests/fixtures/shell/decisions/
         hooks/<check>.jsonl, captured from the pre-cut suites by tests/tools/hook_capture.py)
         through the real hook entry, one test per guard: every row's payload, in its
         rebuilt world (worlds.jsonl), must still get the recorded verdict and rule id. All
         rows of a guard are checked inside its one test and every miss is reported at once,
         each naming the row id, its payload head and the pre-cut test it came from.
         HOOK_TABLES_PRUNE=1 (build time only) drops the rows that do not reproduce instead
         of failing, and records how many in the table header; HOOK_TABLES_MISSES=<file>
         appends one JSON line per miss; HOOK_TABLES_FRESH=1 rebuilds every world per row
         (the check that reusing built worlds changes no verdict).
Used by: the plugin suite.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import hook_replay  # noqa: E402

CHECKS = ("background_test_gate", "bootstrap_gate", "docker_lock_gate", "egress_guard", "egress_ws",
          "env_guard", "git_commit_check", "merge_guard_pre", "merge_question_gate",
          "ops_frame_guard", "output_secret_scan", "process_table_guard", "task_write_guard",
          "unattended_gate", "wait_filler_gate", "worktree_guard", "write_checks")
if os.environ.get("HOOK_TABLES_PRUNE") == "1":   # a build prunes every table it wrote
    CHECKS = tuple(sorted(p.stem for p in hook_replay.TABLES.glob("*.jsonl") if p.stem != "worlds"))
# A slow table (its guard runs git per row) is split into shards, so -n spreads it out.
SHARDS = {"git_commit_check": 5, "env_guard": 2}
PARAMS = [(c, k, SHARDS.get(c, 1)) for c in CHECKS for k in range(SHARDS.get(c, 1))]


def _read(path):
    header_line, _, body = path.read_text(encoding="utf-8").partition("\n")
    header = json.loads(header_line)
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == header["sha256"], \
        f"{path.name}: body drifted from its header digest"
    return header, [json.loads(line) for line in body.split("\n") if line.strip()]


def _shard_rows(rows, shards):
    """Row numbers dealt out to shards: each context's rows in runs of at most a quarter
    of a shard, heaviest run first onto the lightest shard, so the shards weigh about
    the same and a context's worlds are built about once per shard."""
    by_ctx = {}
    for n, row in enumerate(rows):
        by_ctx.setdefault(row["ctx"], []).append(n)
    size = max(1, -(-len(rows) // (shards * 4)))
    runs = [ns[i:i + size] for _, ns in sorted(by_ctx.items()) for i in range(0, len(ns), size)]
    loads = [[0, set()] for _ in range(shards)]
    for run in sorted(runs, key=lambda r: (-len(r), r[0])):
        lightest = min(loads, key=lambda load: load[0])
        lightest[0] += len(run)
        lightest[1].update(run)
    return [load[1] for load in loads]


@pytest.fixture(scope="module")
def worlds():
    return hook_replay.load_worlds(hook_replay.TABLES / "worlds.jsonl")


@pytest.mark.parametrize("check,shard,shards", PARAMS,
                         ids=[f"{c}-{k + 1}of{n}" for c, k, n in PARAMS])
def test_every_recorded_decision_of_the_guard_replays(check, shard, shards, worlds, tmp_path):
    table = hook_replay.TABLES / f"{check}.jsonl"
    assert table.exists(), f"no recorded decisions for {check}: {table}"
    header, rows = _read(table)
    assert header["check"] == check and rows, f"{table.name}: empty or mislabelled"
    fakes = hook_replay.fakes()
    cache = None if os.environ.get("HOOK_TABLES_FRESH") == "1" else tmp_path / "cache"
    prune = os.environ.get("HOOK_TABLES_PRUNE") == "1"
    if prune and shard:
        pytest.skip("a prune rewrites the whole table from its first shard")
    mine = _shard_rows(rows, shards)[shard]
    misses, kept = [], []
    for n, row in enumerate(rows):
        if not prune and n not in mine:
            continue
        got = hook_replay.replay(row, tmp_path / f"row{n}", worlds, fakes, cache)
        if got == row["want"]:
            kept.append(row)
        else:
            misses.append(f"{row['id']} {row['raw'][:120]!r} from {row['src'][:1]}: "
                          f"want {row['want']} got {got}")
            if os.environ.get("HOOK_TABLES_MISSES"):   # build time: one JSON line per miss
                with open(os.environ["HOOK_TABLES_MISSES"], "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"id": row["id"], "check": check, "got": got}) + "\n")
    if prune:
        body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept)
        header.update(rows=len(kept), sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                      dropped_not_reproduced=header.get("dropped_not_reproduced", 0) + len(misses))
        table.write_text(json.dumps(header, sort_keys=True) + "\n" + body, encoding="utf-8")
        return
    assert not misses, f"{len(misses)} of {len(rows)} {check} rows changed:\n" + "\n".join(misses)
