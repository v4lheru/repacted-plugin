---
name: n8n-validation-expert
description: "Use when an n8n workflow fails a 400, activation or a run: what each error or warning means and how to fix it, operator rules, broken connections, false positives. API-first."
---

# n8n Validation Expert

How to read and fix n8n validation problems when you work over the REST API (no n8n MCP
server). Instance behaviour, the write recipe and the endpoint table live in `repacted-n8n:n8n-ops`.
The MCP server's validator, profiles and auto-fix tools are described in
[MCP_ERA.md](MCP_ERA.md) (MCP-era only).

---

## Validate early, validate often

Validation is iterative: expect 2-3 check → fix rounds, and fix errors before warnings. A round
that ends in "valid" is the start of testing, not the end.

## The API-first validation loop

The REST API has no validate endpoint. These are the checks it does give you, cheapest first:

1. **Offline structural check** of the workflow JSON before any write: unique node names and
   ids, every connection endpoint resolves, every `$('Node')` reference resolves, IF outputs
   0/1, Switch indices within its rules (+1 fallback), every node reachable, the operator rules
   below. The full list is n8n-ops `build-expressions-and-code` §Offline validation.
2. **Schema check by the write itself.** `POST /workflows` and `PUT` are strict
   (`additionalProperties: false`). A 400 "must NOT have additional properties" names the
   object (`/settings`), never the field. Ask the instance what it accepts first:
   `GET /api/v1/discover?include=schemas&resource=workflow`.
3. **Activate once it is structurally done.** An inactive workflow has never been
   node-validated: activation is the only check that names a bad node parameter or trigger.
   Respect the owner's `active:false`; activate a copy you created if the original must stay off.
4. **Run it and read the execution**, not the workflow: `GET /executions?workflowId=<id>&includeData=true`.
   `status=success` is not health when a node uses `onError: continueRegularOutput`: check the
   destination state.

Always back up (`GET /api/v1/workflows/<wid>`) as a separate command before a PUT.

## Severity

1. **Errors (must fix)** block activation or execution: `missing_required`, `invalid_value`,
   `type_mismatch`, `invalid_reference`, `invalid_expression`.
2. **Warnings (should fix)** do not block: `best_practice`, `deprecated`, `performance`.
   Example: Slack rate limits → add `onError: 'continueRegularOutput'` with `retryOnFail`.
3. **Suggestions (optional)**: `optimization`, `alternative`.

## Common errors: what they mean and the fix

| Error | Meaning | Fix |
|---|---|---|
| `missing_required` (≈45% of errors) | a required field is empty | copy the field set from a working node of the same type and version on the same instance; add the value |
| `invalid_value` (≈28%) | a value outside the allowed options (e.g. operation `send` where `post, update, delete` are allowed) | use a listed option; the error message usually lists them |
| `type_mismatch` (≈12%) | wrong type, e.g. `"100"` for a number | convert (`limit: 100`) |
| `invalid_expression` (≈8%) | expression syntax, e.g. `$json.name` without braces | `={{$json.name}}`; see `repacted-n8n:n8n-expression-syntax` |
| `invalid_reference` (≈5%) | a referenced node does not exist (a typo like `'HTTP Requets'`, or a renamed node) | fix the name in every expression and in `connections` |

Field-by-field examples for each type: [ERROR_CATALOG.md](ERROR_CATALOG.md).

## Operator structure: now a manual check

The MCP server used to rewrite these on every save. **The REST API stores exactly what you send**,
so check them yourself before the PUT:
- **Binary operators** (`equals`, `notEquals`, `contains`, `notContains`, `greaterThan`,
  `lessThan`, `startsWith`, `endsWith`) compare two values: NO `singleValue` property.
- **Unary operators** (`isEmpty`, `isNotEmpty`, `true`, `false`) check one value:
  `singleValue: true` is required.
- **IF v2.2+ and Switch v3.2+** need the complete `conditions.options` metadata. Copy it from a
  working node of the same version.

## What no tool fixes

- **Broken connections** (a connection to a deleted or renamed node): remove the stale keys from
  the `connections` map, or recreate the node, then PUT the whole workflow.
- **Branch-count mismatch** (3 Switch rules, 2 output connections): add the missing connections
  or remove the extra rules.
- **Paradoxical corrupt state** (the API returns corrupt data but rejects every update): may need
  direct database repair on a self-hosted instance; escalate, do not loop.

## Workflow-level errors

| Symptom | Fix |
|---|---|
| connection from `Transform` to `NonExistent`: target not found | remove the stale connection or create the node |
| circular dependency A → B → A | restructure to remove the loop |
| multiple trigger nodes: only one executes | remove the extra triggers or split the workflow |
| a node not connected to the flow | connect it or delete it |

## False positives

Some warnings are acceptable in context: missing error handling (simple, test or non-critical
workflows), no retry logic (idempotent calls, APIs with their own retries, manual triggers),
missing rate limiting (internal or low-volume APIs), unbounded query (small known tables,
aggregations). Fix them in production workflows handling important data. Record every warning you
accept, and why. Detail and decision trees: [FALSE_POSITIVES.md](FALSE_POSITIVES.md).

## Recovery strategies

1. **Start fresh** when a config is badly broken: a minimal config copied from a working node,
   then add one feature at a time, checking after each.
2. **Binary search** when it validates but runs wrong: disable half the nodes on a copy, run,
   and keep halving.
3. **Clean stale connections** on "node not found": edit `connections` by hand (the MCP
   `cleanStaleConnections` operation is gone).
4. **Fix operators by rule** on operator-structure errors: apply the three rules above and diff
   against the backup before the PUT.

## Do / don't

- Do check after every significant change, read the whole error, fix one error at a time, prove
  the result from the instance (GET back, activation, an execution), and document accepted
  warnings.
- Don't activate with unresolved errors, assume a 200 means valid, trust MCP-era auto-sanitization
  (it does not run), or ignore every warning.

## Related skills

- `repacted-n8n:n8n-ops`: the write recipe, discover, activation and executions.
- `repacted-n8n:n8n-expression-syntax`: expression errors.
- `repacted-n8n:n8n-node-configuration`: required fields and property dependencies.
