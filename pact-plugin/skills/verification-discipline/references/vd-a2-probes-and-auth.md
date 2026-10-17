# VD-A2: API, auth and environment probes

Context: read this before concluding anything from an API status code, an auth error, a
reachability probe, a scoped tool call or a spoofed request. Each of these returns a
well-formed answer that is often about something other than what you asked. Limits, schemas
and success envelopes are in [vd-a2-api-limits-and-envelopes.md](vd-a2-api-limits-and-envelopes.md).

## Canonical rules
- **A2.40** An auth error (401/403) has several causes with the same status: a wrong copy, a stale key, or a live key without that entitlement. Call another endpoint the key should reach, and read the error body.
  - Compare a fingerprint of your copy with the DEPLOYED secret: a hash prefix, never the value. Two checks of one artifact are one check, because independence is a property of the source.
  - Identity endpoints (`/me`) often need a scope that a scoped token lacks, so try a data endpoint too. Run a graded negative ladder: real, garbage, empty, well-formed fake, no header.
  - Sweep regional hosts and version headers before concluding. A field inventory belongs to its version header, and a pinned set is not a superset of an unpinned one.
  - An existence check is valid per grain. Run an invented-sibling control at the grain you emit, because auth-gated is not the same as fabricated.
  - Make a 403 a first-class outcome, distinct from a throttle. Never add a fallback chain onto another env var. Read every grep hit on the credential's name.
- **A2.41** A bound port or a TCP connect is not a working service. Assert on an application-level or protocol-level response, and control-probe reachability against a target that cannot work.
  - Implausible timing (0.00 s to a remote host) means something local is answering. Pair every sabotage run with an unsabotaged control run of the same case.
  - A Kubernetes `tcpSocket` probe is strictly weaker than `httpGet`. Treat an inherited environmental fact as recall: re-measure it, and report any correction loudly.
- **A2.42** A well-formed 200 may describe the wrong target. Make the service identify itself (a membership, its own base URL, a digest) and check that it matches what you asked for.
  - A tool may silently discard its scope parameter. Call it twice with different scopes: byte-identical output means the scope is ignored. Prefer the CLI with an explicit scope flag.
  - Suspect any wrapper of a stateful CLI that has a linked, current or default target. Assert a self-identifying field in the payload.
  - Verify a premise's SUBJECT before its claim. A wrongly shaped caveat narrows you away from the answer, and a 404 can be a permissions answer.
  - A live-state reading is a photograph. Re-verify at the moment you assert it, hardest of all in wrap-up summaries. The present tense over an earlier fact is the tell.
- **A2.43** A narrower or under-privileged identity can return 200 with fewer rows, or none. Pair any inventory or set comparison with a non-empty cardinality or visibility check.
  - A degrade flag set only in `catch` cannot see a narrower identity. A read naming a resource id 404s and fails closed, while an account-wide read just returns a shorter list.
  - `?.x ?? DEFAULT` on a safety-gate input collapses a three-valued answer, so split "read and empty" from "not read". The security design half is in repacted:security-review-patterns.
- **A2.44** A behaviour, field shape or limit measured on one API surface is not a property of the API. Verify on the exact call and argument shape the code sends, and measure each surface's own ceiling.
  - Recipe: a known-good value in the last slot, asserting count AND set; rotate that slot; a negative control; count separated from bytes; a shared-budget test.
  - Establish per surface whether the limit fails loud (400) or silent (200 with the tail dropped).
  - Record a misleading error body verbatim, and write down why the richer surface was chosen.
- **A2.45** A spoofed-identity probe is weak evidence both ways unless every identity gets a byte-identical response. Pair it with a same-client control on another path, read headers, date corroboration.
- **A2.46** A paginated walk must stop only on an EMPTY page, and advance by the records received, because a short page may be server truncation. A test fake must honour a page size smaller than requested.
  - Dedupe by id across pages, and treat a non-empty page that adds nothing new as degraded, not terminal.
- **A2.47** When content reads are denied, the platform's derived artifacts (a search index, thumbnail, size or checksum) are partial oracles. Control for the filename, and for a known-good file.

## Examples
- A2.40: two live keys answered one API identically, while only one reached the enterprise element (verified 2026-09-14, a vendor API). The finding was written wrong twice before the second endpoint was tried.
- A2.40: a scoped help-desk API token got 401 on the identity endpoint and 200 on a content endpoint. Garbage tokens got a different 401 code.
- A2.41: a TCP connect to a private database IP reported OPEN in 0.00 s, and so did a non-existent address in the same range. A postgres SSLRequest told them apart.
- A2.42: `list_variables` in the Railway MCP ignored `serviceId` and returned the linked service's variables byte-for-byte (verified 2026-09-19). Three GitHub org slugs resolved to strangers' orgs.
- A2.43: a scopeless token got 200 on `tools/list` and an empty inventory, and a set comparison against the empty set reported no mismatch.
- A2.44: two sibling elements with identical request shapes accepted 250 and 248 or-clauses, with the same misleading SCHEMA_CONFLICT body (verified 2026-09-16, a vendor API).
- A2.45: four user agents got a byte-identical 199-byte 403, which ruled out bot management. A robots rule that looked corroborating was 7 months old.
- A2.46: omitting `limit` returned 20 of 49 records at HTTP 200 with no warning (verified on a headless CMS content API).
- A2.47: "image-only" slide decks were falsified by a full-text search under metadata-only scope. The filename had to be controlled for.
