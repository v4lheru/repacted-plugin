# CSS tokens, theming, fonts and brand

Context: how colour tokens, theme switches, webfonts and brand palettes fail silently between
the source and the rendered page. Read this before you write or review Tailwind v4 tokens, a
light/dark switch, an `@font-face`, a chart palette or a brand extraction, and whenever a UI
"looks plain", "has black boxes" or "renders in the wrong font".

## Canonical rules
1. Judge Tailwind output from the COMPILED stylesheet, never the config or the JSX. Build, `tsc` and tests all pass while every colour is dead.
2. v4: a CSS variable as an arbitrary value is `text-(--x)`. The v3 form `text-[--x]` compiles to `color: --x`, which the browser drops (measured, Tailwind 4.3.3).
3. Catch it with a whitespace-tolerant check on the built CSS: a declaration whose value starts with `--` (`:\s*--`). Unminified output has a space; `:--` alone only matches minified CSS.
4. v4 `border-*` and `divide-*` default to `currentColor` (v3 used gray-200); set the colour explicitly (measured, Tailwind 4.3.3).
5. An unresolvable CSS `@import` (e.g. a half-installed package) makes the build emit NO stylesheet at exit 0. The built-CSS check must fail on zero files and on a byte floor.
6. Tailwind's scanner reads class-like strings in COMMENTS: quoting the broken syntax in a comment regenerates the broken rule. Describe it in prose.
7. Falsify the CSS check: synthesise each failure, confirm `var(--x)` does not trip it, then reintroduce the real defect and watch the build stay green while the check goes red. Run it inside `npm test`.
8. v4 applies `/NN` opacity to theme colours itself (`color-mix(in oklab, … NN%, transparent)`), including `@theme inline` tokens like `rgb(var(--ink))` (measured, Tailwind 4.3.3).
9. "v4 tokens need `<alpha-value>`" is FALSE: v4 emits the placeholder literally, the browser drops the declaration, and `bg-x` AND `bg-x/40` render transparent (measured).
10. SVG `fill`/`stroke`/`stop-color` attributes take no `/NN` modifier: use `var(--color-x)` plus `fill-opacity`/`stop-opacity`, or a `fill-x/50` class.
11. `rgb(var(--t))` is valid only when `--t` holds a channel triple (`200 40 20`); around a full-colour token it is invalid and SVG paints black. Know which kind each token is.
12. A black box, an invisible gradient or a missing stroke with no console error: grep the chart's attributes for token references and check each token's compiled value.
13. v4 `@theme inline` tokens can reference runtime variables, so a brand swap is one `setProperty` on `:root`, no rebuild. Define derived shades (hover, pressed) in OKLCH.
14. A `:root` fallback beside `[data-theme="x"]` blocks has EQUAL specificity (0,1,0): source order decides and one mode never renders. Use `:root:not([data-theme])`.
15. Test a theme fallback by REMOVING the attribute in devtools, not by disabling JS (a server-rendered attribute makes the no-JS test pass with or without the fallback).
16. Role-completeness check per theme token: non-empty in both modes, NOT-equal where modes must differ, EQUAL where they must match. A missing custom property computes to `""`.
17. Flash-free toggle: default palette on `:root` (attribute absent), alternate under `[data-theme="light"]`, a tiny inline `<head>` script before the token stylesheet, one shared colour-var helper.
18. Theme toggle a11y: native `<button role="switch" aria-checked>` with an action-shaped label; drive the control's visual from a `data-*-pref` attribute the bootstrap sets, not `:checked`.
19. Verify theming LIVE: read computed colours before and after a real click, and reload with the stored preference to confirm the attribute is set before paint.
20. Recharts under class dark mode needs two colour paths: CSS-var tokens for DOM chrome, context-resolved JS hex for SVG marks, from separately validated dark steps (never an auto-flip).
21. Brand colours are surface and type tokens, not a series palette. Run the dataviz validator per mode against the real surface; expect about two to pass, then add new hue families.
22. A sub-3:1 accent contrast warning obliges a relief channel (direct labels and a table view); comment it so nobody tidies it away. Record the validator commands beside the palette.
23. Two stops of one ramp abutting in a stacked meter read as one fill and overstate progress. Separate segments with the canvas colour (`box-shadow`), and never reuse the progress hue for scope.
24. When a spec restricts an accent to an allowlist, grep every use (variable and hex) build-wide and diff against the allowlist; drift arrives one defensible component at a time.
25. A custom property in `@font-face` `src` never substitutes (at-rules attach to no element), so the face is dropped silently. Interpolate the bundler URL into the CSS TEXT.
26. `document.fonts.check()` returns true with no face loaded, and 400-vs-500 width checks pass on the fallback stack. Assert `document.fonts` entries (family, weight range, `status === 'loaded'`).
27. Compare rendered width against a known-good baseline or an explicit missing-family probe. The one self-firing signal is the console's "preloaded but not used" warning.
28. Vite `?url` imports sanitise bracketed filenames and preload from static HTML. A copied WOFF2 wins on bytes (about 55% smaller than TTF); keep it only with its regeneration command in the repo.
29. After converting a variable font, confirm the `fvar` and `gvar` tables survived, or it renders at one weight. A plain `.css` file cannot reference a `?url` import.
30. Extract a live site's brand from COMPUTED styles (Playwright: backgrounds, text colours, CTA, `document.fonts`), then build an OKLCH scale holding hue and tapering chroma.
31. When a faithful V1 is "not bold enough", keep the content and change the visual frame around one committed thesis and one scarce accent; keep V1 intact for the diff.
32. Exec dashboards: verify a component library's maintenance before locking it (Tremor had a 2024 acquisition caveat), and use `tabular-nums` for numerics so sorting does not jitter.

