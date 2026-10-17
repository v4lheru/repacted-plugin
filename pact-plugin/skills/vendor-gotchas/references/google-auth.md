# Google client auth off GCP

Context: google-auth libraries running outside Google Cloud (Railway, Fly, Render, a VM) can hang
while probing for ambient credentials. Read this when a Google API call is slow or hangs only in a
hosted environment. Railway-specific field notes are in `railway.md`; Drive, Docs, Sheets and Apps
Script are in `repacted:google-workspace`.

## Canonical rules
1. `google-auth-library`, `google-ads-api` and the google-cloud SDKs probe the GCE metadata server (`169.254.169.254`) to detect ambient credentials, even when you pass explicit OAuth credentials. Off GCP the probe can block token acquisition for a long time.
2. The tell is `MetadataLookupWarning: received unexpected error = All promises were rejected` next to a slow or hung request (an HTTP 499 when the client gives up).
3. It is region-sensitive: the same code can work in one host region and hang in another, because the failure latency depends on how the network treats the link-local address. "Changed region and it broke" is a clue.
4. Fix: set `METADATA_SERVER_DETECTION=none` on EVERY service making Google calls (the web service and any cron or poller), then redeploy. It is safe only when the app uses explicit credentials, not ADC.
5. Diagnose by timing the same call locally, where the probe fails fast: seconds locally and minutes in production points at the probe, not your code.

## Examples
- 4, 5: an account enumeration took 4.2 s locally and hung 133 s in a Railway EU service until the client closed the request.
