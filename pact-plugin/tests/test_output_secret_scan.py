"""
Location: pact-plugin/tests/test_output_secret_scan.py
Summary: hooks/output_secret_scan.py: a secret value in Bash
         output is replaced before the model sees it, and the model is told not to repeat it.
         Every value is assembled at import, so the repository's own scan never sees a whole
         token, and the .env files are temporary fixtures.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

import dispatch_registry as registry

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK = PLUGIN_ROOT / "hooks" / "output_secret_scan.py"
DISPATCH = PLUGIN_ROOT / "hooks" / "dispatch.py"

VALUE_A = "Zq7mR2" + "vLp9xWk3Tb"           # a key with a secret-like name
VALUE_B = "hT4nQ8" + "sWm2Kd6Yp1Rz"         # export, double quotes
VALUE_C = "uY3bN7" + "cXq5Lf9Gv2"           # single quotes, inline comment
VALUE_D = "Wp2Ks9" + "Lm4Qx7Tz8Rb"          # .env.local
VALUE_U = "ключ" + "Σ7xQ2mR9vL"             # unicode value
EXAMPLE_VALUE = "Ex4mPl3" + "Va1ueNotUsed9"  # .env.example only
NESTED_VALUE = "N3st3d" + "Va1ueUsed8Qx"     # sub/.env, one directory down
DEEP_VALUE = "D33pD1r" + "Va1ueNotUsed7"     # two down, skipped or symlinked dirs only
GITHUB_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

ENV_TEXT = "\n".join([
    "# local settings",
    "",
    f"DB_PASSWORD={VALUE_A}",
    f'export API_TOKEN="{VALUE_B}"',
    f"SERVICE_SECRET='{VALUE_C}'   # rotated monthly",
    "SHORT_TOKEN=abc12",
    "OTHER_KEY=your_api_key_here",
    "NODE_ENV=production",
    "AWS_REGION=eu-west-1",
    "LOG_DIR=/var/log/app",
    f"UNICODE_SECRET={VALUE_U}",
    "",
])


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30,
                   capture_output=True)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git_init(root)
    (root / ".env").write_text(ENV_TEXT, encoding="utf-8")
    (root / ".env.local").write_text(f"PAYMENT_KEY={VALUE_D}\n", encoding="utf-8")
    (root / ".env.example").write_text(f"PAYMENT_KEY={EXAMPLE_VALUE}\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / ".env").write_text(f"NESTED_KEY={NESTED_VALUE}\n", encoding="utf-8")
    return root


def _env(home: Path) -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}


def _payload(cwd: Path, stdout: str = "", stderr: str = "", tool: str = "Bash") -> dict:
    return {"hook_event_name": "PostToolUse", "session_id": "s-wc-test", "cwd": str(cwd),
            "tool_name": tool, "tool_input": {"command": "./run.sh"},
            "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False,
                              "isImage": False}}


def run_hook(payload, home: Path, raw: bytes | None = None) -> subprocess.CompletedProcess:
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True,
                          env=_env(home), timeout=60)


def _out(done: subprocess.CompletedProcess) -> dict:
    assert done.returncode == 0
    return json.loads(done.stdout)["hookSpecificOutput"]


def _assert_no_value(done: subprocess.CompletedProcess, *values: str) -> None:
    for value in values:
        for form in (value, quote(value, safe="")):
            assert form.encode("utf-8") not in done.stdout
            assert form.encode("utf-8") not in done.stderr
            assert json.dumps(form).strip('"').encode() not in done.stdout


# --- .env values ------------------------------------------------------------------------

class TestEnvValues:
    def test_a_value_in_stdout_is_replaced_and_the_model_is_told(self, project, home):
        done = run_hook(_payload(project, stdout=f"connecting with {VALUE_A}\nok\n"), home)
        out = _out(done)
        new = out["updatedToolOutput"]
        assert new == {"stdout": "connecting with [REDACTED:env-value]\nok\n", "stderr": "",
                       "interrupted": False, "isImage": False}
        assert "DB_PASSWORD" in out["additionalContext"] and ".env" in out["additionalContext"]
        assert "not repeat" in out["additionalContext"]
        _assert_no_value(done, VALUE_A)

    def test_a_value_inside_a_token_and_in_stderr_is_replaced(self, project, home):
        done = run_hook(_payload(project, stdout=f"x=pre{VALUE_A}post",
                                 stderr=f"warn: {VALUE_D}"), home)
        new = _out(done)["updatedToolOutput"]
        assert new["stdout"] == "x=pre[REDACTED:env-value]post"
        assert new["stderr"] == "warn: [REDACTED:env-value]"
        assert ".env.local" in _out(done)["additionalContext"]
        _assert_no_value(done, VALUE_A, VALUE_D)

    def test_comments_export_quotes_and_inline_comments_are_read(self, project, home):
        text = " ".join([VALUE_A, VALUE_B, VALUE_C, VALUE_U])
        done = run_hook(_payload(project, stdout=text), home)
        assert _out(done)["updatedToolOutput"]["stdout"] == " ".join(["[REDACTED:env-value]"] * 4)
        context = _out(done)["additionalContext"]
        for key in ("DB_PASSWORD", "API_TOKEN", "SERVICE_SECRET", "UNICODE_SECRET"):
            assert key in context
        _assert_no_value(done, VALUE_A, VALUE_B, VALUE_C, VALUE_U)

    def test_unicode_around_a_value(self, project, home):
        done = run_hook(_payload(project, stdout=f"🔑 值:{VALUE_B}✓ é"), home)
        assert _out(done)["updatedToolOutput"]["stdout"] == "🔑 值:[REDACTED:env-value]✓ é"

    def test_short_placeholder_and_plain_word_values_are_ignored(self, project, home):
        text = "abc12 your_api_key_here production eu-west-1 /var/log/app"
        done = run_hook(_payload(project, stdout=text), home)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")

    def test_example_files_are_not_read(self, project, home):
        done = run_hook(_payload(project, stdout=EXAMPLE_VALUE), home)
        assert (done.returncode, done.stdout) == (0, b"")

    def test_an_env_file_one_directory_down_is_read(self, project, home):
        done = run_hook(_payload(project, stdout=f"got {NESTED_VALUE}"), home)
        out = _out(done)
        assert out["updatedToolOutput"]["stdout"] == "got [REDACTED:env-value]"
        assert "NESTED_KEY from sub/.env" in out["additionalContext"]
        _assert_no_value(done, NESTED_VALUE)

    def test_an_env_file_two_directories_down_is_read(self, project, home):
        """F10 (rv2): a monorepo's apps/api/.env, the case the docstring cites."""
        folder = project / "apps" / "api"
        folder.mkdir(parents=True)
        (folder / ".env").write_text(f"API_TOKEN={DEEP_VALUE}\n", encoding="utf-8")
        done = run_hook(_payload(project, stdout=f"got {DEEP_VALUE}"), home)
        out = _out(done)
        assert out["updatedToolOutput"]["stdout"] == "got [REDACTED:env-value]"
        assert "API_TOKEN from apps/api/.env" in out["additionalContext"]
        _assert_no_value(done, DEEP_VALUE)

    @pytest.mark.parametrize("where", ["deep", "node_modules", ".git", "symlink"])
    def test_deeper_skipped_and_symlinked_env_files_are_not_read(self, project, home, tmp_path,
                                                                 where):
        if where == "deep":
            folder = project / "apps" / "api" / "v1"
        elif where == "symlink":
            folder = tmp_path / "outside"
            (project / "linked").symlink_to(folder, target_is_directory=True)
        else:
            folder = project / where / "pkg" if where == "node_modules" else project / where
        folder.mkdir(parents=True, exist_ok=True)
        if where == "node_modules":
            (project / "node_modules" / ".env").write_text(f"DEP_TOKEN={DEEP_VALUE}\n")
        (folder / ".env").write_text(f"DEP_TOKEN={DEEP_VALUE}\n", encoding="utf-8")
        done = run_hook(_payload(project, stdout=DEEP_VALUE), home)
        assert (done.returncode, done.stdout) == (0, b"")

    def test_odd_subdirectory_names_and_many_subdirectories_stay_bounded(self, project, home):
        odd = project / "ünï code dir‮"
        odd.mkdir()
        (odd / "prod.env").write_text(f"ODD_SECRET={DEEP_VALUE}\n", encoding="utf-8")
        done = run_hook(_payload(project, stdout=DEEP_VALUE), home)
        assert _out(done)["updatedToolOutput"]["stdout"] == "[REDACTED:env-value]"
        for index in range(250):
            (project / f"d{index:03d}").mkdir()
        started = time.monotonic()
        done = run_hook(_payload(project, stdout=f"{VALUE_A} {DEEP_VALUE}"), home)
        assert time.monotonic() - started < 30
        # The odd directory sorts after d000..d249, past MAX_SUBDIRS: the root file still counts.
        assert _out(done)["updatedToolOutput"]["stdout"].startswith("[REDACTED:env-value] ")

    def test_a_subdirectory_cwd_reads_the_git_toplevel(self, project, home):
        done = run_hook(_payload(project / "sub", stdout=VALUE_A), home)
        assert _out(done)["updatedToolOutput"]["stdout"] == "[REDACTED:env-value]"

    def test_outside_git_the_cwd_is_the_root(self, tmp_path, home):
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / ".env").write_text(f"DB_PASSWORD={VALUE_A}\n", encoding="utf-8")
        done = run_hook(_payload(plain, stdout=VALUE_A), home)
        assert _out(done)["updatedToolOutput"]["stdout"] == "[REDACTED:env-value]"

    def test_url_encoded_and_base64_values_are_replaced(self, project, home):
        special = "Pa55:w0rd/With&Sym"
        (project / ".env.test").write_text(f"DB_PASS={special}\n", encoding="utf-8")
        encoded = base64.b64encode(VALUE_A.encode()).decode().rstrip("=")
        done = run_hook(_payload(project, stdout=f"{quote(special, safe='')} {encoded}"), home)
        new = _out(done)["updatedToolOutput"]["stdout"]
        assert quote(special, safe="") not in new and encoded not in new
        _assert_no_value(done, special)

    def test_a_value_split_across_lines_is_named_but_cannot_be_replaced(self, project, home):
        """Known limit: a value wrapped over two lines is seen (the notice says so) but not
        replaced, since no single span of the output holds it."""
        done = run_hook(_payload(project, stdout=f"{VALUE_A[:7]}\n{VALUE_A[7:]}\n"), home)
        out = _out(done)
        assert "updatedToolOutput" not in out
        assert "split across lines" in out["additionalContext"]
        assert "DB_PASSWORD" in out["additionalContext"]

    def test_a_missing_env_file_gives_nothing(self, tmp_path, home):
        bare = tmp_path / "bare"
        bare.mkdir()
        _git_init(bare)
        done = run_hook(_payload(bare, stdout=VALUE_A), home)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads 000")
    def test_an_unreadable_env_file_is_skipped_quietly(self, project, home):
        (project / ".env").chmod(0)
        try:
            done = run_hook(_payload(project, stdout=f"{VALUE_A} {VALUE_D}"), home)
        finally:
            (project / ".env").chmod(0o600)
        out = _out(done)
        assert out["updatedToolOutput"]["stdout"] == f"{VALUE_A} [REDACTED:env-value]"
        assert done.stderr == b""


