# Secrets and redaction

Context: how credentials leak through code paths nobody meant to log (errors, URLs, fixtures,
history), and how redaction and secret-scan controls fail silently. Read when reviewing a client
that carries a credential, an error or log path, a fixture recorder, a redactor or sanitiser, or
any "we scanned and it's clean" claim.

## Canonical rules (a checklist: run top to bottom)
1. Send credentials in headers (`Authorization`, `x-api-key`), never in a query string: URLs reach proxy and access logs, exception strings and every redirect hop.
2. httpx embeds the full URL, query string included, in `HTTPStatusError`'s message, so `logger.error(f"… {e}")` logs a `params=` token on every non-2xx. Flag query-param secrets whether or not the URL is logged.
3. Node/undici `fetch` differs: DNS and connection errors say "fetch failed" without the URL, but a malformed URL puts the whole URL in the message. Measure per runtime; a hardcoded valid base is correct by coincidence (MEDIUM), not by control.
4. Some vendor APIs require `?key=`: an unfixable upstream shape. Mitigate operationally (the egress proxy must not log full URLs, a `key=` rule in the log sanitizer, rotation) and say so.
5. Never splice an upstream response body into a thrown error or a stored error: OAuth servers, YouTube and Monday echo credentials in error bodies. Log a sanitised preview server-side and throw the status only; sanitise inside the function that PERSISTS errors to close the class.
6. Route handlers returning `err.message` pass internal detail (Postgres constraint and RLS text) to the client: return a generic message with an opaque error id and log the detail.
7. A redirect credential-stripping guard matches a fixed list of header NAMES (`authorization`, `cookie`, `x-api-key` …); `x-goog-api-key`, `x-figma-token`, `private-token`, `dd-api-key` survive the hop. Read the list before choosing a header, and still prefer a header to `?key=`.
8. A `logging.Filter` cannot redact `record.exc_text`: the Formatter fills it AFTER filters run. Format `exc_info` inside the filter (or redact in a Formatter) and test through a real `StreamHandler` with `logger.exception`.
9. Root-handler redaction misses uvicorn's own handlers (`propagate=False`; Starlette re-raises and `uvicorn.error` logs the raw traceback) and `sys.excepthook`/`threading.excepthook`: sweep every logger's handlers and replace the hooks.
10. Secret scans keyed on LABELS (`api_key=`, `Bearer`, `password:`) miss POSITIONAL secrets inside a URL in an error string. Scan by key PREFIX (`AIza`, `xox[baprs]-`, `ghp_`, `eyJ`, vendor prefixes) and by shape (a 24+ char opaque query value).
11. Every scan gets a positive control on the same column or tree (a string known to be there), or a zero cannot tell "clean" from "broken". Count the carriers too (rows with an error, with a URL, with a query string).
12. A redactor that masks MOST lines makes its misses look like ordinary content. Match case-insensitively and by value shape (`//user:pass@`, long opaque runs); prefer allow-listing what may print (keys and lengths) over deny-listing.
13. Presence evidence outranks a scan's all-clear: if you saw a secret printed, say so even when a tool reports clean. Ask what the scan could not see (recursion, case, other directories such as `subagents/`).
14. Build redaction post-conditions from a needle source INDEPENDENT of the extractor: a check that iterates the extractor's own tokens can only prove "what we found is gone". Report structural hits as a count, never the values.
15. Split the failure kinds: a RECALL gap (never extracted) is a finding; a MATCHER failure (an extracted token survived) is a HALT.
16. Triage extractors by consumers: patterns with a second consumer (a derivation that must agree) fail closed; pure token lists fail silent. Audit those first.
17. Prefer a bounded allowlist to a widened regex, and measure the widening's collateral on real prose first (a naive URL widening ate `Node.js`, `asp.net`, `socket.io` …).
18. Before calling an existing guard sufficient for a change, name the mechanism it keys on and check your change trips it; a real guard is often narrower than what it appears to protect.
19. A key-name DENYLIST sanitizer (drop keys `user_list`, `top_users`) is not PII coverage: audit every response path, remove the source columns, and regex the assembled `JSON.stringify(payload)` for emails.
20. Inject the forbidden token into EVERY free-text field of a fixture (names, folders, titles), not the one the team's test covers, and regex the whole payload.
21. Recorded vendor payloads can carry a key in asset URLs (a headless CMS: the space key in every asset path): redact before writing, assert the function under test is byte-identical on redacted vs raw, then grep the written file.
22. Sanitising a committed file leaves the data in history. Check `git show <sha>:<path>`; amend with `--only <path>` while unpushed (`git branch -r --contains` empty); once pushed, rotate the credential.
23. Judge an exemption ("skip vendor URLs") on CONTENTS, not container; scan percent-decoded and base64-decoded forms too.
24. Untracking a leaked file is not purging it: rotate (plaintext on disk counts as compromised), and scrub history only if the repo will be pushed.
25. Secret-grep the WHOLE committed tree, including baseline reference files (example workflows, sample configs), then confirm `git ls-files` (tracked) and `git check-ignore` (exit 1 = will be pushed).
26. n8n export credential ids are opaque pointers, not secrets; an inlined token VALUE in node parameters is the leak.
27. On a direct-push-to-master project, `git diff master..HEAD` a feature branch for security fixes it would silently revert; review the COMMITTED state (`git show HEAD:<file>`), not the working tree.
28. A key inlined into a client bundle is public: grep the BUILT bundle (`.next/static`, `dist/assets`) for `service_role`, `sk-`, Slack token prefixes, `refresh_token`, `SESSION_SECRET`, `VITE_`.
29. OAuth `client_credentials`: prefer `Authorization: Basic base64(id:secret)` over body fields; cache the token with a refresh margin (about 10% of expiry, at most 10 minutes).
30. The last `CREATE OR REPLACE` is the live definition, and commented-out SQL greps like shipped SQL: grep the name across ALL migrations and read the grants file, not the file a dispatch points at.
31. A control the design record says exists may never have been built: grep for it with a positive control before softening a finding, and ask whether its precondition is even implementable.
32. Encryption at rest: AES-GCM with a random IV, a versioned ciphertext format (`v1:iv:tag:ct`), per-purpose keys (HKDF context) or envelope encryption with a KMS-held master key.
33. PII fields are never logged, access and exports are audit-logged, and erasure propagates to every downstream store (see data-pii-and-llm).

## Examples
- 2: an actor-rental client passed its token in `params=`; routine 403s wrote it to logs.
- 8: a redacting filter's traceback tests hand-set `exc_text`, a state the pipeline never produces; the suite was green while the control was inert.
- 10: a job-metadata column scanned for labels reported clean; 3 of 695 error rows even had a URL, so the positional path had simply never been exercised.
- 13: a non-recursive transcript scan found a credential in 5 files; the recursive truth was 63 occurrences in 8, and a writable one reported absent.
- 19: an all-staff dashboard's sanitizer dropped two key names while emails flowed out under `display_name`.
- 25: a live-format chat bot token sat in a committed example workflow outside every coder zone.
