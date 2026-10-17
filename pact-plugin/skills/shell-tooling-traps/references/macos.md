# macOS on this machine

Context: the user's Mac may be a managed corporate device (an MDM profile, an endpoint security agent, zsh, BSD
userland, Homebrew) and the Bash tool runs sandboxed. Read this before you touch quarantine
attributes or signatures, audit or migrate Mac settings, blame a remote for a network fault during
a long run, or trust a command whose effect lands outside the sandbox.

## Canonical rules
1. The userland is BSD: no `grep -P`, no `timeout`, no `setsid`, `sed -i ''` needs its empty argument. Check `command -v` before relying on a GNU tool.
2. A sandboxed command can exit 0 and do nothing outside the sandbox (clipboard, `open`, `osascript`, notifications, `say`). Run it with `dangerouslyDisableSandbox: true` and read the effect back.
3. `pbcopy` in the sandbox writes a clipboard the user never sees. Verify with `pbpaste | cmp - <file>` (not bare `diff`, which rtk rewrites).
4. Gatekeeper "could not verify" popups come from the `com.apple.quarantine` xattr, not the signature. Strip it one file per Bash call: `xattr -d com.apple.quarantine "<file>"`.
5. NEVER `codesign --force -s -` binaries and never loop `xattr -d` over many files in one process: an endpoint security agent can kill the process (exit 1, no output).
6. `xattr -rd` on a repo root aborts on read-only `.git/objects`; target `node_modules` or the `.node` files directly.
7. Never tell the user to click "Move to Trash" on a Gatekeeper prompt for an npm binary: it deletes the binary and breaks the build ("Cannot find module @rollup/...").
8. A write that an endpoint security agent kills may still land (`defaults write` exits 1): verify with `defaults read` before retrying. You cannot allowlist it yourself; that is your IT team's call.
9. When auditing or migrating Mac settings, classify every item PERSONAL / CORP-MANAGED / AMBIGUOUS, and skip corp-managed ones (MDM profiles, Managed Preferences, EDR, VPN, SSO agents).
10. Migration scripts must be idempotent: check-then-install for every step (`brew list <pkg> || brew install <pkg>`), and never blind-overwrite a dotfile.
11. Before calling a remote degraded during an unattended run, check the Mac slept: `pmset -g log | grep -E "Sleep|Wake|DarkWake"` against the failure time (the log is local time).
12. On battery a Mac set to `sleep 1` with wake-on-network off sleeps: DarkWake keeps TCP keepalives but breaks fresh TLS handshakes and large streams.
13. For long unattended network runs, add retry with backoff and wrap the run in `caffeinate -i`; state the remaining battery rather than silently stopping.

## Examples
- 3: two drafts were reported "copied"; `pbpaste` held 63 bytes of unrelated text from hours earlier.
- 5: an ad-hoc `codesign` of `.node` files was SIGKILLed mid-run with no output.
- 7: "Move to Trash" deleted rollup and esbuild; the build failed with a wrong-platform error.
- 11: three reports of a "degraded" remote service matched a Sleep/DarkWake cycle every 54 seconds.
