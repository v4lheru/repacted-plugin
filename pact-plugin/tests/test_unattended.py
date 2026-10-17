"""
Location: pact-plugin/tests/test_unattended.py
Summary: Unattended mode: the switch (hooks/shared/unattended.py read_mode), the
         classifier of AskUserQuestion calls, the gate (hooks/unattended_gate.py), the
         command (bin/repact-unattended) and the session-start block.

The classifier sits on the approval channel, so its rows are hostile on purpose: an act
hidden by case, by a look-alike or zero-width character, by spacing, or named only in an
option; odd shapes; a flag that cannot be read. Every such row must answer always-ask, or
(for the flag) OFF. Hook and command runs use a temporary config root, project directory
and session directory.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import session_init
import shared.session_init_lead as session_init_lead
from shared import unattended as u
from helpers import effective_hooks_config  # noqa: E402

PLUGIN = Path(__file__).resolve().parent.parent
HOOK = PLUGIN / "hooks" / "unattended_gate.py"
COMMAND = PLUGIN / "bin" / "repact-unattended"
SESSION = "sess-unattended"


def question(text="Which file layout fits the module?", labels=("One file (Recommended)", "Two files"),
             descriptions=None, **extra):
    options = [{"label": label, "description": (descriptions or {}).get(label, "an option")}
               for label in labels]
    return {"question": text, "header": "Layout", "options": options, **extra}


YES_NO = ("Yes (Recommended)", "No")

# --- the classifier ---------------------------------------------------------

ALWAYS_ASK_ROWS = {
    # the class, by its plain words
    "merge": (question("Merge the branch now?", YES_NO), "act_named"),
    "push to main": (question("Push to main?", YES_NO), "act_named"),
    "force-push": (question("Force-push the rebased branch?", YES_NO), "act_named"),
    "branch delete": (question("Delete the old branch?", YES_NO), "act_named"),
    "close the PR": (question("Close the PR?", YES_NO), "act_named"),
    "deploy": (question("Deploy to the cluster?", YES_NO), "act_named"),
    "n8n": (question("Edit the n8n workflow in place?", YES_NO), "act_named"),
    "transfer": (question("Transfer the workflow to the team?", YES_NO), "act_named"),
    "drop": (question("Drop the table first?", YES_NO), "act_named"),
    "publish": (question("Publish the page?", YES_NO), "act_named"),
    "ship": (question("Ship it?", YES_NO), "act_named"),
    "release": (question("Cut the release?", YES_NO), "act_named"),
    "go live": (question("Go live tonight?", YES_NO), "act_named"),
    "roll out": (question("Roll out to everyone?", YES_NO), "act_named"),
    "production": (question("Run it against production?", YES_NO), "act_named"),
    "remove": (question("Remove the fixture rows?", YES_NO), "act_named"),
    "purge": (question("Purge the cache?", YES_NO), "act_named"),
    "wipe": (question("Wipe the scratch volume?", YES_NO), "act_named"),
    "send": (question("Send the summary?", YES_NO), "act_named"),
    "post": (question("Post it to the channel?", YES_NO), "act_named"),
    # 1.4.0 review finding 2: the vendors' own verbs and the targets of an outward act
    "accept MR into main": (question("Accept MR !42 into main now that the pipeline is green?", YES_NO), "act_named"),
    "glab mr accept": (question("Run glab mr accept 42?", YES_NO), "act_named"),
    "land PR": (question("Land PR #42 on main?", YES_NO), "act_named"),
    "land with no target": (question("Land it once the checks pass?", YES_NO), "act_named"),
    "squash into main": (question("Squash the feature branch into main?", YES_NO), "act_named"),
    "squash and fold": (question("Squash and fold it in?", YES_NO), "act_named"),
    "apply the terraform plan": (question("Apply the Terraform plan to the production project?", YES_NO), "act_named"),
    "apply a plan, no tool named": (question("Apply the plan now?", YES_NO), "act_named"),
    "helm upgrade": (question("Run helm upgrade for the dashboard on the cluster?", YES_NO), "act_named"),
    "kubectl apply": (question("Run kubectl apply for the new config map?", YES_NO), "act_named"),
    "prune remote branches": (question("Prune the stale remote branches?", YES_NO), "act_named"),
    "git branch -D": (question("Run git branch -D feature/x?", YES_NO), "act_named"),
    "git branch -d": (question("Run git branch -d feature/x?", YES_NO), "act_named"),
    "abandon MR": (question("Abandon MR !12, it is superseded?", YES_NO), "act_named"),
    "abandon, no MR named": (question("Abandon the superseded request?", YES_NO), "act_named"),
    "archive workflow": (question("Archive the old workflow on the production instance?", YES_NO), "act_named"),
    "move workflow": (question("Move the workflow to the support project?", YES_NO), "act_named"),
    "update origin/main": (question("Update origin/main with the rebased branch?", YES_NO), "act_named"),
    "promote": (question("Promote the build to staging?", YES_NO), "act_named"),
    "git clean": (question("Run git clean -fdx in the repo?", YES_NO), "act_named"),
    "kill": (question("Kill the running pipeline?", YES_NO), "act_named"),
    "rotate": (question("Rotate the Slack bot token?", YES_NO), "act_named"),
    "pull request": (question("Open a pull request for it?", YES_NO), "act_named"),
    "upstream": (question("Rebase it onto upstream?", YES_NO), "act_named"),
    # 1.4.0 review verification: force push in other words, and outward acts
    "update the remote": (question("Update the remote with the rebased feature branch?", YES_NO), "act_named"),
    "rewrite shared history": (question("Rewrite history on the shared branch?", YES_NO), "act_named"),
    "sync the shared remote": (
        question("Sync the shared remote branch with my rebased history?", YES_NO), "act_named"),
    "share a doc": (question("Share the Google doc with the client?", YES_NO), "act_named"),
    "make a repo public": (question("Make the repository public?", YES_NO), "act_named"),
    "grant access": (question("Grant the contractor write access?", YES_NO), "act_named"),
    "refund": (question("Refund the customer?", YES_NO), "act_named"),
    "cancel a subscription": (question("Cancel the customer's subscription?", YES_NO), "act_named"),
    "cancel, no customer named": (question("Cancel the subscription?", YES_NO), "act_named"),
    "reply to a customer": (question("Reply to the customer thread in the help desk?", YES_NO), "act_named"),
    "create a ticket": (question("Create the Jira ticket for the client?", YES_NO), "act_named"),
    "create a ticket, no tool named": (question("Create a ticket for the bug?", YES_NO), "act_named"),
    "trigger a pipeline": (question("Trigger the GitLab pipeline?", YES_NO), "act_named"),
    "trigger, no forge named": (question("Trigger the pipeline again?", YES_NO), "act_named"),
    "replace a secret": (question("Replace the Secret Manager value?", YES_NO), "act_named"),
    "restart a pod": (question("Restart the dashboard pod on the cluster?", YES_NO), "act_named"),
    "scale to zero": (question("Scale the service to zero?", YES_NO), "act_named"),
    "migration on a shared db": (
        question("Run alembic upgrade head on the shared database?", YES_NO), "act_named"),
    "upgrade head, no tool named": (question("Upgrade the schema to head?", YES_NO), "act_named"),
    # 1.4.0 review re-probe (#55): messaging, shared docs, data writes, live services,
    # access, money, history on a server, the tracker and the calendar
    "activate a workflow": (question("Activate the workflow on the shared instance?", YES_NO), "act_named"),
    "disable a workflow": (
        question("Disable the scheduled workflow on the shared instance?", YES_NO), "act_named"),
    "amend on the server": (question("Amend the last commit that is already on the server?", YES_NO), "act_named"),
    "repo visibility": (question("Change the repo visibility to internal?", YES_NO), "act_named"),
    "add a collaborator": (question("Add the client as a collaborator on the repo?", YES_NO), "act_named"),
    "access to a bucket": (question("Give the vendor access to the bucket?", YES_NO), "act_named"),
    "notify by slack": (question("Notify the client by Slack?", YES_NO), "act_named"),
    "message in slack": (question("Message the account manager in Slack?", YES_NO), "act_named"),
    "client's google doc": (question("Write the reply in the client's Google Doc?", YES_NO), "act_named"),
    "edit a confluence page": (question("Edit the shared Confluence page?", YES_NO), "act_named"),
    "update a confluence page": (question("Update the Confluence page for the support team?", YES_NO), "act_named"),
    "hosting secret": (question("Set the hosting platform secret for the API key?", YES_NO), "act_named"),
    "charge a card": (question("Charge the card for the overage?", YES_NO), "act_named"),
    "credit note": (question("Issue the credit note to the client?", YES_NO), "act_named"),
    "bigquery backfill": (question("Run the backfill against the BigQuery warehouse?", YES_NO), "act_named"),
    "bigquery reload": (question("Reload the reporting table in BigQuery?", YES_NO), "act_named"),
    "postgres update": (question("Run the UPDATE on the users table in Postgres?", YES_NO), "act_named"),
    "stop a service": (question("Stop the Cloud Run service?", YES_NO), "act_named"),
    "tracker items": (question("Mark the tracker items as done for the whole team?", YES_NO), "act_named"),
    "decline a meeting": (question("Decline the meeting for the user?", YES_NO), "act_named"),
    "revert and update the server": (
        question("Revert the commit on the default branch and update the server?", YES_NO), "act_named"),
    "send an invoice": (question("Invoice the client for March?", YES_NO), "act_named"),
    # the same, odd inputs
    "upper-case refund": (question("REFUND the Customer?", YES_NO), "act_named"),
    "hyphen split rewrite": (question("Re-write the history of the branch?", YES_NO), "act_named"),
    "punctuation around public": (question("Make the repo... public?!", YES_NO), "act_named"),
    "curly apostrophe": (question("Cancel the customer’s subscription?", YES_NO), "act_named"),
    "curly quotes": (question("Share “the Q3 doc” with them?", YES_NO), "act_named"),
    "pair only in a label": (
        question("Next step?", ("Rebase the shared branch (Recommended)", "Wait")), "act_named"),
    # the same, hidden
    "hyphen split push, no target": (question("pu-sh the branch?", YES_NO), "act_named"),
    "upper-case accept": (question("ACCEPT the MR?", YES_NO), "act_named"),
    "extra spaces and flags": (question("Run  git   branch   -D   -q  feature/x?", YES_NO), "act_named"),
    "act inside bash -c": (question("Run bash -c 'glab mr accept 42'?", YES_NO), "act_named"),
    "fullwidth main": (question("Rebase onto \uff4d\uff41\uff49\uff4e?", YES_NO), "act_named"),
    "cyrillic a in master": (question("Update m\u0430ster?", YES_NO), "non_ascii"),
    "target only in a label": (question("Next step?", ("Land on main (Recommended)", "Wait")), "act_named"),
    # hidden acts
    "mixed case": (question("MeRgE the branch?", YES_NO), "act_named"),
    "zero-width in merge": (question("Me\u200brge the branch?", YES_NO), "act_named"),
    "zero-width joiner in delete": (question("Del\u200dete the rows?", YES_NO), "act_named"),
    "soft hyphen in deploy": (question("De\u00adploy it?", YES_NO), "act_named"),
    "word joiner in deploy": (question("Dep\u2060loy it?", YES_NO), "act_named"),
    "fullwidth delete": (question("\uff44\uff45\uff4c\uff45\uff54\uff45 the rows?", YES_NO), "act_named"),
    "combining mark in merge": (question("me\u0301rge the branch?", YES_NO), "act_named"),
    "cyrillic e in delete": (question("d\u0435lete the rows?", YES_NO), "non_ascii"),
    "greek o in deploy": (question("depl\u03bfy it?", YES_NO), "non_ascii"),
    "cyrillic whole word": (question("\u0421\u0434\u0435\u043b\u0430\u0442\u044c?", YES_NO), "non_ascii"),
    "symbol letter": (question("\U0001F1F2erge the branch?", YES_NO), "non_ascii"),
    "letter spacing": (question("m e r g e the branch?", YES_NO), "act_named"),
    "dotted spacing": (question("d.e.l.e.t.e the rows?", YES_NO), "act_named"),
    "hyphen split": (question("de-ploy it?", YES_NO), "act_named"),
    "newline split": (question("de\nploy it?", YES_NO), "act_named"),
    "control char split": (question("mer\x00ge the branch?", YES_NO), "act_named"),
    "digit for letter": (question("depl0y it and de1ete the rest?", YES_NO), "act_named"),
    "camel case": (question("Call autoDeploy afterwards?", YES_NO), "act_named"),
    "act only in a label": (question("Next step?", ("Push to main (Recommended)", "Wait")), "act_named"),
    "act only in a description": (
        question("Next step?", ("Go on (Recommended)", "Wait"),
                 descriptions={"Go on (Recommended)": "this deletes the old rows"}), "act_named"),
    "act only in the header": ({**question("Next step?", YES_NO), "header": "Deploy"}, "act_named"),
    "act only in an unknown field": ({**question("Next step?", YES_NO), "note": "then merge"}, "act_named"),
    "recommended on an always-ask question": (
        question("Merge the branch?", ("Merge (Recommended)", "Wait")), "act_named"),
    # no single recommended option
    "no recommended": (question(labels=("Two columns", "One column")), "no_recommended"),
    "two recommended": (question(labels=("Two (Recommended)", "One (recommended)")), "several_recommended"),
    "recommended only in a description": (
        question(labels=("Two", "One"), descriptions={"Two": "(Recommended)"}), "no_recommended"),
    # shapes
    "multiSelect": (question(multiSelect=True), "multi_select"),
    "multiSelect not a bool": (question(multiSelect="false"), "non_string"),
    "options empty": ({**question(), "options": []}, "options_missing"),
    "options missing": ({"question": "Which?", "header": "H"}, "options_missing"),
    "options not a list": ({**question(), "options": {"label": "A (Recommended)"}}, "options_missing"),
    "option not an object": ({**question(), "options": ["A (Recommended)", "B"]}, "non_string"),
    "question not a string": ({**question(), "question": 7}, "non_string"),
    "question empty": ({**question(), "question": "  "}, "non_string"),
    "question a list": ({**question(), "question": ["Which?"]}, "non_string"),
    "label not a string": ({**question(), "options": [{"label": None}, {"label": "B (Recommended)"}]}, "non_string"),
    "description not a string": (
        {**question(), "options": [{"label": "A (Recommended)", "description": 3.5}]}, "non_string"),
    "header not a string": ({**question(), "header": float("nan")}, "non_string"),
    "huge question": (question("Which layout? " + "x" * 200_000), "too_long"),
    "huge option list": (
        {**question(), "options": [{"label": f"Option {n}"} for n in range(500)]}, "too_long"),
    "not an object": ("Merge?", "not_a_question"),
    "null": (None, "not_a_question"),
}


@pytest.mark.parametrize("item, code", ALWAYS_ASK_ROWS.values(), ids=ALWAYS_ASK_ROWS.keys())
def test_a_question_in_doubt_is_always_ask(item, code):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_ALWAYS_ASK, code)


DEFAULT_ROWS = {
    "plain": question(),
    "typographic punctuation": question("Which name \u2014 \u201cthe first\u201d or the second?",
                                        ("First (Recommended)", "Second")),
    "accented vowel": question("Which name for the helper?", ("caf\u00e9_name (Recommended)", "other")),
    "upper-case mark": question(labels=("One file (RECOMMENDED)", "Two files")),
    "local refactor": question("Extract the parser into its own local module?", YES_NO),
    "test naming": question("Name the test after the behaviour or the function?", ("Behaviour (Recommended)", "Function")),
    # a code token in the option taken that shares a stem with an always-ask word
    "postgres is not post": question("Which name for the helper?", ("postgres_cache (Recommended)", "other")),
    "product is not prod": question("Which name for the field?", ("product_title (Recommended)", "other")),
    "acceptance is not accept": question("Which name for the test?", ("test_acceptance_rows (Recommended)", "other")),
    "landing is not land": question("Which folder for the module?", ("src/landing (Recommended)", "src/pages")),
    "domain is not main": question("Which file for the class?", ("domain_model.py (Recommended)", "models.py")),
    "process is not pr": question("Which name for the constant?", ("PROCESS_NAME (Recommended)", "other")),
}


@pytest.mark.parametrize("item", DEFAULT_ROWS.values(), ids=DEFAULT_ROWS.keys())
def test_a_plain_question_with_one_recommended_option_is_default(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_DEFAULT, "recommended")
    assert len(verdict.ref) == 12


# 1.4.0 defaulted these reversible questions; they name no local class or hold a word
# outside the local words, so 1.4.1 halts them (a false halt, by design)
FORMER_DEFAULT_ROWS = {
    "postgres is not post": question("Use postgres or sqlite for the cache?", ("postgres (Recommended)", "sqlite")),
    "product is not prod": question("Which product name goes in the title?", ("Alpha (Recommended)", "Beta")),
    "typographic punctuation": question("Plan A \u2014 or \u201cplan B\u201d?", ("A (Recommended)", "B")),
    "a possessive": question("At the user's end, which label?", ("Short (Recommended)", "Long")),
    "for certain": question("Which is right for certain cases?", ("First (Recommended)", "Second")),
    "accented vowel": question("Which caf\u00e9 layout?", ("Grid (Recommended)", "List")),
    "upper-case mark": question(labels=("Two columns (RECOMMENDED)", "One column")),
    "acceptance is not accept": question("Which acceptance criteria wording reads better?", ("Short (Recommended)", "Long")),
    "landing is not land": question("Which landing page layout?", ("Grid (Recommended)", "List")),
    "domain is not main": question("Keep the domain model or the DTO?", ("Domain (Recommended)", "DTO")),
    "apply without a plan": question("Apply the same rename to the tests?", YES_NO),
    "move without a workflow": question("Move the helper into the shared module?", YES_NO),
    "clean without git": question("Clean up the wording of the summary?", YES_NO),
    "upgrade a dependency": question("Upgrade the lint rule from warning to error?", YES_NO),
    "process is not pr": question("Which process name fits the log line?", ("Worker (Recommended)", "Job")),
    "local branch name": question("Call the local branch fix/parser?", YES_NO),
    "rebase a local branch": question("Rebase my local branch onto the feature branch?", YES_NO),
    "public method": question("Make the helper method public or keep it private?", ("Private (Recommended)", "Public")),
    "shared module": question("Keep the shared types in one file?", YES_NO),
    "restart a counter": question("Restart the retry counter at zero?", YES_NO),
    "run a local pipeline": question("Run the eval pipeline on the sample set?", YES_NO),
    # reversible coding questions that use the #55 words
    "http client": question("Wrap the HTTP client in a retry helper?", YES_NO),
    "server module": question("Should the server module export one handler or two?", ("One (Recommended)", "Two")),
    "workflow helper": question("Rename the workflow helper function in the test?", YES_NO),
    "access checks": question("Add access checks to the route handler?", YES_NO),
    "stop a loop": question("Stop the loop when the counter reaches the limit?", YES_NO),
    "css visibility": question("Toggle the panel visibility on click?", YES_NO),
    "credit an author": question("Credit the original author in the docstring?", YES_NO),
    "calendar widget": question("Use a calendar widget or a plain date input?", ("Widget (Recommended)", "Input")),
}


@pytest.mark.parametrize("item", FORMER_DEFAULT_ROWS.values(), ids=FORMER_DEFAULT_ROWS.keys())
def test_a_reversible_question_outside_the_local_class_halts(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) in {(u.CLASS_ALWAYS_ASK, "not_local"), (u.CLASS_ALWAYS_ASK, "no_local_class")}


def test_no_local_word_is_an_always_ask_word():
    assert [word for word in u.LOCAL_WORDS if u.names_act(word)] == []


# 1.4.1 inversion: only a question shown to be local and reversible takes the default.
# review9's #55 probe set (36 outward phrasings, 21 defaulted under 1.4.0) is not on disk;
# these 36 are written anew: chat and email, tickets, PRs and MRs, deploys, deletes,
# payments, account changes, and indirect phrasings.
OUTWARD_PROBES = (
    "Ping the client",
    # chat and email
    "Ping the client about the delay?", "Let them know the fix is in?",
    "Loop in the account team on the thread?", "Give the partner a heads-up by text?",
    "Reach out to the reseller today?", "Write back to the person who filed the report?",
    "Fire off the weekly digest to the list?", "CC the director on the follow-up?",
    # tickets, PRs and MRs
    "Raise this with their support desk?", "Log a bug in the tracker for the backend team?",
    "Put the change up for review on the forge?", "Mark the review as approved?",
    "Get the fix into the default branch tonight?", "Bring the fix onto the integration line?",
    # deploys
    "Ship it?", "Roll the new build onto the servers?", "Make the new version available to everyone?",
    "Flip the feature flag for all users?", "Point the DNS record at the new host?",
    "Swap traffic over to the green stack?",
    # deletes
    "Get rid of the old storage bucket?", "Tear down the staging stack?",
    "Clear out the old rows in the users table?", "Nuke the stale environments in the cloud account?",
    "Empty the trash on the team drive?",
    # payments
    "Settle the outstanding bill with the provider?", "Renew the annual licence now?",
    "Move the team to the paid plan?", "Approve the expense for the conference?",
    # account changes
    "Add the new hire to the org?", "Kick the intern out of the workspace?",
    "Change the owner of the project to the new lead?", "Hand the repo over to the other group?",
    "Turn off two-factor for the service user?",
    # indirect
    "Go ahead with the plan we discussed?", "Do the thing from yesterday's call?",
)
assert len(OUTWARD_PROBES) == 37


@pytest.mark.parametrize("text", OUTWARD_PROBES)
def test_an_outward_phrasing_without_a_listed_word_halts(text):
    for item in (question(text, YES_NO), question(text, ("Go ahead (Recommended)", "Wait"))):
        assert u.classify_question(item).cls == u.CLASS_ALWAYS_ASK


LOCAL_ROWS = {
    # naming
    "name a helper": question("Which name for the new helper function?",
                              ("parse_window (Recommended)", "window_parser"), header="Naming"),
    "name a test": question("Name the test after the behaviour or the function?",
                            ("Behaviour (Recommended)", "Function"), header="Naming"),
    "call a class": question("What should we call the new class?",
                             ("SessionReader (Recommended)", "ReaderSession"), header="Naming"),
    "naming style": question("Which naming style for the constants?",
                             ("UPPER_CASE (Recommended)", "camelCase"), header="Naming"),
    # test scope
    "which tests": question("Which tests should cover the parser change?",
                            ("Unit tests only (Recommended)", "Unit and integration tests"), header="Tests"),
    "a regression test": question("Add a regression test for the empty input case?", YES_NO, header="Tests"),
    "touched files": question("Run only the touched test files or the whole suite?",
                              ("Touched test files (Recommended)", "Whole suite"), header="Tests"),
    # file layout in the worktree
    "where a module goes": question("Which folder should the new module go in?",
                                    ("hooks/shared/window.py (Recommended)", "bin/window.py"), header="Layout"),
    "one file or two": question("Keep the helpers in one file or split them into two files?",
                                ("One file (Recommended)", "Two files"), header="Layout"),
    "fixture folder": question("Which folder for the test fixtures?",
                               ("tests/fixtures (Recommended)", "tests/data"), header="Layout"),
}


@pytest.mark.parametrize("item", LOCAL_ROWS.values(), ids=LOCAL_ROWS.keys())
def test_a_local_question_takes_the_default(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_DEFAULT, "recommended")


NOT_LOCAL_ROWS = {
    # names no local class
    "unmatched": (question("Which colour for the banner?", ("Blue (Recommended)", "Red")), "no_local_class"),
    "proceed": (question("Proceed?", YES_NO), "no_local_class"),
    "a class word only in the header": (
        question("Go with the first one?", YES_NO, header="Naming"), "no_local_class"),
    # a local class beside a word outside the local words
    "name and ping": (question("Which name should we use before we ping the client?",
                               ("parse_window (Recommended)", "other")), "not_local"),
    "test on staging": (question("Which tests should run on staging?", ("Unit (Recommended)", "All")), "not_local"),
    "rename a branch": (question("Rename the branch to the new name?", YES_NO), "not_local"),
    "plain outward word in the taken label": (
        question("Which name for the helper?", ("Ping them (Recommended)", "parse_window")), "not_local"),
    "outward word in the taken description": (
        question("Which name for the helper?", ("parse_window (Recommended)", "other"),
                 descriptions={"parse_window (Recommended)": "then we tell the vendor"}), "not_local"),
    # a code token stands only in the option taken
    "a script named in the question": (
        question("Run notify_team.py on the test file?", YES_NO), "not_local"),
    "call a function named in the question": (question("Call ping_client in the test?", YES_NO), "not_local"),
    "a verb beside a code token in the taken label": (
        question("Which test file?", ("Run notify_team.py (Recommended)", "other")), "not_local"),
    "a code token in the taken description": (
        question("Which test file?", ("The first (Recommended)", "other"),
                 descriptions={"The first (Recommended)": "then run notify_team.py"}), "not_local"),
    "scripts in the test folder": (question("Run the scripts in the test folder?", YES_NO), "not_local"),
    # a place outside the worktree
    "absolute path": (question("Which folder for the module?", ("/etc/window (Recommended)", "bin")),
                      "outside_worktree"),
    "home path": (question("Which folder for the module?", ("~/bin (Recommended)", "bin")), "outside_worktree"),
    "parent path": (question("Which folder for the module?", ("../other/bin (Recommended)", "bin")),
                    "outside_worktree"),
    "url": (question("Which folder for the module?", ("https://x.io/bin (Recommended)", "bin")),
            "outside_worktree"),
    # a field the tool does not define
    "unknown question field": ({**question("Which name for the helper?", YES_NO), "note": "ok"}, "not_local"),
    "unknown option field": (
        {**question("Which name for the helper?", YES_NO),
         "options": [{"label": "Yes (Recommended)", "extra": "x"}, {"label": "No"}]}, "not_local"),
}


@pytest.mark.parametrize("item, code", NOT_LOCAL_ROWS.values(), ids=NOT_LOCAL_ROWS.keys())
def test_a_question_not_shown_local_halts(item, code):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_ALWAYS_ASK, code)


@pytest.mark.parametrize("item, code", [
    (question("Which n\u0430me for the helper?", YES_NO), "non_ascii"),
    (question("Which name for the helper?", ("parse_wind\u043ew (Recommended)", "other")), "non_ascii"),
    (question("", YES_NO), "non_string"),
    (question("\u200b", YES_NO), "no_local_class"),
    (question("Which name for the helper? " + "name " * 1000, YES_NO), "too_long"),
    (question("Which name? " + "name " * 700, YES_NO), "default"),
    (question("Rename the branch and push to main?", YES_NO), "act_named"),
    (question("Rename the test file and push it?", YES_NO), "act_named"),
    (question("Don't deploy yet, which name for the helper?", YES_NO), "act_named"),
    (question("Which name for the helper?", ("deploy_helper (Recommended)", "other")), "act_named"),
    (question("Which folder for the module?", ("origin/main (Recommended)", "bin")), "act_named"),
], ids=["cyrillic a in name", "cyrillic o in the taken label", "empty", "zero-width only",
        "very long", "long but under the cap", "local word beside push to main",
        "local word beside push", "a negation", "act in a code token", "a remote ref as a folder"])
def test_the_local_check_at_its_boundary(item, code):
    verdict = u.classify_question(item)
    assert (verdict.code if verdict.cls == u.CLASS_ALWAYS_ASK else verdict.cls) == code


def classes(tool_input):
    return [(item.index, item.cls, item.code) for item in u.classify_call(tool_input)]


def test_a_call_gets_one_verdict_per_question_and_the_worst_names_the_row():
    verdicts = u.classify_call({"questions": [question(), question("Merge it?", YES_NO), question()]})
    assert [(v.index, v.cls) for v in verdicts] == [
        (1, u.CLASS_DEFAULT), (2, u.CLASS_ALWAYS_ASK), (3, u.CLASS_DEFAULT)]
    assert u.worst(verdicts).code == "act_named"


@pytest.mark.parametrize("tool_input, expected", [
    (None, [(1, "always_ask", "questions_missing")]),
    ("questions", [(1, "always_ask", "questions_missing")]),
    ({}, [(1, "always_ask", "questions_missing")]),
    ({"questions": []}, [(1, "always_ask", "questions_missing")]),
    ({"questions": question()}, [(1, "always_ask", "questions_missing")]),
    ({"questions": [question(), 7]}, [(1, "default", "recommended"), (2, "always_ask", "not_a_question")]),
    ({"questions": [question()], "note": "then deploy"}, [(1, "always_ask", "act_named")]),
    ({"questions": [question()], "note": "\u0437\u0430\u0442\u0435\u043c"}, [(1, "always_ask", "non_ascii")]),
    ({"questions": [question()], "note": "x" * 50_000}, [(1, "always_ask", "too_long")]),
], ids=["null", "string", "empty", "no questions", "questions not a list", "one not an object",
        "act outside the questions", "non-ascii outside", "huge outside"])
def test_an_odd_call_never_answers_default_for_the_odd_part(tool_input, expected):
    assert classes(tool_input) == expected


def test_more_questions_than_the_tool_takes_are_all_always_ask():
    verdicts = u.classify_call({"questions": [question() for _ in range(40)]})
    assert len(verdicts) == 16
    assert {(v.cls, v.code) for v in verdicts} == {(u.CLASS_ALWAYS_ASK, "too_many")}


def test_deep_nesting_does_not_raise_and_is_always_ask():
    nested = "x"
    for _ in range(5000):
        nested = [nested]
    assert classes({"questions": [{**question(), "extra": nested}]}) == [(1, "always_ask", "too_long")]


def test_a_check_that_raises_answers_always_ask(monkeypatch):
    monkeypatch.setattr(u, "classify_question", lambda *_: 1 / 0)
    assert classes({"questions": [question()]}) == [(1, "always_ask", "check_error")]


# --- the switch -------------------------------------------------------------

def flag(folder: Path, body) -> None:
    (folder / u.FLAG_NAME).write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())


def test_the_flag_wins_over_the_environment_and_no_flag_leaves_it_to_the_environment(tmp_path):
    assert u.read_mode(str(tmp_path), {}) == (False, "unset")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (True, "env")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "0"}) == (False, "unset")
    assert u.read_mode("", {u.ENV_NAME: "1"}) == (True, "env")
    u.write_flag(str(tmp_path), False, "2026-10-03T00:00:00+00:00")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (False, "flag")
    u.write_flag(str(tmp_path), True, "2026-10-03T00:00:00+00:00")
    assert u.read_mode(str(tmp_path), {}) == (True, "flag")
    assert os.stat(tmp_path / u.FLAG_NAME).st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body", [
    b"", b"not json", b"\xff\xfe\x00", b"[true]", b"true", b"null",
    {"unattended": "true"}, {"unattended": 1}, {"unattended": None}, {"on": True},
    b'{"unattended": true}' + b" " * 5000, b"[" * 100_000,
], ids=["empty", "text", "binary", "list", "bare true", "null", "string value", "int value",
        "null value", "other key", "oversize", "deep"])
def test_a_malformed_flag_reads_off_even_when_the_environment_says_on(tmp_path, body):
    flag(tmp_path, body)
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (False, "flag_malformed")


def test_a_flag_that_is_not_a_readable_file_reads_off(tmp_path):
    directory, link, locked = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for folder in (directory, link, locked):
        folder.mkdir()
    (directory / u.FLAG_NAME).mkdir()
    flag(tmp_path, {"unattended": True})
    (link / u.FLAG_NAME).symlink_to(tmp_path / u.FLAG_NAME)
    flag(locked, {"unattended": True})
    (locked / u.FLAG_NAME).chmod(0)
    cases = [directory, link] + ([locked] if os.geteuid() != 0 else [])
    for folder in cases:
        assert u.read_mode(str(folder), {u.ENV_NAME: "1"}) == (False, "flag_unreadable"), folder.name


# --- the gate ---------------------------------------------------------------

@pytest.fixture
def root(tmp_path):
    """A temporary home, config root and project; returns (env, session directory)."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key != u.ENV_NAME and not key.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PROJECT_DIR=str(project))
    return env, tmp_path / "config" / "pact-sessions" / "proj" / SESSION


