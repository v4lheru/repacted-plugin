# Changelog

Release notes for rePACTed, newest first. Entries start at the first public release.

## 1.7.1 (2026-10-17)

First-run fixes from a clean-install walkthrough.

- The `CLAUDE.md` lock file lives in `${XDG_STATE_HOME:-~/.local/state}/repacted/locks/`, not in the project, so `git add -A` no longer picks it up.
- `/repacted:init` adds `CLAUDE_CODE_ENABLE_TODO_TOOLS="1"` next to the Agent Teams flag, and its last line says what the doctor's exit code means.
- `repact-doctor` leaves out the rows that only apply to the plugin's own source repository when they do not apply.
- Python 3.9 works as the requirements say: scratch cleanup, the task-file reader and two time and number checks used behaviour that only Python 3.11 and newer have.

## 1.7.0 (2026-10-17)

First public release.
