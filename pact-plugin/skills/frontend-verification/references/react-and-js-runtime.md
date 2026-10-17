# React state and JS runtime traps

Context: React, TanStack Query and plain-JS runtime behaviour that renders a blank, stale or wrong
UI while every type and test stays green. Read this when a panel is blank or never updates, a
default "should apply", results must survive tab switches, a new request parameter changes what
renders, or a chart library lays out wrongly.

## Canonical rules
1. React Query v5 (`networkMode: 'online'`) PAUSES a query when offline: `status 'pending'`, `fetchStatus 'paused'`, `isLoading` false, no data, no error. Branching on loading/error/data renders blank.
2. Handle `fetchStatus === 'paused'` explicitly; if a codebase never mentions `fetchStatus` or `isPaused`, every query surface has this hole. Reproduce with `onlineManager.setOnline(false)`.
3. Look for what should have happened twice: `retry: 1` failing predicts two console errors, a paused query predicts one.
4. A non-nullable field constrains the POPULATED case only (`data → view`), never `¬data`. Write the implication out before citing a type as proof a state is unreachable.
5. A test helper that falls back to a wider scope (`?? document.body`, `|| {}`) answers a bigger question, almost always yes. Throw when the region is missing, or add the `data-testid`.
6. "Renders something" ≠ "says something": hold the states meant to speak (error, empty, paused) to text, and loading skeletons to their element.
7. TanStack Query 5.90 cancels an in-flight query on last-observer unmount only if `queryFn` READ `signal`; destructuring or spreading the context counts. Take no parameter to let a billed call finish.
8. With `staleTime`/`gcTime: Infinity`, remounting onto an ERRORED query with no data still refetches unless `retryOnMount: false`. Pin each refetch trigger with a counting test plus a mutant.
9. Radix Tabs (1.1.x) UNMOUNTS inactive `TabsContent`; tests activate a tab with `fireEvent.mouseDown`, not `.click()`. Verify unmount behaviour in `node_modules`, not docs.
10. To keep non-deterministic (LLM) results across tab switches, lift only the key and view state above the unmount boundary, and let React Query own results keyed by a monotonic `runId`.
11. Do not keep hidden panels mounted to preserve state: duplicate `data-testid`s break selectors, and hidden observers still refetch on reconnect.
12. `useSyncExternalStore` needs a referentially STABLE `getSnapshot` (cache it in the source, invalidate on mutation), a real `subscribe`, and a module-level server snapshot, or React loops.
13. If a parent re-render already drives the value, read it directly; `useSyncExternalStore` is only for an independent change source (clock, socket, external store).
14. A guard that runs on the INITIATING side of a state transition must take the intended post-transition state as a parameter; the flag the transition sets is still stale in that closure.
15. Object spread keeps an explicit `undefined`: `opt: cond ? v : undefined` overwrites a library default (e.g. `throwOnError`). Spread the key conditionally: `...(cond ? { opt: v } : {})`.
16. Verify a guarantee on a surface where exactly ONE mechanism provides it; a page with redundant paths cannot tell you which one works.
17. `new Error(undefined).message` is `''`, not `"undefined"` (template interpolation does give `"undefined"`). Reproduce the old path in `node -e` before writing a characterisation test.
18. When a release makes the UI ALWAYS send a new parameter, diff the response with and without it and find every branch keyed on the new field. A capability that exists may no longer be reachable.
19. Instructional copy is a testable assertion ("Select an article to…"). Click it in the default state; React handlers are invisible in `outerHTML`.
20. `@visx/hierarchy` `<Tree>` given both `size` and `nodeSize` uses `nodeSize` and ignores the viewport (a flat row top-left). Pass only `size`.
21. Recharts v3 `Tooltip` formatter values are `ValueType | undefined`, not `number`: coerce with `Number(value)`. Do not wrap SVG chart children for bounding-rect overlays; wrap the parent `<div>`.
22. TipTap: set `immediatelyRender: false` in `useEditor` to avoid SSR hydration errors; tables need separate extension packages; the document model is portable JSON (`getJSON`).
23. Click-to-load video facade: an `<a href>` to the watch page, one delegated listener, validate the id BEFORE `preventDefault()`, set the iframe `title`, and move focus to it after replacing.
24. Only `mqdefault` (320x180) and `maxresdefault` (1280x720) YouTube thumbnails are 16:9, and `maxresdefault` 404s for sub-720p masters: use `mqdefault` in `src`. Reserve the box twice.
25. Demo mode for an auth-gated dashboard: a BUILD-time mode flag, a data-source adapter at the hook layer, a swapped auth provider, and CSS-variable branding. Prove "no /api calls" in the network panel.
26. Derive mock fixtures from the CONSUMING components, not the server endpoints; vary mock latency per call so hooks do not resolve in lockstep.
27. To show why a Postgres FTS result matched, use `ts_headline` with retrieval's regconfig and query parser; split on control-char sentinels BEFORE unescaping HTML; account for every query.

## Examples
- 1: a production panel went blank; four failure mechanisms were ruled out before the paused state was found.
- 5: the same tests gave 2 failures with the `document.body` fallback and 4 assertion-level failures once scoped.
- 12: a no-op `subscribe` crashed every route in an app with "Maximum update depth exceeded".
- 15: a per-call-site opt-out disabled `throwOnError` at every call site; a nine-page sweep passed because another hook still threw.
- 18: a pre-selected source filter routed every default search to a branch whose rows could not be selected.
