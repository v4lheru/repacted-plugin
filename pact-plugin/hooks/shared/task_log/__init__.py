"""The write side of the per-project task log (D10 PROJECT-TASKS, package P1).

Location: pact-plugin/hooks/shared/task_log/. Design: the design notes
the design notes sections 3, 4, 6 and 9. `write` holds the locked append and every
write-time rule; `evidence` verifies evidence with git plumbing; `bridge` carries platform task
acceptance into the log. The read side is hooks/shared/project_tasks.py. Used by the hooks
(amendment_gate, evidence_gate, session_ledger) and by bin/repact-task with its bin/_tasks
importers, views, atlas and registry. Nothing is imported here: a caller names the submodule.
"""

from __future__ import annotations
