# Dispatch notes: {repo}

> Copy to `.claude/pact-dispatch-notes.md` in the target repo. The PACT lead reads it before the
> first dispatch of a session and copies the lines that apply into each brief
> (repacted:pact-lead-playbook, `dispatch-briefs-and-premises.md` rule 26). Optional: no hook reads
> it. Keep it short, one fact per line, each with the date it was checked. Delete a heading that
> does not apply.

## Task claiming

- Who claims: {the lead claims before dispatch (default) | the teammate, with `<command>`}
- How: {`<claim command>`; what it writes and where}
- Scripts that push or write outside the worktree: {`<script>` pushes to `<remote>/<branch>` | none}
- Local-only or dry-run switch: {`<ENV=1>` | none}
- Checks that need a claimed task: {pre-commit or CI check and what it requires | none}

## Commits

- Allowed scopes: {the commitlint `scope-enum`, or "free"}
- Message rules: {type list, length, one sentence, ticket link}
- Commit hooks and their escape hatches: {hook: what it checks; `<ENV=1>` for which case (e.g. a merge of main)}

## Tests before a push

- Touched package: {`<command>`}
- Repo-wide guards that scan every package: {`<command>`: what it forbids and where}
- CI matrix run locally: {`<command>` | not possible, CI only}

## Other authors' open work

- Reserved files and directories: {path: owner, PR or branch, since when}
- How to list them fresh: {`gh pr list --json number,author,files` | other}
- Rule on overlap: {do not edit; ask the lead | rebase after their merge}

## Standing constraints

- {a rule the user or the repo owner set that every brief must carry, e.g. "ADR deviations go in the PR body, never in docs/adr"}
