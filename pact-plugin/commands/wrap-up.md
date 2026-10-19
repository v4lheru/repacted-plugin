---
description: Clean up at the end of a session and bring the docs up to date
---
# PACT Wrap-Up Protocol

End-of-session close-out: consolidate, sync docs, clean up, retrospect, then decide. For a PR still open and not ready, see [pause.md](pause.md). Each step's detail: [close-out.md](../reference/wrap-up/close-out.md) (steps 1, 5, 7, 8), [worktree-cleanup.md](../reference/wrap-up/worktree-cleanup.md) (step 6); why: [cleanup-rationale.md](../reference/wrap-up/cleanup-rationale.md).

## 1. Memory consolidation (Pass 2)

Answer the step-4 retrospective and fix the difficulty rating FIRST, then dispatch the secretary's Consolidation Harvest with both INSIDE its task description, quiet (no propagate sentence): the template is [close-out.md §1](../reference/wrap-up/close-out.md#1-the-consolidation-dispatch). Skip only for a trivial session (one comPACT, no variety score). Record `{consolidation_ran}` as the literal `true` or `false`. Do not wait for the harvest: steps 2-3 run beside it.

## 2. Documentation sync

Run the pin scan with no content (load [reference/init/pin.md](../reference/init/pin.md) and run it as written), and confirm `docs/<feature>/preparation/` and `docs/<feature>/architecture/` match the implementation (archive obsolete docs to `docs/archive/`).

## 3. Workspace cleanup

Delete the temporary files this session created (`temp_test.py`, `debug.log`, ...).

## 4. Orchestration retrospective

Done in step 1, from [retrospective.md](../reference/wrap-up/retrospective.md). Trivial session: `no retrospective this session`.

## 5. Journal drain-before-close

The single drain gate. Confirm the secretary finished the step-1 harvest ("All journal entries processed to pact-memory."); **only on confirmation** go on to steps 6-7: no destructive step may run before the harvest has read what it would destroy. Cannot confirm: warn the user ([close-out.md §5](../reference/wrap-up/close-out.md#5-when-the-drain-cannot-be-confirmed)). Then write the events; use `0` for a count the secretary cannot give:

```bash
set -e
"{plugin_root}/bin/repact-journal-write" --site wrap-up.md --type session_end --session-dir '{session_dir}'
case '{consolidation_ran}' in
  true)
    "{plugin_root}/bin/repact-journal-write" --site wrap-up.md --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}
JSON
    ;;
  false) ;;
  *) echo "[wrap-up.md] invalid {consolidation_ran} flag: '{consolidation_ran}' (expected literal 'true' or 'false')" >&2; exit 1 ;;
esac
```

## 6. Worktree cleanup

Gated on step 5. From inside the worktree run `gh pr view --json state,headRefName,headRepository,headRepositoryOwner`, then take exactly one branch ([worktree-cleanup.md](../reference/wrap-up/worktree-cleanup.md) has every sub-step and prompt):

**A — PR is MERGED**: close the task log's task now, before the worktree goes, without asking, and report what you wrote (or the refusal):

```bash
python3 "{plugin_root}/bin/repact-task" done <ref> --evidence merge:<sha>
```

Then `SendMessage(to="secretary", message="[team-lead→secretary] Then propagate the store into the Working Memory block.", summary="Propagate: arc closed on a merged PR")`, `/repacted:worktree-cleanup`, the local and the remote branch delete each through its own minted `AskUserQuestion`, and `git pull --ff-only` on `main`.

**B — No PR exists**: first `SendMessage(to="secretary", message="[team-lead→secretary] Then propagate the store into the Working Memory block.", summary="Propagate: arc closed with no PR")`, then `/repacted:worktree-cleanup`. No delete, no `main` sync.

**C — PR exists but is not merged**: no cleanup, no delete, and NO propagate request (deliberate). Write `session_paused` with `consolidation_completed: true` and report "Worktree preserved — PR still open. Use `/repacted:pause` to consolidate and pause, or `/repacted:peer-review` to continue review."

## 7. Task audit

`TaskList`; settle abandoned `in_progress` tasks; complete the feature task ONLY on branch A or B (never while step 8 may still pick pause). Report the task counts, then show as printed `repact-session-numbers`, `repact-unattended list` and `repact decide --count` ([close-out.md §7](../reference/wrap-up/close-out.md#7-task-audit)).

## 7b. KB Queue Drain

Optional: only when a knowledge base is configured (`paths.kb` in the profile; with none, `vault-note queue` exits 3: skip this step). Before step 8's commit, drain earlier sessions' KB queue items ([kb-queue.md](../reference/wrap-up/kb-queue.md)): run `python3 "{plugin_root}/bin/vault-note" queue`; if any is new, run `vault-note drain --blind-brief` and dispatch ONE fresh blind answerer with only those situations, never the notes or the KB; mark each claim against its blind answer, record it (`drain --decide`, §3), then `python3 "{plugin_root}/bin/vault-note" apply`. No lead: the main agent drains. Empty: skip. Never ask the user; relay the `held for the user` line and, always, `bin/repact-distill queue`'s line (§6).

## 8. Session decision

Report the task flags (read-only), rebuild the repo profile, commit the task log and its views (no task log: skip, [close-out.md §8](../reference/wrap-up/close-out.md#8-the-session-decision)):

```bash
python3 "{plugin_root}/bin/repact-task" next
python3 "{plugin_root}/bin/repact-profile" build
python3 "{plugin_root}/bin/repact-task" commit
```

Then `AskUserQuestion` with exactly **"Yes, continue"** (report "Ready for next task."), **"Pause work for now"** (invoke `/repacted:pause`; it stops the teammates) and **"No, end session"** (stop each teammate by name with `TaskStop`, the secretary last, then report).
