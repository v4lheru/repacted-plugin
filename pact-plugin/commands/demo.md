---
description: Try rePACTed on a small example project, where one agent fixes one failing test in a scratch copy
disable-model-invocation: true
---
# Demo: one small fix, start to finish

The demo copies a tiny example project (`examples/toy/`, a word counter with one failing test) into a new scratch directory, runs one `/repacted:comPACT` round in which one specialist fixes the bug, then prints the session report. It writes nothing to the user's current repository. Every command below uses absolute paths: never `cd` in the session's shell, so the report in step 6 still finds this session.

1. Tell the user, in one or two lines, what the demo will do (the paragraph above) and that it starts one helper agent.
2. Make the scratch copy (a new directory under the system temp dir, made its own git repository with one starting commit):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/examples/demo_setup.py"
   ```

   On `refused` (the scratch directory would land inside the current repository, for example through a `TMPDIR` set inside it), show the line as printed and stop. Otherwise use the printed `demo dir` path in every later step. The copy is its own git repository outside the user's, so the demo skips worktree setup.
3. Show the failing test (expected: 1 failed, 4 passed):

   ```bash
   ( cd "<demo dir>" && if python3 -c "import pytest" 2>/dev/null; then python3 -m pytest -q -p no:cacheprovider; else python3 -m unittest -v; fi )
   ```

4. Run one `/repacted:comPACT` round as written in [comPACT.md](comPACT.md), with these choices:
   - Task: `backend: make the failing test in <demo dir>/test_wordcount.py pass by fixing the bug in <demo dir>/wordcount.py. Do not edit the test. Work only inside <demo dir>.`
   - Feature task `Fix the demo word count`, variety total 4, so a single-task dispatch: one `pact-backend-coder`, no teachback task. Auditor skipped: a one-line fix in a scratch copy.
   - Pre-Invocation: skip the worktree (step 1) and the task-log write (step 4); use `<demo dir>` wherever the brief says worktree path.
   - After the HANDOFF: run step 3's command again (expected: 5 passed), then commit in the scratch copy: `git -C "<demo dir>" -c user.name=demo -c user.email=demo@example.invalid commit -qam "Fix the word count."`. Skip the "What next?" question and the PR.
5. Show the fix: `git -C "<demo dir>" show --stat --patch HEAD`.
6. Print the session report (`{session_id}` is the last part of the session directory):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-report" --session {session_id}
   ```

7. Close with three lines: the scratch directory path (the user may delete it; nothing else was written), what the report shows (spend by role, wakes, rejections), and the next step: `/repacted:init --repo` in their own repository, then `/repacted:comPACT <task>` on a real task.
