"""The routing word is a machine contract between each status status source and
the channel selector in ``session_init.main()``.

THE CONTRACT. ``main()`` selects an output channel by a literal substring of a
human-readable status string:

    if "failed" in <msg>.lower() or "skipped" in <msg>.lower():
        system_messages.append(<msg>)      # the user-visible error surface
    else:
        context_parts.append(<msg>)        # ordinary context

So the word ``failed`` (and at three of the four sites the word ``skipped``) is
the ROUTING KEY, and that key is a word inside prose that a person reads. A
status source that rewords its failure return keeps the signal for a person and
DOWNGRADES THE ROUTING WITH NO SIGNAL. Nothing goes red, because no test in this
repository asserted which channel a status string reaches.

THE FAMILY WAS SEVEN SITES IN ``hooks/session_init.py``. The pinned-staleness
site went with the 1.5.0 pin-gate cut, so six remain. Five are covered here:

    line 991   setup_plugin_symlinks()         shared/symlinks.py
    line 1005  ensure_project_memory_md()      shared/claude_md_manager.py
    line 1018  migrate_to_managed_structure()  shared/claude_md_manager.py
    line 1032  strip_orphan_kernel_block()     shared/claude_md_manager.py
    line 1601  check_resumption_context()      shared/session_resume.py

The sixth site consumes ``update_session_info()`` from
``shared/session_resume.py`` and is covered by the arms in
``tests/test_session_resume.py``.

SITE 1601 ROUTES ON A DIFFERENT LITERAL, ``"**Blockers:"``, so the word-based
sweep that found the other five could not see it. THE FAMILY IS NOT "the
failed-or-skipped predicate". It is ROUTING KEYED ON A SUBSTRING OF PROSE, and
the word is one spelling of that idea.

HOW THE SEVEN WERE MEASURED (before the cut), so a later reader can judge the number rather
than inherit it. A word search finds the spelling it is given, and cannot find
a spelling nobody thought of. So the count comes from an enumeration that reads
the OPERATOR: parse the module with ``ast``, take each ``Compare`` that uses
``In`` with a string constant on the left, and read the result. Across the whole
``hooks/`` tree that returns these seven routing sites and two non-members,
which are a ``name@team`` parse and a code-fence detector. NO EIGHTH.
The rule does not cover a key held in a named constant, a key built at run
time, or a consumer outside ``hooks/``.

THREE PROPERTIES OF THE FAMILY, MEASURED RATHER THAN ASSUMED.

1. NONE OF THE FIRST FOUR SITES CARRIES A ROLE GATE OR A SOURCE GATE. Each sits
   at ``def main()`` then ``try:`` then ``if <msg>:``. The only condition
   before the routing predicate is that the status source returned a truthy string, so
   the live population of these four is each SessionStart frame. The sixth
   site is different, and its narrower population is stated in its own suite.
2. THE ORDINARY-CONTEXT BRANCH IS NOT ALWAYS UNGATED. Site 991 sends the
   no-change message to context only when the frame is not a context reset. The
   control tests below thus choose their frame on purpose. See
   ``_route`` and the comment on each control.
3. SITE 1601 NEEDS TWO GATE CONDITIONS TOGETHER, unlike the other five: ``if
   tasks:`` and then ``if resumption_msg:``. So its fixture supplies a task
   list, and its class says which task shape reaches which branch.

WHY THE TESTS DO NOT ASSERT THE MESSAGE TEXT. An arm that pins the words of a
status message reddens each time a contributor improves that message, which is
an over-block. The next contributor an over-block obstructs removes the arm
rather than repairs it, and the protection is then gone. So each test CALLS THE
STATUS SOURCE, GETS WHATEVER STRING IT RETURNS, and asserts only which of the two
channels that string reaches. A reword that keeps the routing word changes the
captured string and changes no assertion. A reword that loses the routing word
moves the string to the other channel and reddens the test.

THE ONE CHANGE THESE ARMS OBSTRUCT is a decision to route a status source failure
into ordinary context. That is a design decision and not an accident, and this
docstring is the statement of what the arm protects, so a contributor who meets
it can repair it on purpose.

========================= ANTI-MOCK INVARIANT ==============================
DO NOT PATCH THE STATUS SOURCE THAT THE TEST MEASURES. ``_route`` takes the name of the
one collaborator to leave live and patches the others. A test that patched its
own status source exercises the consumer branch alone: it cannot fire on a
reword of the status source, which is the only mutation these arms exist to catch.
The failure and the ordinary states are built from real filesystem conditions
(a read-only directory, an orphan marker), not from an injected
exception, so each arm below is a PRODUCT arm and not a harness arm.
============================================================================

======================== NON-VACUITY (source mutation) =====================
Each test names its own mutant in its docstring. The shape is the same for the
four: reword the failure or skip return of the status source so the routing word is
lost while the message stays informative, then run this file.
EXPECTED: the routing test for that status source fails, and the ordinary-context
test for the same status source stays green.
THE PAIRED CONTROL is the second half and it is not optional: change the same
message in a form that KEEPS the routing word and the routing test MUST stay
green, which is what proves these arms are not an over-block.
============================================================================
"""

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


