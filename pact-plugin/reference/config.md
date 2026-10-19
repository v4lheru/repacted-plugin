# PACT Configuration Reference

> **Purpose**: The single reference for every PACT runtime option — how to set it, what it does, and the two `settings.json` gotchas.
>
> **Usage**: Read this when enabling greedy-fix or autonomous scope detection, tuning the dispatch gates, or persisting PACT config across sessions.

---

## Options

All PACT options are `PACT_*` environment variables, resolved once per session by `hooks/shared/pact_config.py`. Because the resolver reads `os.environ`, any env-var source works.

| Option | Type | Default | Consumer | Effect |
|---|---|---|---|---|
| `PACT_PR_GREEDY_FIX` | bool | `0` (off) | orchestrator (peer-review) | When on, peer-review greedily auto-delegates **all** minor + future reviewer findings as reversible fixes. `merge` / `close` / `push` stay user-gated; SACROSANCT is never overridden; findings that are widely out of scope, extremely expansive, or pattern-violating are surfaced (not silently dropped). Off = the default per-finding review gate. |
| `PACT_AUTONOMOUS_SCOPE_DETECTION` | bool | `0` (off) | orchestrator (scope detection) | When on, scope detection may auto-decompose a multi-scope task **without** user confirmation when all strong signals fire and no counter-signals are present. Off = the Confirmed tier (the orchestrator proposes; the user confirms). |
| `PACT_DISPATCH_INLINE_MISSION_MODE` | enum | `warn` | hook (`dispatch_gate`) | Disposition of the dispatch-gate inline-mission heuristic: `warn` (advisory `additionalContext`), `deny` (block the spawn), `shadow` (journal only — observed but neither warns nor denies). Any unrecognized value falls back to `warn`. |
| `PACT_DISPATCH_VARIETY_MODE` | enum | `deny` | none since 2.1.0 | No check reads it since 2.1.0: `repact dispatch` takes the variety itself (`--variety`) and refuses a dispatch without a valid one. A value set here is ignored. |
| `PACT_REPACT_SHIM` | bool | `1` (on) | hook (`session_init`) | The `repact` command on PATH: each session start keeps a small shim at `~/.local/bin/repact` that runs the installed plugin's `bin/repact` with your arguments. It never writes over a `repact` it did not write, and never edits a shell rc file; `repact-doctor` prints the PATH line to add when `~/.local/bin` is not on PATH. Off: the next session start deletes its own shim and writes nothing. The doctor also reads it from the `env` block of settings.json. |

**Bool parsing**: `1` / `true` / `yes` / `on` (case-insensitive) → **on**; everything else — including `0`, an unrecognized value, and an unset variable → **off**. This is exact-membership, not truthiness: `0` is off.

**Consumer** distinguishes two runtimes. **LLM-consumed** options (`PACT_PR_GREEDY_FIX`, `PACT_AUTONOMOUS_SCOPE_DETECTION`) are surfaced to the orchestrator at session start in an injected **PACT Runtime Config** block, because markdown flows cannot read environment variables. **Hook-consumed** options (the two `*_MODE` gates) are read directly by the Python hooks at hook load.

## `PACT_TEST_MEMORY_DIR`: for tests only

**It moves the memory database and nothing else.** It does NOT move `session-tracking/`, which is in the same root but resolves through `get_claude_config_dir()` and thus follows `CLAUDE_CONFIG_DIR`. If you set only this variable, the database moves and the `session-tracking/` data stays where it was. This divides one set of memory data between two trees. The boundary comes first here because that division is the risk.

**It is there so that a test process cannot touch the live database. It is not a supported procedure to move your data.** A general procedure of that type is not available at this time. The test harness sets this variable. Do not set it to move a database of your own.

**It is missing from the Options table above, and that is on purpose.** Each row in that table resolves one time for each session, through `hooks/shared/pact_config.py`. This variable resolves at use time, in `get_memory_dir()` in `skills/pact-memory/scripts/config.py`, and it is a test-harness variable, not a control on behavior. A row in that table can tell the reader three things that are incorrect.

If you find the variable in a process list or in the source, these are its mechanics:

- **Controls** the memory database: `get_memory_dir()` and each path derived from it, which is `memory.db`.
- **Default**, if unset: `~/.claude/pact-memory`.
- **An empty value counts as unset.** Thus `PACT_TEST_MEMORY_DIR=""` cannot move the database to the current directory by accident.
- **The resolver redirects. It does not refuse.** It cannot tell a test path from a live one. It sees only if you set the variable.

