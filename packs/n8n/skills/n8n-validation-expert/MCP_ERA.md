# MCP-era validation tools (not available over the REST API)

> **MCP-era, not available over the REST API.** rePACTed drives n8n over each instance's REST API (no n8n MCP
> server). Everything in this file describes the tools the n8n-mcp server provided.
> None of them exists on the REST API. It is kept because the error meanings, the operator rules
> and the habits it teaches still apply: the API-first equivalents are in [SKILL.md](SKILL.md).

---

## The validate → fix loop, as the MCP server ran it

The n8n-mcp telemetry counted **7,841 occurrences** of this loop:

```
1. Configure node
2. validate_node        (about 23 s spent reading the errors)
3. Read the error messages
4. Fix
5. validate_node again  (about 58 s spent fixing)
6. Repeat until valid   (usually 2-3 iterations)
```

Example (a Slack "create channel" config, three rounds):

```javascript
let config = { resource: "channel", operation: "create" };
validate_node({ nodeType: "nodes-base.slack", config, profile: "runtime" });
// → Error: Missing "name"
config.name = "general";
validate_node({ nodeType: "nodes-base.slack", config, profile: "runtime" });
// → Error: Missing "text"
config.text = "Hello!";
validate_node({ nodeType: "nodes-base.slack", config, profile: "runtime" });
// → Valid
```

The lesson that survives: validation is iterative, 2-3 rounds is normal, and each round fixes the
errors first. Over the REST API the rounds are the API-first loop in SKILL.md.

## Validation profiles

The server's `validate_node` / `validate_node_operation` took a `profile`:

| Profile | Use when | Checks | Trade-off |
|---|---|---|---|
| `minimal` | quick checks while editing | required fields, basic structure | fastest, most permissive, misses issues |
| `runtime` (the recommended default) | before deploying | required fields, value types, allowed values, basic dependencies | balanced; some edge cases missed |
| `ai-friendly` | AI-generated configs | as runtime, more tolerant | about 60% fewer false positives; may allow questionable configs |
| `strict` | production, critical workflows | everything, incl. best practice, performance, security | most warnings, some false positives; too noisy during development |

Over the REST API the closest equivalents: `minimal` ≈ the structural offline check; `runtime` ≈ PUT +
activate; `strict` ≈ activate + a test execution + the false-positive review in FALSE_POSITIVES.md.

## Auto-sanitization (a server feature, not n8n's)

The MCP server rewrote operator structures on every `n8n_create_workflow`,
`n8n_update_partial_workflow` or save it made:
- binary operators (`equals`, `notEquals`, `contains`, `notContains`, `greaterThan`, `lessThan`,
  `startsWith`, `endsWith`): removed `singleValue`;
- unary operators (`isEmpty`, `isNotEmpty`, `true`, `false`): added `singleValue: true`;
- IF v2.2+ and Switch v3.2+: added the complete `conditions.options` metadata.

**Nothing does this on the REST API.** A PUT stores exactly what you send, so these three rules are
now manual checks (SKILL.md §Operator structure).

It could not fix: broken connections (references to deleted nodes), a Switch with more rules than
output connections, or "paradoxical corrupt states" where the API returns corrupt data but rejects
updates (this last one may need direct database repair on a self-hosted instance).

## Tool calls that have no REST equivalent

| MCP call | What it did | Over the REST API |
|---|---|---|
| `get_node({nodeType})` / `get_node_essentials` | node schema: properties, `required`, options | copy the shape from a working node of the same type on the same instance (`GET /workflows/{id}`); the node-configuration skill |
| `validate_node`, `validate_node_operation` | per-node validation with a profile | offline structural check, then PUT + activate |
| `validate_workflow({workflow, options:{validateNodes, validateConnections, validateExpressions, profile}})` | whole-workflow validation | offline structural check (n8n-ops build-expressions-and-code §Offline validation), then activate |
| `n8n_update_partial_workflow({id, operations:[{type:"cleanStaleConnections"}]})` | dropped connections to missing nodes | edit the JSON `connections` map yourself, PUT the whole workflow |
| `n8n_autofix_workflow({id, applyFixes:false})` then `applyFixes:true` | previewed, then applied operator and metadata fixes | apply the three operator rules yourself; diff against the backup before the PUT |

## The validator's result shape

```javascript
{
  "valid": false,
  "errors":   [{ "type": "missing_required", "property": "channel",
                 "message": "Channel name is required", "fix": "Provide a channel name (lowercase, no spaces)" }],
  "warnings": [{ "type": "best_practice", "property": "errorHandling",
                 "message": "Slack API can have rate limits", "suggestion": "Add onError: 'continueRegularOutput'" }],
  "suggestions": [{ "type": "optimization", "message": "Consider using batch operations for multiple messages" }],
  "summary": { "hasErrors": true, "errorCount": 1, "warningCount": 1, "suggestionCount": 1 }
}
```

It was read in the order: `valid` first, then every error (must fix), then warnings (decide), then
suggestions (optional). The same order applies to the API's 400 bodies, activation errors and
execution errors.
