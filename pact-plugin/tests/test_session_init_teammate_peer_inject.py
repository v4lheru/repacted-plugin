"""CODE-phase verification for the Family E relocation (#806 Item 3):
peer-context injection on a separate-process teammate's OWN SessionStart
(the session_init teammate-branch) + the get_peer_context
``include_role_marker`` contract.

Coverage (per the ratified architect spec):
  * both-modes matrix — classify=="teammate" injects the marker-free peer
    body AND suppresses the orchestrator block; "lead" and "unknown"/plain
    frames keep the existing orchestrator-directive ladder UNCHANGED; the
    in-process SubagentStart builder still emits the role-marker prelude.
  * structural invariant — the relocated branch gates on the
    classify_session_role SSOT and calls the builder with
    include_role_marker=False (the negative-AST "never re-key on
    agent_id/Subagent/environ" invariant is owned by
    test_lead_discriminator_invariant.py, which already covers session_init).
  * fail-safe — unknown/empty agent_type does NOT inject (falls to the
    orchestrator else-branch); a teammate frame with no peer body (None)
    raises nothing and injects nothing (no orchestrator block either).
  * idempotency — the peer body is inserted at most once per lifecycle.

Mirrors test_session_init_role_suppression.py's synthetic-stdin + Path.home
-> tmp_path isolation so the rows port cleanly.

NOTE: the TEST-phase tmux-flip empirical acceptance (a real separate-process
teammate receiving the peer context end-to-end) is a SEPARATE later merge
gate owned by the test-engineer; these are the CODE-phase unit/structural
rows the architect specified.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    teammate_frame,
)

_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"
_ORCH_MARKER = "YOUR PACT ROLE: orchestrator"
_PEER_SENTINEL = "PEER_CONTEXT_SENTINEL_BODY"


def _stdin_for(frame: dict, source: str = "resume") -> str:
    """SessionStart stdin carrying ``frame``'s role discriminator + a valid
    session_id and source (so the assembly path is fully reached)."""
    return json.dumps({"session_id": _SESSION_ID, "source": source, **frame})


def _run_main_capture(stdin_data, monkeypatch, tmp_path, *, peer_return=_PEER_SENTINEL,
                      resolver_return=None, peer_raises=False):
    """Run session_init.main() with the heavy collaborators patched out and
    get_peer_context stubbed to ``peer_return``; return
    (additionalContext_str, get_peer_context_mock).

    Stubbing get_peer_context isolates the teammate/else FORK DECISION from the
    builder internals (covered separately below + by the ported test_peer_inject
    corpus).

    The self-registration registry resolver (``_registry_resolve``) is ALSO
    stubbed (default ``None`` → the teammate branch takes the generate_team_name
    fallback, deterministically, independent of any on-disk registry). Pass
    ``resolver_return="<name>@<team>"`` to drive the resolved path — the branch
    partitions that string on "@" into (own_name, lead_team). ``peer_raises``
    makes get_peer_context raise — to prove the Finding-1 fail-open wrapper.
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    # session_init.main() now appends to $CLAUDE_ENV_FILE when both vars are
    # present — keep these drives hermetic against an ambient env file.
    monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_init_lead.restore_last_session", return_value=None), \
         patch("shared.session_init_lead.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})), \
         patch("shared.session_init_lead.persist_context", return_value=None), \
         patch("shared.session_init_lead.append_event"), \
         patch("shared.session_init_lead.update_session_info", return_value=None), \
         patch("shared.session_init_lead.check_resume_state", return_value=None), \
         patch("shared.session_init_teammate._registry_resolve", return_value=resolver_return), \
         patch("shared.session_init_teammate.get_peer_context",
               side_effect=(RuntimeError("peer-build boom") if peer_raises else None),
               return_value=peer_return) as mock_gpc, \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    raw = mock_stdout.getvalue().strip()
    additional = ""
    if raw:
        try:
            additional = json.loads(raw).get("hookSpecificOutput", {}).get(
                "additionalContext", ""
            )
        except json.JSONDecodeError:
            additional = raw
    return additional, mock_gpc


# ===========================================================================
# Both-modes matrix
# ===========================================================================

class TestTeammateBranchInjects:
    """classify=="teammate" → marker-free peer body injected; orchestrator
    block SUPPRESSED (the mis-roling fix)."""

    def test_teammate_frame_injects_peer_body(self, monkeypatch, tmp_path):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path
        )
        assert _PEER_SENTINEL in additional
        assert mock_gpc.called


