# Protocol: live re-probe checklist

Context: the notes stamped "re-probe" cannot be checked offline (as of Claude Code
2.1.288, repacted 1.3.7): each needs a session event. H = hooks-and-harness, W = worktrees,
T = task-store-and-harvest, M = pact-memory, R = resume-and-team-identity; the number is the rule.

## Mission
When a live session reaches one of the events below, run its rows and settle the notes they name.

## Steps
1. Pick the event the session already has: B a refusal that names a worktree, C a resume or a
   compaction, D a drained task store ("Task not found", an empty TaskList), E two agent types
   in one session. Only A is staged on purpose.
2. For A, in a bypassPermissions session, add a Pre and PostToolUse hook to settings mid-session:
   it appends stdin to a scratch file and answers `ask` for one sentinel command. Run the
   sentinel in the lead and in one teammate.
3. Run the row, compare with the two result columns, then re-stamp, rewrite or delete the line
   (SKILL.md rule 5) and log it in the refinement log.

| Event | Notes | Still true | Changed |
|---|---|---|---|
| A | H5 | the dump gets rows with no restart | no rows until a restart |
| A | H1 | the sentinel runs unprompted; a `deny` variant is blocked | a prompt appears, or `deny` runs |
| A | H3, H4 | PostToolUse fires for the unprompted call; payload `permission_mode` is the live mode, also when settings `defaultMode` says another | no PostToolUse, or the payload follows settings |
| A | H2 | the teammate's dump carries the lead's `session_id`; the lead's `agent_id` is null | distinct session ids |
| B | W1, W2 | a lead pin clears with ExitWorktree keep; a teammate's peer-tree pin clears with the re-key | the other fix clears it, or neither |
| B | W3, W4 | the re-key errors "is the current working directory" and the next write lands; against the main tree it errors "not a linked worktree" and clears nothing | other error text, or no effect |
| B | W6, W7 | `pwd` and `git log -1` differ between two commands with no `cd`; a relative-path count differs from the absolute-path count | stable across 10 commands |
| B | W9, W10 | git with -C on another tree is refused, your own passes; the spawn env working directory disagrees with the dispatch | both pass, or the two agree |
| B | H16, W8 | with rtk on, the refusal names your OWN tree and `/usr/bin/git` passes | plain `git` passes |
| C | R8 | the session block in the prompt copy of CLAUDE.md is older than the file on disk | the two agree right after the resume |
| C | R13 | the first spawn result echoes the real `name@team`; a wrong team in a prompt still routes | the spawn fails or lands elsewhere |
| C | W14, compaction half | the first Edit after a compaction is refused until a re-Read | it applies with no re-Read |
| D | T3, T4 | the files are in another team dir or the journal (`repact-payload <id>` reads a drained task from it) | found nowhere |
| D | T12 | a deleted throwaway task takes its `metadata.handoff` with it, no warning | a warning, or the file stays |
| D | T13 | after a bare-name TaskStop that reports success the name is still in the team config's `members[]` | the name is gone |
| D | T17 | an "expected record" failure passes on an unchanged resend | it never occurs in 20 writes, or the resend fails |
| E | M11, M12 | type A cannot write type B's memory dir; some spawn's `pwd` is another agent's memory dir | the write lands; every cwd is the project |
| E | M14 | a long index line written by Edit reads back cut with an ellipsis | it reads back whole |
| E | H9 | a teammate's background job that touches a marker stops at the turn end | the marker keeps moving |
| E | H10 | a spawn with worktree isolation has no task tools after ToolSearch | the tools load |

The rest, when they occur. H13: the next EPERM on a path read earlier passes on one retry.
H14: such plugins still rewrite models and deny spawns (needs a remote read).
H15: a transcript shows a server's plain name but no `"name":"mcp__<server>__` entry. H18: a grep
for stray tool-call markup after Writes finds a hit (cannot be provoked). H17: an HTML-only mail
reads back empty; first
find which server the note means.

## Guidelines
- Pattern: one row per event is enough; never stage a pin, a drain or an EPERM to run a row.
- Anti-pattern: a probe that writes to a live team, task or session directory other than the throwaway task of T12.

## Next
Run `self-refine.md` for the notes you settled.
