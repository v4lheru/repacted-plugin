# VD-A2: API limits, unsent schemas, envelopes, and going one level down

Context: read this before closing a risk with a live API call, reporting a write from its
response, or reporting "blocked" or "not available". The companion probes (auth, reachability,
scope, identity) are in [vd-a2-probes-and-auth.md](vd-a2-probes-and-auth.md).

## Canonical rules
- **A2.48** A request schema your tests never transmit is unproven, and "docs say unsupported" is not "silently dropped". If a cheap experiment resolves an uncertainty, run it instead of writing it down.
  - Execute anything a remote system decides (tool schemas, request bodies, migrations, IAM, CI YAML) against that system once, then guard the SERIALISED artifact in a test.
  - "Unsupported" can mean ignored, stripped or rejected. Measure which, or say "I have not made a live call".
- **A2.49** For a limit-shaped risk (grammar size, tokens, payload bytes, rows, nesting, caps), test the LARGEST instance you ship. A pass on a smaller one carries no information about it.
  - When you hand a risk over, name the case that tests it. Price a loud failure by blast radius times frequency, not by how noisy it is.
- **A2.50** Probe a credential's write scope with an empty batch request. Count its 200 only once a credential known to lack the scope gets 403 on the same call. Run a no-credential control on read probes too.
- **A2.51** An API may report unavailability only for items explicitly requested. Send the explicit filter, so that "never looked" stays distinguishable from "found nothing".
  - An empty array and an omitted field usually mean opposite things. Never default a per-item discriminator.
- **A2.52** Enum fields an LLM fills carry more values than the frontend union declares. Gate on the specific value, with a fallback. Check the live distribution before calling a gated path verified.
- **A2.53** A no-read constraint bars content, not metadata: part names, sizes and hashes still test structural claims. State the identity level (value, part or byte). Each licenses a different shortcut.
- **A2.54** A success envelope reports the call, not the work. Ask which response field would differ if nothing had happened, and read THAT field. When no field would differ, verify out of band.
  - A zero count or an empty id list beside a green status is a no-op wearing a success. Status and effect are independent in BOTH directions.
  - Before telling anyone a write landed, read the returned id back and take a read-only count.
- **A2.55** A hypothesis you already believe gets checked one level too shallow. When evidence confirms a belief, go one level down before reporting: from existence to values, from claim to mechanism.
  - Do not declare something "blocked" from one failed attempt. Find which layer failed (client, project API, service-account scope, resource ACL), and try the next one.
  - Negatives derived from documents overstate what is missing. "Cannot determine X" earns one more attempt: is it a limit of the tool, or of the query?
  - A stale read with a plausible mechanism attached feels complete, so re-read a negative before escalating it. Before computing a rate from a sample, ask what selected the sample.
  - Verify the behaviour the requirement names, not the tokens the fix introduced. Before running a check, state what its failure would look like.
  - An identical config is not an identical execution: run the production object once. A harness that normalises types cannot see a type-fidelity bug in the real driver.
  - Validate a classifier by making it emit the other answer.
  - A number handed to a teammate is an unverified assumption dressed as a specification. Say where it came from.

## Examples
- A2.48: a tool schema with `minimum`/`maximum` on numbers was rejected with 400, not stripped. 115 unit tests stayed green while the feature was 100% dead (Anthropic SDK 0.124.0, date not recorded).
- A2.49: strict tool use compiled with 13 criteria and returned 400 "compiled grammar is too large" at 15 (verified 2026-09-08, Anthropic API). The live check had used the 13-criterion matrix.
- A2.50: an empty-inputs batch upsert returned 200 with the scope and 403 with a retired scopeless token, and no record was created (HubSpot CRM v3 API, date not recorded).
- A2.51: without an explicit source list, `unavailableSources` came back empty, and four unsearched sources looked like "no matches".
- A2.52: a source-gated deep link could not be exercised because the target value had zero rows in the live data.
- A2.53: two sheets reported as "byte-identical" were value-identical, while their archive parts differed in size and hash.
- A2.54: a memory sync returned `ok: true` with `projected: 0`, and a vendor health check returned `healthy: true` with `is_logged_in: false`.
- A2.55: a field existed and looked like the clean fix, but its values placed an office in the wrong country. An "org-blocked" Drive write worked through service-account impersonation.
