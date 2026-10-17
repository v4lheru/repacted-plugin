# /repacted:bootstrap — reference

Location: pact-plugin/reference/bootstrap/details.md. The detail `commands/bootstrap.md` links to, moved
verbatim when the command was slimmed. Read on demand; the command holds the steps.

## Team-config expectations

**Team-config expectations (bidirectional)**: on resume, a PRESENT team config is reused — never re-create it. An ABSENT config is the NORM after a clean session end — the platform provisions the session team; both states are normal, neither is corruption. **Ghost-detection**: if a spawn or send fails with a team-not-found error, do NOT blind-retry `Agent()` — the platform spawn is non-atomic and the process may already be running. Check for inbound messages from the "failed" agent name first; re-spawn only if it stays silent.

## Paused and refreshed state, in full

Paused state is surfaced **automatically** by the `session_init` hook: when the previous session ran `/repacted:pause` — or ran `/repacted:wrap-up` and took its branch for a PR that exists but is not merged, which writes the same event — it wrote a `session_paused` event to the session journal, and `session_init` reads that event on resume and injects the paused-work prompt into the SessionStart context. There is **no `paused-state.json` file** — do not attempt to read one (nothing writes it). Watch the SessionStart context for a "Paused work detected" line. If it appears, **do not silently resume.** Surface it to the user and ask whether to continue the paused workflow or start fresh; their choice drives next-step dispatch.

**Refreshed state**: also watch the SessionStart context for a refreshed-workstream prompt (a "Refreshed workstream detected" line carrying a `refresh_ts=` key). If present, this is a DECLARED CONTINUATION, not a fresh start: surface the mid-flight state to the user (feature, next phase, worktrees, any HALT line) and AUTO-PROCEED to respawn the specialists the named next phase needs. Ask the user ONLY on inconsistency: the prompt's HALT line has no matching live blocker task in `TaskList` (or live blockers exist the prompt doesn't mention), a listed worktree path does not exist on disk, or the prompt's `team_name` (when present) does not match the `team_name` in the **Current Session** block. A HALT line always surfaces to the user regardless.

If the prompt said `refresh_ts=UNAVAILABLE`, skip the write (the prompt may re-surface once; its staleness downgrade bounds the repetition). Never write a consumption event when no refresh prompt surfaced. On a quit-then-new-session resume the consumption event lands in the NEW session's journal (a harmless orphan record) — fire-once on that path is enforced by the one-hop-back journal read, while the ts-bound consumption covers the same-session paths (`/compact` and same-session `--resume`).

The same three conditions apply unchanged: skip the write if the prompt said `pause_ts=UNAVAILABLE`, never write a consumption event when no paused prompt surfaced, and expect the event in the NEW session's journal on a quit-then-new-session resume. Write it for EVERY paused prompt that surfaces — a stale one, one reporting a merged or closed PR, one mentioned only as the losing claim beside a newer refreshed one, and **one the user answers by starting fresh rather than resuming**. Surfacing is the whole trigger. Do NOT gate this write on the user choosing to continue the paused workflow: that choice decides your next-step dispatch and decides nothing about this write.

**Why it is keyed that way**, because the failure it prevents does not look like a failure. A surfaced prompt makes the session skip its Working Memory rebuild, and this write is the only thing that retires the claim. A session ended by `/repacted:wrap-up` with a PR still open writes a paused claim as a matter of course, so the next session surfaces one, freezes its block, and reports `Working Memory: not rebuilt — a resumption claim surfaced at session start`. If the user said start fresh and the write was skipped, that reported reason is false. Nothing errors, and no single session looks wrong — the freeze does not persist, it is re-created from a new claim each time, so every session on its own appears to be behaving correctly while the block is never rebuilt for as long as the PR stays open. Retiring the claim on surfacing is what keeps that from becoming the steady state.

**What this write does and does not bound.** A single freeze is already bounded, structurally and without help: the marker is written to the CURRENT session's journal while a claim is read from the PREVIOUS session's, so no marker can freeze a second session, and a claim is visible one hop back and no further. What this write bounds is whether the PROMPT RE-SURFACES — and therefore whether a NEW freeze is minted from the same claim. It delivers that bound only where the consumption event lands in the SAME journal as the claim, which is the `/compact` and same-session `--resume` paths; on a quit-then-new-session resume it is the orphan record described above, it retires nothing, and the one-hop bound is doing all the work. Read that bound per claim, not in aggregate: a producer that mints a fresh claim every session, as an open PR does, regenerates the freeze whatever this write did about the last one. So a run of individually-expiring freezes is indistinguishable from a persistent one, and nothing here promises the block gets rebuilt soon.

## Step 2 as it was (session-start secretary spawn, retired)