def run_gate(env, tool_input, *, raw=None, tool_name="AskUserQuestion", **extra_env):
    payload = raw if raw is not None else json.dumps(
        {"tool_name": tool_name, "session_id": SESSION, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True,
                          capture_output=True, env={**env, **extra_env}, timeout=60)


def journal(session_dir: Path) -> list[dict]:
    path = session_dir / "session-journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_off_allows_the_question_and_writes_nothing(root):
    env, session_dir = root
    done = run_gate(env, {"questions": [question("Merge it?", YES_NO)]})
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})
    assert not session_dir.exists()


def test_on_refuses_with_one_verdict_per_question_and_journals_no_question_text(root):
    env, session_dir = root
    done = run_gate(env, {"questions": [question(), question("Merge the zebra branch?", YES_NO)]},
                    REPACT_UNATTENDED="1")
    assert done.returncode == 2
    envelope = json.loads(done.stdout)["hookSpecificOutput"]
    assert envelope["hookEventName"] == "PreToolUse" and envelope["permissionDecision"] == "deny"
    reason = envelope["permissionDecisionReason"]
    assert reason in done.stderr
    assert "q1 DEFAULT" in reason and "q2 ALWAYS-ASK (act_named)" in reason
    assert "telegram_notify" in reason and "telegram_ask" not in reason
    assert "zebra" not in reason and "layout" not in reason
    rows = journal(session_dir)
    assert [row["type"] for row in rows] == ["gate_refusal"]
    row = rows[0]
    assert (row["hook"], row["verdict"], row["rule"], row["class"]) == (
        "unattended_gate", "deny", "act_named", "always_ask")
    assert [(item["class"], item["code"]) for item in row["questions"]] == [
        ("default", "recommended"), ("always_ask", "act_named")]
    text = (session_dir / "session-journal.jsonl").read_text()
    assert "zebra" not in text and "layout" not in text.lower() and "Recommended" not in text
    # a refused question leaves nothing an approval guard could read: one file, the journal
    assert [path.name for path in Path(env["CLAUDE_CONFIG_DIR"]).rglob("*") if path.is_file()] == [
        "session-journal.jsonl"]


