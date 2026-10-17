# Hooks, merge guard and harness behaviour

Context: measured behaviour of Claude Code hooks, permissions, spawns, plugins and transcripts, plus
the PACT merge-guard recipe. Read it when a hook seems not to fire or not to block, when a merge
guard prompts, when a tool you were promised is missing, when a background job dies, or when
designing a hook or a plugin. Skill-authoring runtime traps are in skill-authoring-runtime.md.

Stamp legend: `(verified|observed DATE, VERSION; until MILESTONE)`; "re-probe" marks a Claude Code
platform behaviour with no fork fix.
`{config_dir}` is `$CLAUDE_CONFIG_DIR` when set, else `$HOME/.claude`.
Marks ("re-probe", "practice", "until Mn"): defined once, in [SKILL.md](../SKILL.md).

## Canonical rules
1. PreToolUse `ask` is silently ignored under bypassPermissions while `deny` is enforced; an `ask` floor is no floor in agent sessions. (observed 2026-07-23, Claude Code, version n/a; re-probe)
2. Subagents share the parent `session_id`; `agent_id` is null for the orchestrator. State keyed on `session_id` is per session TREE, not per actor. (observed 2026-07-23, Claude Code, version n/a; re-probe)
3. A PostToolUse firing proves the tool ran, not that anyone approved it; mint approval state only when `permission_mode` is in an allowlist of prompting modes. (observed 2026-07-23, Claude Code, version n/a; re-probe)
4. Reason from the hook payload's `permission_mode`; `permissions.defaultMode` in settings can disagree with the live mode. (observed 2026-07-23, Claude Code, version n/a; re-probe)
5. Hooks registered in settings mid-session DO fire without a restart; prove a hook design with a throwaway probe that dumps stdin. (observed 2026-07-23, Claude Code, version n/a; re-probe)
6. Merge guard: answer with AskUserQuestion, single-select, a short "Yes" label, and the exact command in backticks in that option's description. The hook needs the picked option (label equal to the answer) to carry the command in its label or description; multi-select, a free-text answer, or the command only in the question text approves nothing. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by probe with a synthetic answer; re-probe)
7. The merge guard covers git, `gh` and `glab` verbs and merge calls made through `gh api` or `glab api`: each is denied until an AskUserQuestion carries the literal command. The approval is bound to the checkout the approved command runs in (for a bare merge with no number, also to the branch): run it from the same directory, or put the same `cd <repo> &&` in the question and in the run. A pipe or a second command on the line does not break the match. (verified 2026-10-02, repacted 1.3.6 and 1.3.7, by probe)
8. AskUserQuestion is absent from subagent toolsets; a teammate cannot obtain or simulate a click, so it reports the need to the lead. (observed 2026-10-02 in a teammate spawn, Claude Code 2.1.288, repacted 1.3.7; re-probe)
9. In a teammate session every background mechanism (run_in_background, detached start_new_session) is reaped at the turn boundary; hand long jobs to the lead. (observed 2026-06-03, Claude Code, version n/a; re-probe)
10. A missing tool is usually the spawn shape: `isolation: worktree` spawned a background agent without task tools. Report the gap and the compensation. (observed 2026-09-06, Claude Code, version n/a; re-probe)
11. Persist plugin state in `{config_dir}/plugins/data/<plugin>-<marketplace>/`; the cache dir is per version and stranded on update. (verified 2026-10-02, Claude Code 2.1.288, repacted 1.3.7, by directory listing; re-probe)
12. A refusal can still have had its effect, and a success string can hide a no-op; spend one call checking the effect before routing around it. (observed 2026-08-25, Claude Code, version n/a; practice)
13. A mid-session EPERM on a path read minutes earlier can be transient (sandbox or sync); report it, retry once after the lead replies, never self-complete. (observed 2026-06, Claude Code, version n/a; re-probe)
14. Never install a plugin beside repacted that rewrites subagent models, denies spawns or denies AskUserQuestion. (verified 2026-09-26; re-probe)
15. Count MCP usage from transcripts by `"name":"mcp__<server>__` tool_use entries only; the plain name appears in every session's deferred-tool list. (observed 2026-09-26, Claude Code 2.1.283; re-probe)
16. The rtk rewrite plus the worktree guard can refuse all git; call `/usr/bin/git` against your own tree (see worktrees.md). (observed 2026-09-08, rtk 0.41.0; re-probe)
17. A Gmail MCP `read_email` returns only the text/plain part; HTML-only mail cannot be field-verified through it. (observed 2026-08, gmail MCP, version n/a; re-probe)
18. Observed: a Write's content can end with stray tool-call markup (cause not established). Tail-check written files and grep them with `grep -nE '<(parameter|invoke)[ >]'` before committing. (observed 2026-09-26, Claude Code 2.1.283; re-probe)

## Detail

