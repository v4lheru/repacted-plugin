---
name: shell-tooling-traps
description: "Use when shell output is evidence or looks wrong: rtk rewrites if installed (git log/diff, grep, find, tests, lint, helm), test counts via tcount, macOS vs GNU tools, pipes and exit codes, heredoc quoting, long background jobs, sleep, Gatekeeper, and heavy compute (a default cap of 2 workers, nice 19)."
---

# Shell tooling traps

Context: shell output is evidence only when the instrument is honest. Where installed, rtk's hook
rewrites many commands to save tokens; on macOS the userland is BSD under zsh; and the Bash
tool may be sandboxed. This skill removes the measured traps while keeping rtk's savings: counts
come from `tcount`, history reads go raw, everything else is checked before it is believed.

## Workflow
1. **Reporting a test result?** Run `tcount <cmd>` and quote its counts line. Exit 3 ("NO TEST
   COUNT") means NOT RUN, never passed; exit 1 means failures, a non-zero runner exit or zero
   tests. The run-ledger hook records every test run; the HANDOFF evidence gate checks your counts
   against it. → `references/rtk.md`
2. **Making an absence claim, a count or a history read** (other people's commits, bodies,
   patches, searches)? Use `rtk proxy <cmd>` or the absolute binary (`/usr/bin/git`,
   `/usr/bin/grep`). The recommended rtk config sends body, patch, stat and search reads of
   `git log`/`git show`/`git diff` raw; anything else rtk rewrites may be truncated or wrong.
   Read SHAs with `git rev-parse`, never from `git log`. → `references/rtk.md`
3. **Output looks too short, too clean or plainly wrong?** Run `rtk-probe <cmd>` (read-only
   commands only: it runs twice) and compare hooked vs raw bytes, lines and exit codes before you
   reason about it. → `references/rtk.md`
4. **Testing an exit status?** NEVER pipe the command you test: `cmd | tail` reports tail's status
   and zsh's `${PIPESTATUS[0]}` is empty. Redirect to a log, then `echo "exit=$?"`. Never chain a
   search after a fallible command with `&&`; silence then reads as "none found". Print a marker
   per step and positive-control every absence in the same call.
   → `references/silent-failures-and-quoting.md`
5. **Putting text into a shell string** (commit messages, `-c` scripts, heredocs)? Use single
   quotes or `<<'EOF'`, or write a file and pass `-F <file>`. Backticks, `$` and `!` inside double
   quotes are substituted away with exit 0. Read the stored text back.
   → `references/silent-failures-and-quoting.md`
6. **Scripts run by launchd, cron or a detached launch?** Use absolute paths: `~` and `$HOME` are
   not expanded, PATH is minimal, relative outputs land in the launcher's cwd. Check `launchctl
   print`, never `launchctl list`. → `references/env-and-file-traps.md`
7. **On macOS:** there is no `grep -P` or `timeout`. Strip quarantine with single-file `xattr -d`,
   one per Bash call; NEVER codesign or loop over binaries (an endpoint security agent can kill it). Effects outside
   the sandbox (clipboard, `open`) need `dangerouslyDisableSandbox: true` and a read-back. Before
   blaming a remote for a gap in an unattended run, check `pmset -g log` for sleep.
   → `references/macos.md`
8. **Long job (> 10 min) or heavy compute** (mutation runs, repeated suites, benchmarks, corpus or
   vault scans)? By default use at most 2 workers and `nice -n 19`: tcount runs every command at niceness 19
   and caps vitest, jest, dotnet and pytest -n at 2 workers; REPACT_TCOUNT_NO_CAP=1 turns both
   off. Launch long jobs from the lead's session with `Popen(start_new_session=True)` and an
   on-disk ledger, never from a teammate shell; teammates hand the launch to the lead.
   → `references/long-and-heavy-jobs.md`

## Map
- `references/rtk.md` the rtk defect catalogue, the config, `tcount`, `rtk-probe`, the ledger.
- `references/silent-failures-and-quoting.md` pipes, chains, missing tools, zsh, quoting, stderr.
- `references/env-and-file-traps.md` launchd and cron, venvs, relative paths, backups, NUL bytes,
  ignore rules, ports, installers, image validation.
- `references/macos.md` BSD tools, sandbox effects, Gatekeeper and endpoint security agents, settings
  migration, sleep.
- `references/long-and-heavy-jobs.md` detached launches, ledgers, the 2-worker cap.
- Commands (plugin `bin/`, on the Bash PATH): `tcount`, `rtk-probe`. Run them; do not reimplement.

## Related skills
- repacted:verification-discipline: what a count or an absence means, and whether a check can fail.
- repacted:concurrent-agent-git: committed-tree checks and shared-index git.
- repacted:safe-ops: `.env` files (`env-get`, never source), destructive operations.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
