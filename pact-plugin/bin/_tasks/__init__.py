"""The command-line side of the per-project task log (D10 PROJECT-TASKS).

Location: pact-plugin/bin/_tasks/. Design: the design notes
sections 3, 4, 6 and 9. `importers`, `views`, `atlas` and `registry` serve bin/repact-task. The
write side (the locked append, evidence, the acceptance bridge) is hooks/shared/task_log/, which
the hooks import too; the read side is hooks/shared/project_tasks.py.
"""