def test_a_question_refused_as_always_ask_stays_always_ask_with_new_options(root):
    env, session_dir = root
    first = run_gate(env, {"questions": [question(labels=("Two", "One"))]}, REPACT_UNATTENDED="1")
    again = run_gate(env, {"questions": [question()]}, REPACT_UNATTENDED="1")
    assert "ALWAYS-ASK (no_recommended)" in first.stderr
    assert "ALWAYS-ASK (asked_before)" in again.stderr and "DEFAULT" not in again.stderr
    assert journal(session_dir)[-1]["rule"] == "asked_before"


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '"text"', "{", "[" * 100_000],
                         ids=["empty", "text", "list", "string", "cut", "deep"])
def test_stdin_that_is_not_a_json_object_exits_0_without_a_decision(root, raw):
    env, session_dir = root
    done = run_gate(env, None, raw=raw, REPACT_UNATTENDED="1")
    assert done.returncode == 0 and "permissionDecision" not in done.stdout
    assert not session_dir.exists()


def test_another_tool_is_allowed_while_on(root):
    env, _ = root
    done = run_gate(env, {"command": "git merge"}, tool_name="Bash", REPACT_UNATTENDED="1")
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})


def test_a_malformed_flag_allows_the_question_and_says_so_in_the_journal(root):
    env, session_dir = root
    session_dir.mkdir(parents=True)
    flag(session_dir, b"{broken")
    done = run_gate(env, {"questions": [question()]}, REPACT_UNATTENDED="1")
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})
    assert [(row["type"], row["state"], row["cause"]) for row in journal(session_dir)] == [
        ("unattended_mode", "off", "flag_malformed")]


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_the_gate_and_the_mode_import_no_approval_guard_and_no_way_out():
    """They cannot mint an approval or reach a network, a model or a subprocess: they
    import neither the merge guard nor anything that could."""
    assert imported_modules(PLUGIN / "hooks" / "shared" / "unattended.py") == {
        "__future__", "hashlib", "json", "os", "re", "stat", "tempfile", "unicodedata", "typing"}
    assert imported_modules(HOOK) == {
        "__future__", "json", "os", "shlex", "sys", "pathlib",
        "shared", "shared.pact_context", "shared.session_journal"}


