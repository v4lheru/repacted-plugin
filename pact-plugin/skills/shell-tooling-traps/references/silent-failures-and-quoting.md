# Silent failures, exit codes and quoting

Context: shell mechanics that turn a failed or never-run command into output that reads as a
result. Read this before you test an exit status, chain commands, run a search whose emptiness is
your finding, or put text containing backticks, `$` or `!` into a shell string. What a count or an
absence MEANS is repacted:verification-discipline; this file is about getting an honest reading.

## Canonical rules
1. A pipeline's exit status is its LAST stage's: `cmd | tail; echo $?` reports tail. Never pipe a command whose status you test or report.
2. Capture, then read: `cmd > /tmp/out.log 2>&1; echo "exit=$?"`, then read the log. `set -o pipefail` works but is not inherited by a one-off tool call.
3. In zsh `${PIPESTATUS[0]}` is EMPTY (arrays are 1-indexed, the array is lowercase `pipestatus`). Use `$?` or `${pipestatus[1]}`; an empty status is a broken instrument.
4. When raw output and a status word or summary disagree, the raw output wins. Errors on screen next to `exit 0` mean go find the pipe.
5. `grep` exits 1 on no match, so `grep ... && next` never runs `next`, and its silence reads as a clean result. Never chain a search behind anything.
6. Separate independent diagnostics with `;`, print numbered markers (`echo "== 3 =="`), and check every marker printed. A missing marker means the command never ran.
7. Make the empty case print: `... || echo "NONE FOUND"`. "Searched, found nothing" and "never searched" must not look the same.
8. Unchained, one fact per line, for anything you will report or act on. A half-run verification command gets believed; a half-run build gets noticed.
9. A missing binary exits 127 and prints nothing; piped into `head` or `wc` the 127 vanishes. macOS has no `timeout`, `setsid` or `grep -P`. Check `command -v <tool>` first.
10. A tool can be present but shimmed (`find` as a shell function to `bfs`). For evidence use the absolute binary (`/usr/bin/find`, `/usr/sbin/lsof`) and guard it with `[ -x ]`.
11. `env -i`, cron, launchd and minimal containers drop `/usr/sbin` and friends from PATH; a missing tool then reads as a negative. Guard existence, not just output.
12. Never `2>/dev/null` a command whose EMPTINESS is your evidence: it turns "refused", "errored" and "never ran" into "found nothing".
13. Before interpreting a result, compute the range it could possibly return. An impossible result (zero files touched in a tree checked out a minute ago) indicts the instrument.
14. Positive-control every absence in the same invocation: same tool, same flags, same argument form, same scope; vary only the needle. Add an absent needle too.
15. BSD `grep -E` has no `\s` and no `-P`: a census regex silently misses lines. Use `[[:space:]]`, python, or an `ast` walk for code-structure questions.
16. Report a sweep's denominator with its zero: "314 files scanned, 0 hits". Scope by source paths (`src tests`) rather than `.` plus exclude flags.
17. zsh aborts the whole command on an unmatched unquoted glob (`--include=*.ts`). Quote every glob a program should receive: `--include='*.ts'`.
18. zsh does not word-split unquoted variables: `$FLAGS` or `$FILES` with several items arrives as ONE argument, and loops get one item. Use an array (`"${IGN[@]}"`) or `${=FLAGS}`.
19. Backticks, `$` and `!` inside ANY double-quoted string (`-m`, `-c`, `echo`, an unquoted heredoc) are expanded away with exit 0. Use single quotes or `<<'EOF'`.
20. Write non-trivial commit messages to a file and `git commit -F <file>`; then read the message back with `git log -1 --format=%B`.
21. Verify stored text by reading it back, never by the tool's `ok`. A deleted identifier leaves a double space between words: sweep for `[A-Z]  +[A-Z]`.
22. `json.dumps` defaults to `ensure_ascii=True`, so `£` or an em-dash is stored escaped and a literal probe never matches. Probe with `ensure_ascii=False`, or compare counts.
23. Sourcing a `.env` with a space in a key name skips that line as a failed command and still exits 0. Never source; see repacted:safe-ops `env-get`.
24. Name columns, never positions: `awk '{print $3}'` on output with multi-token fields gives a wrong value under a right label. Use `-o json`, `custom-columns` or `jq`.
25. A green that depends on something outside the repo (an untracked `node_modules` symlink, a parent checkout's install) belongs to the machine. Say what it used.
26. Before you name a mechanism in a correction, test it to the same standard as the claim you are correcting. A right verdict with a wrong reason is what gets reused.

## Examples
- 1: `npm ci 2>&1 | tail -20` showed EBADENGINE and reported exit 0; the install had failed.
- 3: `=== tsc exit: ${PIPESTATUS[0]} ===` printed blank after every gate and was read as fine.
- 5: `grep -ci absent F && grep -c PRESENT F` never ran the second grep; the lead contradicted a peer on its silence.
- 9: `timeout 100 grep -rl X /path | head` returned nothing on a box without `timeout`; the string was present.
- 12: a shimmed `find ... 2>/dev/null` reported 0 recently modified files; the true count was 309.
- 15: a `\s` census missed a module-level cache dict just before a two-replica rollout.
- 17: `grep -rn pat --include=*.ts .` died on the glob and was nearly reported as "no hardcodes".
- 18: `FILES=$(grep -l ...); pytest $FILES` passed every path as one argument; pytest found no such file and `tcount` reported ZERO TESTS RAN.
- 19: a double-quoted `git commit -m` lost both backticked predicates, the two clauses the commit existed to record.
- 23: a hand-built `.env` loaded 6 of 11 credentials with exit 0.
- 24: `kubectl get cronjobs | awk '{print $3}'` labelled schedule hours as "suspend=3/6/9".
