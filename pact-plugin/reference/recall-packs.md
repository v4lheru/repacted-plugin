# Recall fragments for a skill pack

> **Purpose**: a skill pack (a Claude Code plugin installed beside repacted) tells recall which tool calls should point the agent at its skills.
>
> **Usage**: put one or more `*.json` files in a `recall.d/` folder at the root of your plugin. Run `repact-doctor`: its `skill packs` line counts your triggers, or names every fragment it skipped and why.

---

## A minimal fragment

`recall.d/triggers.json` in a plugin whose `.claude-plugin/plugin.json` says `"name": "acme"`:

```json
{
  "version": 1,
  "about": "Recall triggers for the acme pack.",
  "skills": {
    "acme-deploy": {
      "why": "you are deploying with acmectl; the skill holds the rollback steps",
      "commands": ["\\bacmectl\\s+deploy\\b"],
      "hosts": ["\\.acme\\.example$"],
      "globs": ["**/acme.yaml"]
    }
  }
}
```

The plugin must also ship `skills/acme-deploy/SKILL.md`. When a Bash command matches, recall points the agent at `Skill(acme:acme-deploy)`.

## The fields

| Field | Meaning |
|---|---|
| `version` | `1`. Any other value skips the file. |
| `about` | Optional note for people. Recall never reads it. |
| `skills` | One entry per skill, at most 64 per file. The key is the skill's folder name under `skills/`, bare (`acme-deploy`) or with your own pack name (`acme:acme-deploy`). |
| `why` | One line, at most 300 characters: why the skill fits this moment. |
| `commands` | Regexes matched against a Bash command (its first 1024 characters). |
| `hosts` | Regexes matched against the host of any URL in a Bash command or a WebFetch (the first 16 hosts, 253 characters each). |
| `globs` | Globs matched against the file path of an Edit or Write (its first 1024 characters). |

Each list holds at most 16 entries of at most 200 characters, and an entry needs at least one pattern. Skill names are lowercase ASCII letters, digits and hyphens. A pack name is ASCII letters, digits, `-` and `_`, is not `repacted` in any case, and differs from every other installed pack's in more than case.

## Rules

- **Add only.** Your triggers are added under `<your pack>:<skill>`. A key naming another pack's or core's skill is dropped. A name claimed twice (two of your files, or two installs of the same pack) is dropped everywhere. Either way the doctor lists it as a conflict; recall never picks a winner.
- **One open choice per regex.** A regex may hold at most one place where it can match in more than one way: one repeat (`*`, `+`, `?`, or `{m,n}` with m below n) or one alternation (`a|b`), with no repeat or `|` inside a repeat and no back-reference. `\bacmectl\s+(deploy|push)\b` holds two (the `\s+` and the `|`) and is refused. Python regexes cannot be stopped once they start, and a chain of such choices can take minutes on one short command. Split a long pattern into several, or write the spaces literally: `\bacmectl (deploy|push)\b`.
- **A broken file is skipped, never fatal.** A file over 64 KB, not UTF-8, not JSON, with a repeated key, `NaN` or `Infinity`, a symlink that leaves the plugin folder, or an unknown field is skipped whole, and the doctor says which and why. Recall goes on with every other trigger.
- **Your text is data.** Recall shows your `why` inside a `<recalled-lesson origin="pack">` element, escaped, the way it shows knowledge-base text. Control and format characters are refused.
- **Limits over all packs**: 16 packs, 16 files per pack, 256 patterns in all, counted by pack name, then file name, then order in the file, before any pattern compiles; a skill past the limit is skipped. A matched skill whose `SKILL.md` is over 256 KB is not recalled.

## Which installs count

Recall reads the harness registry `plugins/installed_plugins.json` in the Claude config folder (`$CLAUDE_CONFIG_DIR`, else `~/.claude`). A user-scope install counts everywhere. A project or local install counts in sessions under its project folder. An install set to `false` in `enabledPlugins` (user, project or local settings, the later one deciding) does not count.

A plugin loaded with `claude --plugin-dir` is not in the registry, so its fragments are not read. Install it from a marketplace (a local folder marketplace works) to test them.
