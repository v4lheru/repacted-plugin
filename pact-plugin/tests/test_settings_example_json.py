"""
Shape validation for templates/settings.example.json.

Under v4.0.0 the user-facing per-project entry point is
`.claude/settings.json` with `"agent": "repacted:pact-orchestrator"`. The plugin
ships a sample at `pact-plugin/templates/settings.example.json` for users to
copy from. This test guards the shape so the example stays canonical
through future changes.

"""
from pathlib import Path


SETTINGS_EXAMPLE_PATH = (
    Path(__file__).parent.parent / "templates" / "settings.example.json"
)

EXPECTED_AGENT_VALUE = "repacted:pact-orchestrator"


def test_settings_example_file_exists():
    assert SETTINGS_EXAMPLE_PATH.exists(), (
        f"settings.example.json missing at {SETTINGS_EXAMPLE_PATH}"
    )


