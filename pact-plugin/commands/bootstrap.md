---
description: Run at session start to reconnect the agent team and show any paused work
---

# Session-Start Ritual

> **Agent Teams only.** This ritual reconnects a team, so it applies when the session runs as Agent Teams (`/repacted:init --teams`, or `"execution": "teams"` in `.repacted/policy.json`). In the default subagent mode the bootstrap hooks are off and nothing waits on it.

The persona's Session-Start Ritual is the contract. Run the steps in order. Commands use `{team_name}`, `{session_dir}`, `{plugin_root}`, `{config_dir}` as literal brace-wrapped placeholders, replaced by hand; hook-delivered values win over the `CLAUDE.md` Current Session block, and `{config_dir}` is `$CLAUDE_CONFIG_DIR` if set, else `$HOME/.claude`. Detail: `reference/bootstrap/details.md`.

## Step 1 — Identify the session team

`team_name` comes from the SessionStart context, else the Current Session block. The platform provisions one team per session; never create one. A present config on resume is reused. An ABSENT config is the NORM after a clean end; neither is corruption. If a spawn fails with team-not-found, do NOT blind-retry `Agent()` (the spawn is non-atomic): Check for inbound messages from that name first.

## Step 2 — On-demand secretary

Do NOT spawn the secretary here: `session_init` computes the session briefing it used to deliver. Spawn it before the first memory query, save or harvest, when `secretary` is not in `{config_dir}/teams/{team_name}/config.json` `members[]`:

1. `TaskCreate(subject="secretary: <deliverable>", description="<mission>")` (teachback-exempt; no Task A)
2. `TaskUpdate(task_id, owner="secretary")`
3. `Agent(name="secretary", team_name="{team_name}", subagent_type="pact-secretary", description="Spawn session secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team {team_name}. As your FIRST action, Invoke Skill(\"repacted:teammate\") to record your identity. Then check `TaskList` for tasks assigned to you.")`

The name `secretary` is load-bearing; reuse a live one. respawn it after `/repacted:pause checkpoint`, BEFORE any `SendMessage`: a send to the stopped name resurrects its stale pre-checkpoint transcript.

## Step 3 — Surface paused state

The hook injects the prompts; there is no `paused-state.json`. "Paused work detected": surface it, ask continue or start fresh, never resume silently. "Unpaused exit detected": the same, with no consumption write. "Refreshed workstream detected" (`refresh_ts=`): a DECLARED CONTINUATION; surface the mid-flight state and AUTO-PROCEED, asking only on an inconsistency. A HALT line always surfaces to the user regardless.

**Refresh consumption write**: immediately after confirming resumption, the `refresh_ts=` value VERBATIM. Skip if the prompt said `refresh_ts=UNAVAILABLE`. Never write a consumption event when no refresh prompt surfaced.

```bash
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_refresh_consumed --session-dir '{session_dir}' --stdin <<'JSON'
{"refresh_ts": "{refresh_ts}"}
JSON
```

**Paused-state consumption write**: on SURFACING, once the choice is put, before and whatever the answer, the `pause_ts=` value VERBATIM. Skip if the prompt said `pause_ts=UNAVAILABLE`; never write a consumption event when no paused prompt surfaced. Write it for EVERY paused prompt that surfaces, including one the user answers by starting fresh (why: details).

```bash
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_pause_consumed --session-dir '{session_dir}' --stdin <<'JSON'
{"pause_ts": "{pause_ts}"}
JSON
```

## Step 4 — Plugin banner

Echo the plugin banner line (`PACT plugin: ...`) from the SessionStart reminder. If a compaction dropped the banner, take the version from the `- Plugin root:` path, else `plugin.json`. Echo the `doctor:` line too when the reminder has one: the hook prints it only when a check is not green, and the user must see it.

## Step 5 — Project tasks

The `tasks:` line reports them. Run `python3 "{plugin_root}/bin/repact-task" init --status`; on `unanswered`, ask ONCE with `AskUserQuestion`, never in plain text, whether to keep tasks in the repo (yes: `init`; no: `init --decline`). Either answer is remembered. No answer, or the user's standing rules say not to ask: do NOT init, do not ask again, skip the task-log boundary writes (`repact-task start`, `done`, `commit`) this session, and say so once at wrap-up. A generated `docs/progress.md` is the resume state; never edit it, use `repact-task`.

## Unattended mode

The switch is `REPACT_UNATTENDED=1` at launch or `"{plugin_root}/bin/repact-unattended" on --session-dir '{session_dir}'`, set by the owner; while it is ON the SessionStart reminder carries the rules (`reference/unattended-runs.md`).

## Marker (hook-managed)

`bootstrap_marker_writer.py` writes `<session_dir>/bootstrap-complete` on the next prompt once the team is identifiable on disk. No LLM action; no secretary needed.
