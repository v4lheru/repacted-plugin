# n8n is API-first, no n8n MCP

The six n8n skill READMEs (`n8n-code-javascript`, `n8n-code-python`, `n8n-expression-syntax`,
`n8n-node-configuration`, `n8n-validation-expert`, `n8n-workflow-patterns`) come from the n8n-skills
collection and predate this pack.
Where a README names n8n-mcp tools (`get_node`, `validate_node*`, `n8n_autofix_workflow`,
`n8n_update_partial_workflow`, the validation profiles) those are MCP-era and not used in this
pack; a skill that kept its MCP-era procedure has it in its own `MCP_ERA.md`. The API-first route for
every instance is `repacted-n8n:n8n-ops` ([SKILL.md](../SKILL.md)): read, build, edit, activate
and archive workflows over the REST API with per-instance keys. Node syntax, expressions, Code-node
rules and validation errors stay in the n8n-* skills; the transport is always the API.
