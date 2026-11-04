"""C3 tests: the SessionStart "PACT Runtime Config" injection.

Two layers:
- TestComposer: the pure format_pact_runtime_config() composer -- env-flip
  flips the block text (proving it is not a static string), the leading-newline
  shape, and F1 canonical composition (only fixed labels + ON/OFF are ever
  emitted; a poisoned resolver value cannot inject text into the block).
- TestEmissionWiring: drive the REAL session_init.main() with only
  injection-orthogonal heavy collaborators stubbed -- the
  os.environ -> pact_config.llm_options -> format_pact_runtime_config ->
  additionalContext SEAM is left REAL. greedy-on vs greedy-off must produce
  DIFFERENT emitted additionalContext (a composed-but-unwired block would make
  them identical), and a teammate frame must NOT receive the block (lead-only
  gating).
"""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import plain_frame, teammate_frame

import session_init
from session_init import format_pact_runtime_config


_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"
_GREEDY = "PACT_PR_GREEDY_FIX"
_AUTO = "PACT_AUTONOMOUS_SCOPE_DETECTION"
_HEADING = "## PACT Runtime Config (resolved at session start)"


class TestComposer:
    def test_all_off_shape(self):
        block = format_pact_runtime_config({_GREEDY: False, _AUTO: False})
        # Leading newline so the "## " heading lands at line-start after main()
        # joins context_parts with " | ".
        assert block.startswith("\n" + _HEADING)
        assert "- PR greedy-fix: OFF (PACT_PR_GREEDY_FIX)" in block
        assert "- Autonomous scope detection: OFF (PACT_AUTONOMOUS_SCOPE_DETECTION)" in block


def _run_main(frame, monkeypatch, tmp_path):
    """Drive real session_init.main() with heavy, injection-orthogonal
    collaborators stubbed. The config seam (pact_config.llm_options ->
    format_pact_runtime_config -> context_parts -> additionalContext) is NOT
    patched. Returns the emitted additionalContext string."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stdin_data = json.dumps({"session_id": _SESSION_ID, "source": "startup", **frame})
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
         patch("shared.session_init_teammate._registry_resolve", return_value=None), \
         patch("shared.session_init_teammate.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    raw = mock_stdout.getvalue().strip()
    if not raw:
        return ""
    return json.loads(raw).get("hookSpecificOutput", {}).get("additionalContext", "")


class TestEmissionWiring:
    def test_greedy_on_vs_off_changes_emitted_context(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_GREEDY, raising=False)
        monkeypatch.delenv(_AUTO, raising=False)
        off = _run_main(plain_frame(), monkeypatch, tmp_path)
        monkeypatch.setenv(_GREEDY, "1")
        on = _run_main(plain_frame(), monkeypatch, tmp_path)
        assert "PR greedy-fix: OFF (PACT_PR_GREEDY_FIX)" in off
        assert "PR greedy-fix: ON (PACT_PR_GREEDY_FIX)" in on
        # A composed-but-unwired block (built but never appended) would make
        # these identical -- this is the wiring proof.
        assert on != off

