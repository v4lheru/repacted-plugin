# Long jobs, halts and loops

Context: the moments where the lead must stop, size or own something: a HALT or flagged risk, a
review loop that keeps finding real defects, a job that runs longer than a turn, and an irreversible
sequence more than one agent could touch.

## Canonical rules
1. Surface a HALT to the user at once, but size it before stopping everyone: measure what is NEW and where it went (exact-match scan, counts only, with a control).
2. Treat an agent's self-report of its own leak as an overestimate until measured; keep unrelated lanes running, and present a sized picture with a "resume and clean up" default.
3. A review loop that keeps finding real defects will not stop itself; only the lead can call it. Declare a finding budget at dispatch ("three rounds, then we log the rest").
4. On the second finding of one defect CLASS, re-brief for the class: name it, ask for a structural guarantee and a test that fails on the next instance. When it appears a third time, stop fixing instances and log the class; a fix for a composition defect can move the seam instead of closing it.
5. Sort findings early into "blocks the commit", "blocks the live flag" and "follow-up", and enforce the buckets.
6. Watch the team's aggregate spend, not only your own context; do not narrate cost to the user, do cap loops (the resolved tension).
7. Launch jobs longer than a few minutes from the LEAD session with a detached launcher and an on-disk ledger; a teammate's background jobs die at its turn boundary.
8. A detached job never notifies anyone on exit: watch its ledger or log as the health signal, and do not kill it on a quiet phase without telling a stall from a no-output phase.
9. Heavy local compute (mutation runs, repeated suites, benchmarks): by default at most 2 workers and `nice -n 19`.
10. An irreversible step needs a single named owner confirmed by BOTH parties immediately before it; never infer ownership from the last message each holds.
11. Never hand over an irreversible sequence mid-flight; if you must, get acknowledgement from both the new and the old owner before either acts. A retraction can cross too.
12. Before declaring an agent unresponsive, ask it; silence during a hard step is usually care.
13. Classify each step by what a double application costs before worrying about coordination, and put the human-gated step where a double or premature application costs most.
14. Agents share one git and API identity, so the platform cannot say which agent acted; the actor must record it at the time.
15. At `/repacted:wrap-up`, run the distill pass: `distill-inbox list`, `plan [--suggest]`, edit the skill references yourself, `apply`, then `check-index --all`.

## Detail

**Launcher shape (mechanics in repacted:shell-tooling-traps).** A small Python launcher starts the
real job with `subprocess.Popen(..., start_new_session=True)`, writes a pidfile and exits; the job
appends an exit marker and keeps a resumable, fsync'd ledger so a kill costs nothing and a re-run
skips done work. `nohup` dies within seconds of the launching call returning, and
`run_in_background` can be reaped after minutes. Two jobs sharing one API token halve each other's
throughput under per-token rate limits; serialise or accept it.

**Calling the stop.** Every individual dispatch in a runaway loop looks justified, which is why the
lead is the one least likely to stop it. Around the third instance of a class, the right move is to
log the rest as a follow-up task. The token budget is a constraint the lead is accountable
for, like any other boundary.

**Dual drivers.** When a handover and its retraction cross, both agents act in good faith on the
latest message each holds. Idempotent steps forgive that (a second merge attempt fails harmlessly);
irreversible ones (an install, a deploy on the wrong pipeline) do not. "It turned out fine" and "the
process worked" are different claims; record a dual-driver execution in the release record.

**Lazy secretary.** Nothing in this skill assumes a secretary exists at session start. The session
briefing is the runtime's hook output in your SessionStart context. The distill pass is your own
step; "query the secretary" is fine wherever history is needed, because the query spawns it. The
distill pass internals are in repacted:memory-hygiene `distill-pass.md`.

## Examples
- A reviewer reported printing `.env` values and self-declared a HALT; the lead stopped eleven
  agents before a scan showed nothing new had leaked. The user called it the wrong focus.
- A seven-agent review loop found the same defect class in a fourth location and spent a large share of
  a weekly token budget in one session; the user had to stop it.
- A handover and its retraction crossed; two agents each tried to merge the same MR, and only timing
  kept the second from pressing the deploy.
- A one-hour backfill died repeatedly under `nohup` and `run_in_background`; a detached launcher from
  the lead session ran it to completion with zero errors.