The secretary no longer spawns at session start: it spawns on demand, per the command's
Step 2, and the session briefing is computed by the `session_init` hook. The retired text:

Spawn the session secretary using single-task dispatch — the `pact-secretary` agentType is exempt from the teachback gate. No Task A teachback round-trip.

1. `TaskCreate(subject="secretary: deliver session briefing", description="<full mission: deliver session briefing on spawn, answer memory queries during the session, process HANDOFFs at workflow boundaries; CONTEXT / MISSION / INSTRUCTIONS / GUIDELINES per the orchestrator persona §13 Recommended Agent Prompting Structure>")` — single work task. The subject names a **discrete deliverable** (the briefing), NOT the secretary's standing role; the standing duties (memory queries, HANDOFF harvest) live in the description as mission context and are tracked by their own later tasks.
2. `TaskUpdate(task_id, owner="secretary")` — assign to the secretary; no `addBlockedBy` (no teachback gate)
3. Spawn the secretary with exactly this call (the template bootstrap.md carries):

   ```
   Agent(name="secretary", team_name="{team_name}", subagent_type="pact-secretary", description="Spawn session secretary", prompt="YOUR PACT ROLE: teammate (secretary).\n\nYou are joining team {team_name}. As your FIRST action, Invoke Skill(\"repacted:teammate\") to record your identity. Then check `TaskList` for tasks assigned to you.")
   ```
    - **Use `subagent_type="pact-secretary"` and the canonical `name="secretary"` — the literal name is load-bearing**.

The secretary delivers the session briefing at spawn, answers memory queries during the session, and processes HANDOFFs at workflow boundaries. The briefing task is a discrete deliverable: the secretary MUST self-complete it (`TaskUpdate(status="completed")`) as the final act of delivering the briefing — you do NOT complete it, and you MUST NOT expect to. Completing the task does NOT end the secretary's role; it continues as memory consultant and HANDOFF harvester for the rest of the session. Memory queries from any other agent are blocked until the secretary is alive.

Spawn the secretary **only once per session** — reuse the same secretary for any subsequent memory queries or HANDOFF harvesting — AND respawn it after `/repacted:refresh`, whose shutdown stopped the previous secretary process. Post-refresh, respawning is MANDATORY BEFORE any `SendMessage` to the secretary name: a send to the stopped name resurrects its stale pre-refresh transcript instead of starting fresh.

## Session Placeholder Variables

Command files use `{team_name}`, `{session_dir}`, `{plugin_root}`, and `{config_dir}` as literal brace-wrapped placeholders. **Substitution is manual textual replacement** performed by the orchestrator before invoking shell commands — there is no template engine.

`{config_dir}` is this session's Claude config root — the value of `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`. Read it off an absolute path the platform already injected into your context — your plugin root is `{config_dir}/plugins/…` — rather than shelling out for the variable. Substitute it before running any command; never assume `~/.claude`. It is env-derived, so it has no CLAUDE.md line and no context-JSON key in the table below.

| Placeholder | CLAUDE.md line | Context JSON key | Description |
|-------------|---------------|-----------------|-------------|
| `{team_name}` | `- Team:` | `team_name` | Session team name |
| `{session_dir}` | `- Session dir:` | derived from `session_id` + `project_dir` | Session journal directory |
| `{plugin_root}` | `- Plugin root:` | `plugin_root` | Installed plugin root for CLI paths |

**Source precedence**: when the `session_init` hook delivers substitution instructions inline (in the SessionStart system reminder at the top of the session), **those hook-delivered values are authoritative** and take precedence over the Current Session block in `CLAUDE.md`. The `CLAUDE.md` block is the fallback source, used only when the hook context has been lost (e.g., after compaction drops the initial system reminder).

**Per-field fallback**: if an individual variable is missing from `CLAUDE.md` (e.g., a session block written by an older `session_init` that didn't record `- Plugin root:`), fall back to `pact-session-context.json` in the current session directory for that one variable. Do not re-read the whole set from JSON when a single field is missing.

**Last-resort fallback for `{plugin_root}`**: if both `CLAUDE.md` and `pact-session-context.json` are unavailable, use `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/protocols/pact-plugin/../"` (symlink traversal). If the resolved path does not exist, stop and report the issue to the user rather than continuing with a broken path.

## Marker, in full

The bootstrap-complete marker at `<session_dir>/bootstrap-complete` is written by the
`bootstrap_marker_writer.py` UserPromptSubmit hook once the session team is identifiable on disk: its
`config.json` names at least one member, or (config-less substrate) `teams/{team_name}/inboxes/`
exists. It no longer waits for a secretary. If a `bootstrap_gate` refusal says the marker is missing
after the ritual, the team directory has not appeared yet (the platform creates it about 38 s after
SessionStart); send the next prompt once it has.