# --- the shared detector ----------------------------------------------------------------

class TestDetector:
    def test_a_detector_class_is_replaced_without_any_env_file(self, tmp_path, home):
        bare = tmp_path / "bare"
        bare.mkdir()
        done = run_hook(_payload(bare, stdout=f"remote token {GITHUB_TOKEN} set"), home)
        out = _out(done)
        assert GITHUB_TOKEN not in out["updatedToolOutput"]["stdout"]
        assert "[REDACTED:" in out["updatedToolOutput"]["stdout"]
        assert "not repeat" in out["additionalContext"]
        _assert_no_value(done, GITHUB_TOKEN)


# --- size, shape and fail-open ----------------------------------------------------------

class TestBoundaries:
    def test_clean_output_gives_no_output_at_all(self, project, home):
        done = run_hook(_payload(project, stdout="all 42 tests passed\n"), home)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")

    def test_huge_output_is_scanned_up_to_the_cap_in_bounded_time(self, project, home):
        filler = "lorem ipsum dolor sit amet " * 200_000          # about 5.4 MB
        stdout = VALUE_A + filler + VALUE_B
        started = time.monotonic()
        done = run_hook(_payload(project, stdout=stdout), home)
        assert time.monotonic() - started < 20
        new = _out(done)["updatedToolOutput"]["stdout"]
        assert new.startswith("[REDACTED:env-value]lorem")
        # Known limit: past the scan cap a value is left as it was.
        assert new.endswith(VALUE_B) and len(new) == len(stdout) - len(VALUE_A) + 20

    @pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"tool_name": "Bash"}',
                                     b'{"tool_name": "Bash", "tool_response": "text"}',
                                     b'{"tool_name": "Bash", "cwd": 5, "tool_response": {}}'])
    def test_odd_stdin_gives_nothing(self, raw, home):
        done = run_hook(None, home, raw=raw)
        assert (done.returncode, done.stdout) == (0, b"")

    def test_another_tool_gives_nothing(self, project, home):
        done = run_hook(_payload(project, stdout=VALUE_A, tool="Read"), home)
        assert (done.returncode, done.stdout) == (0, b"")

    def test_odd_shapes_keep_their_types(self, project, home):
        payload = _payload(project, stdout=VALUE_A)
        payload["tool_response"]["extra"] = [1, None, {"nested": f"n {VALUE_D}"}, 2.5]
        new = _out(run_hook(payload, home))["updatedToolOutput"]
        assert new["extra"] == [1, None, {"nested": "n [REDACTED:env-value]"}, 2.5]
        assert new["interrupted"] is False and new["isImage"] is False


