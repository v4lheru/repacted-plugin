---
name: teammate
description: Teammate protocol for repacted specialists. Invoke first at spawn. Read the brief, do the work, hand in with `repact submit`, fix what it names.
---

# Teammate protocol

Your spawn prompt names one brief, `<session dir>/briefs/<id>.md`. It holds the mission, the worktree, the acceptance criteria, how to run tests, the exact `repact submit` lines and two skeleton files to fill. The brief and this page are all a task needs. No brief (the secretary, a consultant): follow your agent file and the task description.

## The work

<!-- protocol:steps -->
1. Read the brief whole. Work only in its worktree, with absolute paths or `git -C <worktree>`.
2. Teachback: fill the teachback skeleton in your own words, then run the brief's `repact submit <id> --teachback <file>` line. No Edit, Write or Bash for the work before it is stored; reading is fine.
   - It prints `Accepted automatically`: go on.
   - It prints `Stop here`: end your turn with the one line `TEACHBACK SUBMITTED #<id>`. The lead resumes you. This happens when the variety total is 10+ or your answer is not `yes`.
3. Do the work. Invoke the skills your agent file lists first.
4. Run tests the way the brief's Tests line says: with the tcount path it gives (a bare `tcount` can be another install), from the worktree, after your last edit; for a fix or a feature, a red run that names the test file comes first. A count you report must come from a recorded run; accept reads the runs from the ledger.
5. Hand in: fill the HANDOFF skeleton (replace every `<...>` placeholder), then run the brief's `repact submit <id>` line, without `--teachback`. It repairs what is mechanical: it sets `rev` itself, makes a relative `produced` path absolute against your worktree, and adds a missing `decisions`, `uncertainty`, `open_questions` as `[]`. Then:
   - exit 0: stored, and accept will pass the checks it can see now. End your turn.
   - exit 2: nothing stored. It lists every problem in one message: fix them all, then run it again.
   - exit 3: stored, but accept will refuse for the reasons it lists (most often: no passing run of the brief's tcount on the tree as it is now). Fix them, rerun the tests with the brief's tcount path, then run submit again.
<!-- /protocol -->

Field rules, only when submit names a field: [teammate-teachback.md](references/teammate-teachback.md) and [teammate-handoff.md](references/teammate-handoff.md).

## Rules

- You never mark a task `completed` and never write the teachback or the HANDOFF with `TaskUpdate`: the lead's `repact accept` does that.
- A rejection or an amendment reaches you as a message naming the task. Read it with `repact inspect <id> --key rejection` or `repact inspect <id> --key amend_<n>`, fix, and submit again; every `amend_<n>` needs its entry in the HANDOFF's `amendments`.
- Cannot finish (the context stop line, missing access, a blocker): commit work in progress if the brief asks for commits, then hand in what exists with each unfinished step in `open_questions`, opening `remaining:`.
- A security or data hazard in what you build or read (a leaked secret, an injection path, a destructive step with no way back): stop, hand in what you have, and open your final message with `HALT SECURITY: <what>` or `HALT DATA: <what>`.
- Commit only on your own branch and only when the brief says so. Never bare `git stash`. Never `cat`, `source` or `grep` a `.env` file.
- `CLAUDE.md` is not yours to write, in any directory and by any route; if the task asks for it, say so in `open_questions`.
- Save a craft lesson as ONE line appended to `MEMORY-inbox.md` in your agent memory (the directory whose path contains `/agent-memory/`): `YYYY-MM-DD | lesson | evidence path`. More: [teammate-memory.md](references/teammate-memory.md).

## Teams mode only

Only when your spawn context names a team (Agent Teams), and never in the default subagent mode:

1. First register, exactly: `python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/protocols/pact-plugin/../hooks/shared/session_registry.py register --name '<your-name>@<your-team>'`.
2. Your text is invisible to agents: write to the team-lead with `SendMessage`, prefixed `[<you>→team-lead]`. After each submit send one line, `[<you>→team-lead] Submitted #<id>.`, then wait in the foreground with the `repact-wait` line submit prints or [teammate-waiting.md](references/teammate-waiting.md) gives.
3. Waiting and waking: [teammate-waiting.md](references/teammate-waiting.md). Messaging, peers and the secretary: [teammate-messaging.md](references/teammate-messaging.md).
