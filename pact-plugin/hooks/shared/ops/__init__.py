"""
Location: pact-plugin/hooks/shared/ops/__init__.py
Summary: The operation CLI's core: the five
commands that do the lead's and the coder's task bookkeeping, `repact dispatch`, `submit`,
`accept`, `reject` and `amend`. Each checks its input, makes ONE task write in a fixed order
and is safe to rerun after a crash (records.py). This module holds the two types every op
returns or raises.
Used by: bin/_repact_ops.py (the CLI) and hooks/subagent_stop.py (the backstop
calls submit). Lives under hooks/shared so both may import it (tests/contract/test_import_layers).

Exit codes the CLI maps: 0 done, 1 accept turned facts into a rejection, 2 refused (nothing
written), 3 stored with warnings (submit's evidence preview).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class OpError(Exception):
    """A refusal: nothing was written. `rule` is a shared/rules.py id; `problems` are every
    reason found, one line each, so the caller fixes them all in one pass."""

    def __init__(self, rule: str, problems: list, exit_code: int = 2):
        super().__init__("; ".join(problems))
        self.rule = rule
        self.problems = [str(problem) for problem in problems]
        self.exit_code = exit_code


@dataclass
class Result:
    """What an op prints (at most 6 lines) and its exit code."""

    lines: list = field(default_factory=list)
    exit_code: int = 0
    task_id: str = ""
    whole: bool = False     # print every line (the receipt), not the first six