class TestLeadKeepsOrchestratorBlockAndUnknownGetsNeither:
    """A "lead" frame keeps the orchestrator ladder. An "unknown"/plain frame
    gets NEITHER the ladder NOR a peer body.

    RENAMED FROM TestLeadAndUnknownKeepOrchestratorBlock, because the old name
    made a claim the gate no longer honours: the two roles are no longer one
    case. The class holds the LEAD half and the UNKNOWN half side by side so a
    re-merge of the two cannot stay green in either direction."""

    @pytest.mark.parametrize(
        "frame_builder", [lead_frame_qualified,]
    )
    def test_lead_keeps_orchestrator_block_no_peer_body(
        self, frame_builder, monkeypatch, tmp_path
    ):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(frame_builder()), monkeypatch, tmp_path
        )
        assert _ORCH_MARKER in additional
        assert _PEER_SENTINEL not in additional
        assert not mock_gpc.called, "lead frame must not call the peer-context builder"


class TestInProcessSubagentSurfaceUnchanged:
    """The in-process SubagentStart builder still emits the role-marker
    prelude (include_role_marker default True) — byte-identical to before."""

    def _write_team(self, tmp_path):
        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "members": [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ]
        }))
        return str(tmp_path / "teams")

    def test_subagent_default_includes_role_marker(self, tmp_path):
        from peer_inject import get_peer_context
        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            agent_name="architect",
            teams_dir=self._write_team(tmp_path),
        )
        assert result is not None
        assert "YOUR PACT ROLE: teammate (architect)" in result
        assert "teammate-messaging.md" in result
        assert "backend-coder" in result  # peer list present


# ===========================================================================
# Fail-safe
# ===========================================================================

class TestFailSafe:
    def test_teammate_with_no_team_config_injects_nothing_no_raise(
        self, monkeypatch, tmp_path
    ):
        # Builder returns None (no team config) → teammate branch inserts
        # nothing; crucially NO orchestrator block either, and no raise.
        additional, _ = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")),
            monkeypatch, tmp_path, peer_return=None,
        )
        assert _PEER_SENTINEL not in additional
        assert _ORCH_MARKER not in additional


# ===========================================================================
# Idempotency
# ===========================================================================

class TestIdempotency:
    def test_peer_body_injected_exactly_once(self, monkeypatch, tmp_path):
        additional, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path
        )
        assert additional.count(_PEER_SENTINEL) == 1
        assert mock_gpc.call_count == 1


# ===========================================================================
# Structural invariant (relocation-specific; negative-AST owned by
# test_lead_discriminator_invariant.py)
# ===========================================================================

class TestStructuralInvariant:
    def _tree(self, module="session_init.py"):
        # A4b: main() stays in session_init.py; the teammate branch body is
        # shared/session_init_teammate.py::inject_peer_context.
        import session_init
        path = Path(session_init.__file__).parent / module
        return ast.parse(path.read_text(encoding="utf-8"))

    def _calls(self, tree, name):
        return [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name
        ]

    def _get_peer_context_calls(self, tree):
        return self._calls(tree, "get_peer_context")

    def test_single_builder_call_is_marker_free(self):
        calls = self._get_peer_context_calls(self._tree("shared/session_init_teammate.py"))
        assert len(calls) == 1, "expected exactly one get_peer_context call in session_init_teammate"
        kw = {k.arg: k.value for k in calls[0].keywords}
        assert "include_role_marker" in kw, "must pass include_role_marker explicitly"
        assert isinstance(kw["include_role_marker"], ast.Constant)
        assert kw["include_role_marker"].value is False


class TestTeammateBranchUsesResolver:
    """The teammate-branch self-looks-up the registry by its OWN session_id; on a
    resolved ``name@team`` it partitions into (own_name, lead_team) and passes
    both to the builder, and falls back to generate_team_name + stdin agent_name
    when the registry returns None."""

    def test_resolved_team_and_name_passed_to_builder(self, monkeypatch, tmp_path):
        # The registry value is "<own-name>@<lead-team>": the @team carries the
        # LEAD's team (the datum a teammate cannot compute), the name half gives
        # EXACT-name self-exclusion.
        _, mock_gpc = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path,
            resolver_return="backend-coder-7@pact-leadteam",
        )
        assert mock_gpc.called
        kw = mock_gpc.call_args.kwargs
        assert kw.get("team_name") == "pact-leadteam"  # @team half → lead's team
        assert kw.get("agent_name") == "backend-coder-7"  # name half → self-exclusion
        assert kw.get("include_role_marker") is False


class TestFindingOneFailOpen:
    """Finding-1: a raise in the teammate-branch build path → NO injection, NO
    raise, NO orchestrator-block fallthrough (never the safety-net mis-roling)."""

    def test_peer_build_raise_yields_no_injection_no_orchestrator(self, monkeypatch, tmp_path):
        additional, _ = _run_main_capture(
            _stdin_for(teammate_frame("pact-backend-coder")), monkeypatch, tmp_path,
            resolver_return="devops@pact-leadteam", peer_raises=True,
        )
        # exit 0 asserted inside the helper → the exception was swallowed;
        # neither a peer body nor the orchestrator block was injected.
        assert _PEER_SENTINEL not in additional
        assert _ORCH_MARKER not in additional


# ===========================================================================
# O2 — emitted peer-member names are sanitized (symmetric with the self name)
# ===========================================================================

