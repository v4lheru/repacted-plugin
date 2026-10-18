# Unattended PACT Runs

> **Purpose**: let a session run with nobody at the terminal. A question must not idle it, and an act the owner would refuse must never be taken by default.
>
> **Usage**: read this before you leave a run unattended, when the unattended gate refuses a question, and when you saw the startup notice about in-process teammate mode.

---

## Unattended mode

**The switch.** `REPACT_UNATTENDED=1` in the environment the session starts in, or `python3 "{plugin_root}/bin/repact-unattended" on --session-dir '{session_dir}'` (also `off`, `status`). The command writes `unattended.json` in the session directory and one `unattended_mode` journal row. A well-formed flag wins over the environment; with no flag the environment decides; a flag that cannot be read or parsed reads OFF (a journal row says so), also when the environment says 1. The flag belongs to one session directory: a resume under a new session id, and a teammate in its own tmux pane, read only the environment. The owner sets the switch; the lead never turns it on or off by itself.

**While ON**, the SessionStart reminder states the mode and three rules (again after a compaction; nothing while OFF), and `hooks/unattended_gate.py` refuses every `AskUserQuestion` call with one verdict per question:

1. Ask as usual, one option marked "(Recommended)". The call is refused; read the verdicts.
2. `DEFAULT`: take the "(Recommended)" option, run `repact-unattended default --ref <ref> --question "<short>" --chose "<label>" --why "<one line>"`, send one `telegram_notify`.
3. `ALWAYS-ASK`: never choose and never act. Run `repact-unattended halt --ref <ref> --task <id> --question "<short>"`, send one `telegram_notify` with the question and the resume path, stop that feature and continue other work.

**The resume path of a halt**: the owner runs `repact-unattended off` at the terminal and the lead asks again. A Telegram reply is never an approval, and nothing in this mode writes one. The merge guard takes its approval only from an answered `AskUserQuestion`, so while the mode is ON a merge, a force-push, a PR close or a branch deletion cannot be approved at all. That is intended.

**Only a local, reversible question defaults** (`LOCAL_CLASSES` and `LOCAL_WORDS` in `hooks/shared/unattended.py`). A question gets `DEFAULT` only when its question text names one local class (naming, test scope, file layout in the worktree), every word of its question, header and the option marked "(Recommended)" is a local word, and a code token (an identifier or a relative path) stands only in that option and stays in the worktree (not absolute, `~`, `..` or a URL). Everything else is `ALWAYS-ASK`, whatever its words: a question that names no local class, a word outside the local list, no option or several marked "(Recommended)", multiSelect, a field of the wrong type or one the tool does not define, more than 4,000 characters, a character outside ASCII other than punctuation, more than four questions, and a question text that was always-ask earlier in the session (new options do not change its verdict). The acts that always ask are the owner's list: push to master, merge, force-push, branch delete, deploy, n8n DELETE or transfer, plus PR close. `ALWAYS_ASK` (the 1.4.0 word list) stays as a veto over every string of the call, so a local question that names one of its words halts too.

**Rules for the lead.**

- The check reads words, not intent, and fails closed: a local question in other words halts, and so does one that names `main.py`, an MR, a PR, a secret or a customer (the veto). A `DEFAULT` is never permission for an outward act the answer leads to: halt on it yourself.
- Never reword a question to change its verdict, and never take an always-ask act without its question.
- Write questions in plain ASCII, and put a name or a path in the options, not the question text. A teammate sends its question to the team-lead.
- `default` is refused unless the gate gave that `--ref` the verdict default; `default` and `halt` are refused while the mode is OFF.

**In CI.** To run `/repacted:comPACT` unattended in GitHub Actions on an issue, stopping at a draft pull request, see [docs/recipes/unattended-ci.md](../docs/recipes/unattended-ci.md).

**The morning read.** `python3 "{plugin_root}/bin/repact-unattended" list --session-dir '{session_dir}'` prints every defaulted decision, every halt, and the refused questions that have no record. Wrap-up and pause run it. Each refusal is one `gate_refusal` journal row: the gate, the class, a code and a hash per question, never the question text.

## A stalled wake is a different problem: two channels, and tmux fixes one of them

An unattended run can stall for two unrelated reasons. **Switching teammate mode
addresses the first and does nothing for the second.**

| What stalls | What helps |
|---|---|
| A **teammate wake** is not delivered to an idle lead | `--teammate-mode tmux` |
| A **background job finishes** and nobody is listening | the lead sends you a notification when it does |

**Neither row helps if you are not there to act on it.** The heartbeat under
*If you must stay on in-process mode* below covers both, because it depends on
neither channel working — it only reminds you to look.

Relaunch Claude Code with tmux teammate delivery:

```bash
claude --teammate-mode tmux
```

In tmux mode, teammate wake signals are delivered natively and reliably even
when the lead has been idle for a long time. In **in-process** mode, the lead
can sit idle waiting for a wake that needs a manual nudge — fine when you are
watching the session, a stall risk when you walk away.

### The second channel: a job finishing into an empty room

A backgrounded job — a long test run, a build, a gate — completes without
sending anyone a message. **This path never touches teammate delivery, so tmux
mode changes nothing about it.** The lead is instructed to notify you when a
watched job finishes and to tell you the deadline by which you should expect a
result, so that silence past that time is itself the signal to come back.

If you walk away from a run waiting on a long job, note the deadline the lead
gave you. **A run that is quiet past its stated deadline is the case to check on,
whichever teammate mode you are in.**

You can also set it permanently in `~/.claude/settings.json`:

```json
{ "teammateMode": "tmux" }
```

> **In tmux mode, set the task-tools value too.** A teammate in its own split
> pane is a separate Claude Code process, so the tools it gets follow its own
> model. A lead can hold the task tools while a teammate does not. The `env`
> block that restores them sits in the same `~/.claude/settings.json` shown
> above, which each Claude Code process reads at start, so one entry is
> sufficient for the lead and the teammate. See
> [If specialist agents will not spawn](https://github.com/v4lheru/repacted-plugin#if-specialist-agents-will-not-spawn).

## If you must stay on in-process mode

Keep a lightweight external heartbeat in another terminal so you periodically
return to nudge the lead. **This covers both channels, because it does not
depend on either one working** — it only reminds you to look:

```bash
while sleep 300; do printf '\a'; done   # bell every 5 min as a "check the session" cue
```

This does not fix delivery — it just reminds you to glance at the run. For
truly hands-off operation, prefer `--teammate-mode tmux`.
