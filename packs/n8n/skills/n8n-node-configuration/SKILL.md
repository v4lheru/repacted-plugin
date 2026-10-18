---
name: n8n-node-configuration
description: "Use when configuring an n8n node's parameters: which fields an operation requires, property dependencies (displayOptions), and per-node patterns for Slack, HTTP, IF and databases."
---

# n8n Node Configuration

Operation-aware node configuration with property dependencies, for work over the REST API
(no n8n MCP server). Instance behaviour and the write recipe live in `repacted-n8n:n8n-ops`.
The MCP server's `get_node` discovery tool is described in [MCP_ERA.md](MCP_ERA.md)
(MCP-era only).

---

## Configuration philosophy

**Progressive disclosure**: start from the smallest valid config and add fields only when needed.
Most configurations need the required fields plus one or two options, not the full schema.

## Where the schema comes from (API-first)

1. **A working node of the same type and `typeVersion` on the same instance** (`GET /api/v1
   /workflows/<wid>`). Build node shapes from real workflows, never from docs: parameter names
   and nesting change between versions (the HTTP node's pagination block is nested twice, and the
   shallow form is ignored silently).
2. **The node's source definition** (its `displayOptions`, `required` flags and defaults) when a
   field's visibility is unclear.
3. **`GET /api/v1/credentials/schema/{type}`** for the fields of a credential type.
4. **Activation** is the check: an inactive workflow is never node-validated, so activate (a copy,
   if the original must stay off) to learn whether a required parameter is missing.

## Core concepts

### 1. Operation-aware configuration

Not all fields are always required: resource + operation decide which ones are.

```javascript
// Slack, operation 'post'
{ "resource": "message", "operation": "post",
  "channel": "#general",   // required for post
  "text": "Hello!" }       // required for post

// Slack, operation 'update'
{ "resource": "message", "operation": "update",
  "messageId": "123",      // required for update (different!)
  "text": "Updated!" }     // channel NOT required for update
```

### 2. Property dependencies

Fields appear and disappear with other fields' values. The mechanism is `displayOptions`:

```javascript
{ "name": "body",
  "displayOptions": { "show": { "sendBody": [true], "method": ["POST", "PUT", "PATCH"] } } }
// "body" shows when sendBody = true AND method is POST, PUT or PATCH
```

HTTP Request: `method: "GET"` has no body; `method: "POST"` exposes `sendBody`, and
`sendBody: true` makes `body` (`contentType`, `content`) required.

### 3. The configuration loop

```
1. Identify node type, typeVersion and operation
2. Copy the smallest working node of that type from the same instance
3. Set the operation's required fields
4. Offline check (repacted-n8n:n8n-validation-expert), then PUT and activate
5. If a field is unclear, read the node's displayOptions
6. Add optional fields as needed, checking again after each
7. Run once and read the execution data
```

**Example: HTTP POST of JSON.** Start with `method: "POST"`, `url`, `authentication: "none"`;
the check reports `sendBody` required for POST; add `sendBody: true`; it then reports `body`
required when `sendBody=true`; the complete config is:

```javascript
{ "method": "POST", "url": "https://api.example.com/create", "authentication": "none",
  "sendBody": true,
  "body": { "contentType": "json",
            "content": { "name": "={{$json.name}}", "email": "={{$json.email}}" } } }
```

## Common dependency patterns

| Pattern | Example | Rule |
|---|---|---|
| Boolean toggle | HTTP `sendBody` | `sendBody: true` → `body` appears and is required |
| Operation switch | Slack `resource`/`operation` | `post` shows channel, text, attachments; `update` shows messageId, text |
| Type selection | IF conditions | `type: "string"` + `contains` → value1, value2; `type: "boolean"` + `equals` → value1, value2 and different operators |

When a check fails and you do not understand why a field is missing or required, read that
field's `displayOptions`.

## Common node patterns

1. **Resource/operation nodes** (Slack, Google Sheets, Airtable): `{"resource": "<entity>",
   "operation": "<action>", ...}`. Choose the resource, then the operation, then set that
   operation's required fields.
