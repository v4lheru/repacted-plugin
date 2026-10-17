"""The warning when the upstream PACT plugin is enabled beside repacted.

shared/coexistence.py reads enabledPlugins from the user, project and
project-local settings, plus the managed sources. Under `repact` in alternate
mode (REPACTED_LAUNCH=alternate) the three non-managed scopes are skipped,
because the launcher's --settings flag disables PACT for that session; a
managed entry still warns. session_init surfaces the warning as a
systemMessage on startup and resume only.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.coexistence import coexistence_warning


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_launch_env(tmp_path, monkeypatch):
    """No test reads this machine's managed settings, or inherits a repact session's mode."""
    monkeypatch.delenv("REPACTED_LAUNCH", raising=False)
    managed = tmp_path / "managed"
    managed.mkdir()
    monkeypatch.setenv("REPACTED_MANAGED_SETTINGS_DIR", str(managed))
    return managed


@pytest.fixture
def scopes(tmp_path):
    config_dir = tmp_path / "config"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return {
        "config_dir": config_dir,
        "project_dir": str(project_dir),
        "user": config_dir / "settings.json",
        "project": project_dir / ".claude" / "settings.json",
        "local": project_dir / ".claude" / "settings.local.json",
    }


@pytest.mark.parametrize("scope", ["user", "project", "local"])
def test_pact_enabled_in_any_scope_warns_and_names_the_file(scopes, scope):
    _write(scopes[scope], {"enabledPlugins": {"PACT@pact-marketplace": True, "repacted@repacted-local": True}})

    warning = coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"])

    assert warning is not None
    assert warning.startswith("repacted: PACT is also enabled (")
    assert str(scopes[scope]) in warning
    assert "claude plugin disable PACT@pact-marketplace" in warning


def test_two_scopes_are_both_named_and_the_command_is_given_once(scopes):
    _write(scopes["user"], {"enabledPlugins": {"PACT@pact-marketplace": True}})
    _write(scopes["local"], {"enabledPlugins": {"PACT@pact-marketplace": True}})

    warning = coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"])

    assert str(scopes["user"]) in warning and str(scopes["local"]) in warning
    assert warning.count("claude plugin disable") == 1


@pytest.mark.parametrize("settings", [
    {"enabledPlugins": {"PACT@pact-marketplace": False}},
    {"enabledPlugins": {"repacted@repacted-local": True}},
    {"enabledPlugins": {"PACTX@elsewhere": True, "notPACT@x": True}},
    {"enabledPlugins": {"PACT@pact-marketplace": "true"}},
    {},
], ids=["disabled", "only-repacted", "lookalike-keys", "non-boolean", "no-enabledPlugins"])
def test_no_warning_when_pact_is_not_enabled(scopes, settings):
    _write(scopes["user"], settings)

    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is None


def test_no_warning_when_no_settings_file_exists(scopes):
    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is None
    assert coexistence_warning(None, config_dir=scopes["config_dir"]) is None


@pytest.mark.parametrize("payload", ["{not json", "[]", '{"enabledPlugins": ["PACT@pact-marketplace"]}', ""],
                         ids=["malformed", "top-level-list", "list-not-dict", "empty-file"])
def test_malformed_settings_never_raise_and_do_not_mask_another_scope(scopes, payload):
    _write(scopes["user"], payload)
    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is None

    _write(scopes["project"], {"enabledPlugins": {"PACT@pact-marketplace": True}})
    assert str(scopes["project"]) in coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"])


def test_an_unresolvable_config_dir_returns_none():
    with patch("shared.coexistence.get_claude_config_dir", side_effect=RuntimeError("no home")):
        assert coexistence_warning(None) is None


# ---------- alternate mode and the managed scope ----------

@pytest.mark.parametrize("scope", ["user", "project", "local"])
def test_alternate_mode_skips_the_scopes_the_launch_flag_overrides(scopes, scope, monkeypatch):
    _write(scopes[scope], {"enabledPlugins": {"PACT@pact-marketplace": True}})
    monkeypatch.setenv("REPACTED_LAUNCH", "alternate")

    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is None


@pytest.mark.parametrize("launch", [None, "alternate"])
def test_a_managed_entry_warns_in_every_mode(scopes, _isolated_launch_env, monkeypatch, launch):
    managed_file = _isolated_launch_env / "managed-settings.json"
    _write(managed_file, {"enabledPlugins": {"PACT@pact-marketplace": True}})
    if launch:
        monkeypatch.setenv("REPACTED_LAUNCH", launch)

    warning = coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"])

    assert warning is not None and str(managed_file) in warning
    assert "only the administrator can remove it" in warning


def test_a_managed_drop_in_counts_and_a_later_one_can_turn_it_off(scopes, _isolated_launch_env):
    _write(_isolated_launch_env / "managed-settings.d" / "10-on.json",
           {"enabledPlugins": {"PACT@pact-marketplace": True}})
    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is not None

    _write(_isolated_launch_env / "managed-settings.d" / "20-off.json",
           {"enabledPlugins": {"PACT@pact-marketplace": False}})
    assert coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"]) is None


def test_a_broken_managed_reader_never_raises_and_keeps_the_user_scope(scopes):
    from shared import coexistence

    _write(scopes["user"], {"enabledPlugins": {"PACT@pact-marketplace": True}})
    with patch.object(coexistence, "managed_sources",
                      side_effect=RuntimeError("boom")):
        warning = coexistence_warning(scopes["project_dir"], config_dir=scopes["config_dir"])
    assert warning is not None and str(scopes["user"]) in warning


# ---------- routing in session_init ----------

def _run_session_init(monkeypatch, tmp_path, source):
    from session_init import main

    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    payload = {"session_id": "aabb1122-0000-0000-0000-00000000c0e1", "source": source,
               "agent_type": "repacted:pact-orchestrator"}
    with patch("shared.session_init_common.setup_plugin_symlinks", return_value=None), \
         patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_resume._check_pr_state", return_value=""), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", new_callable=io.StringIO) as stdout:
        with pytest.raises(SystemExit) as exit_info:
            main()
    assert exit_info.value.code == 0
    return json.loads(stdout.getvalue())


@pytest.mark.parametrize("source, warned", [("startup", True), ("resume", True), ("compact", False), ("clear", False)])
def test_session_init_routes_the_warning_to_system_message_on_launch_only(monkeypatch, tmp_path, source, warned):
    # The autouse fixture points the config root at tmp_path/.claude.
    _write(tmp_path / ".claude" / "settings.json", {"enabledPlugins": {"PACT@pact-marketplace": True}})

    output = _run_session_init(monkeypatch, tmp_path, source)

    assert ("repacted: PACT is also enabled" in output.get("systemMessage", "")) is warned
    additional = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "repacted: PACT is also enabled" not in additional


def test_session_init_is_silent_under_the_alternate_launch(monkeypatch, tmp_path):
    _write(tmp_path / ".claude" / "settings.json", {"enabledPlugins": {"PACT@pact-marketplace": True}})
    monkeypatch.setenv("REPACTED_LAUNCH", "alternate")

    output = _run_session_init(monkeypatch, tmp_path, "startup")

    assert "PACT is also enabled" not in output.get("systemMessage", "")


def test_session_init_is_silent_when_only_repacted_is_enabled(monkeypatch, tmp_path):
    _write(tmp_path / ".claude" / "settings.json", {"enabledPlugins": {"repacted@repacted-local": True}})

    output = _run_session_init(monkeypatch, tmp_path, "startup")

    assert "PACT is also enabled" not in output.get("systemMessage", "")
