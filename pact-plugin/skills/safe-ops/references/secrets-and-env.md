# Secrets, key files and first commits

Context: how to use a secret without it reaching the transcript, a git repo or a build context, and
who writes secret values. Read this before you touch a `.env` or key file, before you print a config file that holds
environment values, before `git init` or the first `git add` in a directory, and before you create
or rotate a credential.

## Canonical rules
1. Never `source`, `.`, `set -a`, `cat`, `grep`, `head` or `echo` a `.env` or key file. Sourcing executes the file; prose lines in it get echoed, secrets with them.
2. Use `env-get FILE KEY[,KEY...] -- <cmd>`: it parses only `KEY=value` lines, sets the keys in the child's environment, execs the command and never prints a value.
3. Refer to the value through the environment, in single quotes (`sh -c 'echo "len=${#KEY}"'`). Never paste a value into command text.
4. For an API call a guard must see, keep the guarded CLI as the program and feed only the header: `curl -H @<(env-get FILE KEY -- sh -c 'printf "Header: %s\n" "$KEY"') ...`.
5. Never wrap a guarded CLI inside another program or a quoted string (`env-get ... -- sh -c 'curl ...'`): guards match the segment's program, so the wrapped call goes unseen.
6. `env-get` exits 2 when a key is missing or empty, or the file is missing or readable by others (`chmod 600` it); errors name the file and key, never the value.
7. No settings allow rule for `env-get` (it runs any child command), and none for `source`: the old `Bash(source .env)` and `Bash(source:*)` rules are removed.
8. Never print part of a secret: no prefixes (`${KEY:0:10}`), no suffixes. Verify a key landed by its NAME and LENGTH (`${#KEY}`) or a probe call that returns 200.
9. The user writes secret values (Secret Manager versions, key files, tokens). You name the file and key, ask, then verify presence, length and a 200 probe.
10. Testing a credential the user gives you is fine (for example an OAuth `client_credentials` call), with the token never printed. Then let the user store it and verify the stored version.
11. In every brief that touches a secret file, write: "read only key X with env-get, never source, never cat or grep the file".
12. Before `git init`, the first `git add`, or the first commit in a folder you did not just create, scan for secret files and ignore them BEFORE staging.
13. Scan with: `ls -la; find . -maxdepth 3 \( -name '.env*' -o -name '*.pem' -o -name '*.key' -o -iname 'credential*' -o -iname 'secret*' \)` (stderr not suppressed).
14. Minimum `.gitignore` for a new repo: `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `secrets/`, `credentials.json`, `token.json`, `service-account*.json`.
15. Confirm with `git status --ignored` before the add, and `git ls-files | grep -iE 'env|key|pem|secret|credential'` after the first commit.
16. Stage explicit paths, not `git add -A` or `git add .`, in any repo that may hold untracked secrets or other agents' files.
17. If a secret reached a local, unpushed commit, rebuild history before any push; if it was pushed or uploaded, the key must be rotated (tell the user; rotation is theirs).
18. To scrub a leaked value from a transcript or log, search for the LITERAL value read from the source file, not a regex over its shape, and mask it in place with a same-length mask.
19. A hand-kept `.env` should hold exactly `KEY=value` lines. Keys with spaces are silently skipped by a shell (exit 0); `env-get` ignores them too, so it reports the key missing.
20. Keep secret files out of the code directory an agent reads when answering questions: per-instance key files live in `~/.config/<tool>/`, mode 600.
21. A PreToolUse hook (the plugin's env_guard) enforces rules 1 and 2 for `.env`, `.env.*`, `*.env`, `.envrc` and `.netrc`: Read and Bash reads are denied, Grep is rewritten to skip them, a recursive grep or rg must exclude them, and `env`, `printenv` and `export -p` dumps are denied. Its deny names `env-get --keys FILE` for listing key names. Its limit: it sees the call, never the output, so it cannot scrub what a program that reads the file itself prints.
22. Egress is the boundary: a PreToolUse hook (the plugin's egress_guard) denies a credential shape in any MCP tool input, a WebFetch url, an Artifact page or upload, and any Bash command that runs a network or clipboard client (curl, wget, gh, glab, ssh, scp, rsync, nc, pbcopy). Its deny names the kind only; pass the value with `env-get FILE KEY -- <cmd>` instead. Traffic to localhost only is local and passes. It reads the call, not files uploaded by path (`curl -d @file`, `scp file host:`).
23. Config that is not a `.env` carries secrets too (compose files, appsettings, Helm values, CI variables): never print an environment block or a connection string from one, with `cat`, `grep` or a script; print key names only. These files are outside rule 21's patterns, and whatever a command prints stays in the transcript.
24. Never read the process table's command lines (`ps aux`, `ps -ef`, `ps -o args`/`command`, `pgrep -f`/`-a`, `pstree`, `top -c`, `/proc/<pid>/cmdline`): a process started with a token as an argument prints it into the transcript, and Claude Desktop starts MCP servers that way. Use pid and name only: `pgrep -x NAME`, `pgrep -l NAME`, `ps -axo pid=,etime=,pcpu=,comm=`. A PreToolUse hook (the plugin's process_table_guard) denies those shapes and names the safe form. Its limit: it matches command text, so `eval`, a built command or a script file pass it.

## Examples
- 1: a dispatch told an agent to source a repo `.env` holding prose notes; zsh echoed a database password into the transcript.
- 8: an allow rule shaped to print `Key starts with: ${KEY:0:10}` was deleted from settings.
- 9: a Secret Manager version write was interrupted by the user, who added the value themselves.
- 12: `git init && git add . && git commit` on a pre-staged folder committed three API keys; history had to be rebuilt.
- 18: a shape regex over a leaked password matched 1,624 fixture strings and missed the real one, which contained `@`.
- 24: a process listing that printed command lines copied the Supabase access tokens, passed as arguments to Desktop MCP servers, into a transcript; the tokens had to be rotated.
