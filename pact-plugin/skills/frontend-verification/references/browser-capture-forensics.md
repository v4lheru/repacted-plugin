# Browser capture and deployed-frontend forensics

Context: screenshots, Playwright probes, headless Chrome and PDFs lie in specific, repeatable ways,
and a deployed frontend can be examined even behind SSO. Read this before you take a screenshot as
evidence, probe a page with `page.evaluate`, verify motion, render a PDF, or diagnose a blank page
or a "deployed but still broken" report.

## Canonical rules
1. A capture is evidence only if the SAME run asserted the page first: `location.href`, the resolved theme, `innerWidth`, and a known content count. Fail the capture rather than save it.
2. The Playwright MCP browser is SHARED between teammates: a peer can navigate or resize it mid-sequence. For load-bearing captures drive your own Chrome over CDP with its own `--user-data-dir`.
3. Set the theme with `Emulation.setEmulatedMedia` (`prefers-color-scheme`), not by writing `localStorage`; a single-writer theme script overwrites storage on load.
4. A static server whose directory was deleted and rebuilt keeps serving the old inode and 404s everything while the port stays healthy. Restart it after any rebuild that recreates the directory.
5. `python3 -m http.server` sends no no-cache headers: cache-bust probes (`?v=${Date.now()}`) and print the measured values, not only the verdict, so stale data is visible.
6. MCP `browser_take_screenshot` needs an absolute path inside an allowed root, and does not create directories. A failed targeted snapshot resets the page to `about:blank`; re-navigate first.
7. Playwright `screenshot()` defaults to `animations: 'disabled'` and captures the END keyframe. For motion pass `animations: 'allow'` and SEEK: pause every `getAnimations()` entry and set `currentTime`.
8. Assert motion from the animated property sampled at known times against the declared duration; identical readings across a swept parameter mean the probe is not wired, not that the system is stable.
9. `fullPage: true` resizes the viewport and restarts Recharts `ResponsiveContainer` mount animations, so charts capture empty. Judge charts from DOM geometry; check bars scale with the data.
10. `chrome-headless-shell --virtual-time-budget` advances neither `requestAnimationFrame` nor `performance.now()`. Verify rAF motion in a real browser by counting DISTINCT sampled values from t=0.
11. When the harness round-trip is slower than the animation, load the page in an iframe watched from its own `load` event; sampling afterwards only proves the end state.
12. An element screenshot taller than the viewport is stitched and paints sticky headers mid-image. Scroll the element to the top and capture the viewport instead.
13. `page.evaluate('() => {…}')` (a string holding a function) returns `undefined` silently. Wrap it `'(' + BODY + ')()'` and throw on an empty probe result.
14. Never call `navigator.clipboard.readText()` in automation: it blocks on a permission prompt until the tool times out. Assert the deterministic string the export builder produces.
15. After any hung or aborted evaluate, hard-reload and re-read in a separate call; a half-applied action can masquerade as a code bug.
16. Before asserting a source constant is in built bytes, ask what the minifier did (folded, inlined, mangled). Assert the value the running code uses; check a glob matched files before counting.
17. `fetch('/api/...')` from `page.evaluate` in an SPA can return the SPA's HTML shell, not the API; check state at the data source instead. SVG elements have no `.click()`; dispatch a `MouseEvent`.
18. Snapshot refs go stale after re-renders; re-snapshot. Click a visible dropzone, not a hidden file input. Dismiss auto-opened dialogs whose backdrop intercepts clicks.
19. Check `browser_console_messages` right after an action: a swallowed 403 shows only as "Failed to load resource", never as a toast.
20. Any `sandbox` value on an `<iframe>` makes Chrome show a broken-document icon instead of a PDF (measured across six variants). Gate on media type instead, and always offer an open or download link.
21. Headless Chrome cannot confirm a PDF painted (`embeds: 0`); it only distinguishes "viewer loaded" from "viewer refused". Say which you established.
22. PDF page budget: render a tall `@page`, read the lowest text extent with `pdftotext -bbox`. An extra page is usually a whitespace line box; pass `--run-all-compositor-stages-before-draw`.
23. A blank page with 404'd bundles may be a stale cached `index.html`. Curl the entry document from the pod AND the ingress, fetch one referenced asset, then check the entry document revalidates.
24. Serve the entry document `no-cache` and hashed assets immutable. Measure `fromDiskCache` on a warm load over CDP rather than reasoning from headers.
25. Behind SSO, the login shell still serves the deployed bundle: read its `<script src>`, curl the hashed asset, and EXECUTE the extracted reducer in Node. A marker in a bundle is not behaviour.
26. Date a deployment from the cluster (image digest, config version, ReplicaSet creation time), not from the repo; a screenshot dates itself by the features it shows.
27. Measure layout without building by serving reconstructed markup with the DEPLOYED stylesheet; run a positive control (a known width, `cssRules.length > 0`) and measure at two viewports.
28. When a feature "works" but users still complain, enumerate every action that WRITES the state the feature reads; the bug is usually an over-broad write.
29. Attempt shared SSO accounts once and stop at the credential prompt; never retry into a lockout.

## Examples
- 1: six "verified" captures were of a 404 page served by a static server whose `dist/` had been recreated.
- 7: six full-page screenshots of a 9 s ring animation all showed the arc at the end keyframe.
- 9: a blank-chart screenshot matched the exact bug QA was hunting, while the DOM held correctly proportioned bars.
- 10: a lead twice concluded a logo draw was not animating from headless captures; a real browser showed it completing.
- 23: minutes after a verified release, a blank page traced to the browser's cached `index.html` naming the previous bundle.
