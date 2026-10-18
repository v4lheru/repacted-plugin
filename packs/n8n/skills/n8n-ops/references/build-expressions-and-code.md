# Build: expressions, item lineage and Code nodes

Field-measured traps in how items flow between n8n nodes, how expressions resolve, and how Code nodes behave in the task-runner sandbox. Read when building or debugging a workflow's data path, or validating workflow JSON offline. Syntax basics live in the pack's `repacted-n8n:n8n-*` node-syntax skills; this file holds only what cost real runs.

## Canonical rules
1. A `=`-prefixed field is a TEMPLATE: only `{{ }}` executes. JS concatenation outside braces is emitted as literal source.
2. `$('Node').item` resolves through pairedItem lineage; across parallel branches or Code-fabricated items it silently yields empty. Use `.first()` for a known-single upstream value.
3. Referencing an unexecuted node throws before `?.` or `||` apply. Guard with `$if($('X').isExecuted, $('X').first().json.f, fallback)` in every expression field.
4. Slack, HTTP, Sheets and DB nodes REPLACE `$json` with the response. Make send nodes parallel siblings of clobbering writers instead of reaching back through them.
5. `$('stable').item` can fail to resolve back through a Sheets append's pairedItem remap; the sibling layout is the robust fix.
6. `continueErrorOutput` emits only `{error}`; the input fields are gone but pairedItem survives, so the handler reads `$('Upstream').item.json`.
7. The LangChain agent node emits only `{output}`; recover the request by pairedItem index, not position (error outputs shift alignment).
8. Set node v3.4 replaces the item with the assigned fields; set `includeOtherFields:true` when downstream reads the item stream.
9. Always Output Data emits `[{json:{}}]` instead of zero items and poisons downstream nodes; leave it false and guard before AI nodes.
10. Exception: a DML node (BigQuery MERGE/INSERT/UPDATE/DELETE) emits zero items on success and n8n skips the next node. Set `alwaysOutputData:true` on it.
11. Branches reconverging on one node carry only one branch's items: insert a Merge in append mode with `numberInputs:N`, one distinct input per branch.
12. A Code node calling `$('Upstream').all()` bypasses every filter between them; consume `$input.all()` and detect by comparing per-node item counts.
13. `getNodeParameter('options', i, defaults)` applies defaults only when `options` is absent; set every key you depend on once you set any.
14. Code node "Run Once for Each Item" returns one `{json}` object; an array is the All-Items shape. Fan-out nodes must be All-Items.
15. Use `$runIndex`, never `$('Node').runIndex` (undefined from n8n 2.19.5).
16. `setTimeout` in a Code node never fires in the task runner; build waits from chained native Wait nodes and a Set-node counter.
17. `new URL()` throws in some Code-node sandboxes; parse hosts with a regex and verify URL-derived fields in a live execution.
18. Sanitize scraped URLs by char code first: a leading zero-width or bidi character defeats the scheme test and `trim()`.
19. `require('crypto')` needs `NODE_FUNCTION_ALLOW_BUILTIN`; prefer Web Crypto or pure JS, and flag the prerequisite.
20. Code nodes cannot make authenticated calls (`httpRequestWithAuthentication` throws every run); use an HTTP Request node with a credential.
21. Dedup on a normalized CONTENT key built only from fields every delivery channel populates; never on a provider id.
22. A single-writer UPDATE-by-key on a sibling branch of the INSERT depends on runtime branch order, and a 0-row UPDATE is silent: chain it after the insert or check the branch index.
23. Edit `jsCode` inside workflow JSON programmatically from a temp file, then round-trip-check with `new Function`; avoid template literals.
24. Validate every rendered `blocksUi` by substituting `{{ }}` and parsing the result as JSON: a hard gate, not a warning.

## Detail

**Templates.** Correct shape, checked against 101 production `blocksUi` values on one instance: `={ "blocks": [ … "text": "… {{ expr }} …" ] }`. A Drive `queryString` with no `{{ }}` sends the literal expression, matches nothing, and an existence preflight concludes "not created" and makes a duplicate. An offline validator that only checks params that are entirely `={{…}}` skips templates and nested resourceLocator values.

**Lineage.** `.item` for same-lineage per-item joins; `.first()` for a single upstream value read from another branch. An error-output handler is a direct descendant, so `.item` is safe there. Only IF, Switch and NoOp pass `$json` through; call nodes clobber it. Audit the whole happy path after unblocking a gate: a latent clobber only bites once items reach the post-send node.

