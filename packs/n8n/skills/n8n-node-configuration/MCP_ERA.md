# MCP-era node discovery (not available over the REST API)

> **MCP-era, not available over the REST API.** rePACTed works n8n over each instance's REST API (no n8n MCP
> server). The `get_node` discovery tool below belonged to the n8n-mcp server and has
> no REST equivalent. It is kept as a record of what the tool told you, so the habits carry over:
> the API-first sources are in [SKILL.md](SKILL.md) §Where the schema comes from.

## `get_node` detail levels

| Call | Returned | When it was used |
|---|---|---|
| `get_node({nodeType})` (`detail: "standard"`, the default) | required fields, common options, the operation list, metadata; about 1-2K tokens | first, for about 95% of configurations (the most-used discovery pattern; about 56 s between configuration edits in the server's telemetry) |
| `get_node({nodeType, mode: "search_properties", propertyQuery: "auth"})` | property paths matching the query, with descriptions and `displayOptions` | a specific field: auth, headers, body |
| `get_node({nodeType, detail: "full"})` | the complete schema with every nested option and `displayOptions` rule; about 3-8K tokens | only when standard was not enough, e.g. to see why a field is missing or required |

The server's decision tree: standard first → configure if it has what you need → search_properties
for a specific field → full detail only if still unclear. The API-first version of the same rule:
copy the smallest working node first, and read the node's full source definition only when a field's
visibility is unclear.

## Other server calls this skill used to name

| MCP call | Over the REST API |
|---|---|
| `get_node_essentials`, `get_property_dependencies` | a working node's parameters; the node's `displayOptions` in its source definition |
| `validate_node`, `validate_node_operation` (with a `profile`) | the API-first loop in `repacted-n8n:n8n-validation-expert` (offline check, PUT, activate, run) |
| `n8n_update_partial_workflow` | a full-workflow `PUT` with the strict body (`repacted-n8n:n8n-ops`), after a separate backup |
| `search_nodes` | the node types already used on the instance (`GET /workflows`, following `nextCursor`), or the n8n node docs |

## Auto-sanitization

The server added `singleValue: true` to unary IF/Switch operators, removed it from binary ones, and
filled IF v2.2+ / Switch v3.2+ `conditions.options` on every save. The REST API does none of this:
see `repacted-n8n:n8n-validation-expert` §Operator structure.
