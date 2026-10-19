# Project specialists: bring your own agents

A repository can ship its own PACT specialists beside the ones the plugin ships. The lead dispatches them like the shipped ones: one work task through `repact dispatch`, the same dispatch gate and the same HANDOFF checks.

## Make one

```
repact-agent new mobile --description "Builds and tests the iOS and Android apps."
```

This writes `.claude/agents/pact-mobile.md` from `templates/agent.md`: the frontmatter (`name: pact-mobile`, `model: inherit`, `memory: project`) and a body that starts with the teammate binding. Fill in the three paragraphs the template marks, commit the file, then:

```
repact-agent check
```

`check` reads every agent file the project's `.claude/agents` directories hold, with the same reader the dispatch gate uses, and prints one line per file: `ok` when the lead can spawn it (or when it is a plain agent PACT does not touch), `refused` with the reason otherwise. It exits 1 when any file is refused. `repact-agent check FILE` checks one file.

The lead spawns it with `subagent_type="pact-mobile"`.

## What makes a file a specialist

A project agent is a PACT specialist only when all of these hold. Anything else is refused at spawn time with a rule id and the way through (`docs/troubleshooting.md`).

| Rule | Why |
|---|---|
| The frontmatter `name` is `pact-<role>`: lowercase letters, digits and inner hyphens (never three in a row), at most 64 characters, and equal to the file name without `.md`. | The host identifies an agent only by its `name`; a plain ASCII name cannot impersonate another by case or Unicode form. |
| The body (after the frontmatter, outside code blocks and HTML comments) contains `Skill("repacted:teammate")`. | The teammate binding: without it the agent does not follow the teachback, HANDOFF and waiting protocol (`dispatch_gate.project_agent_unbound`). |
| The file is a regular file, not a symbolic link, at most 64 KiB, UTF-8 without a byte-order mark. | The file is repository-supplied text that becomes an agent's instructions; the reader accepts only what it can read with certainty (`dispatch_gate.project_agent_invalid`). |
| The frontmatter uses plain `key: value` lines, and `name:` is a plain one-line value. | A tab, quoted key, flow mapping, merge key or a `name:` that is a block, list or multi-line value could give the host a name this reader does not see. Such a file is never a specialist, and it claims the names its frontmatter could spell (below). Unusual syntax in other keys, such as a `description: \|` block with space-indented lines, does not matter. |
| The frontmatter opens with a line `---` and closes with a line `---` (trailing spaces allowed), and contains no other `---`. | The host ends the frontmatter at the first `---` after the opener, even in the middle of a line, and accepts `---<tab>`, `--- text` or `----` as delimiters; a file where the host's cut differs from a line-by-line one could carry a different name. Such a file is never a specialist, and it claims the names either cut could spell. |
| No other file defines the same name. | Two definitions leave the host's choice unspecified (`dispatch_gate.project_agent_ambiguous`). |

A project agent whose name does not start with `pact-` is not a PACT specialist and is not checked: the host runs it as a plain agent, outside the team protocol.

## Shipped names cannot be shadowed

The host starts a project agent before a plugin agent of the same name. So a project file that claims a shipped specialist's name (by its `name:`, in any case or Unicode form, under any file name) makes the bare spawn of that specialist start the project's file instead. The dispatch gate refuses that bare spawn (`dispatch_gate.project_agent_shadows_plugin`); the namespaced spelling `repacted:pact-<role>` always names the plugin's own agent and is not affected. A link from `.claude/agents/pact-<role>.md` to the plugin's own file of the same name is not a second definition.

A file whose name cannot be read with certainty claims every shipped name its frontmatter text spells, compared case-folded and in NFKC form; the body does not count, since the host reads the name only from the frontmatter. It claims **every** name when its frontmatter has something YAML could use to build text the file does not spell: an escape (a backslash), a tag (`!`) or an anchor (`&`, which an alias `*` then repeats), or when it is a regular file this reader cannot read for another reason. A file the host cannot load claims nothing: a dangling link, a FIFO or device, a file you may not open. `repact-agent check` lists the shipped names such a file blocks, or says `ok` when it blocks none. A tree the gate cannot read completely (nested more than 8 levels, more than 200 agent files) leaves every name uncertain: bare spawns of shipped specialists are refused until the file or the tree is fixed (`dispatch_gate.project_agents_unreadable`). Fix it, or spawn by the namespaced spelling.

## Which directories are read

Every `.claude/agents` directory from the project directory up to the repository root (the nearest directory holding `.git`), each recursively, following directory links. In a linked worktree (`git worktree add`), the main checkout's `.claude/agents` too: the host appears to read it (seen in its binary, not confirmed on a live host), and reading one more directory can only refuse more. Outside a repository, only the project directory's own. The user-scope directory (`$CLAUDE_CONFIG_DIR/agents`, else `~/.claude/agents`) is never read as project scope, even when the project directory is your home directory.

This follows the host's documented behaviour (https://code.claude.com/docs/en/sub-agents, section "Choose the subagent scope", read 2026-10-17): identity comes only from the `name` frontmatter field; `.claude/agents/` is scanned recursively; project subagents are discovered by walking up from the working directory to the repository root; and when several locations define the same name, the higher-priority one is used, in the order managed settings, the `--agents` CLI flag, `.claude/agents/`, `~/.claude/agents/`, then a plugin's `agents/` directory.

**Not read:** user-scope agents, agents from managed settings and the `--agents` flag. A specialist defined there is not registered (its spawn is refused as not registered), and a shipped name it shadows is not detected.

## Turning project specialists off

A team policy (`.repacted/policy.json`, see `reference/config.md`, "Team policy") can turn project specialists off for everyone in the work tree:

```json
{"version": 1, "agents": {"project_scope": "off"}}
```

Then a spawn of a project specialist is refused with `dispatch_gate.project_agents_disabled`, and the refusal names the policy file. The shadowing check above still applies. `allow` is the default and, since a policy can only tighten, `allow` in a policy changes nothing. `repact-doctor --policy` shows the effective value.

## Same checks as shipped specialists

A valid project specialist is a registered specialist everywhere the plugin asks: the dispatch gate's name, team and task rules, the teachback and HANDOFF ordering gates, the task lifecycle checks and the session journal. None of these grants a registered type an exemption; the exemptions (the secretary's) are tied to fixed shipped names, which a project file cannot claim.
