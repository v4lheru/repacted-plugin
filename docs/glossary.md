# Glossary

Each row gives the plain word, the key or file name you see in rePACTed, and one line about the idea. The last column links the page that explains it. Paths start at your Claude Code [config folder](#config-folder), shown here as `~/.claude`.

| Plain word | Key or file | What it means | Page |
|---|---|---|---|
| <a id="agent-memory"></a>agent memory | `~/.claude/agent-memory/<agent>/MEMORY.md` | Notes that one agent type keeps across sessions. Only the top of the index loads. | [Memory](concepts/memory.md) |
| <a id="agent-teams-flag"></a>Agent Teams flag | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` in `settings.json` | The Claude Code setting that lets one session start teammates. `/repacted:init` turns it on. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="amendment"></a>amendment | `metadata.amend_<n>`, `repact-amend` | A change to a brief that the lead writes on the task after the teammate started. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="bootstrap"></a>bootstrap | `/repacted:bootstrap` | The lead's first step in a session: find the team and show paused work. Spawns are refused until it ran. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="compact"></a>comPACT | `/repacted:comPACT` | Send one or more specialists on a small, self-contained task, with no phases. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="config-folder"></a>config folder | `$CLAUDE_CONFIG_DIR`, else `~/.claude` | Where Claude Code and rePACTed keep settings, teams, tasks, sessions and memory. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="digest"></a>digest | 12 hex characters in a `stored on #<id>` line | A short fingerprint of a stored teachback or HANDOFF. The lead checks the notify against it. | [HANDOFF](concepts/handoff.md) |
| <a id="distill-pass"></a>distill pass | `distill-inbox` | At wrap-up, the lead moves new lessons from the inboxes into skills or docs. | [Memory](concepts/memory.md) |
| <a id="doctor"></a>doctor | `repact-doctor` | A health check: one line per check, then the mode line. | [Guards](concepts/guards.md) |
| <a id="env-get"></a>env-get | `env-get FILE KEY -- <cmd>` | Runs a command with one value from a `.env` file, without printing the file. | [Guards](concepts/guards.md) |
| <a id="full-mode"></a>full mode | `mode: full (Agent Teams on)` | Guards and teams both work. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="guard"></a>guard | a hook such as `env_guard` | A check that reads a tool call before it runs and refuses it or rewrites its output. | [Guards](concepts/guards.md) |
| <a id="guards-only-mode"></a>guards-only mode | `mode: guards-only (Agent Teams flag off)` | The guards work, but the lead cannot start teammates. | [Guards](concepts/guards.md) |
| <a id="handoff"></a>HANDOFF | `metadata.handoff` | A teammate's final report on its work task. | [HANDOFF](concepts/handoff.md) |
| <a id="inbox"></a>inbox | `MEMORY-inbox.md` | The file where an agent adds each new lesson as one line. | [Memory](concepts/memory.md) |
| <a id="init"></a>init | `/repacted:init`, `repact-init` | One setup command for your machine and, with `--repo`, the current repository. | [Quickstart](quickstart.md) |
| <a id="kb"></a>knowledge base (KB) | `paths.kb` in `~/.config/repacted/profile.json` | An optional folder of notes you own. It is off until you set the path. | [Memory](concepts/memory.md) |
| <a id="lead"></a>lead | `repacted:pact-orchestrator` | The agent in your main session. It plans, hands out tasks, checks results and completes tasks. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="managed-block"></a>managed block | the project `CLAUDE.md` | The part of `CLAUDE.md` that rePACTed keeps up to date: session pointers, pins and working memory. | [Memory](concepts/memory.md) |
| <a id="merge-guard"></a>merge guard | `merge_guard_pre`, `merge_guard_post` | Refuses a merge, force-push, branch delete or push to main until you approve it. | [Guards](concepts/guards.md) |
| <a id="orchestrate"></a>orchestrate | `/repacted:orchestrate` | Build a feature in phases, with a team. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="output-secret-scan"></a>output secret scan | `output_secret_scan` | Replaces a secret value in command output with `[REDACTED:...]` before Claude sees it. | [Guards](concepts/guards.md) |
| <a id="pact-memory"></a>pact-memory | `~/.claude/pact-memory/memory.db`, skill `repacted:pact-memory` | A searchable store of decisions and lessons that every agent and session shares. | [Memory](concepts/memory.md) |
| <a id="phase"></a>phase | `PREPARE`, `ARCHITECT`, `CODE`, `TEST` | The steps of `/repacted:orchestrate`. Each phase is a task. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="pin"></a>pin | `/repacted:pin-memory`, `/repacted:prune-memory` | Context you keep in `CLAUDE.md` on purpose, so every session sees it. | [Memory](concepts/memory.md) |
| <a id="refusal"></a>refusal | `<check>: <reason> → <instead> [rule: <id>]` | The line a guard prints when it stops a tool call. | [Guards](concepts/guards.md) |
| <a id="rejection"></a>rejection | `metadata.teachback_rejection`, `metadata.handoff_rejection` | The lead's note on what to fix. The teammate revises on the same task. | [Teachback](concepts/teachback.md) |
| <a id="repact-explain"></a>explain | `repact-explain` | Shows the last refusals with the rule id, the way through and the help page anchor. | [Guards](concepts/guards.md) |
| <a id="repact-payload"></a>payload reader | `repact-payload <task id>` | Prints a stored HANDOFF or teachback, with its rev and digest. | [HANDOFF](concepts/handoff.md) |
| <a id="repact-wait"></a>wait command | `repact-wait <task id>` | Waits until the task file changes, then prints one line. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="rev"></a>rev | `metadata.handoff.rev` | The HANDOFF version: 1 on the first write, one more on each rewrite. | [HANDOFF](concepts/handoff.md) |
| <a id="rule-id"></a>rule id | `[rule: env.read.operand]` | The name of the rule behind a refusal. The troubleshooting page has a section for each id. | [Guards](concepts/guards.md) |
| <a id="secretary"></a>secretary | `pact-secretary` | A teammate that saves decisions and lessons from HANDOFFs into pact-memory and answers history questions. | [Memory](concepts/memory.md) |
| <a id="session-journal"></a>session journal | `~/.claude/pact-sessions/` | The log of one session's events, such as waits, wakes and refusals. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="specialist"></a>specialist | `agents/pact-*.md` | A teammate with one job, such as preparer, architect, backend coder or test engineer. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="stored-line"></a>stored line | `Teachback stored on #<id>`, `HANDOFF stored on #<id>` | The line a check prints after a teachback or HANDOFF reaches the task file. | [HANDOFF](concepts/handoff.md) |
| <a id="task"></a>task | `~/.claude/tasks/<team>/<id>.json` | One piece of work, with a status, an owner and metadata. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="task-a-b"></a>Task A, Task B | the teachback task, the work task | The pair of tasks the lead creates for each teammate. | [Teachback](concepts/teachback.md) |
| <a id="tcount"></a>tcount | `tcount <cmd>` | Runs your tests and records a count that a HANDOFF can cite. | [HANDOFF](concepts/handoff.md) |
| <a id="teachback"></a>teachback | `metadata.teachback_submit` | The teammate says back the brief before it works. The lead accepts by completing Task A. | [Teachback](concepts/teachback.md) |
| <a id="team"></a>team | `session-<8 hex>`, `~/.claude/teams/<team>/config.json` | The lead and its teammates in one session. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="teammate"></a>teammate | an agent started into the team | An agent that works on tasks the lead gives it, in its own context. | [Lead and teammates](concepts/lead-and-teammates.md) |
| <a id="uncertainty"></a>uncertainty item | `metadata.handoff.uncertainty` | Something the teammate is not sure of. Each item opens with `fixed:`, `known-limit:` or `not-applicable:`. | [HANDOFF](concepts/handoff.md) |
| <a id="variety"></a>variety | `metadata.variety` on the work task | The lead's scores from 1 to 4 for novelty, scope, uncertainty and risk, and their total. | [Teachback](concepts/teachback.md) |
| <a id="wait-marker"></a>wait marker | `metadata.intentional_wait` | What a teammate writes before it waits: the reason, who can end the wait, and since when. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="wake"></a>wake | any write to the task file | The event that ends a `repact-wait`. | [Task files and waits](concepts/task-files-and-waits.md) |
| <a id="worktree"></a>worktree | `.worktrees/<branch>/` | A second checkout of your repository on its own branch, for one feature. | [Worktrees](concepts/worktrees.md) |
| <a id="worktree-guard"></a>worktree guard | `worktree_guard`, `PACT_WORKTREE_PATH` | When the path is set, refuses edits outside the active worktree. | [Worktrees](concepts/worktrees.md) |