## Examples
- 2: a whole app written with `text-[--color-…]` shipped "1px black hairline boxes and no greys"; `var(` appeared zero times in the stylesheet.
- 9: three of four chart bugs in one dashboard (solid-black treemap, invisible gradient, missing reference line) came from tokens carrying `<alpha-value>`.
- 14: two preparers independently prescribed a `:root` dark fallback that would have killed light mode.
- 23: `1 done, 8 started of 25` rendered as a bar that read "nine of 25 done".
- 26: a dropped face measured 712.27 px vs 734.95 px for weights 400 and 500, identical to `system-ui`.

## Lookup: the T-TW measurement (2026-09-26, tailwindcss 4.3.3 CLI, Chrome for Testing 148)
Recipe: `npm i tailwindcss@4 @tailwindcss/cli@4 playwright-core` in a scratch dir; an `input.css` with
`@import "tailwindcss"`, `@theme { --color-brand: <hex 0c2238, rgb 12 34 56> }`, `@theme inline { --color-ink: rgb(var(--ink));
--color-legacy: rgb(var(--ink) / <alpha-value>) }`, `:root { --ink: 200 40 20 }`; a page using each class
below; `tailwindcss -i input.css -o out.css` (and `--minify`); serve over HTTP (file:// blocks `cssRules`)
and read `getComputedStyle` in headless Chrome.

| Case | Compiled | Rendered |
|---|---|---|
| `bg-brand/50` | `color-mix(in oklab, var(--color-brand) 50%, transparent)` | `oklab(… / 0.5)` |
| `bg-ink/40` (no `<alpha-value>`) | `color-mix(in oklab, rgb(var(--ink)) 40%, transparent)` | alpha 0.4 |
| `bg-legacy`, `bg-legacy/40` | `rgb(var(--ink) / <alpha-value>)` literally | `rgba(0, 0, 0, 0)` |
| `text-[--x]` / `text-(--x)` | `color: --x` / `color: var(--x)` | inherited black / correct |
| `border`, `divide-y` | no colour set | the parent's `color` |
| SVG `fill="var(--color-brand)"` / `var(--color-legacy)` | — | correct / black |
| SVG `fill="rgb(var(--ink))" fill-opacity="0.4"` / `rgb(var(--color-brand))` | — | correct, 0.4 / black |
| `stop-color="var(--color-brand)" stop-opacity="0.3"` / `fill-brand/50` class | — | correct, 0.3 / `/ 0.5` |

The grep hint `:--` found 0 hits in the unminified output (`color: --x`) and 1 in the minified output.