<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->
The `pact-memory` skill carries the full rule.

## Where to set the options

Any env-var source works. In order of persistence:

- **Recommended — the `settings.json` `env` block** (launch-method-independent). Claude Code applies it at startup regardless of how `claude` was launched:
  ```json
  { "env": { "PACT_PR_GREEDY_FIX": "1", "PACT_AUTONOMOUS_SCOPE_DETECTION": "1" } }
  ```
- **Shell rc export.** Use `~/.zshenv` to cover every zsh invocation; `~/.zshrc` covers only interactive shells (GUI/dock launchers, `cron`/`launchd`, and some IDE integrations will not source it):
  ```bash
  export PACT_PR_GREEDY_FIX=1
  ```
- **One-shot launch prefix**, valid only when the variable is not pinned in the `env` block (see gotcha 1):
  ```bash
  PACT_PR_GREEDY_FIX=1 claude …
  ```

## Precedence

Claude Code merges the settings tiers into the process environment before a hook subprocess starts, so the resolver sees one already-merged `os.environ`:

```
managed settings → command-line args → env vars → settings files (local → project → user) → defaults
```

## Two gotchas

1. **The `env` block overrides a same-named shell variable.** A value pinned in `settings.json`'s `env` block wins over a shell export of the same name — so a one-shot `PACT_PR_GREEDY_FIX=0 claude …` will **not** override a `"PACT_PR_GREEDY_FIX": "1"` pinned in the `env` block. A one-shot override works only when the variable is not pinned there.
2. **A malformed `settings.json` is dropped WHOLESALE in headless mode.** In `-p` / headless mode, a syntax error anywhere in `settings.json` makes Claude Code silently ignore the **entire** file — including the whole `env` block, so every PACT option persisted there stops applying. `session_init` warns at startup if it reads a malformed `settings.json`. Keep the file valid JSON.

**Namespace guardrail**: name each PACT option `PACT_*`, and do not give a PACT option a `CLAUDE_CODE_*` name. A `PACT_*` name makes the owner of an option unambiguous.

## Team policy

A repository can commit `.repacted/policy.json` at its root to make the guards stricter for everyone who works in it. The policy can only **tighten**: a value that would loosen what this machine already has is ignored, never applied. Start from `templates/policy.example.json`.

| Key | Merge rule |
|---|---|
| `version` | `1`. Another value is noted; the keys are still read as version 1. |
| `modes.<check>` | The stricter of this machine's mode (`bin/checks.json`, else the shipped one) and the policy's, ranked `off` < `shadow` < `advisory`/`note` < `gate`/`enforce`. Checks: `handoff_evidence`, `commit_rule`. A word the check does not have is ignored; `amendment_gate`, `ref_dispositions` and `rejection_shape` were retired in 2.1.0 and are noted. |
| `commit_rule.deny_words` | Added to this machine's words (at most 200, each up to 100 characters); never removes one. |
| `pact_options.PACT_DISPATCH_INLINE_MISSION_MODE`, `pact_options.PACT_DISPATCH_VARIETY_MODE` | The stricter of the env value and the policy's, ranked `shadow` < `warn` < `deny`. |
| `egress.allow_pragma` | `false` makes the egress guard scan lines marked `pragma: allowlist secret` too. `true` changes nothing. The commit scan's pragma is not affected. |
| `egress.network_programs` | Program names added to the ones whose commands the egress guard scans (the whole command; their upload arguments are not parsed). |
| `session.cost_cap_usd` | A number of US dollars above 0 (at most 1,000,000): the session cost cap. The lower of this and `PACT_SESSION_COST_CAP_USD` wins; a policy cap above this machine's is ignored and noted. See [Session cost cap](#session-cost-cap). |
| `agents.project_scope` | `off` stops specialists a project brings in `.claude/agents` from being spawned as teammates (`reference/project-agents.md`). `allow`, the default, changes nothing; any other value is ignored. |

**Where it is read.** The root of the work tree the hook runs in, the nearest directory going up from the working directory that holds `.git`, and the root of every work tree that one sits inside (a submodule, a vendored repository, an empty `.git`, a linked worktree kept inside the main checkout). All of them apply together, the stricter value winning, so a nested `.git` cannot drop an outer policy. A linked worktree reads its own checked-out copy. A policy in a directory that is not a work tree root is never read.

