# Session roots and shared resources

Context: which tree, branch and directory you are really in, and the resources several agents share
without owning (scratchpad, build dirs, ports, databases, lockfiles, setup scripts). Read it before
any write whose result depends on location, before starting a server or build in a shared tree, and
before running a setup or cleanup command you did not write.

## Canonical rules
1. Establish the frame in the SAME command as any write or reported number: `pwd; /usr/bin/git rev-parse --show-toplevel --abbrev-ref HEAD; /usr/bin/git log -1 --format=%h`.
2. The session root can move between two consecutive commands with no `cd`; verify per operation, never once per session, and address every write by absolute path.
3. Use `rev-parse --show-toplevel` for location; `git branch --show-current` reports whatever tree you are now in and cannot reveal a move.
4. Check whose tree you are in without git by reading `.git/worktrees/<name>/HEAD`; if your session points at a peer's tree, tell its owner.
5. Push early. A remote branch or a forge commit is immune to a local root that moves; "is my work in what ships" is `merge-base --is-ancestor <mine> origin/master`.
6. Making a command readable to a guard is not permission: aiming `-C` at a tree the guard refused is evasion. Escalate, or commit server-side through the forge API.
7. A peer's report about shared state is a timestamped reading; when it conflicts with yours, re-measure and reconcile on a timeline, and state the time and place of your own reading.
8. A dispatch's stated pre-state of a shared system (schema, rows, files) is evidence with a timestamp; read the target yourself right before writing to it.
9. For a destructive op, make the check and the op one command (`git rm`, `mv -n`, `test -z "$(git status --porcelain -- f)" && rm f`); prefer tools that fail when state moved.
10. Do not re-assert a field you are not changing on a record another agent also writes; last-write-wins reverts theirs silently.
11. The session scratchpad is shared by every agent in the session: prefix files with your agent name, `ls` before destructive ops, guard with `test -e` instead of clearing.
12. Put servers, ports and data dirs outside the shared scratchpad; Unix socket paths have a short length limit that scratchpad paths exceed, so use a short `/tmp/<name>`.
13. Read a setup script, and any command an error message recommends, before running it; expand npm aliases and scan for `dropdb`, `DROP`, `TRUNCATE`, `rm -rf`, `--force`, `reset`, `db:push`.
14. If a setup command destroys a fixed-name shared resource, use a private uniquely named copy (or the sanctioned skip flag), verify the shared one survived, and report the substitution.
15. A declined test tier is reported as NOT RUN with the reason; "my diff touches no SQL" is an argument, not a measurement.
16. Two builds or dev servers in one worktree collide through the shared build dir; verify from an rsync'd private copy or a private `--outDir`, never by deleting shared build state.
17. Use `npm ci`, not `npm install`, in a shared tree; a bare install rewrote the shared lockfile.
18. Kill and verify servers by the PID bound to YOUR actual port (`lsof -tiTCP:<port> -sTCP:LISTEN`), never by process name; read the real port from the startup banner.
19. Announce ad-hoc port picks (which port, how long, what ran); a peer who loses a bind race diagnoses credentials or VPN, not a collision.
20. A fresh worktree has no `node_modules`: install per worktree (`pnpm install --prefer-offline`) and build workspace packages that export `dist/` before trusting typecheck errors.
21. pnpm does not expose transitive deps; add them explicitly at the resolved version and check `require.resolve` from the package dir.
22. A worktree nested inside its repo makes ESLint 8 load both configs; lint with `--no-eslintrc --config .eslintrc.json --resolve-plugins-relative-to .` and calibrate it.
23. Do not make a private `git worktree add` to escape a worktree-isolated session: Edit/Write key on the session's pinned tree, so it stays refused. Report and stop.

## Detail

**Isolated verification copy.**
```bash
rsync -a --delete --exclude node_modules --exclude .next --exclude .git "$SRC/" "$WORK/"
ln -s "$SRC/node_modules" "$WORK/node_modules"
```
Run your dev server there on its own port; re-sync per round and record the commit in the report.
Assert hydration (a control actually opens, `main-app.js` returns 200), not just an HTTP 200 or a
test id: a half-rebuilt `.next` serves a server-rendered page that never hydrates. Check the dev
server log before the diff: `routes-manifest`, "React Client Manifest" and `_next/static` 404s are
infrastructure. A half-written Astro `dist` (chunks and `.mjs`, no `.html`) is a build caught
mid-flight, not an SSR switch; check `astro.config.mjs`. Frameworks with `--outDir`/`distDir` can
build to a private directory. A static server started in a dir you later delete keeps listening and
404s everything.

**node_modules in a fresh worktree.** If package files are unchanged
(`git diff --stat HEAD -- package.json package-lock.json` is empty; a wrapped `diff` can lie),
symlink the main checkout's `node_modules` and run tools from `node_modules/.bin`. Never disable
`engine-strict` or edit `.npmrc` (in one repo the guard prevented a Node 24 ESM failure that made no
page compile). Turbopack `next build` rejects a symlinked `node_modules`; clone it with `cp -Rc`
when a build is needed. Symlink from the repo root, since a worktree's own copy may be empty. An
untracked symlink makes greens machine-dependent; remove it before staging and say so in the
handoff. PACT worktree bugs (pins, refused writes) are in repacted:pact-field-notes.

**ESLint calibration.** Plant a parse error (proves the file is read, exit 1) and a known project
rule violation such as `<img>` under `next/core-web-vitals` before trusting a clean run; a weak
control like `var x = 1` passes and proves nothing. The permanent fix is `"root": true` in the
repo's config; propose it, do not apply it mid-task.

**Setup scripts.** `node -e "console.log(require('./package.json').scripts['<name>'])"` expands an
alias. The durable fix is usually the error MESSAGE that recommends the destructive command. The
choice between a sanctioned skip flag (reported UNVERIFIED) and a private scratch database is the
dispatch's; both are honest if reported.

**Stale shared state.** A stale dispatch usually UNDERSTATES progress, because its author acted
after writing it; the risk is duplicate work. When state diverges, ask which cause produced it (a
content hash can say which file loaded where a row count only says "loaded"). A worktree guard that
fires on one write does not mean the earlier writes went to the right tree; check
`rev-parse --show-toplevel` for the file you are writing.

## Examples
- An agent verified its own worktree, then ran `git branch -m`; the rename hit a peer's branch
  because the pin moved between the two commands.
- `rm -rf "$SCRATCH/pgd"` ran in a directory also holding three agents' files; `rm -rf` on an
  absent path is silent, so it was impossible to say afterwards whether `pgd` had been a peer's.
- An error message prescribed `npm run test:db:setup`; the script began with `dropdb` on a
  database another lane was using.
- `next dev -p 3002` silently took 3037; tearing down "my port" killed a teammate's server.
