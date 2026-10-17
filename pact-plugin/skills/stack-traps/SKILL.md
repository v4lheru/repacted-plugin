---
name: stack-traps
description: "Use when Node/TypeScript or Python backend tooling misbehaves: module resolution, typegen and strictness gaps, monorepo scripts, vitest mocks, pydantic-settings, async event loops, office-document libraries, text normalisation, venvs."
---

# Stack traps

Context: Node/TypeScript and Python backend tooling traps that cost real debugging time. A type
guarantee stops short of runtime, a validator accepts what it should not, a package resolves
without being declared, an event loop or worker runs the code somewhere unexpected. Each rule
was measured in a real project.

## Workflow
1. **Name the layer that owns the guarantee** (type checker, runtime validator, package
   manager, event loop, worker, document library) and ask whether it enforces anything at
   runtime. Open the matching reference.
2. **Reproduce the trap in the smallest script before changing code.** Check the INSTALLED
   version (`npm ls <pkg>`, `pip show <pkg>`), not the manifest: several rules are version-bound.
3. **TypeScript guarantees stop where type-checking stops** (the general rule is in
   repacted:verification-discipline `vd-c2b`). Add a runtime check at the boundary, and make
   every type guard fire in both directions before trusting it. → `references/typescript-node.md`
4. **Validation and env parsing:** strings arrive from query params, headers and `.env`; check
   what the schema does with `"false"` and with `""`. → `references/typescript-node.md`
5. **Python async, frameworks and workers:** confirm which loop, middleware layer or process runs
   the code, and falsify a guard by moving its threshold. → `references/python-backend.md`
6. **Documents and text:** inspect the structure before mutating (python-pptx), and fold text
   with an explicit table, never `normalize()` alone. → `references/python-backend.md`
7. **Report with the tool version** and the command that reproduced it.

## Map
- `references/typescript-node.md`: `satisfies` and `never` limits, distributive conditionals,
  zod coercion and empty-string env, npm extraneous, pnpm workspace imports, `tsc -b`,
  Turborepo scaffolds, vitest hoisting and class mocks, Fastify `inject`.
- `references/python-backend.md`: asyncio loop poisoning, exception-idiom reuse, FastAPI receive
  exceptions, Starlette caching, Celery beat and visibility timeout, python-pptx templates,
  Unicode folding.

## Related skills
- repacted:verification-discipline: `vd-c1b` (fixtures and mocks that cannot fail) and `vd-c2b`
  (type-level guarantees).
- repacted:postgres-supabase: Postgres drivers (postgres-js, asyncpg, drizzle, pg-boss).
- repacted:frontend-verification: UI rendering, bundler output, Next.js and Astro routing.
- repacted:shell-tooling-traps: test counts, rtk-rewritten output, macOS vs GNU tools.
- `repacted:pact-coding-standards`: general coding conventions.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