**The merge guard.** Whether approval exists is the user's call (for example, a rule that an
explicit intent for a specific PR or branch is the approval). The guard cannot see the user's turn,
so it still prompts after an explicit order; answer it with the recipe in rule 6, e.g. an option
labelled "Yes" whose description is Run `git push origin master`. It sees `glab mr merge` and merge
calls to the GitLab and GitHub APIs as well as git and gh verbs (rule 7), so the ask happens BEFORE
the call. The approval holds for the checkout the approved command runs in. Asked and run in one
checkout, or in a worktree of the same repo, the command passes behind a `cd`, with a pipe and
with a second command on the line. It is refused when the `cd` lands in another repo, and a bare
`glab mr merge` (no number) is refused on another branch. Asked from a directory that is no
checkout, nothing is approved unless the approved command itself carries the `cd <repo> &&`; the
run then carries the same. The guard refuses `--delete-branch` and `--remove-source-branch`
unless the approved command names them, and a command approved with the flag must run with it.
It does not check an API field (`should_remove_source_branch=true` on a `glab api` merge) or the
merge request's own setting: check those two by hand, then clear the flag or name the deletion
in the question. Do not invent an MR flow a project does not use to avoid the prompt.
(verified 2026-10-02, repacted 1.3.6 and 1.3.7, by probe)

**Hook design facts.** Pre and Post share `agent_id, agent_type, cwd, hook_event_name,
permission_mode, prompt_id, session_id, tool_input, tool_name, tool_use_id, transcript_path`;
Post adds `tool_response` and `duration_ms`. `session_id` is byte-identical across a Pre/Post pair,
so a PostToolUse-mints-a-grant pattern can key on it; never fall back to a host, pid or cwd key.
Unknown mode must not mint a grant, and unknown mode must deny. A "no cross-session leak" test does
not catch per-tree sharing: that hazard is distinct actors on one id. Keep `ask` for the user's own
prompting sessions and emit `deny` for irreversible ops. A PACT source comment claiming in-session
hook instrumentation is not viable is wrong.

**Long jobs.** In the LEAD session, a Python launcher using `start_new_session=True` with a pidfile,
an exit marker, a resumable ledger and a death-vs-exit monitor survives; `nohup` dies in seconds and
`run_in_background` can be reaped after minutes. The launcher mechanics are in
repacted:shell-tooling-traps. A teammate builds and verifies the script, hands execution to the
lead, and waits (`intentional_wait` with a reason such as awaiting the lead's run).

**Spawn shape.** When a tool is missing, ToolSearch returning nothing is evidence about your
surface, not the platform. Say precisely what is missing, what someone else can do instead (flip
your status, write your handoff from your message), why proceeding is safe, and offer to stop.
Never assert a boundary drain or any check you have not run.

**Plugins and channels.** Find the data dir from `$CLAUDE_PLUGIN_ROOT` or from the module's own
path (the `plugins/cache/<marketplace>/<plugin>` segment); the data dir name inverts the order
(`<plugin>-<marketplace>`). A local directory marketplace also gets a cache copy per version under
`plugins/cache` (its install record names that copy as the install path); whether a session reads
the cache copy or the directory is not probed (observed 2026-10-02, listing only; re-probe). A channel plugin needs `"channel"` in the plugin manifest keywords and an `mcpServers`-wrapped
MCP config at the plugin root. With the Python MCP SDK (FastMCP, SDK 1.26.0), declaring
`claude/channel` needs two monkey-patches: inject `experimental_capabilities` in
`create_initialization_options`, and capture the write stream in `run`, then send a
`notifications/claude/channel` JSON-RPC notification. (observed 2026-04, Claude Code, version n/a)

**Transcripts.** Sessions are JSONL under `{config_dir}/projects/<escaped path>/`, subagents under
`<session>/subagents/`. Record types: `user`, `assistant`, `system`, `attachment`,
`file-history-snapshot`, `permission-mode`, `last-prompt`. `tool_result` blocks are the bulk; text
blocks carry the knowledge. `{config_dir}/history.jsonl` has one line per prompt. Retention follows
`cleanupPeriodDays` (default about a month), so state the window of any count. A
With the default root, `{config_dir}/settings.local.json` is only the project-local file of the
project `~`, not a user scope. (observed 2026-09-26, Claude Code 2.1.283)

## Examples
- A careful approval design assumed `ask` gated agents and that a tool running proved approval; a
  sentinel probe falsified both, silently.
- A 17-minute teammate scrape died at thread 110 of 996 twice; the lead ran the same script to
  completion.
- A teammate had no task tools at all; it had been spawned with worktree isolation as a background
  agent.
- An approved `glab mr merge` was denied behind a `cd` prefix with a pipe and a second command
  ("its approved operation does not match this command"), and the compound line was blamed. A probe showed
  the same line passes in the approved checkout; only a `cd` into another repo is refused.
