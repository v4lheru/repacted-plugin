# TypeScript and Node tooling traps

Context: where a TypeScript type guarantee, a validation library or a Node/pnpm resolution
promise stops short of what the code relies on. Read this when a type "proves" coverage, a
schema parses env or query strings, a monorepo import or typecheck fails oddly, or a vitest
mock will not construct.

## Canonical rules
1. `as const satisfies readonly Union[]` checks each element, never coverage: a subset compiles. Use `Record<Union, X>` for per-member data.
2. For a tuple that must stay literal (e.g. to feed `z.enum`), add an `Exclude<Union, (typeof LIST)[number]>` assertion to never; the error names the missing member.
3. A lookup map fed arbitrary strings: annotate `Record<string, T>` and add `satisfies Record<Vocab, T>`. Indexing stays legal; a missing or stray key fails the build.
4. Prove a type guard fires in BOTH directions (add a vocabulary member; delete a map entry), then restore to 0 errors.
5. `const _never: never = x; return _never` is compile-time only. At runtime an unseen member returns a truthy string, so `if (!result)` takes the match branch.
6. Narrow an external value (DB enum, cast, other team's widened union) with a predicate at the entry boundary and skip unknowns before any state changes.
7. Widening a union is never a one-file change: run `tsc` and treat every error as a site with an unstated assumption. `Record<Union,X>` lookups fail the mirror way (`undefined`).
8. A mapped key filter written `V extends T[K]` distributes and matches every optional key. Write `[V] extends [T[K]]`, and prove it with `Equals` asserts plus `@ts-expect-error` negatives.
9. `z.coerce.boolean()` is JS truthiness: `"false"`, `"0"` and `"no"` parse as `true`. For query params, headers and env use `z.enum(['true','false']).transform(v => v === 'true')`.
10. The same trap applies to `Boolean(process.env.FLAG)`. `z.coerce.number()` and `z.coerce.date()` are representation-aware; boolean is the odd one out.
11. Zod `.optional()` means "may be undefined", not "may be empty": `KEY=` in `.env` fails `z.string().url().optional()`. Preprocess `'' → undefined`, or allow `z.literal('')`.
12. A whole-env `safeParse` with a `{}` fallback reports an unrelated key as missing. Dump `result.error.issues` before trusting the higher-level "env not configured" error.
13. "The import resolves" ≠ "the dependency is declared". `npm ls <pkg>` saying `extraneous` plus 0 lockfile entries means the next `npm ci` (including CI) breaks.
14. Prove it in a scratch copy: `cp package.json package-lock.json <tmp>` then `npm ci --dry-run`. Never run `npm ci` in a real tree whose `node_modules` other worktrees may symlink.
15. Installed, importable and declared are three independent properties; check the one your failure mode depends on (pnpm can install a package that is not importable).
16. pnpm links `@scope/*` workspace packages only into other packages: a repo-root `scripts/` dir that is not one cannot import them. Check `require.resolve` from the script's cwd.
17. If a design has a root script import a promoted shared util, either keep a synced local copy with a drift-guard comment or workspace-ify the scripts dir; escalate the choice.
18. With TS project references, bare `tsc --noEmit` floods false `TS2307 Cannot find module '@app/…'`; run the package's own `tsc -b` typecheck script.
19. Scaffold Next.js by hand inside pnpm/Turborepo: `create-next-app` prompts even with flags. Use `pnpm-workspace.yaml`, `transpilePackages`, and turbo `dependsOn: ["^build"]`.
20. Keep native builds (e.g. Tauri's Rust `tauri build`) out of the turbo pipeline; the package `build` stays frontend-only. `.npmrc` `auto-install-peers=true` avoids peer noise.
21. `vi.mock` factories are hoisted: every spy or ref they use must come from `vi.hoisted(() => …)`, or it is undefined when the factory runs.
22. A mocked class needs a `class` declaration: `vi.fn().mockImplementation(() => ({…}))` wraps an arrow and throws "is not a constructor" under `new`.
23. Testing Fastify with `app.inject`: register the app's real error handler (`setErrorHandler`), or typed app errors surface as 500 instead of their 4xx.
24. Run vitest from the package dir whose config holds `setupFiles`; a root-relative run can skip the setup file and fail at module load on missing env.

## Examples
- 1: three detectors shipped with no UI option; the `Record<Union, Copy>` build went red within minutes when a teammate added a fourth.
- 3: a fifth BigQuery tier would have rendered a "not a known tier" badge in production; the `satisfies` guard made it a build error.
- 5: an alert evaluator typed `Breach | null` returned `"landing_page_unhealthy"` and fired a false breach on every campaign, every poll.
- 11: an empty `SENTRY_DSN=` crashed an app claiming Supabase env was missing; about 15 minutes to trace.
- 13: a reported "cannot find module dotenv" did not reproduce; `npm ls` said extraneous, the third verdict ("works by accident").
- 16: a shared normaliser could not be imported by root seed scripts; they kept a byte-identical copy with a drift guard.