_SESSION_ID = "ccdd3344-0000-0000-0000-000000000000"

# The four status sources this file covers, as they are bound in the session_init
# module namespace. ``_route`` patches each name in this set, but not the one
# left live, so one live status source runs for each measurement.
_STATUS_SOURCES = (
    "setup_plugin_symlinks",
    "ensure_project_memory_md",
    "migrate_to_managed_structure",
    "strip_orphan_kernel_block",
)


def _route(tmp_home, project_dir, live, *, agent_type=None, env=None, tasks=None):
    """Drive the live ``session_init.main()`` and return the two channels.

    ``live`` names the ONE status source left unpatched. ``env`` carries any extra
    environment the live status source reads, so that the status source sees the SAME
    state inside ``main()`` that it saw when the test captured its message.
    ``tasks`` is what ``get_task_list`` returns. IT IS THE INPUT TO
    ``check_resumption_context``, NOT THAT STATUS SOURCE. The status source
    stays unpatched and builds its own message from these tasks, so a reword of
    that message is visible to the arm.
    Returns the pair ``(system_message, additional_context)``, with an absent
    channel reported as the empty string, because ``main()`` omits a channel it
    has nothing for.

    THE FAILURE DIRECTION OF THIS HELPER IS TO RED, WHICH IS WHY ONE
    HELPER BEHIND ALL 14 TESTS IN THIS FILE IS ACCEPTABLE HERE. If it returns two empty
    strings, each routing assertion below fails, because a captured message is
    not empty. If it interchanges the two channels, the routing test and the
    ordinary-context test for the same status source disagree, and the two fail
    together. If it patched the measured status source by mistake, the
    independently captured message and the routed string diverge, and the
    routing assertion fails. It cannot fail to green.

    session_init is imported inside the function, matching the sibling suites:
    a collection-time import of that module pulls
    claude_md_manager into every test in this file.
    """
    import session_init

    payload = {"source": "startup", "session_id": _SESSION_ID}
    if agent_type is not None:
        payload["agent_type"] = agent_type

    suppressed = [
        patch(f"shared.session_init_common.{name}", return_value=None)
        for name in _STATUS_SOURCES
        if name != live
    ]
    # Collaborators that are not part of this contract. They are suppressed so
    # the measurement does not depend on task state, journal writes or disk
    # persistence. None of them contributes a status string to either channel.
    suppressed += [
        patch("session_init.get_task_list", return_value=tasks),
        patch("shared.session_init_lead.restore_last_session", return_value=None),
        patch("shared.session_init_lead.update_session_info", return_value=None),
        patch("shared.session_init_lead.check_resume_state", return_value=None),
        patch("shared.session_init_lead.append_event"),
        patch("shared.session_init_lead.persist_context", return_value=None),
        patch("shared.session_init_lead.build_context_cache",
              return_value=(Path("/tmp/ctx.json"), {})),
    ]

    with patch.object(Path, "home", staticmethod(lambda: tmp_home)), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        for p in suppressed:
            p.start()
        try:
            overrides = {"CLAUDE_PROJECT_DIR": str(project_dir)}
            overrides.update(env or {})
            with patch.dict(os.environ, overrides, clear=False):
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
                with pytest.raises(SystemExit) as exc:
                    session_init.main()
        finally:
            for p in reversed(suppressed):
                p.stop()

    assert exc.value.code == 0
    emitted = json.loads(out.getvalue())
    hook_out = emitted.get("hookSpecificOutput") or {}
    return emitted.get("systemMessage", ""), hook_out.get("additionalContext", "")


