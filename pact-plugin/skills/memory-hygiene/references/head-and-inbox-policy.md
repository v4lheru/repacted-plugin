# Head and inbox policy

Context: what an agent-memory head may hold, where new lessons go instead, how to edit either
file without losing another instance's write, and how the one-off migration fits the rule that
a teammate never shrinks its own index.

## Canonical rules
1. The head is a router. It holds its title, PACT's preamble line (byte-for-byte), the four router lines, and KEEP pointers under `## Role notes`. Nothing else.
2. The size limits are the head budget in the index-upkeep rule of `teammate-memory`. Do not restate the figures anywhere; `check-index` carries them as constants.
3. No emphasis glyphs (star, warning sign, siren), no dated "added" or "moved" notes, no project pointers, no roll-up paragraphs in a head. Once every line is starred, the stars rank nothing.
4. New lesson: ONE line appended to the inbox file (`MEMORY-inbox.md`) as `YYYY-MM-DD | lesson | evidence path`. Never append a lesson to the head, whatever older text says.
5. The evidence path is mandatory: a lesson recorded without where it came from reads as universal forever.
6. Edit the head and the inbox with Edit against the file as it is on disk. Never read, replace and write the whole file in one script: other instances of your agent type write the same files from other sessions, and an append inside your window is lost silently.
7. If an Edit fails to match text you are sure you wrote, re-read and re-anchor. A stale-anchor failure is the lost-update protection working, not a broken tool. Then suspect truncation before a concurrent writer.
8. Keep every index line short with the sharp half first, and read it back after writing: a line once came back cut mid-hook with an ellipsis, silently, and the half that survived was the link.
9. When a script must edit an index: parse each link with a bracket-anchored regex, never split on " — " (an em-dash inside a title ate a link target while the line count stayed identical); compare (title, target) pairs as sets in BOTH directions; guard the read-modify-write with a sha. A count check is not a content check.
10. Run `check-index <dir>` before and after any head edit, and fix every violation it prints. Exit 3 means nothing was checked; it is not a pass.
11. A pointer that is the only route to a leaf is the leaf: a leaf with no pointer is unreachable, so dropping the line deletes the memory in every way that matters.
12. A teammate never compacts, shrinks or reorganises its own index, and refuses the harness's curation nudge while the index rule says so. Shrinking is a recorded migration: every entry gets a recorded destination, the lead (or a script the lead runs) does it, and no session is writing memory at the time.
13. A file written by concurrent peers by construction (the harvest ledger, a registry) is append-only with identity: one short line per pass naming who wrote it, one `write()` under `O_APPEND`. Never prune it mid-file, even when a documented procedure says to.
14. Two scopes, two carriers: agent memory travels with the agent type across projects and is invisible to other agents; a project's `docs/agent-notes/` is read by whoever works on that project next. A fact that must bind both goes in both.
15. The agent definition routes (its "Skills for this role" block), the head points, the inbox holds.

## Detail

**What a head looks like.** The title (`# <agent-type> memory`), a blank line, PACT's preamble
naming satellites by convention, a blank line, then four fixed lines: skills are listed in the
agent definition and project facts live in `docs/agent-notes/`; the inbox pointer; "do not add
lessons to this file"; and the pointer to `ARCHIVE_migrated-to-skills.md`, the plain-text table of
every migrated file and its new home. Dirs with KEEP leaves add `## Role notes` and one list item
per leaf: a short title linking the file, then a hook of a few words.

**Why the inbox and not the head.** PACT 4.8.1's text told agents to append below the preamble,
which guaranteed regrowth: one head grew to a dozen roll-up paragraphs, most of them annotations of
what had been moved. Until the fork restates the index rule, agents may still append to a head; the
distill pass drains those lines too (`references/distill-pass.md`).

**Why a script rewrite is forbidden even when it works.** A rewrite that asserts the old line is
unique before writing feels anchored and is not: uniqueness says nothing about another writer
inside the window. The rule is about the window, not the outcome; a clean result does not make the
method safe.

## Examples
- A backend head grew past the platform's load cut; entries at the end stopped loading while the
  head still looked fine.
- An agent compacting its own index split entries on the separator; one title held an em-dash, a
  link target vanished, and the line count did not move.
- A secretary updated its section of the shared harvest ledger in place; another project's section
  was spliced into it, and colliding task ids marked live HANDOFFs as already processed.
