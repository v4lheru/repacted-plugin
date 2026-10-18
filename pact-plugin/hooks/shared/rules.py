"""
Location: pact-plugin/hooks/shared/rules.py
Summary: The stable rule ids a refusal can carry, each with one fixed sentence, the way
through (`instead`: what to do instead, one plain sentence) and a doc anchor. A check names
its id in `Result.rule` (native) or as a `[rule: <id>]` tail on its reason or stderr
(script); hooks/dispatch.py lifts it into the `gate_refusal` row and the refusal line
`<check>: <reason> → <instead> [rule: <id>]`, and `bin/repact-explain` prints the sentence,
the way through and the anchor back. An id a check emits but this table lacks is a contract
test failure (tests/test_dispatch_rule_tail.py), so the user never meets a bare id; an id
without a way through fails tests/contract/test_refusal_contract.py.
Used by: bin/repact-explain, hooks/dispatch_rules.py (loads this file by path for
`instead`), tests/test_dispatch_rule_tail.py, tests/contract/test_refusal_contract.py.
Imports nothing from the package (a leaf).

Ids are dotted `<check or module>.<rule>`. The dispatcher's own: `<check>.timeout` for a
security check that overran its alarm (fail closed), `<check>.error` for one that failed without deciding,
and `dispatch.task_store_absent` (the
host stopped writing task files). The classifier ids of the security hooks keep their
tables beside the classifiers (hooks/shell/env/programs.py RULES, hooks/shell/merge/arms.py
RULE_IDS and MASK_RULE_IDS, which choose the id for a refusal) and are listed here again,
sentence for sentence, so this is the one registry repact-explain resolves against;
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
    "env_guard.error": _error("env_guard", "command"),
    "merge_guard_pre.error": _error("merge_guard_pre", "git command"),
    "egress_guard.error": _error("egress_guard", "outbound command"),
    "git_commit_check.error": _error("git_commit_check", "commit"),
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
    # process_table_guard (hooks/process_table_guard.py RULE_ID)
    "process.listing": Rule(
        explain=("A command would list processes with their full command lines (ps, pgrep -f, "
                 "pstree, top -c or a /proc argv file), so it was refused."),
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
}

# The shell classifiers' ids, sentence for sentence with their own tables (see the header).
_SHELL_SENTENCES = {
    # env_guard (hooks/shell/env/programs.py RULES)
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
    "env.read.operand": f"Pass the value to the command that needs it with {_ENV_GET}; do not print the file.",
    "env.read.computed": f"Write the file name out literally, and for a secret value use {_ENV_GET}.",
    "env.read.loaded": f"Let the command use the value without printing or sending it, or pass it with {_ENV_GET}.",
    "env.read.loop": f"Read the one key you need with {_ENV_GET} instead of looping over the file.",
    "env.unreadable": "Rewrite the command in plain shell (no odd quoting, escapes or control characters) and run it again.",
    "env.dump.environment": f"Print the one variable you need by name, or use {_ENV_GET}.",
    "env.parse_failed": "Split the command into simpler steps the shell parses plainly, keeping .env-class names out of them.",
    "env.search.unknown_root": ("Write the search root out as a plain path (absolute, or relative after a plain "
                                "`cd <path>`), with no variable, substitution, brace list or **; or use the Grep tool."),
    "env.search.too_wide": "Start the search from a narrower directory, or use the Grep tool.",
    "env.search.reaches_class": ("Exclude the class from the search: grep --exclude='.env*' --exclude='*.env' "
                                 "--exclude=.envrc --exclude=.netrc, or rg -g '!.env*' -g '!*.env' -g '!.envrc' -g '!.netrc'."),
    "env.grep.glob_not_string": "Pass the Grep tool's glob as one string.",
    "env.git.stage": "Stage the files by name (`git add <path>`), or run the command from your own terminal.",
    "env.git.unknown_dir": ("Run git with its directory written out, as `git -C <path> ...` or after a plain "
                            "`cd <path> &&`, or run it from your own terminal."),
    "env.git.created_repo": "Create the repository in one command, then stage into it in the next.",
    "env.git.push_unverified": "Push from your own terminal if you are sure what the push sends.",
    "env.git.push_expanded": "Write the branch name out literally and run the push again.",
    "env.git.push_sends": "Remove the file from the outgoing commits (git rm --cached, then amend or rebase) and push again.",
    "env.git.push_secret": ("Remove the value from the outgoing commits and push again. If you believe it is a "
                            "test fixture or doc example, ask the user to confirm the value is not real before "
                            "adding the pragma the refusal names on that line."),
    "env.git.expanded": "Write the git subcommand out (no variable or alias) and run it again.",
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