2. **HTTP-based nodes** (HTTP Request, Webhook): `method`, `url`, `authentication`. POST/PUT/PATCH
   → `sendBody` available; `sendBody=true` → `body` required; `authentication != "none"` →
   credentials required.
3. **Database nodes** (Postgres, MySQL, MongoDB): `operation="executeQuery"` → `query` required;
   `insert` → table + values; `update` → table + values + where.
4. **Conditional logic nodes** (IF, Switch, Merge): binary operators (equals, contains, ...) take
   value1 + value2; unary operators (isEmpty, isNotEmpty) take value1 only plus
   `singleValue: true`. Nothing adds `singleValue` for you on the REST API.

## Operation-specific examples

```javascript
// Slack: post message
{ "resource": "message", "operation": "post", "channel": "#general", "text": "Hello!",
  "attachments": [], "blocks": [] }                     // attachments, blocks optional
// Slack: update message
{ "resource": "message", "operation": "update", "messageId": "1234567890", "text": "Updated!",
  "channel": "#general" }                               // channel optional (can be inferred)
// Slack: create channel
{ "resource": "channel", "operation": "create", "name": "new-channel", "isPrivate": false }
// (text NOT required for this operation)

// HTTP GET with a credential and query
{ "method": "GET", "url": "https://api.example.com/users",
  "authentication": "predefinedCredentialType", "nodeCredentialType": "httpHeaderAuth",
  "sendQuery": true,                                     // optional
  "queryParameters": { "parameters": [{ "name": "limit", "value": "100" }] } }  // shows when sendQuery=true
// HTTP POST with JSON
{ "method": "POST", "url": "https://api.example.com/users", "authentication": "none",
  "sendBody": true,
  "body": { "contentType": "json", "content": { "name": "John Doe", "email": "john@example.com" } } }

// IF: string comparison (binary: needs value2)
{ "conditions": { "string": [{ "value1": "={{$json.status}}", "operation": "equals", "value2": "active" }] } }
// IF: empty check (unary: no value2, singleValue required and set by you)
{ "conditions": { "string": [{ "value1": "={{$json.email}}", "operation": "isEmpty", "singleValue": true }] } }
```

## Conditional requirements

- **HTTP body**: required when `sendBody = true` AND method IN (POST, PUT, PATCH, DELETE).
  Discover it from the check's error, from the field's `displayOptions`, or by starting minimal and
  iterating.
- **IF `singleValue`**: `true` when operation IN (isEmpty, isNotEmpty, true, false). The MCP
  server used to add it on save; now you set it (see `repacted-n8n:n8n-validation-expert`
  §Operator structure).

## Anti-patterns

- **Over-configuring upfront** (every optional field: `sendQuery: false`, `sendHeaders: false`,
  `timeout`, ...). Start minimal: `{method, url, authentication}`, then add what is needed.
- **Writing without checking.** Back up, run the offline check, PUT, then activate: a PUT that
  returns 200 has validated nothing about node parameters.
- **Ignoring operation context.** Switching Slack `post` → `update` while keeping `channel` and
  omitting `messageId` is wrong. Re-read the operation's required fields whenever the operation
  changes.

## Do / don't

- Do start from a working node, check iteratively (2-3 rounds is normal), read a field's
  `displayOptions` when stuck, respect operation context, and set operator structure yourself.
- Don't reach for the full schema first, configure blindly, copy a config across operations
  without adjusting it, or rely on MCP-era auto-sanitization (it does not run).

## Detailed references

- [DEPENDENCIES.md](DEPENDENCIES.md): property dependencies and `displayOptions` in depth.
- [OPERATION_PATTERNS.md](OPERATION_PATTERNS.md): configuration patterns by node type.

## Related skills

- `repacted-n8n:n8n-ops`: the write recipe, discover, activation and executions.
- `repacted-n8n:n8n-validation-expert`: what each error means and the API-first check loop.
- `repacted-n8n:n8n-expression-syntax`: expression fields.
- `repacted-n8n:n8n-workflow-patterns`: applying patterns with the right configuration.