def test_the_gate_is_registered_once_on_the_question_tool_and_is_synchronous():
    config = effective_hooks_config()
    found = [(event, entry.get("matcher"), hook.get("async", False))
             for event, entries in config["hooks"].items() for entry in entries
             for hook in entry.get("hooks", [])
             if hook.get("command", "").endswith('/hooks/unattended_gate.py"')]
    assert found == [("PreToolUse", "AskUserQuestion", False)]


# --- the command ------------------------------------------------------------

def run_command(env, session_dir, *args, **extra_env):
    return subprocess.run([sys.executable, str(COMMAND), *args, "--session-dir", str(session_dir)],
                          text=True, capture_output=True, env={**env, **extra_env}, timeout=60)


def test_the_command_switches_records_and_lists(root):
    env, session_dir = root
    session_dir.mkdir(parents=True)
    refused_off = run_command(env, session_dir, "halt", "--task", "35", "--question", "Merge it")
    assert refused_off.returncode == 3 and "OFF" in refused_off.stderr

    on = run_command(env, session_dir, "on")
    assert on.returncode == 0 and on.stdout.startswith("unattended: ON (flag)")
    assert "UNATTENDED MODE is ON" in on.stdout

    gate = run_gate(env, {"questions": [question(), question("Merge it?", YES_NO),
                                        question("Which colour?", ("Blue (Recommended)", "Red"))]})
    assert gate.returncode == 2
    row = [row for row in journal(session_dir) if row["type"] == "gate_refusal"][0]
    default_ref, merge_ref, open_ref = (item["ref"] for item in row["questions"])

    record = ("--question", "Which layout", "--chose", "Two columns (Recommended)", "--why", "the plan")
    assert run_command(env, session_dir, "default", "--ref", "0" * 12, *record).returncode == 3
    on_always_ask = run_command(env, session_dir, "default", "--ref", merge_ref, *record)
    assert on_always_ask.returncode == 3 and "always-ask" in on_always_ask.stderr
    assert run_command(env, session_dir, "default", *record).returncode == 2
    assert run_command(env, session_dir, "default", "--ref", default_ref, *record).returncode == 0
    assert run_command(env, session_dir, "halt", "--ref", merge_ref, "--task", "35",
                       "--question", "Merge\nit\x1b[31m now").returncode == 0

    listed = run_command(env, session_dir, "list")
    assert listed.returncode == 0
    lines = listed.stdout.splitlines()
    assert lines[0] == "unattended: ON (flag); defaults 1, halts 1, questions refused 3"
    assert any("Which layout -> Two columns (Recommended); why: the plan" in line for line in lines)
    assert any("task 35: Merge it [31m now" in line for line in lines)
    assert any("not recorded: 1 refused question(s)" in line and open_ref in line for line in lines)

    off = run_command(env, session_dir, "off", REPACT_UNATTENDED="1")
    assert off.returncode == 0 and off.stdout.startswith("unattended: OFF (flag)")
    assert run_gate(env, {"questions": [question()]}, REPACT_UNATTENDED="1").returncode == 0
    assert [(row["state"], row["cause"]) for row in journal(session_dir)
            if row["type"] == "unattended_mode"] == [("on", "command"), ("off", "command")]