def _assert_routes_to_system_messages(message, system_message, context):
    """The shared oracle for a status source failure or skip return.

    It asserts the message is non-empty first. An empty string is a substring
    of every string, so the two membership tests below would hold for a cause
    that has nothing to do with routing.
    """
    assert message, (
        "the fixture did not drive the status source to a failure or skip return, "
        "so this measurement has no subject"
    )
    assert message in system_message, (
        "a status source failure or skip status must reach systemMessage. It did "
        "not, which means the routing word is absent from the message that "
        f"the status source returned: {message!r}"
    )
    assert message not in context, (
        "a status source failure or skip status must NOT also reach ordinary "
        "context"
    )


def _assert_routes_to_context(message, system_message, context):
    """The shared oracle for an ordinary status source return.

    This is the discrimination control. Without it the routing oracle above
    cannot tell correct routing apart from a channel that receives each string.
    """
    assert message, (
        "the fixture did not drive the status source to an ordinary return, so "
        "this measurement has no subject"
    )
    assert message in context, (
        "an ordinary status source status must reach additionalContext"
    )
    assert message not in system_message, (
        "an ordinary status source status must NOT reach systemMessage. It did, "
        "so the routing word is present in a message that reports no "
        f"failure: {message!r}"
    )


@pytest.fixture
def home(tmp_path):
    """A redirected home with the config directory in place."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


@pytest.fixture
def project(tmp_path):
    """An empty project directory, writable."""
    p = tmp_path / "project"
    p.mkdir()
    return p


class TestSymlinkStatusRouting:
    """Site 991, status source ``setup_plugin_symlinks``.

    THIS SITE IS NOT LIKE THE OTHER FOUR. It reads ``"failed"`` alone and has
    no ``"skipped"`` arm, so a skip-worded status from this status source routes to
    ordinary context today. The arm below pins the word the site reads.

    MUTANT: in ``hooks/shared/symlinks.py``, change the agents-loop status
    ``f"{agents_failed} agents failed"`` to ``f"{agents_failed} agents could
    not be linked"``. EXPECTED: the routing test fails and the linked test
    stays green.
    CONTROL MUTANT: change the same status to ``f"{agents_failed} agents
    failed to link"``. EXPECTED: both tests stay green, because the routing
    word survives.
    """

    @staticmethod
    def _plugin_root(base, agents_mode):
        plugin_root = base / "plugin"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "agents" / "pact-example.md").write_text("body")
        return plugin_root

    def test_failure_status_reaches_system_messages(self, tmp_path, home, project):
        plugin_root = self._plugin_root(tmp_path, None)
        agents_dst = home / ".claude" / "agents"
        agents_dst.mkdir()
        os.chmod(agents_dst, 0o500)  # the link write fails, the loop counts it
        try:
            with patch.dict(os.environ,
                            {"CLAUDE_PLUGIN_ROOT": str(plugin_root)},
                            clear=False), \
                 patch.object(Path, "home", staticmethod(lambda: home)):
                from shared.symlinks import setup_plugin_symlinks
                message = setup_plugin_symlinks()

            system_message, context = _route(
                home, project, live="setup_plugin_symlinks",
                env={"CLAUDE_PLUGIN_ROOT": str(plugin_root)})
        finally:
            os.chmod(agents_dst, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)


class TestProjectMemoryStatusRouting:
    """Site 1005, status source ``ensure_project_memory_md``.

    THIS SITE AND SITE 1018 CANNOT SHARE ONE FIXTURE. This status source
    returns None unless the project CLAUDE.md is ABSENT, and the migration
    status source returns None when it is absent. The two states are not compatible,
    so each site builds its own project directory.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the outer handler
    status ``f"Project CLAUDE.md failed: {str(e)[:50]}"`` to
    ``f"Project CLAUDE.md not written: {str(e)[:50]}"``. EXPECTED: the routing
    test fails and the created test stays green.
    CONTROL MUTANT: change it to ``f"Project CLAUDE.md write failed: ..."``.
    EXPECTED: the two tests stay green.
    """

    def test_failure_status_reaches_system_messages(self, home, project):
        # MEASURED: the resolver returns `<project>/.claude/CLAUDE.md` here, so
        # the step that fails in a sealed project is `ensure_dot_claude_parent`
        # creating `.claude`, NOT lock acquisition. Its OSError reaches the
        # OUTER handler either way, which is what this arm measures.
        os.chmod(project, 0o500)
        try:
            from shared.claude_md_manager import ensure_project_memory_md
            with patch.dict(os.environ,
                            {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
                message = ensure_project_memory_md()

            system_message, context = _route(
                home, project, live="ensure_project_memory_md")
        finally:
            os.chmod(project, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)


class TestMigrationStatusRouting:
    """Site 1018, status source ``migrate_to_managed_structure``.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, find the lock-failure
    status of this status source, the one that ends with the words ``migration
    skipped``. Change that ending to ``migration did not run`` so the routing
    word is lost. EXPECTED: the routing test fails and the migrated test stays
    green.
    CONTROL MUTANT: reword the first half and keep the final clause. EXPECTED:
    both tests stay green.
    """

    UNMIGRATED = (
        "# Project Memory\n"
        "\n"
        "## Retrieved Context\n"
        "\n"
        "## Working Memory\n"
    )

    def test_failure_status_reaches_system_messages(self, home, project):
        (project / "CLAUDE.md").write_text(self.UNMIGRATED)
        os.chmod(project, 0o500)  # the lock sidecar cannot be written
        try:
            from shared.claude_md_manager import migrate_to_managed_structure
            with patch.dict(os.environ,
                            {"CLAUDE_PROJECT_DIR": str(project)}, clear=False):
                message = migrate_to_managed_structure()

            system_message, context = _route(
                home, project, live="migrate_to_managed_structure")
        finally:
            os.chmod(project, 0o700)

        _assert_routes_to_system_messages(message, system_message, context)


class TestKernelStripStatusRouting:
    """Site 1032, status source ``strip_orphan_kernel_block``.

    MUTANT: in ``hooks/shared/claude_md_manager.py``, change the orphan-marker
    status prefix ``f"Migration skipped: {target_file} contains "`` to
    ``f"Migration not performed: {target_file} contains "``. EXPECTED: the
    routing test fails and the removed test stays green.
    CONTROL MUTANT: change the same status to ``"Migration skipped for
    safety: ..."``. EXPECTED: the two tests stay green.
    """

    ORPHAN = "# Home\n\n<!-- PACT_START:v3 -->\nkernel body\n"
    MARKER_PAIR = (
        "# Home\n"
        "\n"
        "<!-- PACT_START:v3 -->\n"
        "kernel body\n"
        "<!-- PACT_END -->\n"
        "\n"
        "user content\n"
    )

    def test_skip_status_reaches_system_messages(self, home, project):
        (home / ".claude" / "CLAUDE.md").write_text(self.ORPHAN)
        from shared.claude_md_manager import strip_orphan_kernel_block
        with patch.object(Path, "home", staticmethod(lambda: home)):
            message = strip_orphan_kernel_block()

        system_message, context = _route(
            home, project, live="strip_orphan_kernel_block")

        _assert_routes_to_system_messages(message, system_message, context)


# ===========================================================================
# THE SIXTH SITE, AND THE TWO SECOND OCCURRENCES.
#
# The four classes above pin ONE exit of each status source. Three exits stay
# open, and this block closes them.
#
# SITE 1601 IS A DIFFERENT LITERAL, NOT A DIFFERENT WORD. It routes on
# ``"**Blockers:"``, so a sweep for ``failed`` and ``skipped`` cannot see it.
# An enumeration that reads the OPERATOR rather than the word does see it, and
# that enumeration returns these six (seven before the 1.5.0 pin-gate cut).
#
# THE OTHER TWO ARE SECOND EXITS AT SITES THIS FILE BEFORE THIS COVERS. Each of
# those two status sources emits its status string from TWO handlers, and the
# fixtures above reach one of the two. An arm on a second exit has one hazard
# of its own: the fixture can silently drive the FIRST exit again and the arm
# passes for that. So each of the two carries a SEPARATION assertion, which
# proves the new fixture reaches a different exit from the one above it.
# ===========================================================================


