# CSS layout, specificity and measurement

Context: layout and specificity failures that look intentional in a screenshot and pass every
static check, plus the measurement techniques that prove them. Read this before claiming something
scrolls, fits, is hidden, is centred, points a direction or "works at all breakpoints", and when a
scoped style does not apply.

## Canonical rules
1. jsdom reports every height as 0: scroll, clip, overflow and in-viewport claims pass whether the layout works or not. Measure in a real browser.
2. Cheap real-browser harness: a throwaway page publishes `getBoundingClientRect`, `clientHeight`/`scrollHeight` and `scrollWidth` into a `<pre>`, read via `chrome --headless=new --dump-dom`.
3. For a "it scrolls" claim, assert the content really exceeds the box; a panel that happens to fit proves nothing.
4. Headless Chrome has a 500 px minimum window width; use a CDP device-metrics override for true phone widths. A symlinked Chrome binary will not boot; use a wrapper script.
5. `min-height` on a flex child defeats shrink; inside `overflow: hidden` it clips with no scrollbar. Use `min-h-0 flex-1` down the whole chain, never a better constant.
6. Prove causation when the computed excess (`min-height − (available − siblings)`) EQUALS the measured overflow (`scrollHeight − clientHeight`); an unequal suspect is exonerated.
7. Grep for `calc(100vh` and `min-h-[` together; the bug clusters. A hardcoded chrome offset is usually wrong in both directions (a header measured 61 px and 69 px against a `65px` constant).
8. Two `h-full` cards stacked in ONE grid cell overflow the row onto the next section; make them sibling cells. Detect overlap by `getBoundingClientRect` intersection, not screenshots.
9. A component rule that sets `display` (scoped, 0,2,0) outranks the UA `[hidden]{display:none}` (0,1,0). Guard it `:not([hidden])` and state `[hidden]{display:none}` explicitly.
10. Test hidden state by COMPUTED `display` on a fresh load at the collapsed viewport, plus `checkVisibility() === false`; `hasAttribute('hidden')` passes in the broken world too. Re-check the open path.
11. A `.cls[data-x]` hook (0,2,0) beats every per-variant `.cls--name` rule (0,1,0) regardless of source order or media query. Keep a trait whose value differs per breakpoint at single-class specificity.
12. Scoped styles never reach markup outside the component's own template (a class passed as a prop lands on the child's root with the CHILD's scope id; rendered Markdown gets no scope class).
13. Fix both with a wrapper you own plus `:global()` on the descendant (`.wrapper :global(.x)`). A bare `:global(.x)` can lose to the child's own scoped root rule.
14. The compiled specificity depends on Astro's `scopedStyleStrategy` (attribute vs `:where`); read the BUILT selector and check which element carries which `data-astro-cid-*` before reasoning about wins.
15. Markdown `**bold**` ships `<strong>` at 700; override it where the type system has no 700. Inline links in body text need the small-text contrast step AND an underline (WCAG 1.4.1).
16. Astro: a `{/* comment */}` cannot sit between HTML attributes; put attribute rationale above the tag.
17. A responsive claim checked at breakpoint endpoints says nothing about the band between. Sweep `breakpoint−1`, `breakpoint+1` and steps up to the container max; assert `scrollWidth <= clientWidth`.
18. "Renders but too narrow to use" is the same class and needs a different probe: measure the element's width against what its content needs.
19. Attribute a layout regression by hiding the suspect elements in the LIVE DOM and re-measuring in the same run; no file is touched, so it is safe in a shared tree.
20. A breakpoint is often a multi-file contract (a header media query, a sheet's `min-width`, a JS constant); grep the number app-wide before changing it. Report headroom, not just pass/fail.
21. A stale Tailwind `grid-cols-N` after removing a child leaves a silent hole. Compare the widest-breakpoint track count to direct children; only `tracks > children` is a certain defect.
22. Check header/body grid pairs separately: `grid-cols-7` over `grid-cols-6` renders a tidy table with every label one column off. Count `gridTemplateColumns` tracks at the widest breakpoint.
23. A flex-centred box can measure 0.00 offset while the ink is off-centre. Measure `getBBox()` plus half the stroke, and cap-height via canvas `measureText`; fix what the ink numbers prove.
24. Often the mechanism is EXTENT, not centre: a large glyph beside small cap text overhangs the cap line. Shrink the artwork rather than nudging toward a perception the numbers contradict.
25. A CSS-rotated directional glyph always looks deliberate. Transform its unit vector through `new DOMMatrix(getComputedStyle(el).transform)` and check where the tip lands; include a negative case.
26. `vector-effect="non-scaling-stroke"` moves `stroke-dasharray` and `stroke-dashoffset` into device pixels, so user-unit dash periods tile wrongly and `pathLength` looks ignored.
27. With `pathLength="100"`, the dash period must exceed the path (`14 300`, not `14 86`) or the dash reappears at the start. Verify `(dash + gap) / getTotalLength()` at two viewport widths.
28. Pass dash values as custom properties, not an inline `stroke-dasharray`: the inline declaration outranks the reduced-motion rule that must remove the dash.
29. Register HTML cards on SVG anchors with a wrapper `aspect-ratio` equal to the viewBox's aspect (emitted from the same constants), so `meet` letterboxes by zero and percentage positions land exactly.
30. In a radial card layout the binding constraint is card HEIGHT (wrapped lines fill the ring's interior). Derive the breakpoint from the tallest rendered card; prefer collapsing to a vertical sequence.

## Examples
- 5: three instances in one small app; a panel had 95 px unreachable, exactly `400 − (574 − 269)`.
- 9: a mobile nav sheet rendered open on every phone load; a desktop media query hid it on every wide check.
- 11: three chevrons pointed across the page at 375 px because a `data-dir` hook outranked the narrow-layout rule.
- 17: six nav links overflowed the header from 821 to 1024 px, fine at 820 and at 1180 and above.
- 26: a one-arc dash tiled 1.79 times, so two arcs chased each other and the count changed with width.
