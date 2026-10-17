# rtk: the one defect catalogue

Context: where rtk is installed, a Claude Code hook rewrites many Bash commands to `rtk <cmd>`,
which compresses output to save tokens (verified 2026-09-26, rtk 0.41.0). That is useful for reading and wrong for evidence.
Read this before you take a count, an exit code, a SHA or an absence from shell output. The rtk
hook is NOT transparent (the old "0 tokens overhead" line was about token cost, not fidelity).

## Canonical rules
1. Never take a COUNT or an EXIT CODE from rtk-wrapped output when a decision rests on it. A wrong number reads exactly like a right one.
2. Report test results from `tcount <cmd>` only. It execs the runner outside rtk, prints the counts and the real exit code, and exits 3 when no count parses. It parses pytest, vitest, jest, go test, playwright, cargo, node --test, mocha and dotnet test.
3. A test result is a recorded run: the run-ledger hook logs every test command's parsed counts, and the HANDOFF evidence gate checks claimed counts against that ledger. Do not pipe `tcount` (`tcount ... | grep`, `| tail`): its last two lines, the counts and `log: <path>`, tie the run to your task, so read the log file for detail instead; a count from a run outside tcount (Docker, a gate script) is backed by citing its `.trx` result file by path.
4. Escape rtk with `rtk proxy <cmd>`, an absolute binary (`/usr/bin/git`, `/usr/bin/grep`, `/usr/bin/diff`) or `./node_modules/.bin/<tool>`.
5. `rtk-probe <cmd>` runs a command hooked and raw and compares bytes, lines and exit codes. Run it when output looks too short, too clean or wrong.
6. rtk's exit code is not stable either way: a pytest spawn failure exited 0 on 2026-09-21 and 1 on 2026-09-26 (same version). Only a parsed count is evidence.
7. `python3 -m pytest` (and `--collect-only`) is rewritten to `rtk pytest`, dropping the interpreter; with no `pytest` on PATH it dies "Failed to spawn process". Use `tcount python3 -m pytest`.
8. `npm test`, `pnpm test`, `node --test`, bare `mocha` and `uv run pytest` are not rewritten; `npm run test`, `npx mocha`, `make test`, vitest, jest, go test, cargo test, playwright are.
9. rtk pytest collapses output to `Pytest: N passed, M failed`; "No tests collected" covers both "none matched" (exit 5) and "file not found" (exit 4).
10. `git log` drops merge commits, including HEAD when HEAD is a merge, and can pair one commit's hash with another's date. Read SHAs with `git rev-parse`, never `log`.
11. When two git commands disagree about one fact (log vs rev-parse, log vs `git worktree list`), the instrument is lying. Do not pick the plausible one.
12. Hooked `git log` with bodies (`%b`) loses up to 97% of bytes; diffs lose about 25% and end in `[N more lines]`. An absence found in either is void.
13. `git show <rev>:<path>` (content) and `git log -S/-G` (pickaxe) are lossless; `git show <rev> -- <path>` (a diff) is lossy. Same verb, different safety.
14. Never count diff lines through a pipe (`git show | grep -c`): use `git show --numstat <sha>` or `git diff --numstat`.
15. `git diff > file.patch` can save a summary, not a patch. Check it: `grep -cE '^\+\+\+ '` equals the file count and `grep -c 'more lines\]'` is 0.
16. rtk grep redefines short flags: `-v` is verbose (NOT invert), `-l` is max-len and eats the next arg, `-h` prints help and exits 0. Use `/usr/bin/grep` for any short flag.
16a. `npx prettier --check` (any prettier `--check`/`-c`) through rtk printed "All files formatted correctly" for an unformatted file (real exit 1). Run `./node_modules/.bin/prettier --check` or `rtk proxy npx prettier --check` (measured 2026-09-30).
17. `grep -c -v` passes through while `grep -v` is routed, so it is a false-negative test for routing. Routed output starts `N matches in M files:`.
18. Never trust rtk `diff` for equality: it printed "[ok] Files are identical" for files one line apart, and exits 0 on differing files. Use `cmp`, `rtk proxy diff` or `/usr/bin/diff`.
19. A flag is not a talisman: `npx eslint ... --max-warnings=0` reported 2 errors against a real 4. Each wrapped command form is its own question.
20. Wrapped lint can be wrong both ways at once (summary `0 errors` with exit 1; a fabricated `2 errors` from a failed `npx`). Use `./node_modules/.bin/<tool>`.
21. `helm template` through the hook stops at a fixed 41 lines, even through `> file`. Exactly 41 lines means truncated: rerun with `rtk proxy helm`.
22. `rg`, recursive `grep` over a file with a NUL byte, and `find` with `-not`/`-exec` can come back EMPTY through rtk. Positive-control every absence in the same call.
23. rtk refuses compound `find` predicates on stderr with exit 1, so `find ... 2>/dev/null` forges "no matches". Never silence stderr when emptiness is the evidence.
24. rtk vitest parse failures print `[RTK:PASSTHROUGH]` and leave `output.json` in `.vitest/json` behind; it is shared and stale. Name a fresh `--outputFile` per run.
25. rtk wraps the whole command, so `cmd > log` still captures the summary. Only `rtk proxy`, an absolute binary, or a `bash file.sh` script escapes it.
26. The hook matches only the submitted command string: `eval`, a shell function or a script bypasses it. A probe built that way measures unhooked behaviour.
27. In a worktree-isolated session the hook plus the worktree guard can refuse every `git`: use `/usr/bin/git` as one uncompounded command, only on the tree attributed to you.
28. Condition on rule 27: explicit `-C <abs path>` helped in one guard state and was refused for a tree not attributed to you in another. Check which tree the guard names.
29. Truncation and a moving world both look like "my check said X, reality is not-X". Re-run now: same answer means the instrument lies; a new answer means the world moved.
30. Before retracting a measurement because of an innocent explanation or a peer's narrowing, re-run the identical command both ways. Retractions are believed harder than claims.
31. Never install a plugin beside repacted that rewrites subagent models or denies spawns or AskUserQuestion.