**What is ignored, and noted.** A value that would loosen; an unknown key; a key that only looks like a known one (a fullwidth or other Unicode look-alike); a wrong type (`allow_pragma` must be `true` or `false`, not `0` or `"false"`); a key given twice (neither copy is used). The whole file is ignored when it is a symbolic link (or `.repacted/` is), resolves outside the work tree, is not a regular file, is larger than 64 KiB, is not UTF-8, or is not valid JSON (including `NaN`, `Infinity`, a number like `1e999`, or an integer longer than 18 digits). Nothing in a policy is ever fatal, and there is no switch that turns a policy off.

**How you are told.** `repact-doctor` has a `team policy` line: GREEN with the file's path and how many values it tightens, AMBER when anything in it was ignored. `repact-doctor --policy` prints every value a policy can tighten, its effective value, where it came from (`shipped`, `checks.json`, `env` or `policy`) and each ignored item. A refusal caused by a value the policy raised names the policy files, the key and what was ignored; each key or value it echoes from a file is escaped and cut to 40 characters, and the whole note to about 500.

## Session cost cap

`/repacted:orchestrate` and `/repacted:comPACT` print one line before they dispatch (`repact-cost --preview N`): the planned dispatches and their estimated cost, the session's spend so far and the cap. Once the session's spend is at or past the cap, `dispatch_gate` refuses each new spawn of any agent type with rule `dispatch_gate.session_cost_cap`: PACT specialists, `general-purpose`, built-in, other plugins' and a project's own agents alike. Only the read-only research agents `Explore` and `Plan` and `pact-secretary` are not capped. The cap is the lower of `PACT_SESSION_COST_CAP_USD` (a plain number, like `25` or `12.50`) and the team policy's `session.cost_cap_usd`; with neither set there is no cap.

**Where the spend comes from.** The host records no dollar figure a hook can read. Every assistant message in the session's transcripts (the lead's and each teammate's or subagent's under `<session>/subagents/`) records the API's token usage and model; `hooks/shared/session_spend.py` counts each message once and prices it at Anthropic's list prices, from https://platform.claude.com/docs/en/about-claude/pricing as of 2026-10-17 (5-minute and 1-hour cache writes, cache reads, the Haiku 5.5 long-prompt rate, fast mode, US-only inference and web searches included). Every figure is an **estimate at list API prices, not billing**: a subscription plan is not billed per token, discounts and credits are unknown here, calls outside the session's transcripts (a `claude -p` run) are not counted, and a model missing from the table is priced at its highest rates and named in the refusal. The token counts are exact; the dollars are only as current as the table's date.

**The per-dispatch estimate.** The median spend of one teammate transcript over the past sessions on this machine (this session excluded), labelled `a rough guide` when there are fewer than 5 of them. With none recorded yet the line says `cost unknown (no recorded dispatches on this machine yet)`, prints no figure and never warns that the dispatches would pass the cap. The line prints its basis, for example `(median of 12 past dispatches on this machine)`. The cap and the autopilot's `--max-usd` act on measured spend only, never on this estimate.

**When the spend cannot be read** (no transcript found, an unreadable file, a reader error) the dispatch goes ahead and the lead is told once per session; a `cost_cap_note` row goes to the session journal. A `PACT_SESSION_COST_CAP_USD` that is not a plain number above 0 (`abc`, `1e3`, `nan`, `-5`, `0`) is ignored the same way, with one note. The cap is a budget control, not a security boundary: the running totals are cached in the session directory (`cost-spend.json`), or with no session directory in `${XDG_STATE_HOME:-~/.local/state}/repacted/spend/<session id>.json`, never in the project. The session's own agents can write either, so an agent that rewrote it could lower its own figure.

**Reserved keys.** None today.

## Migration — `autonomous-scope-detection` marker → env var

Earlier versions enabled autonomous scope detection with an `autonomous-scope-detection: enabled` line in `CLAUDE.md`. **That marker is no longer read.** To keep autonomous scope detection on, set `PACT_AUTONOMOUS_SCOPE_DETECTION=1` (via the `settings.json` `env` block or a shell export). An existing `CLAUDE.md` marker is now a no-op — remove it at your convenience.

## Telegram

Telegram notifications are configured separately (their own `.env` with credentials), outside this resolver.
