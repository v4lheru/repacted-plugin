# Accessibility and motion verification

Context: focus, keyboard, accessible-name and reduced-motion checks that pass while the
experience is broken for exactly the people they protect. Read this before you claim a focus ring
paints, a panel traps focus, a control is announced, a disabled option is explained, or motion
respects `prefers-reduced-motion`.

## Canonical rules
1. `:focus-visible` follows the browser's keyboard-modality flag. A programmatic `el.focus()` rings only if that flag is set; after a pointer interaction it does not. Test both arms.
2. Delay does not matter: synchronous, rAF, `setTimeout` and 5 s-later focus moves all ring while the modality is keyboard. A mouse click clears the flag; a keypress restores it.
3. Reset modality explicitly between test arms (arms contaminate each other in order), and label an arm by what it DID, not what you meant it to isolate.
4. Drive focus with a real key (`page.keyboard.press('Tab')`, or CDP `Input.dispatchKeyEvent`, keyCode 9). A bare `.focus()` probe can read the UA default outline and pass with no site focus style.
5. Assert `el.matches(':focus-visible')` plus the computed outline AND the FULL `box-shadow` string; design-system primitives ring via a box-shadow after transparent entries, with `outline: none`.
6. A/B keyboard against mouse on the same element. A text input always matches `:focus-visible`, so a ring on an input says nothing about a div.
7. Walk the real tab order with repeated Tab, not selectors; it verifies order and exposes the unreachable. Measure a suspect on a page you did not write before blaming yourself.
8. Tabbing forever inside a panel proves LOOPING, not trapping. Probe both boundaries: Tab off the last item, Shift+Tab off the first. Then test that leaving focus does not strand the user.
9. Cross-check the component config (`modal`, `trapped`): Radix `FocusScope` loops unconditionally while trapping follows `modal`. Re-probe when config and symptom disagree.
10. A `disabled` control is skipped by focus but stays in the accessibility tree: its `aria-label` is dead for keyboard users, live in browse mode. Name which channel carries the requirement.
11. Positive-control "unreachable" with `element.focus()` from JS; if that fails too, it is genuinely disabled. `aria-disabled="true"` stays focusable when the reason must reach users on focus.
12. `disabled` also breaks your probe: `el.focus()` on it is a silent no-op. Log `document.activeElement` after every focus call and assert it changed.
13. A button that disables itself on activation drops keyboard focus to `<body>`. Prove the cause with an enabled sibling doing the same action, read focus after re-render, then sweep for the shape.
14. Fix ranking for self-disabling buttons: move focus before the disable lands > keep it enabled as a no-op > a roving fallback.
15. CSS can swap an icon by state but can never supply an accessible name (`aria-label` is not settable from CSS; pseudo-element `content` is not a reliable name).
16. Prefer a fixed, ACTION-shaped name ("Switch between dark and light theme") to a state-bearing one; the icon previews the action's target. Verify in the accessibility tree, every state.
17. Global reduced-motion CSS does not stop `requestAnimationFrame`, canvas, WebGL or SMIL motion. Guard in JS with `matchMedia` plus its `change` listener; fail closed without `matchMedia`.
18. `animation: none` on an element does not stop animations on its `::before`/`::after` (`animation` does not inherit). Name each animated pseudo-element in the reduced-motion block.
19. Give a travelling mark `display: none` under reduced motion (frozen mid-path it reads as an artefact); give a mark with a resting state `animation: none` plus its resting values restated.
20. Prove reduced motion: count `document.getAnimations()` whose target is inside the subtree, with and without the media emulated; expect N → 0, and identical layout metrics.
21. For JS motion, emulate `reducedMotion: 'reduce'` and sample the animated property over more than one cycle: the reduce run shows ONE distinct value where the normal run shows many.
22. Dash values for animated SVG strokes go in custom properties, not an inline declaration, so the reduced-motion rule can remove them (see css-layout rule 28).

## Examples
- 1: QA first recorded "async focus never rings" from an arm that ran right after a mouse click, and retracted it after measuring.
- 4: a `.focus()` probe read a 3 px UA outline in the wrong colour; a real Tab press showed the site's 2 px brand ring.
- 5: truncating the `box-shadow` string showed only transparent entries and nearly filed a CRITICAL.
- 8: a 26-Tab sweep was reported as a trap; Shift+Tab off the first item left the panel and dismissed it.
- 13: clearing a filter threw a keyboard user about 30 Tabs back to "Skip to content".