## The rtk config (history reads go raw)
A recommended rtk config (`~/Library/Application Support/rtk/config.toml` on macOS) sets
`[hooks] exclude_commands` so bodies, patches, stats, name lists and history searches run raw:
`git log` with `%b %B -p --patch --stat --name-only --name-status --grep -S -G --all --author
--follow` or a full/raw format, `git show <rev>` without a colon path, and `git diff` with
`--stat --name-only --name-status --numstat`, prettier `--check`/`-c`/`--list-different`, `grep` with `-h`, `-v` or `-l` in a short-flag cluster, and plain `diff` and `cmp` (rule 18). Plain `git log --oneline`, `git status`, `git diff`
and `git show rev:path` stay compressed. The patterns also accept git's global options before the
subcommand (`-C <dir>`, `-c k=v`, `--no-pager`), because `git -C <dir> log -p` was otherwise still
rewritten (verified 2026-09-26 with `rtk rewrite`, rtk 0.41.0: excluded commands print nothing and
exit 1; rewritten ones print `rtk ...` and exit 3, or 0 for `python -m` forms).

## Meta commands
`rtk gain` (savings), `rtk gain --history`, `rtk discover` (missed rewrites), `rtk proxy <cmd>`
(raw run), `rtk rewrite "<cmd>"` (what the hook would run), `rtk --version`. If `rtk gain` fails,
a different `rtk` (Rust Type Kit) is installed under the same name.

## Examples
- 10: `git log --oneline -3` omitted the merge commit at HEAD; `rtk proxy git log` showed it.
- 12: a commit-message audit found 7 hits in hooked `git log`; the proxied read found 16.
- 14: `git show <sha> | grep -c '^[+-]'` gave 0 for a commit `--stat` put at 158 lines.
- 15: a redirected `git diff` held `[206 more lines]` and no `+++` headers; applying it would have lost work.
- 18: two Dockerfiles differing by one version line came back "Files are identical", exit 0; two later teams hit the same false identical, and on 2026-09-26 it showed the hunk but still exited 0.
- 21: 221-line and 1,444-line renders both came back as 41 lines of NetworkPolicies.
- 22: `rg -l segment src/` returned nothing; `rtk proxy` found 32 files.
- 24: a harness read a stale `output.json` and reported all 15 mutants surviving.
- 30: a lint finding nearly retracted after a scope confound; the identical re-run held.
