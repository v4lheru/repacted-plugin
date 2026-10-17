---
name: security-review-patterns
description: "Use when implementing or reviewing auth, credentials or sensitive data, or reviewing code or a design for security: the non-negotiable credential rules (backend proxy only; no secret in frontend code, logs or prompts), sessions, SSRF and credential-bearing fetches, redaction, multi-tenant RLS, launchd or cron scripts, Apps Script, LLM output in UI, PII."
---

# Security review patterns

Context: the plugin's one security skill. Step 1 carries PACT's non-negotiable credential rules
intact (absorbed from `pact-security-patterns`); they bind WRITING code as much as reviewing it.
The references are checklists drawn from defects that actually occurred in real projects,
so a review finds the classes that bite in practice, not only the textbook ones.

## Workflow
1. **Apply the SACROSANCT Rules (Non-Negotiable).** These rules are ABSOLUTE and must NEVER be
   violated.

   **Rule 1: Credential Protection**

   **NEVER ALLOW in version control:**
   - Actual API keys, tokens, passwords, or secrets
   - Credentials in frontend code (VITE_, REACT_APP_, NEXT_PUBLIC_ prefixes)
   - Real credential values in documentation or code examples
   - Hardcoded secrets in any file committed to git

   **ONLY acceptable locations for actual credentials:**

   | Location | Example | Security Level |
   |----------|---------|----------------|
   | `.env` files in `.gitignore` | `API_KEY=sk-xxx` | Development |
   | Server-side `process.env` | `process.env.API_KEY` | Runtime |
   | Deployment platform secrets | Railway, Vercel, AWS | Production |
   | Secrets managers | Vault, AWS Secrets Manager | Enterprise |

   **In Documentation - Always Use Placeholders:**
   ```markdown
   # Configuration
   Set your API key in `.env`:
   API_KEY=your_api_key_here
   ```

   **Rule 2: Backend Proxy Pattern**

   ```
   WRONG:  Frontend --> External API (credentials in frontend)
   CORRECT: Frontend --> Backend Proxy --> External API
   ```

   **Architecture Requirements:**
   - Frontend MUST NEVER have direct access to API credentials
   - ALL API credentials MUST exist exclusively on server-side
   - Frontend calls backend endpoints (`/api/resource`) without credentials
   - Backend handles ALL authentication with external APIs
   - Backend validates and sanitizes ALL requests from frontend

   **Verification Checklist:**
   ```bash
   # Build the application
   npm run build

   # Search for exposed credentials in bundle
   grep -r "sk-" dist/assets/*.js
   grep -r "api_key" dist/assets/*.js
   grep -r "VITE_" dist/assets/*.js
   # All above should return NO results
   ```
2. **Map the sinks first.** List where untrusted input, a credential or PII can end up: log,
   error string, URL, HTML render, Slack message, fixture, commit, prompt. Review sink by sink.
3. **For each auth boundary, attack the binding, not the gate:** what the gate authorises, who
   can set the header it trusts, what happens when an env var is empty (fail-open defaults), and
   whether every follow-up request passes the same check. → `references/auth-and-access.md`
4. **For each secret or redaction control, ask what it cannot see:** a name-based header list,
   `exc_text` filled after the filter, a token inside a URL in an exception string, a redactor
   whose post-condition is built from its own extractor. Build the check independently of the
   thing checked, and give every scan a positive control. → `references/secrets-and-redaction.md`
5. **For PII and LLM output, check render side effects** (remote images, autolinks, mrkdwn
   breakout), erasure paths (does a cascade fire on the scrub path?), and proxies for protected
   attributes. → `references/data-pii-and-llm.md`
6. **Reproduce a claimed threat before recommending or preserving a mitigation.** Read-only prod
   access means passive reads only: NEVER send a crafted request to prod; prove it locally on the
   pinned version.
7. **Report each finding** with file and line, the discriminating case that shows it, and its
   blast radius (two axes when persistence and exposure differ). No personification ("the code
   wants to…"). NEVER quote a secret value, a token prefix or a person's data in a finding.

## Map
- `references/auth-and-access.md` fail-open auth, sessions, SAML, Supabase JWT and shared-project
  allowlists, authz gates, one-shot approval binding, SSRF, Apps Script, launchd; the OWASP
  one-screen checklist and the auth implementation baseline.
- `references/secrets-and-redaction.md` query-string and error-body leaks, redirect strip lists,
  logging filters, scan and redactor blind spots, fixtures, git history, bundles, encryption.
- `references/data-pii-and-llm.md` LLM render sinks, Slack and Apps Script sinks, erasure and
  retention, per-surface ZDR, LLM scoring of people, UK ADM law.

## Related skills
- repacted:postgres-supabase (RLS, grants and default-privilege mechanics; anon traps).
- repacted:safe-ops (handling a secret yourself: `env-get`, first commits, deleting by id).
- repacted:verification-discipline (a guard's silence is not approval; controls declared but
  not armed; positive controls).

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
