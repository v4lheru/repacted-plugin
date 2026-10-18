# repacted-n8n

The n8n pack for rePACTed: build, fix and deploy n8n workflows over the instance's REST API, with no n8n MCP server.

- `skills/`: `n8n-ops` (the REST craft: discover, pagination, strict bodies, backups before a write, publish and activation, executions, credentials, data tables) and six node-syntax skills (Code nodes in JavaScript and Python, expressions, node configuration, validation errors, workflow patterns).
- `agents/pact-n8n.md`: the PACT n8n specialist.
- `recall.d/triggers.json`: points a session at `n8n-ops` when a command calls an n8n REST API.

Install it after the core plugin, from the same marketplace:

```
claude plugin install repacted@repacted
claude plugin install repacted-n8n@repacted
```

Keep one key file per instance (for example `~/.config/n8n/<instance>.env`, mode 600). You write the key; the agent only reads it through a process substitution and never prints it.

Credits and licenses: [NOTICE.md](NOTICE.md).