# --- registration and the whole dispatcher path -----------------------------------------

class TestThroughTheDispatcher:
    def test_registered_on_post_tool_use_bash(self):
        assert any(check.name == "output_secret_scan" and check.matcher == "Bash"
                   for check in registry.checks_for("PostToolUse"))

    def test_no_value_reaches_stdout_stderr_or_any_written_file(self, project, home):
        group = next(",".join(names) for names in registry.tool_groups("PostToolUse")
                     if "Bash" in names)
        payload = _payload(project, stdout=f"{VALUE_A} {GITHUB_TOKEN}", stderr=VALUE_D)
        env = {**_env(home), "CLAUDE_PROJECT_DIR": str(project)}
        done = subprocess.run([sys.executable, str(DISPATCH), "PostToolUse", group],
                              input=json.dumps(payload).encode(), capture_output=True,
                              env=env, timeout=120)
        assert done.returncode == 0
        out = json.loads(done.stdout)["hookSpecificOutput"]
        assert set(out["updatedToolOutput"]) == {"stdout", "stderr", "interrupted", "isImage"}
        assert "not repeat" in out["additionalContext"]
        _assert_no_value(done, VALUE_A, VALUE_D, GITHUB_TOKEN)
        fixtures = {project / ".env", project / ".env.local"}
        for path in home.rglob("*"):
            if path.is_file() and path not in fixtures and ".git" not in path.parts:
                data = path.read_bytes()
                for value in (VALUE_A, VALUE_D, GITHUB_TOKEN):
                    assert value.encode() not in data, path