def test_the_command_needs_a_session_directory(root, tmp_path):
    env, _ = root
    assert run_command(env, tmp_path / "missing", "status").returncode == 4
    bare = subprocess.run([sys.executable, str(COMMAND), "status"], text=True,
                          capture_output=True, env=env, timeout=60)
    assert bare.returncode == 4 and "--session-dir" in bare.stderr


# --- the session-start block --------------------------------------------------

def test_session_start_prints_the_rules_only_while_on_also_after_a_compaction(tmp_path, monkeypatch):
    monkeypatch.delenv(u.ENV_NAME, raising=False)
    monkeypatch.setattr(session_init_lead, "reconstruct_session_dir", lambda *_: str(tmp_path))
    rows = []
    monkeypatch.setattr(session_init_lead, "append_event", lambda event, session_dir=None: rows.append(event))

    assert session_init.unattended_context(SESSION, "/proj", "startup") is None
    assert rows == []

    u.write_flag(str(tmp_path), True, "2026-10-03T00:00:00+00:00")
    for source in ("startup", "resume", "compact", "clear"):
        block = session_init.unattended_context(SESSION, "/proj", source)
        assert block.startswith("UNATTENDED MODE is ON (set by the session flag)")
        assert "AskUserQuestion" in block and "telegram_notify" in block and "never" in block
    assert rows == []

    flag(tmp_path, b"{broken")
    monkeypatch.setenv(u.ENV_NAME, "1")
    assert session_init.unattended_context(SESSION, "/proj", "compact") is None
    assert [(row["type"], row["state"], row["cause"]) for row in rows] == [
        ("unattended_mode", "off", "flag_malformed")]

    (tmp_path / u.FLAG_NAME).unlink()
    rows.clear()
    assert "set by the environment" in session_init.unattended_context(SESSION, "/proj", "startup")
    assert "set by the environment" in session_init.unattended_context(SESSION, "/proj", "compact")
    assert [(row["state"], row["cause"]) for row in rows] == [("on", "env")]
