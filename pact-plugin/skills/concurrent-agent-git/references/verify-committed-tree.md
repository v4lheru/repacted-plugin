# Verify the committed tree, and attribute changes correctly

Context: every local check (typecheck, tests, build) reads the working directory, which holds
untracked files and peers' uncommitted edits. Read this before declaring a commit good, before
reporting a pass count from a shared tree, and before crediting or blaming a change for a red or
green gate.

## Canonical rules
1. Build and test what git has, not what the disk has: `git archive <sha> | tar -x -C <scratch>`, symlink `node_modules` from the repo root, run the checks there with `tcount`.
2. Before committing in a shared tree, archive the INDEX: `git add <your paths>`, `TREE=$(git write-tree)`, `git archive "$TREE"`. It leaves one dangling tree object; mention it.
3. Prefer `git archive` over a detached `git worktree add`: it registers no worktree and touches no git state, which matters in shared trees and under worktree guards.
4. Working green plus staged red = you depend on a peer's uncommitted file: report a co-commit set; never stage their file to make your tree build.
5. Working green plus committed red = an untracked file props up the build: track it. A new module left untracked passes every local check.
6. Grep the import statement form (`from "@/lib/x"`), never the bare module name; comments mention modules too. Report a hazard in pending work as a pre-commit hazard.
7. For an existence or location claim about committed code, read the commit (`git show <sha> --stat`, `git ls-tree`), not the worktree you looked at earlier.
8. Compare a commit message's described scope against `git show --stat <sha>`; a staging slip can leave promised files unstaged. Ask the author one pointed question.
9. A gate flipping after your action is not caused by it in a shared tree: check peer commits and mtimes first; establish cause with cold builds in archived trees, one variable each.
10. Never report "X was never a real problem" from an observation taken after an uncontrolled interval; say "green here, cause not established".
11. When the full suite goes red around a peer's in-flight rewrite, run only your own specs to prove your deliverable, attribute the red by file owner, and do not reconcile to their unfinished contract.
12. Blocked on a peer's pending change? Apply it in an rsync'd scratch copy and build there: it measures your work and reveals the breakage their change will cause.
13. Run mutation experiments in `git archive` snapshots of a NAMED commit, and re-run every finding against current HEAD before reporting; a peer may have fixed it meanwhile.
14. A peer's unresolved imports name the modules they are about to write; read them to catch two modules for one concept before either side commits.
15. On resuming a task in a shared tree, check `git status` for untracked specs and `git diff` the source for leftover sabotage before writing anything.
16. When your Edit fails with "string not found" on a file you own, a peer edited it: re-read, bind to shipped behaviour, re-run the full suite, and ask them to route changes through you.
17. Read the SKIPS in a clean-tree run as well as the failures; a suite that no-ops without a missing fixture is reporting on its own guards.
18. Make an oracle-dependent test fail rather than skip when a required flag is set (`ORACLE_REQUIRED=1` plus a path override), and have CI supply both.
19. A guard must tell absence (skip loudly) from breakage (fail); "nothing to check yet" is not "discovery is broken".
20. Run tests on the runtime CI pins when the host differs: fetch that Node build into scratch and put it first on PATH rather than caveating the result.

## Detail

**Committed-tree check.**
```bash
S=<scratch>/verify-<sha>; mkdir -p "$S"
/usr/bin/git archive <sha> | tar -x -C "$S"
ln -s "<repo root>/node_modules" "$S/node_modules"
cd "$S" && tcount npx vitest run && npx tsc --noEmit
rm "$S/node_modules"                      # remove the symlink before any rm -rf of the dir
```
Use `$(git write-tree)` in place of `<sha>` for the staged set. `tcount` belongs to
repacted:shell-tooling-traps and records a count instead of a bare exit code.

**The two numbers are different claims.** A staged-tree run (your work plus HEAD) and a
working-tree run (plus every peer's unstaged edit) can differ by a peer's whole module; report the
staged number for your commit, labelled as such.

**Causation recipe.** Three cold-cache builds in archived trees: pristine base (green), base plus
the suspect file (red, failure reproduced), base plus the suspect plus the fix (green). A cold
cache holds no stale artifact, and the middle row reproduces the failure with no fix present. A
rebuild of a tree that already contains someone's fix will credit the rebuild.

**Mutation against all gates.** Run each mutation against lint, unit and integration tiers, not
only the gate under test, or you report gaps another gate closes. A test's name is a claim: if the
argument that should carry the guarantee is empty, the test is vacuous. Deeper method belongs to
repacted:verification-discipline.

**Resumed context.** Leftover `&& false`, `COUNTER-TEST` comments or dead derivations are sabotage
to restore (`git checkout -- <file>`, then `git diff --quiet`), not the real fix. Consolidate a
duplicate spec into one file.

## Examples
- A file imported an untracked module; tsc was clean, 547 tests passed and the build succeeded
  locally. An archive of the commit failed at once.
- A staged tree passed 334 tests while the working tree said 361; the difference was a peer's
  in-flight module that the commit did not contain.
- An agent credited its rebuild for a green build and told the peer their already-committed fix was
  unnecessary; timestamps showed the fix landed before the rebuild.
- Two of five audit findings were already fixed at HEAD by a commit landed mid-audit.
