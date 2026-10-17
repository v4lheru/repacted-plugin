# Skill authoring: validator and runtime traps

Context: what breaks when validating a skill, packaging it, or running it outside
Claude Code (claude.ai uploads, the Skills API, Cowork). Read it before authoring or packaging a
skill. Split from hooks-and-harness.md because that file would pass the reference size target.

Stamp legend: `(verified|observed DATE, VERSION)`; these are tool and platform facts, re-probe on
a new release.
`{config_dir}` is `$CLAUDE_CONFIG_DIR` when set, else `$HOME/.claude`.

## Canonical rules
1. Read your skill validator before authoring: a backticked token with a `/` and a listed extension must resolve inside the skill, so create files before pointers. (verified 2026-09-26)
2. Document an external path without breaking the validator: drop the extension, or write it without backticks. (verified 2026-09-26)
3. Router hard limits: body within the validator's line and word thresholds; no empty file, no odd fence count, no file ending mid-sentence. (verified 2026-09-26)
4. Only six frontmatter keys survive a claude.ai upload, the Skills API and `package_skill.py`: name, description, license, compatibility, metadata, allowed-tools. (observed 2026-08-12, claude.ai)
5. A local skill validator may not check the frontmatter key allowlist; a skill can pass every local check and fail at upload. Add a check when distribution matters. (observed 2026-08-12)
6. Ask "which runtime?" first: Cowork sandboxes have no persistence, blocked egress by default, and connector tokens never enter the sandbox. (observed 2026-08, Cowork)
7. Cowork sessions do not read `{config_dir}/skills/`; they load skills enabled for the claude.ai account, synced at session start. (observed 2026-08-12, Cowork)
8. Render every figure and its Source line from a provenance ledger, never from prose; wrong-footnote and duplicate-slide defects are the same failure. (observed 2026-08)
9. Make "unavailable" a rendered value with a failure reason; a silently missing figure is a fabricated claim by omission. (observed 2026-08)
10. Ship credentials by risk class: read-only may ship, spend- or write-capable never ships, person-identifying goes to per-user config at onboarding. (observed 2026-08)

## Detail

**Validator facts (one validator, read 2026-09-26).** References are markdown links (bracket text, then a parenthesised path)
and backticked `a/b.ext` tokens (extensions md, py, sh, json, yaml, yml, txt). A reference resolves
relative to the referring file or the skill root. `templates/` is skipped. Warnings, not errors:
each `protocols/*.md` needs a `## Next` section, and MUST/ALWAYS/NEVER should sit inside a
numbered workflow. The description limit defaults high; the repacted catalog budget sets a tighter
one, passed as `--max-description`. Cross-skill paths never resolve, so name another skill's
reference by skill name and bare filename.

**Runtime guard vs post-hoc check.** A rule such as "reject a body that opens with `ERROR`
regardless of HTTP status" must fire when the response arrives, in the shared connector; a static
check can only assert the guard exists at the call site. Split a mechanical flagger (always exit 0,
emits a label stub) from the validator that blocks release on an unreviewed verdict.

**Config.** Stdlib has no YAML parser: use TOML (stdlib `tomllib`, Python 3.11 floor) for
human-written config and JSON for machine state. Keep config outside the skill folder, because a
version drop-in replaces the folder: per-person settings in a home dir, shared client facts beside
the client's outputs. A resolution derived from a premise that was later overturned is void;
re-open it and amend the governing spec. Exercise every optional path in tests.

**Cowork specifics.** Code execution: Python 3.11 with python-pptx, python-docx, openpyxl, pandas
and similar preinstalled, so design for zero installs. Egress goes through a proxy set per
organisation (Enterprise default off, Team default package managers only); a change applies only
to new sessions. MCP and connector calls are server-side and exempt from sandbox egress. The Drive
connector extracts text only, so write a machine-readable state file beside any deliverable that
must be read back. Public docs disagreed on local vs cloud execution during rollout; confirm which
applies to the organisation.

**python-pptx (1.0.2).** Native charts work (line, stacked and clustered columns, pie with
labels); there is no dual-axis API (hand-edit the chart XML behind one function); `Inches()` rounds,
so set the 16:9 canvas with Emu values of 12,192,000 by 6,858,000; the default canvas is 4:3.

## Examples
- A 15-file skill reached zero errors on the first validator run because every file existed
  before any pointer named it.
- A skill with `argument-hint` in its frontmatter passed local validation and packaging, then
  failed the claude.ai upload.