# ── 1.5.0 leakfix L2: the .env class is env_guard's ───────────────────────────────────────
# The env-file list comes from env_guard.file_is_protected, so prod.env, .envrc and an
# upper-case .ENV.local are read; a template (.env.example, prod.env.sample) still is not.
CLASS_VALUES = {"prod.env": "Pr0dEnv" + "V4lueQx9Lm2",
                ".envrc": "Env" + "Rc7Qm3Xv9Lp2Tk",
                ".ENV.local": "Upp3r" + "CaseQx9Lm2Wz"}


@pytest.mark.parametrize("name", sorted(CLASS_VALUES))
def test_leakfix_every_env_class_file_is_read(project, home, name):
    (project / name).write_text(f"export SERVICE_TOKEN={CLASS_VALUES[name]}\n", encoding="utf-8")
    done = run_hook(_payload(project, stdout=f"got {CLASS_VALUES[name]}"), home)
    assert _out(done)["updatedToolOutput"]["stdout"] == "got [REDACTED:env-value]"


def test_leakfix_a_template_of_the_class_is_still_not_read(project, home):
    (project / "prod.env.sample").write_text(f"SERVICE_TOKEN={EXAMPLE_VALUE}\n", encoding="utf-8")
    done = run_hook(_payload(project, stdout=EXAMPLE_VALUE), home)
    assert (done.returncode, done.stdout) == (0, b"")


def test_leakfix_a_directory_named_like_the_class_is_not_read(project, home):
    (project / "venv.env").mkdir()
    done = run_hook(_payload(project, stdout=VALUE_A), home)
    assert _out(done)["updatedToolOutput"]["stdout"] == "[REDACTED:env-value]"
