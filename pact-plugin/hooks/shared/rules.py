"""
Location: pact-plugin/hooks/shared/rules.py
Summary: The stable rule ids a refusal can carry, each with one fixed sentence, the way
through (`instead`: what to do instead, one plain sentence) and a doc anchor. A check names
its id in `Result.rule` (native) or as a `[rule: <id>]` tail on its reason or stderr
(script); hooks/dispatch.py lifts it into the `gate_refusal` row and the refusal line
`<check>: <reason> → <instead> [rule: <id>]`, and `repact inspect --refusals` prints the sentence,
the way through and the anchor back. An id a check emits but this table lacks is a contract
test failure (tests/test_dispatch_rule_tail.py), so the user never meets a bare id; an id
without a way through fails tests/contract/test_refusal_contract.py.
Used by: bin/_front/refusals.py, hooks/dispatch_rules.py (loads this file by path for
`instead`), tests/test_dispatch_rule_tail.py, tests/contract/test_refusal_contract.py.
Imports nothing from the package (a leaf).

Ids are dotted `<check or module>.<rule>`. The dispatcher's own: `<check>.timeout` for a
security check that overran its alarm (fail closed), `<check>.error` for one that failed without deciding,
and `dispatch.task_store_absent` (the
host stopped writing task files). The classifier ids of the security hooks keep their
tables beside the classifiers (hooks/shell/env/programs.py RULES, hooks/shell/merge/arms.py
RULE_IDS and MASK_RULE_IDS, which choose the id for a refusal) and are listed here again,
sentence for sentence, so this is the one registry `repact inspect --rule` resolves against;
tests/test_dispatch_rule_tail.py fails on an id or a sentence the two copies disagree on.
The doc page is the troubleshooting page (one anchor per id, `<id>` with dots and
underscores as dashes), written there as explicit anchors because a heading's own slug
would drop the dots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

REFUSALS_DOC = "docs/troubleshooting.md"


def anchor(rule_id: str) -> str:
    """The troubleshooting anchor of an id: dots and underscores become dashes."""
    return rule_id.replace(".", "-").replace("_", "-")


@dataclass(frozen=True)
class Rule:
    explain: str   # one sentence, present tense, names what happened and why it was refused
    doc: str       # `<page>#<anchor>` the user can open
    instead: str   # one plain sentence: what to do instead (the way through)
    why: str = ""  # troubleshooting's Why line when the rule's family reason does not fit it
    unattended: str = ""  # the way through when unattended mode is on, where it differs


def way_through(rule_id: str, unattended: bool = False) -> str:
    """The way through of an id: its unattended variant when unattended mode is on and the rule
    has one, else `instead`; '' for an unknown id. Unattended, a waiver is offered only as a park
    for a human, never as a way to close."""
    rule = RULES.get(rule_id) if isinstance(rule_id, str) else None
    if rule is None:
        return ""
    return (rule.unattended or rule.instead) if unattended else rule.instead


def _timeout(check: str, what: str, seconds: int) -> Rule:
    return Rule(
        explain=(f"{check} did not answer within {seconds} s, so the {what} was refused "
                 "rather than let through unchecked; retry once, then run repact-doctor."),
        doc=f"{REFUSALS_DOC}#{anchor(check)}-timeout",
        instead=f"Run the same {what} again once; if it is refused again, run repact-doctor.",
    )


def _error(check: str, what: str) -> Rule:
    return Rule(
        explain=(f"{check} failed without a decision (it raised, was killed or exited oddly), "
                 f"so the {what} was refused rather than let through unchecked; run repact-doctor."),
        doc=f"{REFUSALS_DOC}#{anchor(check)}-error",
        instead="Run repact-doctor to see why the check failed, fix what it names, then retry.",
    )


RULES: dict[str, Rule] = {
    "env_guard.timeout": _timeout("env_guard", "command", 15),
    "merge_guard_pre.timeout": _timeout("merge_guard_pre", "git command", 15),
    "egress_guard.timeout": _timeout("egress_guard", "outbound command", 60),
    "git_commit_check.timeout": _timeout("git_commit_check", "commit", 90),
    "worktree_guard.timeout": _timeout("worktree_guard", "file write", 15),
    "write_checks.timeout": Rule(
        explain=("write_checks (the commit rule) did not answer within 60 s, so the "
                 "commit was refused rather than let through unchecked."),
        doc=f"{REFUSALS_DOC}#write-checks-timeout",
        instead=("Run the same commit again once; if it is refused again, run "
                 "repact inspect --rule write_checks.timeout and then repact-doctor."),
    ),
    "env_guard.error": _error("env_guard", "command"),
    "merge_guard_pre.error": _error("merge_guard_pre", "git command"),
    "egress_guard.error": _error("egress_guard", "outbound command"),
    "git_commit_check.error": _error("git_commit_check", "commit"),
    "worktree_guard.error": _error("worktree_guard", "file write"),
    "write_checks.error": _error("write_checks", "commit"),
    "task_write_guard.timeout": _timeout("task_write_guard", "task write", 15),
    "task_write_guard.error": _error("task_write_guard", "task write"),
    "ops_frame_guard.timeout": _timeout("ops_frame_guard", "command", 15),
    "ops_frame_guard.error": _error("ops_frame_guard", "command"),
    "dispatch_gate.timeout": _timeout("dispatch_gate", "agent spawn", 60),
    "dispatch_gate.error": _error("dispatch_gate", "agent spawn"),
    # the task write guard (hooks/task_write_guard.py)
    "task_write.completed": Rule(
        explain=("a task made by `repact dispatch` is completed only by `repact accept`, which "
                 "runs the checks first, so a TaskUpdate setting it completed was refused."),
        doc=f"{REFUSALS_DOC}#task-write-completed",
        instead="Run `repact accept <id>` (the lead); a coder hands in its work with `repact submit <id>`.",
    ),
    "task_write.reserved_key": Rule(
        explain=("the task write named a workflow key (dispatch, teachback, handoff, "
                 "handoff_rejection, acceptance, evidence_waiver, parked, last_op, revision_number or "
                 "amend_<n>), in any spelling or depth, on any task, and only the repact commands "
                 "write those, so it was refused."),
        doc=f"{REFUSALS_DOC}#task-write-reserved-key",
        instead="Run the repact command the refusal names (submit, accept, reject or amend) instead of the task write.",
    ),
    "task_write.unreadable": Rule(
        explain=("the task tool input, its task id or the task file could not be read, so "
                 "whether the write touches a workflow key is unknown and it was refused."),
        doc=f"{REFUSALS_DOC}#task-write-unreadable",
        instead="Send the same call again as plain JSON naming the task id as a string; if the task file is unreadable, run repact-doctor.",
    ),
    "ops_frame_guard.lead_only": Rule(
        explain=("`repact dispatch`, `accept`, `reject` and `amend` are the lead's commands, and "
                 "the host names this Bash call's frame an agent, so it was refused whatever "
                 "`--as` or REPACT_AGENT_NAME say."),
        doc=f"{REFUSALS_DOC}#ops-frame-guard-lead-only",
        instead=("Hand in your own work with `repact submit <id>` and ask the lead to run the "
                 "command; to search for the phrase, use the Grep tool rather than Bash."),
    ),
    "ops_frame_guard.verb_unseen": Rule(
        explain=("`repact` or _repact_ops.py runs under xargs, parallel or find -exec with no "
                 "operation written after it, so the runner supplies words the check cannot see "
                 "and it was refused in every frame."),
        doc=f"{REFUSALS_DOC}#ops-frame-guard-verb-unseen",
        instead="Type the operation and its task id out, for example `repact accept 5`.",
        why=("A verb fed on stdin hid a lead command from the frame check, so a coder could "
             "accept its own task."),
    ),
    "ops_frame_guard.lead_submit": Rule(
        explain=("`repact submit` hands in a coder's teachback or HANDOFF, and this Bash call "
                 "runs in the lead's own frame, so it was refused whatever `--as` says."),
        doc=f"{REFUSALS_DOC}#ops-frame-guard-lead-submit",
        instead=("Accept, reject or amend the task instead; the dispatched coder submits its own "
                 "work, and a HANDOFF a subagent returned is relayed by the SubagentStop backstop."),
        why=("The lead could otherwise store a coder's teachback or HANDOFF itself and then "
             "accept its own words."),
    ),
    "ops_frame_guard.lead_copy": Rule(
        explain=("This Bash call copies or links the `repact` launcher or _repact_ops.py to "
                 "another name in a frame that does not run the lead's commands, where it would "
                 "run them under a name the frame check does not know, so it was refused."),
        doc=f"{REFUSALS_DOC}#ops-frame-guard-lead-copy",
        instead="Run `repact <command>` by its own name; hand in your own work with `repact submit <id>`.",
        why=("A copy of the launcher under another name ran the lead's commands from a coder's "
             "frame unseen."),
    ),
    "dispatch.task_store_absent": Rule(
        explain=("the host did not write the task file it reported creating or updating "
                 "twice in a row, so gates, waits and HANDOFFs cannot see task state; "
                 "Agent spawns are refused until a later task write lands on disk."),
        doc=f"{REFUSALS_DOC}#dispatch-task-store-absent",
        instead=("Make one more TaskUpdate and check its file appeared under the tasks "
                 "directory, then run `repact-doctor --clear task-store` and spawn again."),
    ),
    "team_config_absent": Rule(
        explain=("the session's team has no teams/<team>/config.json on disk, so a teammate "
                 "spawned into it gets no Task tools and cannot see its tasks; the spawn is "
                 "allowed with an advisory to put the whole mission in the spawn prompt."),
        doc=f"{REFUSALS_DOC}#team-config-absent",
        instead=("Put the whole mission in the spawn prompt and ask for the HANDOFF as the "
                 "agent's final message; after a resume, run /repacted:bootstrap first."),
    ),
    # the workflow operations' refusals (hooks/shared/ops/, printed by bin/_repact_ops.py)
    "ops.lead_only": Rule(
        explain=("`repact dispatch`, `accept`, `reject` and `amend` are the lead's commands, and "
                 "this caller named itself a coder (`--as` or REPACT_AGENT_NAME), so nothing was written."),
        doc=f"{REFUSALS_DOC}#ops-lead-only",
        instead=("Hand in your own work with `repact submit <id>`; the lead runs this command, "
                 "without `--as`."),
    ),
    "ops.not_owner": Rule(
        explain=("`repact submit` stores a teachback or HANDOFF only for the coder the task was "
                 "dispatched to, and this caller is not that coder, so nothing was written."),
        doc=f"{REFUSALS_DOC}#ops-not-owner",
        instead=("Run the submit command your brief prints, with `--as <your dispatched name>`; "
                 "the lead accepts or rejects instead of submitting."),
    ),
    "ops.not_dispatched": Rule(
        explain=("the task does not exist in this team, or no `repact dispatch` made it (it has "
                 "no metadata.dispatch), so the workflow command has nothing to act on."),
        doc=f"{REFUSALS_DOC}#ops-not-dispatched",
        instead=("Check the task id and `--team`; make a new work task with `repact dispatch`, "
                 "or handle a task made by hand with TaskUpdate as before."),
    ),
    "ops.invalid_input": Rule(
        explain=("the arguments or the input file failed the command's checks (shape, size, "
                 "strict JSON, placeholders, lookalike keys or paths outside the worktree), so "
                 "nothing was written."),
        doc=f"{REFUSALS_DOC}#ops-invalid-input",
        instead="Fix every problem listed above in one pass, then run the same command again.",
    ),
    "ops.wrong_state": Rule(
        explain=("the task is not in the state this command needs (closed, a teachback not yet "
                 "accepted, or a revision already rejected), so nothing was written."),
        doc=f"{REFUSALS_DOC}#ops-wrong-state",
        instead=("Do the step the refusal names first (submit the teachback, wait for a new "
                 "submit, or dispatch a new task), then run the command again."),
    ),
    "ops.no_session": Rule(
        explain=("the command could not find its session dir or team (no session id in the "
                 "environment and none given), so it cannot tell which tasks to touch."),
        doc=f"{REFUSALS_DOC}#ops-no-session",
        instead=("Run it inside a rePACTed session, or pass `--team <team>` and "
                 "`--session-dir <dir>` as your brief's submit line does."),
    ),
    "ops.not_own_session": Rule(
        explain=("inside a Claude Code session the workflow commands act on that session alone, "
                 "and the --session-dir, --session-id or --team given names another one (or, at "
                 "a terminal with no session id, a dir that is not a session dir), so nothing "
                 "was written."),
        doc=f"{REFUSALS_DOC}#ops-not-own-session",
        instead=("Run the command without --session-dir, --session-id and --team so it finds "
                 "this session itself; another session's tasks are that session's to change."),
        why=("Unattended mode is read from the session dir, so a made-up dir could make an "
             "unattended waiver look attended."),
    ),
    "ops.write_failed": Rule(
        explain=("a write the command needs (the task file, the intent log or a worktree) did not "
                 "land, so the operation stopped; whatever landed is safe to finish by a rerun."),
        doc=f"{REFUSALS_DOC}#ops-write-failed",
        instead="Run the same command again; if it fails again, run repact-doctor.",
        why=("Each command stamps its id into its one task write and logs every side write, so "
             "a rerun skips what landed and finishes the rest."),
    ),
    # dispatch_gate project-agent refusals (hooks/dispatch_gate.py PROJECT_AGENT*_RULE)
    "dispatch_gate.project_agent_shadows_plugin": Rule(
        explain=("A project agent file under .claude/agents claims the name of a shipped "
                 "specialist, and the host starts a project agent before a plugin agent of "
                 "the same name, so the bare spawn was refused."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agent-shadows-plugin",
        instead=("Spawn the shipped agent by its plugin name (`repacted:pact-<role>`), or "
                 "rename the project file's `name:` so it matches no shipped agent."),
    ),
    "dispatch_gate.project_agent_unbound": Rule(
        explain=("The project agent file has no teammate binding (`Skill(\"repacted:teammate\")` "
                 "in its body, outside code blocks and comments), so it would not follow the "
                 "teammate protocol and was refused."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agent-unbound",
        instead=("Add the binding line from templates/agent.md to the file's body, or scaffold "
                 "the agent with `repact-agent new <role>`, then `repact-agent check <file>`."),
    ),
    "dispatch_gate.project_agent_invalid": Rule(
        explain=("The project agent file is not a valid specialist (a symbolic link, over "
                 "64 KiB, not UTF-8, a name that is not a plain pact-<role> equal to the file "
                 "name, or frontmatter outside the plain key: value form), so it was refused."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agent-invalid",
        instead=("Run `repact-agent check <file>` and fix what it names, or scaffold a fresh "
                 "file with `repact-agent new <role>`."),
    ),
    "dispatch_gate.project_agent_ambiguous": Rule(
        explain=("More than one project agent file can claim the spawned name (a duplicate, "
                 "or a file whose name cannot be read with certainty), so which one the host "
                 "starts is unknown and the spawn was refused."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agent-ambiguous",
        instead=("Keep one file defining the name under the project's .claude/agents "
                 "directories; run `repact-agent check` to see what each file claims."),
    ),
    "dispatch_gate.project_agents_unreadable": Rule(
        explain=("The project's .claude/agents directories could not all be read (too deep, "
                 "more than 200 files, or unreadable), so a file shadowing the spawned name "
                 "cannot be ruled out and the bare spawn was refused."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agents-unreadable",
        instead=("Spawn the shipped agent by its plugin name (`repacted:pact-<role>`), or "
                 "trim the .claude/agents tree; `repact-agent check` names the cause."),
    ),
    "dispatch_gate.project_agents_disabled": Rule(
        explain=("The team policy sets agents.project_scope to off, so specialists a project "
                 "brings in .claude/agents are not spawned as teammates."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-project-agents-disabled",
        instead=("Spawn a shipped specialist instead, or ask the policy's owner to change "
                 "agents.project_scope in .repacted/policy.json."),
        why="A team policy can only tighten; the refusal names the policy file that set it.",
    ),
    "dispatch_gate.session_cost_cap": Rule(
        explain=("The session's estimated spend (list API prices over its transcripts' token "
                 "counts) is at or past its cost cap, so no new specialist is spawned."),
        doc=f"{REFUSALS_DOC}#dispatch-gate-session-cost-cap",
        instead=("Finish the work in the lead or in a new session, or raise or unset "
                 "PACT_SESSION_COST_CAP_USD; a cap set in .repacted/policy.json changes only there."),
        why=("The cap is the lower of PACT_SESSION_COST_CAP_USD and the team policy's "
             "session.cost_cap_usd; the refusal names who set it."),
    ),
    # repact accept and submit's preview (hooks/shared/evidence_check.py RULE_IDS)
    "evidence.no_run": Rule(
        explain=("accept found no passing tcount run of the task since dispatch, run in its "
                 "worktree or on its current inputs, or no passing run of a pinned acceptance "
                 "command at all."),
        doc=f"{REFUSALS_DOC}#evidence-no-run",
        instead=("Run the tests with `nice -n 19 tcount <test command>` in the worktree after the "
                 "last edit, then run `repact accept` again."),
        why=("tcount stamps each run with the content tree id of the files it ran against; accept "
             "reads the run ledger, never a count quoted in the HANDOFF."),
    ),
    "evidence.stale_run": Rule(
        explain=("The input files changed after the newest passing run: its tree id is not the "
                 "worktree's tree id now. The refusal names the changed files (config, data and "
                 "build files count, named in produced or not)."),
        doc=f"{REFUSALS_DOC}#evidence-stale-run",
        instead="Run the tests again with tcount after the last edit, then run `repact accept` again.",
        why=("A commit of the same files keeps the tree id and an edit changes it; docs (*.md), "
             "cache output and secret-class files are not inputs."),
    ),
    "evidence.selection_mismatch": Rule(
        explain=("A pinned acceptance criterion has passing runs on the current inputs, but none "
                 "selects the same tests as its command (the same runner, options and targets)."),
        doc=f"{REFUSALS_DOC}#evidence-selection-mismatch",
        instead=("Run the criterion's command exactly as pinned, under tcount, in the worktree; "
                 "output-only flags such as -q may differ."),
        why="A narrower run (another file, a -k filter) does not show the criterion holds.",
    ),
    "evidence.red_first_missing": Rule(
        explain=("A fix or a feature task has no failing tcount run, since dispatch and before "
                 "its first passing run on the current inputs, of a test file that passing run "
                 "covers."),
        doc=f"{REFUSALS_DOC}#evidence-red-first-missing",
        instead=("Run the new test with tcount before the fix (name its file or node id in the "
                 "command) and see it fail, then fix and run it again. If a passing run on these "
                 "files came first: make a temporary revert commit of the fix, run the named test "
                 "with tcount and see it fail, restore the fix, then ask the lead for a waiver: "
                 "`repact accept <id> --waive \"<reason naming that red run>\"`. The waiver does "
                 "not check the run; unattended, it parks the task for the owner."),
        why=("The ledger keeps commands, not test ids, so a red run counts for a file its command "
             "names; it shows a red run existed, not that the intended assertion failed. A "
             "refactor, config or docs task does not need one; a missing or unknown kind is "
             "judged as a feature."),
    ),
    "evidence.needs_human": Rule(
        explain=("accept cannot match a run to the task: the worktree has no tree id (not a git "
                 "worktree, git failed, or too many changed files), the dispatch record has no "
                 "readable time or pin, or a config task changed inputs with no passing run."),
        doc=f"{REFUSALS_DOC}#evidence-needs-human",
        instead=("Fix what the refusal names (commit or trim the worktree, run the check), or "
                 "have a human review the change and reject or re-dispatch the task."),
        why="What the check cannot read is never a pass.",
    ),
    "evidence.uncommitted": Rule(
        explain=("An input file of the worktree is staged, unstaged, untracked or deleted and not "
                 "committed, so the branch HEAD would not ship the files the passing run tested. "
                 "The refusal names the files; docs (*.md), cache output and secret-class files "
                 "are not inputs."),
        doc=f"{REFUSALS_DOC}#evidence-uncommitted",
        instead="Commit your work, then rerun tcount and `repact accept`.",
        why="Accept closes the task on what the branch holds, and a run's tree id counts files a commit has not taken.",
    ),
    # bin/repact-task done and bin/repact-autopilot record done (hooks/shared/task_log/close_gate.py)
    "task_log.close_unaccepted": Rule(
        explain=("In an autopilot run or unattended mode a task-log row closes only when every "
                 "platform task linked to it is completed with a stored HANDOFF and evidence; "
                 "this row has a linked task that is not, a link to another team's task, or no "
                 "linked task at all."),
        doc=f"{REFUSALS_DOC}#task-log-close-unaccepted",
        instead=("Accept each linked platform task with `repact accept`, or close with "
                 "--waive \"<reason>\": attended, the row closes and the waiver is logged; "
                 "unattended, the waiver parks the row for the owner (`repact decide` lists it)."),
        unattended=("Accept each linked platform task with `repact accept` (a failed check goes back "
                    "to the coder as a rejection), or park the row for a human with --waive "
                    "\"<reason>\"; unattended, a waiver never closes it."),
        why=("A row was once closed on a commit while its tasks were still pending with no "
             "HANDOFF; the row is judged from its own links, so a new autopilot run changes "
             "nothing."),
    ),
    # bin/repact-task PARKED_RULE (a row parked for the owner)
    "task_log.parked_for_owner": Rule(
        explain=("The row was parked for the owner by an unattended run, and closing, dropping, "
                 "reopening or restarting it (or changing its status, tags or note) answers the "
                 "owner's question, so only the owner at their own terminal may do it."),
        doc=f"{REFUSALS_DOC}#task-log-parked-for-owner",
        instead=("Leave the row for the owner and go on with other work; the owner runs "
                 "`repact decide <ref> <verb> ...` in their own terminal and retypes the ref."),
        why=("An unattended lead could otherwise drop or close the row and hide the question it "
             "parked for the owner."),
    ),
    "unattended.off_owner_only": Rule(
        explain=("Turning unattended mode off answers every question an unattended run parked, "
                 "so it is the owner's, at their own terminal, and this call came from inside a "
                 "Claude Code session or without an interactive terminal."),
        doc=f"{REFUSALS_DOC}#unattended-off-owner-only",
        instead=("Leave the mode on and go on with other work; the owner runs "
                 "`repact-unattended off --session-dir <dir>` in their own terminal and types off."),
        why=("The session stays unattended until it ends or the owner turns it off, so one "
             "command from inside the run cannot make its waivers look attended."),
    ),
    # git_commit_check (hooks/git_commit_check.py STASH_RULE)
    "git_commit_check.shared_stash": Rule(
        explain=("A git command would use the stash stack that every worktree and session of the "
                 "repository shares without a tag to find its own entry again (a bare `git stash`, "
                 "a push with no -m, save, pop, clear, or a drop that names no entry), so it was "
                 "refused."),
        doc=f"{REFUSALS_DOC}#git-commit-check-shared-stash",
        instead=("Set the work aside with a WIP commit, or with `git stash push -u -m <tag>`, then "
                 "bring it back with `git stash apply <sha>`, the sha from "
                 "`git stash list --format='%H %gs'`."),
        why=("The stash is one stack per repository, not per worktree: a pop or drop takes the top "
             "entry, which may be another session's work. The rule reads the command text as "
             "git_commit_check reads it (prefixes, -C, -c, aliases it can see), so a stash run "
             "from a script file or a command it cannot parse is not caught."),
    ),
    # process_table_guard (hooks/process_table_guard.py RULE_ID)
    "process.listing": Rule(
        explain=("A command would list processes with their full command lines (ps, pgrep -f, "
                 "pstree, top -c, procs, htop, btop or a /proc argv file), so it was refused."),
        doc=f"{REFUSALS_DOC}#process-listing",
        instead=("List processes by name only, with `pgrep -x <name>` or "
                 "`ps -axo pid=,etime=,comm=`."),
    ),
    # process_table_guard (hooks/process_table_guard.py PTY_RULE_ID)
    "process.pty_wrap": Rule(
        explain=("A command would run a KB step that only a human at a terminal may take "
                 "(repact-distill promote, repact-distill team, vault-note drain/decide or "
                 "kb-note drain/decide) inside a pseudo-terminal wrapper (script, expect, "
                 "unbuffer, socat, a terminal multiplexer such as screen or tmux, or a pty "
                 "spawn), so it was refused."),
        doc=f"{REFUSALS_DOC}#process-pty-wrap",
        instead=("Ask the user to run the step themselves in their own terminal; an agent may run "
                 "`repact-distill promote --dry-run` to show what would be written."),
        why=("These steps check for a person at an interactive terminal, and a wrapper would fake "
             "one. The rule matches the command text, so it is a brake on habit, not a boundary: "
             "a wrapper started from a script file evades it, and so does a multiplexer driven "
             "across two separate commands, or a terminal from ssh -tt, asciinema rec -c, "
             "osascript's do script or an editor's terminal."),
    ),
    # the acceptance pin (hooks/shared/acceptance.py PIN_RULE)
    "acceptance.pin": Rule(
        explain=("A HANDOFF closes or carries an acceptance criterion whose line in the acceptance "
                 "file no longer matches the command the lead pinned in the task's "
                 "metadata.acceptance_pin (rewritten, gone from the file, or never pinned), so the "
                 "HANDOFF was returned: the file sits in the coder's own checkout."),
        doc=f"{REFUSALS_DOC}#acceptance-pin",
        instead=("Ask the lead to review the change to the acceptance file and re-pin it with the "
                 "map `repact inspect --pin --acceptance <acceptance.md>` prints, or restore the pinned line."),
        why=("A criterion is closed by a run of the command the lead agreed; the acceptance file "
             "sits in the coder's own checkout, so without the pin a coder could rewrite that "
             "command to one it has already passed."),
    ),
    # settings_guard (hooks/settings_guard.py RULE_ID)
    "settings.write": Rule(
        explain=("A call would write a Claude Code settings file (.claude/settings.json, "
                 "settings.local.json or a settings*.json in the config dir), the user config "
                 "file .claude.json in your home folder (its MCP servers), the running "
                 "plugin's hooks, manifest or bin/checks.json, the plugin cache, the plugin data "
                 "(run ledger and gate state) or the loader's installed_plugins.json or "
                 "known_marketplaces.json, by a path in the command (a glob counts by what it "
                 "could match) or by the claude CLI (mcp add or remove, config set, plugin "
                 "install, enable, update or marketplace add); an agent is refused, and the lead "
                 "or a session with no agent is asked first."),
        doc=f"{REFUSALS_DOC}#settings-write",
        instead=("Run /repacted:init for the plugin's own settings, or edit the file yourself "
                 "in your own editor or terminal."),
    ),
    "settings.task_state": Rule(
        explain=("A Write, Edit, NotebookEdit or Bash call would write the task files "
                 "(<config dir>/tasks/) or a PACT session dir (<config dir>/pact-sessions/: the "
                 "ops log, the unattended flag, the journal), by a path in the command (a glob, a "
                 "variable or a symlink counts by what it names); every frame is refused, the "
                 "lead's too. Reading them is allowed, and the plugin's own bin tools may name them."),
        doc=f"{REFUSALS_DOC}#settings-task-state",
        why=("The repact commands judge a task from these files; a hand edit could complete a "
             "task, reset its rejection count or turn unattended mode off without any check."),
        instead=("Run the repact command for the step (submit, accept, reject or amend), or "
                 "TaskUpdate for a task no repact command made."),
    ),
    "settings.task_log": Rule(
        explain=("A Write, Edit, NotebookEdit or Bash call from an agent would write the repo's "
                 "task log (.repacted/tasks.jsonl), by a path in the command (a glob, a variable "
                 "or a symlink counts by what it names). The lead and a session with no agent "
                 "may still edit it; reading it is allowed to everyone."),
        doc=f"{REFUSALS_DOC}#settings-task-log",
        why=("A row closes only on evidence repact-task checked; a line written by hand could "
             "mark a task done with evidence nothing checked, and a union merge keeps it."),
        instead=("Run repact-task (done, set, reopen) for the change, or hand the step to the "
                 "lead."),
    ),
    "settings.consent": Rule(
        explain=("A Write, Edit, NotebookEdit or Bash call would write a merge approval token "
                 "(<config dir>/merge-authorized-*) or a session transcript (<config "
                 "dir>/projects/**/*.jsonl), or a Bash call would run one of the running "
                 "plugin's hook entries (hooks/*.py, dispatch.py among them); every frame is "
                 "refused, the lead's too. Reading them is allowed."),
        doc=f"{REFUSALS_DOC}#settings-consent",
        why=("The merge guard takes the user's approval from the transcript Claude Code writes "
             "and keeps the token as its use ledger; a hand-written token or line, or a hook "
             "run on a payload the model wrote, would stand in for the user's click."),
        instead=("Ask the user to approve the exact command with AskUserQuestion, then run it "
                 "unchanged."),
    ),
    "egress.url.credential": Rule(
        explain=("A git command named a remote URL whose user part carries a credential (a "
                 "password, a variable such as `$TOKEN`, or a known token), so it was refused."),
        doc=f"{REFUSALS_DOC}#egress-url-credential",
        instead=("Use the URL without the user:password part and let a credential helper supply "
                 "the token (`gh auth setup-git`, or `git config credential.helper`)."),
        why=("git keeps a remote URL in .git/config and prints it with `git remote -v` and in "
             "errors, so a token inside it reaches the transcript in plain text. The rule reads "
             "the command as written: a URL held in a variable set earlier is not seen."),
    ),
}

# The shell classifiers' ids, sentence for sentence with their own tables (see the header).
_SHELL_SENTENCES = {
    # env_guard (hooks/shell/env/programs.py RULES)
    "env.send.file": "A command would send a .env-class or credential file off the machine.",
    "env.read.credential": "A command or tool would print or copy a credential file (ssh key, cloud or kube credential).",
    "env.read.operand": "A command names a .env-class file as an operand a reader would print.",
    "env.read.computed": "A command would print a file whose name the shell computes and that can be .env-class.",
    "env.read.loaded": "A command would print or send a value it loaded from a .env-class file.",
    "env.read.loop": "A shell loop would print lines it reads from a .env-class file.",
    "env.unreadable": "The command holds text this check cannot be sure it reads as the shell does.",
    "env.dump.environment": "The command prints every environment variable.",
    "env.parse_failed": "The command mentions a .env-class name and could not be parsed.",
    "env.search.unknown_root": "A recursive search starts from a path this check cannot resolve.",
    "env.search.too_wide": "A recursive search covers more entries than the walk limit.",
    "env.search.reaches_class": "A recursive search would read a .env-class file and print it.",
    "env.grep.glob_not_string": "Grep's glob is not a string, so the class cannot be excluded.",
    "env.git.stage": "What a git command would stage or commit could not be checked.",
    "env.git.unknown_dir": "The directory a git command works in is not known here.",
    "env.git.created_repo": "The command creates a repository whose contents are not checked.",
    "env.git.push_unverified": "What a push sends could not be verified.",
    "env.git.push_expanded": "A push names what it sends with a shell expansion.",
    "env.git.push_sends": "A push sends a .env-class or credential file.",
    "env.git.push_secret": "A push adds a line carrying a secret-shaped value.",
    "env.git.expanded": "A git command is named by a shell expansion or alias.",
    "env.opaque": "The command's program, subcommand or script is built at run time.",
    "env.read.archive": "An archive written to a pipe would carry a .env-class file.",
    "env.git.history_sent": "git history is piped into a program that sends it.",
    # merge_guard_pre (hooks/shell/merge/arms.py RULE_IDS)
    "mg.merge.explicit": "A PR or MR merge requires an approval bound to this exact command.",
    "mg.close.explicit": "A PR or MR close requires an approval bound to this exact command.",
    "mg.force_push.explicit": "A force-push requires an approval bound to this exact command.",
    "mg.branch_delete.explicit": "A branch delete requires an approval bound to this exact command.",
    "mg.remote_ref_delete.explicit": "A remote ref delete requires an approval bound to this exact command.",
    "mg.mass_delete.explicit": "A remote mass delete requires an approval bound to this exact command.",
    "mg.protection.explicit": "A branch-protection change requires an approval bound to this exact command.",
    "mg.mr_delete.explicit": "An MR delete requires an approval bound to this exact command.",
    "mg.push.to_main.implicit": "A push that lands on main without naming it requires an approval.",
    "mg.push.to_main.expanded": "A push names its destination with a shell expansion or a glob.",
    "mg.danger.unbound": "A destructive shape the arms recognise but no operation type binds.",
    "mg.compound.destructive": "Several destructive operations in one command cannot share one approval.",
    "mg.unmintable.shape": "A shape no approval can bind (GraphQL mutation, body from a file, raw HTTP, or an operation no approval can name inside a shell -c or eval string, a here-string, a subshell, a substitution or a compound statement).",
    "mg.unmintable.flag_flood": "More than 32 flags hide what the command does.",
    "mg.unmintable.long_run": "A run of 512 or more characters cannot be bound to an approval.",
    "mg.token.mismatch": "The approved operation does not match this command.",
    "mg.token.consume_failed": "The authorization token could not be consumed.",
    "mg.token.no_record": "A matching approval token has no answer from the user on record in the session transcript.",
    # merge_guard_pre allow-side mask trace (hooks/shell/merge/arms.py MASK_RULE_IDS)
    "mg.inert.data_program": "A quoted argument of a program that only reads or prints its arguments (DATA_PROGRAMS) is data, not a command.",
    "mg.inert.json_literal": "A quoted argument that parses as a JSON object or array is data to every program but a shell.",
    "mg.inert.heredoc_consumer": "A heredoc body or printed text piped to a program that is not a shell is data (carrier 1 with the pipe-to-shell view).",
}

# The way through for each shell id. The eleven most fired (gate_refusal rows by rule
# across the session journals, 2026-10-16) carry the specific steps; the rest name the
# same step their refusal message gives.
_ENV_GET = "`env-get FILE KEY -- <command>` (or `env-get --keys FILE` for the key names)"
_APPROVE = ("Ask the user to approve this exact command with AskUserQuestion, the literal "
            "command in the chosen option, then run it unchanged.")
_INERT = "Nothing to do: this id marks text read as data on an allowed command, not a refusal."
_SHELL_INSTEAD = {
    "env.send.file": f"Send only the value the service needs, passed with {_ENV_GET}; a credential file stays on this machine.",
    "env.read.credential": "Pass the file's path to the program that uses it (`ssh -i`, `kubectl --kubeconfig`, `AWS_SHARED_CREDENTIALS_FILE`); a public key (*.pub) stays readable.",
    "env.read.operand": f"Pass the value to the command that needs it with {_ENV_GET}; do not print the file.",
    "env.read.computed": f"Write the file name out literally, and for a secret value use {_ENV_GET}.",
    "env.read.loaded": f"Let the command use the value without printing or sending it, or pass it with {_ENV_GET}.",
    "env.read.loop": f"Read the one key you need with {_ENV_GET} instead of looping over the file.",
    "env.unreadable": "Rewrite the command in plain shell (no odd quoting, escapes or control characters) and run it again.",
    "env.dump.environment": f"Print the one variable you need by name, or use {_ENV_GET}.",
    "env.parse_failed": "Split the command into simpler steps the shell parses plainly, keeping .env-class names out of them.",
    "env.search.unknown_root": ("Write the paths the variable or `$(...)` holds out as plain words (absolute, or "
                                "relative after a plain `cd <path>`); a loop over plain words, a brace list and a "
                                "glob are resolved. Or use the Grep tool."),
    "env.search.too_wide": "Start the search from a narrower directory, or use the Grep tool.",
    "env.search.reaches_class": ("Exclude the class from the search: grep --exclude='.env*' --exclude='*.env' "
                                 "--exclude=.envrc --exclude=.netrc, or rg -g '!.env*' -g '!*.env' -g '!.envrc' -g '!.netrc'."),
    "env.grep.glob_not_string": "Pass the Grep tool's glob as one string.",
    "env.git.stage": "Stage the files by name (`git add <path>`), or run the command from your own terminal.",
    "env.git.unknown_dir": ("Write the directory out: `git -C <path> ...`, a plain `cd <path> &&` first, or "
                            "`for d in <path> <path>; do git -C \"$d\" ...; done`, never a `$(...)` value."),
    "env.git.created_repo": "Create the repository in one command, then stage into it in the next.",
    "env.git.push_unverified": "Push from your own terminal if you are sure what the push sends.",
    "env.git.push_expanded": "Write the branch name out literally and run the push again.",
    "env.git.push_sends": "Remove the file from the outgoing commits (git rm --cached, then amend or rebase) and push again.",
    "env.git.push_secret": ("Remove the value from the outgoing commits and push again. If you believe it is a "
                            "test fixture or doc example, ask the user to confirm the value is not real before "
                            "adding the pragma the refusal names on that line."),
    "env.git.expanded": "Write the git subcommand out (no variable or alias) and run it again.",
    "env.opaque": ("Write the program and its subcommand out as plain words, and put a script in a "
                   "file or a quoted heredoc, then run it again."),
    "env.read.archive": ("Leave the class out of the archive (--exclude '.env*' --exclude '*.env' "
                         "--exclude .envrc --exclude .netrc), or write the archive to a file."),
    "env.git.history_sent": ("Push the branch instead (a push is checked for secrets), or send the "
                             "patch or bundle from your own terminal."),
    "mg.merge.explicit": _APPROVE,
    "mg.close.explicit": _APPROVE,
    "mg.force_push.explicit": _APPROVE,
    "mg.branch_delete.explicit": _APPROVE,
    "mg.remote_ref_delete.explicit": _APPROVE,
    "mg.mass_delete.explicit": _APPROVE,
    "mg.protection.explicit": _APPROVE,
    "mg.mr_delete.explicit": _APPROVE,
    "mg.push.to_main.implicit": ("Push a feature branch (`git push origin <branch>`) if main was not meant; else ask "
                                 "the user to approve this exact push with AskUserQuestion, then run it unchanged."),
    "mg.push.to_main.expanded": "Write the destination branch out literally and run the push again.",
    "mg.danger.unbound": "Run the operation as a plain gh, glab or git command the user can approve, or ask the user to run it.",
    "mg.compound.destructive": "Split the command so each destructive operation runs alone, with its own approval.",
    "mg.unmintable.shape": ("Run the operation as one plain command (no shell -c, eval, heredoc, subshell, raw API "
                            "call or chain), then ask the user to approve that exact command."),
    "mg.unmintable.flag_flood": "Drop the flags the operation does not need, then ask for approval of the shorter command.",
    "mg.unmintable.long_run": "Shorten the command (move long text into a file it reads), then ask for approval.",
    "mg.token.mismatch": ("Run exactly the command the user approved (same target, repository, flags and no added "
                          "pipe or redirect), or ask for approval of this one."),
    "mg.token.consume_failed": "Run repact-doctor, then ask the user to approve the command again.",
    "mg.token.no_record": ("Ask the user to approve this exact command with AskUserQuestion and run it within "
                           "15 minutes; from a subagent or teammate, hand the command to the lead."),
    "mg.inert.data_program": _INERT,
    "mg.inert.json_literal": _INERT,
    "mg.inert.heredoc_consumer": _INERT,
}
assert set(_SHELL_INSTEAD) == set(_SHELL_SENTENCES)
RULES.update({
    rule_id: Rule(explain=sentence, doc=f"{REFUSALS_DOC}#{anchor(rule_id)}",
                  instead=_SHELL_INSTEAD[rule_id])
    for rule_id, sentence in _SHELL_SENTENCES.items()
})


def explain(rule_id: str) -> Optional[Rule]:
    """The Rule for an id, or None for a fallback id (`<check>.exit_2`) or an unknown one."""
    return RULES.get(rule_id) if isinstance(rule_id, str) else None