**Code-node mode.** Per-item mode: `return {json:{…}}`; a bare object or an array fails with "A 'json' property isn't an object". All-items mode: `$json` is the FIRST item only; use `$input.all()`. Flipping several per-item nodes that return arrays to all-items is a zero-code fix when each receives one item (for example inside a batch-size-1 loop).

**Waits and loops.** Wait node `{amount:60, unit:"seconds"}`, typeVersion 1.1, with a `webhookId`; one Wait was capped at 60 s on one deployment, so chain them. Counter: `$('Wait A').runIndex + 1` in a Set node; loop back to the first Wait, keep one-time notifications outside the loop, and clone the initial check node as a separate Poll node. `splitInBatches` v3: output 0 = done, output 1 = loop.

**Memory.** Thousands of heavy rows in one query stall downstream nodes (each deep-clones the payload). Outer `splitInBatches` over a partition key, a targeted per-key query, an inner per-row loop; inner "done" returns to the outer loop.

**Dedup.** Two layers: an in-run seen-set for same-batch twins plus a persistent store for cross-run ones. Measured: 24% of one table's keys split because one channel omitted a key field; dropping it collapsed them with one false merge in 2,034. Measure that ratio before shipping. Data Tables have no UNIQUE constraint, so a check-then-insert claim is not atomic (never observed to fire in 2,675 rows). Seed a manually back-filled period into the dedup store with external writes disabled before going live on a rolling window. See data-tables.md for the node's filter semantics.

**Pairing.** Responses come back in input order; when an API does not echo the key, rebuild the key list from the ordered upstream and zip by index, and re-check any positional join after changing an upstream filter. Round-trip your own id (Drive `appProperties`) where possible.

**Map-reduce.** Split in a Code node (one item per chunk), map with an agent node (output is `$json.output`, a string; strip a markdown json code fence before parsing), reduce with `$input.all()`. For a ported Python map-reduce: the per-item LLM step is the only stochastic one; make the durable table the seam (daily mapper with a watermark and MERGE, periodic reducer), and verify any "SQL twin" is runnable before promising to execute it.

**Invisible-character sanitizer (pure ASCII, no `\u` escapes):**
```js
var s=String(url||''),u='';for(var i=0;i<s.length;i++){var c=s.charCodeAt(i);
 if((c>=0x200B&&c<=0x200F)||(c>=0x202A&&c<=0x202E)||c===0x2060||c===0xFEFF||c===0x00AD||c===0x180E||c===0x061C||c<=0x1F||(c>=0x7F&&c<=0x9F))continue;
 if(c===0x00A0||(c>=0x2000&&c<=0x200A)||c===0x202F||c===0x205F||c===0x3000){u+=' ';continue;} u+=s[i];}
u=u.trim(); var mm=u.match(/https?:\/\/[^\s"'<>]+/i); if(mm)u=mm[0]; else if(!/^https?:\/\//i.test(u))u='https://'+u;
// host without new URL(): scheme URLs, bare hosts, ports; require a dot
var m=u.match(/https?:\/\/([^\/\s?#"'<>:]+)/i), host=m?m[1]:((u.match(/([a-z0-9-]+(?:\.[a-z0-9-]+)+)/i)||[])[1]);
host=host?host.replace(/^www\./i,'').toLowerCase():null; if(host&&host.indexOf('.')<0)host=null;
```

## Offline validation (when you cannot or should not execute)

- Structural: unique names and ids, every connection endpoint resolves, `$('Node')` references resolve, IF outputs 0/1 (`main[0]` true, `main[1]` false), Switch indices within rules (+1 fallback), every node reachable, no leaked credential ids.
- Expressions: extract IF conditions and templates from the DEPLOYED JSON, render with a mock `$()` whose `.item` THROWS on an unexecuted node, and prove regression by rendering the backup with the same inputs.
- Sabotage-prove every check: delete a condition in the captured JSON and watch it go red. A check that a broken workflow also passes is worse than none.
- Build node shapes from real workflows on the same instance, never from docs. Label simulated results SIMULATED.

## Examples
- A digest's deep-dive branch used `.item` for the parent `ts` and posted to the channel instead of the thread.
- A dedup gate was decorative: two downstream Code nodes read the pre-filter node, costing ten LLM calls where one item was new.
- A mapper's MERGE wrote correctly, emitted zero items, and its loop processed 1 of 50 calls per run with no error.

