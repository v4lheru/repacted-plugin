# Provider API traps (Anthropic, OpenAI, SDKs)

Context: field-measured provider and SDK behaviour that the docs omit or blur: schema keywords
that are refused, caching that silently does nothing, parameters that 400 on newer models, and
residency and retention facts. Read this before building or changing an LLM call. For current model
ids, prices, cache floors and parameters, the built-in `claude-api` skill is authoritative; this file
never restates them as rules.

## Canonical rules
1. Before relying on any documented parameter, read the INSTALLED SDK's type surface (`.d.ts`). An older SDK may lack a field (for example adaptive thinking or `effort`); substitute the closest valid form and record the deviation in the HANDOFF.
2. A remote service judges a schema, payload or SQL string at request time: a green unit suite cannot see that rejection. Make ONE live call before handing over an LLM integration.
3. Anthropic tool `input_schema` refuses `minimum`/`maximum` (the whole request 400s; the SDK does not strip them). Use `enum` for a numeric domain, or put the bound in the description plus a runtime validator.
4. Add a guard test that the serialised tools contain neither `"minimum"` nor `"maximum"`: re-adding them is the obvious "improvement" and kills the feature.
5. Structured output: when every element is required, use an OBJECT keyed by id (one required property per id, `additionalProperties: false`), not an array. An array one short is still schema-valid.
6. Anthropic caches the JSON schema for up to a day after last use, separately from the ZDR prompt: no personal data in field names, enum values, descriptions or examples. Keep one static, versioned schema and put the reason in a code comment there.
7. Native citations and structured output (`output_config.format`) are mutually exclusive (400). For provenance under a strict schema, require verbatim-quote evidence fields and substring-check them against the source in code.
8. Top-level `cache_control` marks the LAST cacheable block, which is the varying user message: every call writes and none reads. Mark the last STABLE block (the system block as a block array).
9. Never conclude caching works from the parameter being present: read `usage.cache_read_input_tokens` on a SECOND call with a DIFFERENT user message.
10. The minimum cacheable prefix is model-dependent and not monotonic across generations; below it there is no error, only `cache_creation_input_tokens: 0`. Look the floor up in `claude-api`, never from memory.
11. Caching earns out only on CLUSTERED calls: ask how far apart calls sharing the prefix start before asking whether the prefix is big enough. Event-driven calls hours apart are cold every time.
12. Padding a system prompt past the cache floor (schema, taxonomy, few-shot) can be cheaper than a short uncached prompt, because reads bill at about a tenth of the input rate.
13. Parallel workers on a cold cache all miss and all WRITE the prefix. Make one serial warm-up call, then fan out; assert `cache_read > 0` and `cache_creation` near zero on calls 2..N.
14. `usage.input_tokens` is the UNCACHED remainder only. Total prompt = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; cost each field separately.
15. Never estimate Claude tokens with `tiktoken` (it undercounts); use `messages.count_tokens`. Batch API results arrive in arbitrary order: key by `custom_id`.
16. The newest Claude models reject `temperature`, `top_p` and `top_k` (check `claude-api` for which). Bit-exact reproducibility is impossible there: meet "auditable" with persistence (model id string, prompt version and hash, raw response, usage).
17. A model id read from an env var is not provenance, and an alias there resolves to a dated id. Pin the id in the versioned config beside the prompt and check it against a fail-CLOSED allowlist at construction.
18. Model capability gates differ by family for different reasons (for example `inference_geo`, forced `tool_choice`). Gate with an allowlist: a blocklist admits tomorrow's model by default.
19. First-party Claude API residency: `inference_geo` accepts only `"us"` or `"global"` (default `"global"`, meaning anywhere), and workspace geography is US-only and fixed at creation (verified 2026-09-06, platform docs).
20. Log `response.usage.inference_geo` per request: it reports where inference actually ran. Workspace geography also covers PDF page-image transcoding. EU processing means Bedrock or Vertex EU, a different processor chain. On Bedrock Converse, visual PDF reading needs citations on, which excludes strict schemas: residency, scanned-document safety, strict schema, pick two.
21. ZDR is per feature: code execution, programmatic tool calling and Managed Agents are not eligible, and some model families require 30-day retention (verified 2026-09, see `claude-api`). Never route personal data to a non-ZDR family.
22. OpenAI: every GPT-5-family and o-series model rejects a non-default `temperature` (400). Branch on the model FAMILY by prefix (`gpt-5`, `o1`, `o3`, `o4`), never on one model-name substring (verified 2026-09, OpenAI API).
23. Those reasoning models also need `max_completion_tokens`, not `max_tokens`, and their reasoning tokens can dominate a cheap model's output cost: read `usage.completion_tokens_details.reasoning_tokens` from a live call before costing.
24. OpenAI strict Structured Outputs: every property in `required`, `additionalProperties: false` at every level, nullability as a type union (`['string','null']`), and `null` inside a nullable enum's list.
25. OpenAI Deep Research is a Responses API model: background mode, a web search tool required, no function calling or structured output, minutes per task, and expensive per query (verified 2025-06 GA; re-check pricing).
26. Vercel AI SDK `streamText`: iterate `fullStream`, not `textStream`. `textStream` completes silently on an upstream error (a 401 arrives as an empty completion). `AI_APICallError` carries `statusCode`, not `status`.
27. Claude reads PDFs page by page as image plus text, so a scanned PDF with no text layer is read. `.docx` and `.xlsx` are refused in document blocks: convert to text or PDF first.
27a. Apple `.pages` has no public format and no working LibreOffice import: look for the zip's `QuickLook/Preview.pdf`, else route to manual review. Encrypted PDFs hard-fail: detect them first.
28. A text-domain assertion cannot certify a vision payload: after redacting the text layer, a name in a logo or signature image still reaches the model. If redaction is load-bearing, do not send native PDF.
29. Overlay redaction (a drawn black box) leaves the text in the file bytes; only removing the content-stream text operators removes it. Verify by extracting from the output file, never by looking.
30. Two-column documents extract with interleaved columns: fluent, dictionary-shaped and wrong. Length and vocabulary gates pass; only geometry (`pdftotext -bbox`, cluster EVERY word's x-origin) detects it.
31. Keep "unreadable" distinct from "scored 0": a mechanical pre-LLM floor, a required `document_legible` field in the strict schema, and a code invariant that a 0 with no evidence is an extraction fault.
32. Gmail automatic forwarding preserves `From:` and adds `X-Forwarded-For`; a manual Forward replaces `From:` with the forwarder. Detect manual forwards and flag them; never regex the quoted body as authoritative.
33. Gmail push (`users.watch`): the watch expires after about a week (renew daily and alarm on failure), notifications carry only a `historyId`, and delivery is not guaranteed, so a `history.list` reconciliation sweep is mandatory.
34. Gmail notifications can duplicate: key idempotency on the message id under a NOT NULL unique column (repacted:postgres-supabase, NULL upsert keys).

## Examples
- 3, 2: 115 green unit tests; every live call 400'd on `maximum, minimum are not supported` (verified 2026-09-08, `@anthropic-ai/sdk` 0.124.0).
- 8, 9: top-level `cache_control` wrote about 4,000 tokens per call and read 0, costing about 17% MORE than no caching; the system-block placement cut steady-state cost 62%.
- 13: 8 workers on a cold cache: hit ratio 0.081; one warm-up call first: 0.651 and 58% cheaper, same wall clock.
- 17: every comment said the project pinned one model; the env var named an older generation, which accepted a different request shape.
- 22: a substring check for one model name let the rest of the GPT-5 family 400 on `temperature`.
- 23: on a trivial classify, a nano-tier reasoning model emitted about 767 output tokens against 26 for a non-reasoning model.
- 28: a truly redacted text layer passed `identity not in extracted_text` while the page image showed the name.
- 30: a 621-character, 83-word extraction was 100% dictionary words and entirely interleaved.
