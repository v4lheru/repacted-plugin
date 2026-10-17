# Protocol: self-refine (skill-specific part)

Context: field notes decay fastest of all skills, because the fork fixes the bugs they work around
and Claude Code changes the platform under them. Run this at the end of a session that used a note.

Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.

## Mission (specific)
Keep every note true for the installed repacted and Claude Code versions, and record each change.

## Skill-specific steps
- List the notes you used this session and whether each fix worked as written.
- For each note whose stamp says "until Mn": check whether milestone Mn is in the installed
   repacted (its CHANGELOG and tag). If it is, re-run the note's discriminating check once; if the
   symptom is gone, delete the line.
- For a "re-probe" note that failed, re-probe the platform behaviour and either correct the line
   with a new stamp or delete it. Never leave a line you know is false.
- For a new workaround you had to derive, add ONE canonical line to the matching reference, with
   its stamp and, for a fork bug, the backlog milestone that will remove it.
- Re-run a skill validator on this skill and the scrub check on the touched files, then commit
   with a one-sentence message as a patch bump.

## Skill-specific guidelines
- Pattern: delete beats annotate. A fixed bug's workaround left in place is a wrong instruction.
- Pattern: when a live session hits one of its events, run the matching rows of
  [live-reprobe-checklist.md](live-reprobe-checklist.md); they cannot be probed offline.
- Anti-pattern: stating a size figure for the agent-memory index here; point at the index-upkeep
  rule in teammate-memory instead.
- Anti-pattern: growing the router. New detail goes in the reference for its symptom class.
