"""REGRESSION (acme e1baab9f): the lead's retrospective and difficulty rating sat
unread in the secretary's inbox while it finalized the consolidation, because a
message lands only at the recipient's next idle boundary. Wrap-up now composes both
BEFORE the consolidation task exists and puts them in its description; nothing is
sent to the secretary between dispatch and finalization.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAP_UP = (ROOT / "commands" / "wrap-up.md").read_text(encoding="utf-8")


def section(text, heading):
    start = text.index(heading)
    nxt = re.search(r"(?m)^## ", text[start + len(heading):])
    return text[start:start + len(heading) + (nxt.start() if nxt else len(text))]


def test_the_consolidation_description_carries_retrospective_and_rating():
    step1 = section(WRAP_UP, "## 1. Memory Consolidation (Pass 2)")
    create = step1[step1.index("TaskCreate(subject=\"secretary: session consolidation"):]
    description = create[:create.index("TaskUpdate(taskId, owner=\"secretary\")")]
    assert "ORCHESTRATION RETROSPECTIVE:" in description
    assert "DIFFICULTY RATING:" in description
    assert step1.index("Compose the payload BEFORE") < step1.index("TaskCreate(")


def test_nothing_is_sent_to_the_secretary_between_dispatch_and_finalization():
    for heading in ("## 1. Memory Consolidation (Pass 2)", "## 2. Documentation Sync",
                    "## 3. Workspace Cleanup",
                    "## 4. Orchestration Retrospective (Second-Order Cybernetics)"):
        assert 'SendMessage(to="secretary"' not in section(WRAP_UP, heading), heading
