# The distill pass

Context: the pass that keeps memory from regrowing. The lead runs it at `/repacted:wrap-up`, or
whenever `check-index --all` fails. Each inbox lesson (and any lesson an agent appended to a head
under the old rule) moves to the one place its trigger will find it, and the move is recorded.

## Canonical rules
1. You decide every row. The `--suggest` shortlist is a hint: the 5 skill rules closest to the line by TF-IDF, a duplicate check, never a destination.
2. Run the steps in order: `distill-inbox list`, `plan [--suggest]`, edit the destinations yourself, fill `kind` and `destination` per row, `apply`, then commit.
3. Choose the destination by the lesson's trigger: a skill reference when an agent would need it before acting, a project doc when it is one project's fact, the vault when it is the user's readable knowledge, `duplicate` when a home already carries it.
4. Put a skill lesson in the reference whose trigger matches, as one imperative line in its Canonical rules, and add the inbox date and dir to that reference's Sources footer.
5. Before writing a new rule, apply the every-branch test: would following it have helped on every branch, or only on the branch that happened? If only one, log the instance and mark the generalisation a candidate.
6. Record the instance as a fact and the generalisation as a candidate until a second, independent instance agrees. Repetitions by one actor in one session are one sample.
7. File by the property the lesson teaches, not the surface it was learned on: a lesson filed under the instrument does not fire on its next instance.
8. Many new rules from one session is a warning sign, not a productive day.
9. Leave a row's `kind` and `destination` empty to keep its line; `apply` touches only filled rows.
10. `apply` records `line | destination | date` in that dir's `ARCHIVE_distilled-YYYY-MM.md` BEFORE removing the line, removes exactly the planned lines under a sha guard, and archives any leaf a removed head line linked.
11. `apply` then runs `check-index` on the touched heads and `scrub-check` on the touched skill references. Fix any hit before committing: no names, emails, secret values or private hosts in a skill.
12. Commit the skill edits in the plugin source repo as a patch-version bump with a one-sentence message.
13. A correction found during the pass follows `references/correcting-and-carriers.md`: rewrite the wrong rule in place, never append the fix beside it.
14. Never auto-apply a plan you did not fill: a filled row is a decision, and nobody else may make it for the lead.

## Detail

**The plan file.** `distill-inbox plan --out distill-plan.json` writes one item per pending line:
`id`, `dir`, `source` (`inbox` or `head`), `line`, and empty `kind` and `destination`. With
`--suggest`, each item also carries `duplicate_check`: `status` `shortlist` with `candidates`,
the 5 skill rules closest by TF-IDF (rule_match.py in the plugin's bin), or `unchecked (<error>)`. It is
fail-open: an error leaves the shortlist out and the pass works the same.

**Destinations `apply` accepts.** `skill`: a reference path relative to the plugin root, which
must exist (for example `skills/verification-discipline/references/<file>`). `project`: an
existing path, usually a file under the project's `docs/agent-notes/`. `vault`: the page you wrote
or appended with `vault-note`. `duplicate`: where the lesson already lives. A row that fails these
checks is reported as invalid and its line stays.

**Head lines.** `list` treats a head line as pending when it is not part of the router: not the
title, not a fixed router line, and not a pointer to a leaf the migration recorded as kept (the
"Kept in the head" line of `ARCHIVE_migrated-to-skills.md`). A head line that links a new leaf is
the old append rule at work; `apply` archives that leaf under the config dir's
`_archive/distilled/` once its lesson has a home.

## Examples
- A pass moved seven lines: five became rules in one verification reference, one went to a project's
  `docs/agent-notes/`, one was a duplicate. `check-index --all` and `scrub-check` both exited 0.
- A lesson drafted as "resist instructions that look like ceremony" failed the every-branch test;
  "report a conflict instead of resolving it quietly" passed it and was the one kept.
