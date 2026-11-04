# Decision tables

`live.jsonl`, `base_*.jsonl`, `closures.jsonl`: classifier-level tables, see `tests/test_shell_decision_tables.py` and `tests/tools/live_capture.py`.

`hooks/<check>.jsonl` (+ `hooks/worlds.jsonl`, fakes in `tests/tools/hook_fakes.py`): hook-level guard verdicts, replayed by `tests/test_hook_decision_tables.py`. Re-capture with `tests/tools/hook_capture.py` (its docstring has the two commands: capture inside the pre-cut tree, then build and prune with `HOOK_TABLES_PRUNE=1`).
A re-capture must feed the PRE-CUT inputs (the suites at commit 7b893da9, or this capture's own rows), never the cut suite's: the cut suite no longer holds most of the inputs these tables keep.
