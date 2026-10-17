# API: <service name>

Checked: <YYYY-MM-DD> by <agent> against <docs URL> (API version <v>, status production/beta).
Evidence labels: MEASURED (a call you ran, command below), DOCS (page cited), BELIEVED (neither).

## Access
| Property | Value | Label |
|---|---|---|
| Base URL (prod / sandbox) | | |
| Auth scheme and header | | |
| Key file and key names (never values) | `~/.config/<tool>/<id>.env`: `KEY_NAME` | |
| Rate limits and their headers | | |
| Pagination (cursor, page, take/skip) and the exhaustion signal | | |

## Auth tests (each with a control)
| Test | Expected | Actual | Command |
|---|---|---|---|
| Valid credential | 200 | | `curl -sS -o /dev/null -w "%{http_code}" -H @<(env-get <file> KEY -- sh -c 'printf "<Header>: %s\n" "$KEY"') <url>` |
| No credential (negative control) | 401/403 | | |
| Wrong credential | 401 | | |
| Under-scoped credential on a write-scope probe | 403 | | |
If the no-credential call returns the same as the credentialed one, the endpoint authenticates
nobody and the test proved nothing.

## Endpoints used
| Method | Path | Purpose | Tested (date) | Notes (limits, quirks) |
|---|---|---|---|---|

## Errors seen
| Status | Body shape | Meaning | Handling |
|---|---|---|---|
Classify on the payload, not the status: a 200 can carry an `errors` array.

## Open questions and what would settle each
- <question>: <the call or document that answers it>
