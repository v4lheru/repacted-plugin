"""LEAN-DEFAULTS (#254): the token-lean rules stay where the lead and the teammates read them.

One pin for the behaviour change: small tasks are single-task dispatch and no measurement panels
unless the user asks. The tests-lean rule (one regression test per bug, adversarial tests for
high-risk code) is read from every carrier by tests/contract/test_rule_carriers_agree.py
(`tests_lean`, with TESTS_LEAN_CARRIERS as the files that must state it), not pinned here.
"""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent


def test_the_lean_defaults_are_in_the_persona_dispatch_teammate_tester_and_core():
    texts = {name: (PLUGIN / name).read_text(encoding="utf-8") for name in (
        "agents/pact-orchestrator.md", "reference/dispatch/teachback-gated-dispatch.md",
        "skills/teammate/SKILL.md", "agents/pact-test-engineer.md")}
    assert "complexity total is 6 or less is single-task dispatch" in texts["agents/pact-orchestrator.md"]
    assert "No blind-label or judge panels" in texts["agents/pact-orchestrator.md"]
    assert "Small tasks (complexity total 6 or less" in texts["reference/dispatch/teachback-gated-dispatch.md"]
    assert "No mutation or sabotage rows" in texts["skills/teammate/SKILL.md"]
    assert "A FRESH agent per task; at most 2 agents at once" in texts["agents/pact-orchestrator.md"]


def _text(name: str) -> str:
    return (PLUGIN / name).read_text(encoding="utf-8")


def test_review_is_one_reviewer_by_default_in_every_carrier():
    review = _text("commands/peer-review.md")
    assert "Dispatch **ONE reviewer** by default" in review
    assert "only when the user asks or the risk score is 3 or more" in review
    assert "never by the reviewer" in review and "**MANDATORY, with several reviewers too**" in review
    for name in ("commands/peer-review.md", "protocols/lead-reference.md",
                 "reference/peer-review/review-detail.md"):
        assert "at least 3" not in _text(name), name
    assert "ONE reviewer by default" in _text("protocols/lead-reference.md")


def test_the_merge_step_names_branch_deletion_and_the_checkout_binding():
    step = _text("commands/peer-review.md").split("Merge Authorization", 1)[1]
    assert "delete the source branch" in step and "check those two by hand before asking" in step
    notes = _text("skills/pact-field-notes/references/hooks-and-harness.md")
    assert "git and gh verbs only" not in notes and "does not see `glab mr merge`" not in notes
    bound = "The approval is bound to the checkout the approved command runs in"
    for text in (step, notes):
        assert bound in text and "does not break the match" in text and "WHOLE Bash command" not in text


def test_the_ask_rules_have_a_branch_for_a_user_who_is_not_to_be_asked():
    gate = _text("reference/peer-review/minor-future-gate.md")
    assert "**No-ask branch**" in gate and gate.index("**No-ask branch**") < gate.index("**Step A — Initial Gate Question**")
    step5 = _text("commands/bootstrap.md").split("## Step 5", 1)[1]
    assert "ask ONCE with `AskUserQuestion`, never in plain text" in step5
    assert "do NOT init, do not ask again" in step5


def test_a_post_dispatch_directive_is_an_amendment_in_the_playbook_too():
    playbook = _text("skills/pact-lead-playbook/SKILL.md")
    assert "always an `amend_<n>` key" in playbook and "message only an\n   idle owner" in playbook
    assert "AND send the message" not in playbook
    assert "BOTH carriers" not in _text("skills/pact-lead-playbook/references/rulings-and-relays.md")
    assert "needs in order to decide" in playbook
    assert "- load before the first brief;" in _text("agents/pact-orchestrator.md")


def test_user_facing_copy_and_an_unviewable_ui_each_have_a_rule():
    reader = "names the reader, the register and a length cap"
    assert reader in _text("skills/pact-lead-playbook/references/dispatch-briefs-and-premises.md")
    assert reader in _text("reference/dispatch/teachback-gated-dispatch.md")
    copy_check = "read each new user-facing string as the reader the brief names"
    assert copy_check in _text("agents/pact-test-engineer.md") and copy_check in _text("commands/peer-review.md")
    proof = _text("skills/verification-discipline/references/vd-f-deploy-and-write-proof.md")
    assert "- **F.13** When no agent can view the deployed UI" in proof
    assert '"deployed, UI not checked by anyone", never "live and working"' in proof
    assert "one short line is the whole reply" in _text("agents/pact-orchestrator.md")


def test_a_job_the_lead_launched_needs_no_cron_beside_it():
    wait = _text("protocols/lead-reference.md").split("## Wait Instruments", 1)[1]
    assert "wakes you with its own notice, and no hook asks for a cron beside it" in wait
    assert "(a job you did not launch, a wait on a person or a teammate), schedule a recurring `CronCreate`" in wait
