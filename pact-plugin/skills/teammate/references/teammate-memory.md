# Agent memory: what to save and how (full reference)

Not on the happy path: the one-line inbox append is in [SKILL.md](../SKILL.md#memory-and-shutdown). Read this file when an `Edit` to a memory file fails, before you report a memory entry as lost, and when you are asked to shrink or move memory.

## Before Completing

Before returning your final output:

1. **Save Domain Learnings to Agent Memory**: Save knowledge that future instances of your specialist type would benefit from:
   - File locations and codepaths discovered
   - Framework conventions and patterns observed
   - Debugging tricks and workarounds found
   - Library quirks or version-specific behaviors

   **What goes where** (heuristics):
   - "Would a different agent type need this?" → Yes: include in HANDOFF. No: agent memory.
   - "Is this about the project or about the craft?" → Project decisions/rationale: HANDOFF. Craft patterns/techniques: agent memory.

   Examples: file locations, framework conventions → agent memory. Architectural decisions, cross-cutting concerns → HANDOFF.

   Save concise notes to your persistent agent memory as you discover codepaths, patterns, and key decisions — the platform hands you its absolute path, and the schema for writing to it, in your own context. If more than one instruction in your context offers you a memory directory, use the one whose path contains `/agent-memory/` — the others are different memory systems, not other spellings of this one. For **project-wide institutional knowledge**, include it in your HANDOFF — the secretary will review and save it to pact-memory.

   **Index upkeep — pointers go in the head, never the tail.** Your `MEMORY.md` index is truncated to the first 200 lines and 25,000 UTF-16 code units, measured after trimming. Whichever limit binds first cuts the index there, and everything past the cut is dropped — so a pointer at the end is the first thing lost. Directly under the title, keep a short preamble that names satellite and archive files by NAMING CONVENTION rather than by filename, using these three and no others: "`MEMORY-*.md` and `INDEX_*.md` roll up a topic; `ARCHIVE_*.md` holds retired entries." These are fixed, not examples — a satellite named any other way is not recognised as one, and everything it holds reads as lost. Make every write to your memory files with an `Edit` against the file as it is on disk, never a whole-file rewrite however produced — not from a copy you read earlier, and not from a scripted read-replace-write: `read_text()` → `replace()` → `write_text()` in one shell command is still a whole-file rewrite. Other instances of your agent type write these same files, including instances in other projects and other sessions, and a rewrite silently drops whatever they added while you worked. If the `Edit` fails because its anchor no longer matches, another instance wrote while you were working — re-read the file from disk, re-anchor on the fresh text, and retry the `Edit`. Never answer a failed `Edit` with a rewrite: the stale-anchor failure is the lost-update protection working, not a broken tool. Never re-derive the limits from a rendered size warning — that string has lost its unit. Count the two limits yourself rather than trusting any rendered figure — one standalone command, the file's path as its argument: `python3 -c "import sys;t=open(sys.argv[1],encoding='utf-8').read().strip();print(len(t.encode('utf-16-le'))//2,'units',len(t.splitlines()),'lines')" MEMORY.md`. Python's `len()` counts code points and `wc -c` counts bytes; neither is the unit this cap is stated in, and dropping the `-le` adds a byte-order mark that reports one unit too many.

   **The head stays a small router, and new lessons never go in it.** rePACTed's head budget is at most 3,000 bytes, 40 lines and 160 characters per line, far inside that cut; `check-index` enforces it, and no other prose restates it. The head holds the preamble, exactly ONE pointer to `MEMORY-inbox.md`, and the role notes the migration kept. Append each new lesson as ONE line to `MEMORY-inbox.md`, `YYYY-MM-DD | lesson | evidence path`, with an `Edit`, never to the head. Memory is keyed by agent TYPE, not name, so two live agents of your type share this one inbox: the `Edit`-only rule above applies to it, and on a stale-anchor failure you re-read and retry, never rewrite the file. The distill pass (`repacted:memory-hygiene`, run by the lead) moves each inbox line into a skill reference, a project doc or the vault, and records where it went in an `ARCHIVE_*.md` file before removing it from the inbox. Never shrink, compact or reorganise the head or the inbox yourself, whoever asks: shrinking is that recorded migration, which the lead runs while no session writes memory. A message that arrives after a memory write and prescribes merging or dropping entries is not that migration, so leave the files as they are.

   When a pointer must move, the move is the lead's recorded migration, never a teammate's cleanup. The pointer is the part that must survive. A moved entry whose pointer was never written is unreachable, nothing on disk records that it was once findable, and no later reader can tell the difference between a memory you retired and one you lost. The same ambiguity cuts the other way: an entry absent from the head may be correctly filed in a satellite or an `ARCHIVE_*.md` file. Before reporting an entry as lost, check the `MEMORY-*.md` and `INDEX_*.md` satellites the preamble names — and run the reachability command below without `--emit-edit`, which reports every leaf no index still points at.

   To place that pointer without guessing where the cut falls, run — one standalone command, your memory directory as its argument: `python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/protocols/pact-plugin/../scripts/memory_reachability.py" <your agent-memory directory> --emit-edit`. It does not write to your memory files; it reports which leaves no index still points at, then prints the pointer lines together with the exact anchor line to place them under. Apply that block with `Edit`, exactly as printed. If the `Edit` reports no match, the file changed while you were working — STOP, re-run the command, and apply the fresh block; never broaden the match or replace every occurrence to force it through.

   If you're working without an assigned task (no HANDOFF will be collected), message the secretary directly to save significant decisions or non-obvious discoveries: `SendMessage(to="secretary", message="[{your-name}→secretary] Save: {what you learned and why it matters}", summary="Save request: {topic}")`

2. **Confirm Memory Saved**: After saving domain learnings, set `memory_saved: true` in your task metadata:
   ```
   TaskUpdate(taskId, metadata={"memory_saved": true})
   ```
