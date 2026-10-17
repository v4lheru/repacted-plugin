---
name: frontend-verification
description: "Use when building or verifying UI (React, Next.js, Astro, Tailwind v4, CSS) or judging UI from a screenshot, DOM or Playwright run: build-only breaks, tokens, theming and fonts, layout and specificity, focus, motion and a11y, capture traps, proving the rendered state."
---

# Frontend verification

Context: UI claims that are true of the source or the dev server but false of the rendered,
built or deployed page. Each rule was measured in real work. The question is always: which
artifact did you check, and could your check have failed?

## Workflow
1. **Decide which artifact answers the question** (source, compiled CSS or bundle, dev server,
   production build, deployed URL) and check THAT one. `next build` green is not `tsc` green, and
   neither proves anything rendered. → `references/build-and-routing.md`
2. **Tokens, theming and fonts: read the COMPILED CSS and `document.fonts`, never the class names
   or the config.** In Tailwind v4 never keep v3's `<alpha-value>` in a token (it renders
   transparent), and write CSS variables as `text-(--x)`. → `references/css-tokens-theming-fonts.md`
3. **Layout claims: measure in a real browser** (jsdom reports every height as 0). Sweep widths
   INSIDE each breakpoint band, and prove causation with arithmetic that closes exactly.
   → `references/css-layout-and-measurement.md`
4. **Screenshots and captures: assert the page state in the same run first** (URL, theme,
   viewport, a known count), then disable the capture traps (animations disabled by default,
   virtual time freezing rAF, full-page resizes). → `references/browser-capture-forensics.md`
5. **Focus, motion and a11y: test with the real input modality** (a real Tab press, both keyboard
   and pointer arms) and with reduced motion emulated, and read the accessibility tree.
   → `references/a11y-and-motion.md`
6. **React state or runtime surprises** (blank panel, stale guard, default not applied, results
   lost on tab switch): → `references/react-and-js-runtime.md`
7. **Report which artifact, which viewport and which browser you checked**, and what your check
   would have shown if the thing were absent (repacted:verification-discipline).

## Map
- `references/build-and-routing.md`: which artifact, Next.js edge/instrumentation, route exports,
  `router.replace`, `loading.tsx`, render harnesses, dev-only seams, Vite 8, Astro prerender and
  Starlight, scaffolding.
- `references/css-tokens-theming-fonts.md`: Tailwind v4 traps with the T-TW measurement, theme
  switches and fallbacks, Recharts dark mode, brand palettes, `@font-face` and font checks.
- `references/css-layout-and-measurement.md`: real-browser measurement, flex and grid clipping,
  `[hidden]` and specificity, scoped styles, breakpoint bands, ink centring, SVG strokes.
- `references/browser-capture-forensics.md`: capture assertions, Playwright defaults, headless
  limits, PDFs, stale entry documents, forensics on a deployed bundle behind SSO.
- `references/a11y-and-motion.md`: focus-visible modality, traps vs loops, disabled
  controls, accessible names, reduced motion for CSS, pseudo-elements and rAF.
- `references/react-and-js-runtime.md`: React Query paused state and cancellation, Radix tabs,
  `useSyncExternalStore`, spread and `undefined`, reachability of old branches, chart libraries.

## Related skills
- repacted:verification-discipline: artifacts vs descriptions, checks that cannot fail, deploy proof.
- repacted:stack-traps: TypeScript and Node tooling traps that are not about rendering.
- `repacted:pact-testing-strategies`: test design in general.
- repacted:security-review-patterns: LLM output rendered into the UI (this skill covers only the
  rendering mechanics).

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
