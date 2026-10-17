# Build, bundling and routing (Next.js, Astro, Vite)

Context: failures that exist only in one artifact (the production build, the client bundle, the
edge compile, the container) while the source, `tsc` and the unit suite stay green. Read this to
decide WHICH artifact answers your question, and whenever you touch routing, edge-evaluated files,
build config, dev-only seams or scaffolding.

## Canonical rules
1. Name the artifact that answers the question (source, compiled CSS or bundle, dev server, production build, deployed URL) and check THAT one. A green build is not a typecheck, nor a render.
2. `next build` typechecks only files reachable from the app graph. Run `tsc --noEmit` too, and never report "build passes" as "it typechecks" (Next 15.5).
3. `instrumentation.ts` compiles for edge AND node: a `NEXT_RUNTIME` guard around a dynamic import still bundles node deps into edge. Put node work in a separate file imported only under the guard.
4. Only `next build` catches edge-bundle breaks (`Can't resolve 'crypto'`); make it the gate for any change to `instrumentation.ts`, `middleware.ts` or other edge-evaluated files.
5. setInterval in instrumentation.ts is in-process cron only on long-lived servers; see repacted:vendor-gotchas railway.
6. A dead `eslint-disable` for a rule the config never registers fails `next build`'s lint pass ("Definition for rule … was not found"). Delete the directive; do not widen the ruleset.
7. An eslint override keyed on a DIRECTORY makes identical code warn there and break `next build` elsewhere, while vitest stays green. Read `overrides` first; key them on filename patterns.
8. `next.config.ts` makes `typescript` a RUNTIME dependency (Next 15.5 transpiles it on boot): pruning devDeps kills `next start`. Rename to `.js` or ship the full tree, with a loud comment.
9. A dependency's behaviour belongs to the RESOLVED version (lockfile, `node_modules/<pkg>/package.json`), not the declared range floor. When testing a boot, assert an app-level 200.
10. A route file (`route.ts`) may export only handlers and known config keys. Other exports can fail the route-type check months later when an adjacent type shifts; keep helpers in a sibling module.
11. Isolate a build break with a one-variable chain of cold builds made by `git archive <ref> | tar -x`, never `git stash` in a shared tree. Prove a "pure move" by diffing the extracted symbols.
12. An App Router handler HOF must be generic (`<Req extends Request, Rest extends unknown[]>`) and forward the route context through `...rest`, or strict TS rejects `NextRequest` handlers.
13. Next middleware auth: deny by default, and never bypass on a header the client can set.
14. `router.replace()` is a navigation: each call fetches an RSC payload. For URL-synced filter state use `history.replaceState(history.state, '', url)`; check the network panel for `_rsc=`.
15. Next 15.5 prefetches dynamic routes anyway; `loading.tsx` wins by adding a segment Suspense boundary (first paint goes flat across latency). It does not shorten a failing read.
16. Make skeletons say "loading" in words, show no digits, and never sit inside a `<table>`. Before splitting a page with `<Suspense>`, check no element reads state from both halves.
17. A server page passing a function prop to a client component fails to mount the whole subtree while the route returns 200. Fetch the page and count rendered `data-testid`s; grep for `digest`.
18. Route handlers and server components are SEPARATE module instances: a module-level `Map` is not shared. Hold shared state (and DB pools) on `globalThis`.
19. Vite 8 vitest: JSX config key is `oxc`, not `esbuild` (ignored silently). The symptom is "no tests" (a parse error is a collection error); check the FILE count.
20. A `NODE_ENV`-guarded dev prop still ships its NAME if destructured in the signature. Read it inside the guard, then grep the built chunks with a positive control.
21. "Absent from client chunks" and "absent from the build" are different claims: a dev route is still compiled and often prerendered.
22. A dynamic route whose `getStaticPaths` returns `[]` builds zero pages at exit 0. Prove the template with a branch-exercising fixture, then prove its removal against a non-empty dist.
23. A zero-page static build exits 0; any check needing emitted routes (sitemap, route collisions) records a false pass there. Run such checks on the first commit with real routes.
24. Astro `output: 'server'` skips build-time validation only for pages that are NOT `prerender = true`; prerendered pages still validate. Test against real pages, not scaffold stubs.
25. A prebuild validator in an npm script must not use a pipe (`/bin/sh` has no `pipefail`): `esbuild … && node out.mjs`. Declare every build- or runtime-imported package in `dependencies`.
26. Protobuf/gRPC SDKs with bare extensionless ESM imports need `transpilePackages`; keep only Node-native packages (connect-node, http2) in the server-external list.
27. Scaffold `create-next-app` into a temp dir and `rsync` into a non-empty cwd. shadcn v4 init needs `--template --base --preset` explicitly or it prompts even with `--yes`.
28. shadcn Nova's `@theme inline` expects `--font-sans`: map the template's Geist variable to it. Re-skin a shadcn theme by changing CSS-variable VALUES only, never the token names.
29. Tailwind v4 uses `@tailwindcss/postcss` and `@import "tailwindcss"` with no config file; tokens and the v3→v4 traps are in `css-tokens-theming-fonts.md`.
30. A custom Astro `src/pages/index.astro` silently OVERWRITES Starlight's `/` by write order (the build log is the only signal). Remount Starlight through `docsLoader({ generateId })`.
31. Leave Starlight's `autogenerate.directory` as is (it matches paths relative to the collection root). A malformed `generateId` yields a blank page, not an error (Starlight 0.36).
32. Mirror Starlight's theme contract exactly: `starlight-theme` key, `""` means auto, `data-theme` only `dark|light`, a synchronous inline script above every stylesheet.
33. Removing a dependency removes its transitive capabilities too (Starlight brings sitemap and Pagefind). Read its own `package.json` before writing "remove X".

## Examples
- 3: a `postgres` import behind a runtime guard broke `next build` with missing `crypto`, `stream` and `perf_hooks` in the edge bundle.
- 7: 61 test files under a relaxed directory, 7 co-located ones strict; the build broke on code that passed everywhere else.
- 14: typing seven characters issued seven `_rsc=` requests while the fetch debounce held the API to one call.
- 15: first paint tracked emulated latency (432 ms at 400 ms) without `loading.tsx` and stayed at 8 ms with it.
- 17: eight fixture instances failed to mount while the route returned 200, `tsc` and ESLint were clean and 645 unit tests passed.
