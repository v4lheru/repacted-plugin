# Environment, scheduler and file traps

Context: scripts that behave differently under launchd, cron or a detached launch; interpreters
and venvs that break or hide; files that git or a tool silently misclassifies. Read this when a
scheduled or detached job "runs" but produces nothing, when a toolchain works for one agent and
not another, or before you back up, restore, ignore, kill or validate files by script.

## Canonical rules
1. launchd does not expand `~` or `$HOME` and starts with a minimal PATH. Use absolute paths, or `/bin/bash -c 'exec "$HOME/..."'` in ProgramArguments.
2. In a plist, each `<string>` in ProgramArguments is one argv element: a path with spaces needs no quoting there, but must be its own element.
3. launchd exit 78 (EX_CONFIG) means the ProgramArguments binary is missing, often `/opt/homebrew/bin/x` on an Intel-Homebrew Mac. Use `/usr/bin/env x` or the real path.
4. `launchctl list` shows 0 for an agent that never ran. Use `launchctl print gui/$(id -u)/<label>` (`runs`, `last exit code`) and the script's own log.
5. Editing a plist reloads nothing: `launchctl bootout gui/<uid>/<label>` then `launchctl bootstrap gui/<uid> <plist>`. Validate first with `plutil -lint` and `bash -n`.
6. TCC blocks launchd jobs from `~/Documents`, `~/Desktop` and `~/Downloads`; symlinks do not evade it. Relocate the script and its data rather than granting bash Full Disk Access.
7. launchd does not catch up runs missed while the Mac slept. Keep a "last ran at" state file so a job can backfill.
8. A wrapper trap needs the child in the background (`child & wait $!`): `exec` replaces the shell and the SIGTERM trap never fires.
9. For scripts run by hand AND by a scheduler, install deps where the scheduler's interpreter resolves them (`python3 -m pip install --user --break-system-packages`) and load `.env` in-process.
10. User site-packages is versioned (`~/Library/Python/3.12/...`): a Homebrew minor upgrade orphans it. Check that first when a scheduled job starts failing imports.
11. A venv built on Homebrew `python@X.Y` breaks when that formula goes (dangling `.venv/bin/python`, exit 127). Read `pyvenv.cfg`, rebuild; pure-Python deps can run via PYTHONPATH meanwhile.
12. A broken shared venv blocks every teammate; report it to the lead as a shared blocker instead of silently rebuilding shared infra.
13. Under `pytest --strict-config`, a missing plugin shows as "Unknown config option" with 0 tests. Look for the project venv (`ls -d venv .venv backend/venv`) before calling the repo broken.
14. Never build your own venv to route around a test problem: your counts stop being comparable with everyone else's. Use the project's interpreter.
15. A relative output path resolves against the launcher's cwd; under a detached or scheduled launch it lands elsewhere with exit 0. Anchor paths, or state the cwd in the run command.
16. Verify a side-effect command (migration, seed, file generation) by its effect: query the table, stat the file. Its own stdout is not evidence.
17. The ESM guard `import.meta.url === "file://" + process.argv[1]` is false when the path has a space; the script exits 0 doing nothing. Use `pathToFileURL(process.argv[1]).href`.
18. To ship a script that hardcodes `ROOT = Path(__file__).parent.parent` unedited, stage a copy inside the data dir so that dir becomes ROOT; assert its sha256 against source each run.
19. Detect a co-owned script's flags by parsing its `--help` from the staged location; refuse the dependent step when a flag is missing rather than passing it blind.
20. Key any backup on the full relative path, never the basename (`route.ts`, `index.ts`, `__init__.py` collide). Checksum every file after restore.
21. A literal NUL or control byte in source makes git treat the file as binary and hides its diffs. Write the escape (`\u0000`); prove the runtime value is unchanged before and after.
22. "Binary" from git can be one deliberate control byte, not corruption. Count bytes in python and check older commits before "repairing" from HEAD.
23. An unanchored `**/name/` rule in `.gitignore` or `.dockerignore` can swallow a same-named source dir. Run `git check-ignore -v <new dir>`; anchor rules with a leading `/`.
24. Hook-runner templates: quote a single value (`{1}`), never a list (`{staged_files}`), or N paths collapse into one missing file. Probe with a file whose name has a space.
25. `lsof -ti tcp:PORT` returns client sockets too. Kill only listeners: `lsof -nP -iTCP:<port> -sTCP:LISTEN -t`. Identify a process by its arguments, not its binary path.
26. `lsof` truncates COMMAND to 9 characters, and `ps aux | grep` misses live processes. For "is the tunnel up", ask the port: `lsof -iTCP:<port> -sTCP:LISTEN`.
27. Read the lockfile name before running an installer: `pnpm-lock.yaml` with `workspace:` deps fails under npm (`EUNSUPPORTEDPROTOCOL`), and `--silent` hides why.
28. Reading image dimensions is not validating: `sips` prints 800x800 with exit 0 for a truncated JPEG. Check structure (JPEG FFD8..FFD9; PNG chunks with CRC and IEND).
29. A CDN size token (`shrink_800_800`) is an upper bound; classify images by decoded pixels. Variant names differ (`shrink_` vs `crop_`), and rewriting the token returns 403.

## Examples
- 4: `launchctl list` showed 0 for an agent with `runs = 0`; a TCC fix was wrongly declared working.
- 13: two agents independently called a suite broken; `backend/venv` held the plugin all along.
- 15: `output_dir = f"data/{id}"` wrote into the launcher's cwd and logged a plausible path.
- 17: `npm run migrate` printed a success-looking line and applied zero migrations.
- 20: restoring by basename overwrote a 1,945-line cron `route.ts` with a 129-line import route.
- 23: `**/candidates/` in both ignore files dropped the dashboard route; production served a 404.
- 25: killing `lsof -ti tcp:4331` also hit a Playwright browser connected to the port.
- 28: a validator built on `sips` nearly stamped truncated downloads as good.
- 29: 49 photos advertised `shrink_800_800` and delivered as little as 96x96.
