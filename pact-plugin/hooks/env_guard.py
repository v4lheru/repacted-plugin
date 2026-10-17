"""
Location: pact-plugin/hooks/env_guard.py
Summary: The env guard's ENTRY (PreToolUse on Bash: grep_output, decide, main, _emit) and,
         for one release, its FAÇADE: the classifiers live in hooks/shell/env/
         (the design notes) and every name the hooks and the test
         suite import from here is re-exported below. The re-exports retire next
         release with the importers re-pointed in the same commit.
Used by: hooks.json (the Bash PreToolUse chain), the test suite.
Map: grep_output, decide, main, _emit.

Keep .env-class file contents out of the transcript (PreToolUse Read|NotebookRead|Grep|Bash).

Class: .env, .env.*, *.env, .envrc, .netrc, by written name and resolved path; templates
(.example, .sample, .template, .dist) pass unless linked to a protected file. Read and
NotebookRead deny a protected file; Grep denies a protected target and otherwise appends
exclusion globs (updatedInput). Bash denies only printing, dumping the environment, sending,
staging or copying a protected file; env-get and data mentions pass. Bash is read through
shell_words.read_script and judged in every directory it may run in. Fail-closed on what
cannot be read; it sees the call, never the output, so it cannot scrub output. Deny:
envelope on stdout, reason on stderr, exit 2; rewrite: updatedInput, exit 0; allow:
{"suppressOutput": true}, exit 0.
Map, in file order: protected names and globs; Bash word checks and Grep filters; which
commands print a protected file (readers, heredocs); where a command runs (_Directory,
variables, stdin, judge, bash_verdict); git egress (D29); dispatch (grep_output, main).
Doc: the design notes
"""
from __future__ import annotations

import json
import os
import sys
from shell.env import git_egress as _git_egress
from shell.env.git_egress import (Notice)
from shell.env.names import (mentions_class, path_is_protected)
from shell.env.programs import (GREP_EXCLUSION, GREP_HINT, PARSE_MESSAGE, _ALLOW_OUTPUT)
from shell.env.script_verdict import (bash_verdict)
from shell.env.search import (_deny, _named_message)
from shell.env.programs import (  # noqa: F401  # re-export: façade
    DUMP_MESSAGE,
    RULES,
    RULE_MARKS,
    rule_of,
    EGRESS_PROGRAMS,
    FIND_ACTIONS,
    GIT_PATCH_OPTIONS,
    GIT_SAFE,
    GREP_EXCLUSION,
    GREP_HINT,
    GREP_PROGRAMS,
    LOADERS,
    NAME_CONSUMERS,
    OPERAND_READERS,
    PARSE_MESSAGE,
    PRINTERS,
    READERS,
    RG_PROGRAMS,
    SILENT_STDIN,
    SUGGESTION,
    UNREADABLE_MESSAGE,
    WALK_LIMIT,
    WALK_SKIP_DIRS,
    _ALLOW_OUTPUT,
    _DUMP_RUNNERS,
    _FILTER_OPTIONS,
    _GIT_VALUE_OPTIONS,
    _GREP_VALUE_OPTIONS,
    _RG_VALUE_OPTIONS,
)
from shell.env.names import (  # noqa: F401  # re-export: façade
    CLASS_SAMPLES,
    GIT_TEMPLATE_SUFFIXES,
    _BRACE_LIMIT,
    _MENTION,
    _POSIX_CLASSES,
    _SET_PART,
    _STAR,
    _WRITE_REDIRECT,
    _absolute,
    _brace_expansions,
    _bracket,
    _candidates,
    _glob_consumes,
    _glob_reaches_class,
    _glob_tokens,
    _python_glob,
    _token_matches,
    file_is_protected,
    is_protected_name,
    is_template_name,
    mentions_class,
    path_is_protected,
    token_protected,
)
from shell.env.search import (  # noqa: F401  # re-export: façade
    SCRATCH_HINT,
    _GrepFilters,
    _PRINTS,
    _TooLarge,
    _cut_lists_names_only,
    _deny,
    _excluded,
    _file_keys,
    _git_verdict,
    _grep_is_recursive,
    _included,
    _named_message,
    _option_positions,
    _option_values,
    _pattern_by_option,
    _positionals,
    _recurse_verdict,
    _search_roots,
    _sees_hidden,
    _skipped,
    _standing_excludes,
    dumps_environment,
    first_protected_file,
    recursive_search,
)
from shell.env.script import (  # noqa: F401  # re-export: façade
    SHELLS,
    _ASSIGNMENT,
    _BODY,
    _BODY_KEY,
    _BODY_RUNNERS,
    _DYNAMIC_SETTERS,
    _FD_COPY,
    _HEREDOC_OPERATOR,
    _IGNORE_WRITE,
    _INTERPRETER,
    _LEADING_ASSIGNMENT,
    _OPERATOR_PART,
    _QUOTED_ENV_GET,
    _REDIRECTS,
    _REDIRECT_FD,
    _REDIRECT_IN,
    _RESPELLED,
    _SCRIPT_OPTIONS,
    _SCRIPT_PRINTS,
    _SCRIPT_RUNS,
    _STDIN_PRINTERS,
    _STDOUT_SHOWN,
    _STDOUT_TO_FILE,
    _STRING_LITERAL,
    _SUBSTITUTION,
    _SUB_KEY,
    _SUB_MARK,
    _State,
    _Step,
    _UNSEEN_IGNORE,
    _VARIABLE_USE,
    _WORD_SAFE,
    _command_steps,
    _computed,
    _env_get_variables,
    _expand_line,
    _expand_once,
    _heredoc_expansions,
    _literal_rounds,
    _pull_substitutions,
    _read,
    _script_prints_file,
    _sets_variables_unseen,
    _simple_commands,
    _spell_rounds,
    expand_literal_variables,
    normalize_env_get,
)
from shell.env.places import (  # noqa: F401  # re-export: façade
    MAX_PLACES,
    _BACKGROUND,
    _CDPATH_SET,
    _CHDIR_OPTION,
    _COMPOUND_CLOSE,
    _COMPOUND_OPEN,
    _Directory,
    _FREEZERS,
    _LITERAL_ASSIGNMENT,
    _MOVERS,
    _MOVER_WORD,
    _PIPE,
    _PLACE_NAMES,
    _cd_target,
    _fed_or_failing,
    _merge,
    _names_only_search,
    _search_skip,
)
from shell.env.judge import (  # noqa: F401  # re-export: façade
    _BODY_WORD,
    _BODY_WORDS_READ,
    _COMPOUND_END,
    _COMPUTED,
    _DATA_CONSUMERS,
    _ENV_READ,
    _ESCAPES,
    _GIT_NAMES_FROM_STDIN,
    _LOADED,
    _NAMES_FROM_STDIN,
    _PAGERS,
    _PRINTF_SPEC,
    _SSH_VALUE_OPTIONS,
    _STDIN_ACTORS,
    _STDIN_AS_FILE,
    _TEXT_COMMANDS,
    _body_as_script,
    _body_names,
    _echoes_request,
    _glued_unknown,
    _group_words,
    _heredoc_verdict,
    _inner_script,
    _is_env_get,
    _load,
    _printed_text,
    _printing_words,
    _prints_loaded,
    _program,
    _reads_script_from_stdin,
    _script_on_stdin,
    _stdin_role,
    _sub_names,
    _sub_paths,
    _sub_reads,
    _sub_verdict,
    _trace_switch,
    _unescape,
    _uses_loaded,
    command_verdict,
)
from shell.env.script_verdict import (  # noqa: F401  # re-export: façade
    _writes_env_get,
    _PATH_VARIABLE,
    _expand_path_variables,
    _fed_text_verdict,
    _fed_texts,
    _find_filters,
    _listed_names_verdict,
    _loose_verdict,
    _pattern_words,
    _procsub_consumer,
    _quiet_group,
    _script_verdict,
    _spell_pairs,
    bash_verdict,
    judge,
)
from shell.env.git_egress import (  # noqa: F401  # re-export: façade
    CREATED_REPO,
    CREDENTIAL_NAMES,
    CREDENTIAL_SUFFIXES,
    EXPANDED_GIT,
    EXPANDED_PUSH,
    GIT_TIMEOUT_S,
    Notice,
    PUSH_TIMEOUT_S,
    UNCHECKED_STAGING,
    UNKNOWN_DIRECTORY,
    UNVERIFIED_PUSH,
    _EXPANDED_WORD,
    _GIT_ADD_VALUE_OPTIONS,
    _GIT_COMMIT_VALUE_LETTERS,
    _GIT_COMMIT_VALUE_OPTIONS,
    _GIT_DIFF_NAMES_ONLY,
    _GIT_PUSH_VALUE_OPTIONS,
    _GIT_SHOW_PATCH,
    _Unverifiable,
    _check_add,
    _check_commit,
    _check_push,
    _check_show,
    _commit_flags,
    _env_only,
    _first_protected,
    _git_names,
    _git_pass,
    _git_split,
    _in_repo,
    _inline_alias,
    _known_directories,
    _push_sources,
    _resolves_none,
    _run_git,
    _stage_message,
    _unknown_pathspecs,
    git_egress_pass,
    git_egress_verdict,
    git_protected_kind,
    is_credential_file,
)

# ─── Patch propagation (lives and dies with the façade) ──────────────────────────
# The suite patches names on this module (monkeypatch.setattr(guard, "WALK_LIMIT", ...)).
# Every reader of such a name now lives in a package module with its own binding, so a
# plain setattr here would reach no reader. Setting an attribute on this module therefore
# also sets it on every shell.env module that binds the name (the definer and its
# importers); the monkeypatch undo takes the same route. tests/contract/test_shell_package.py
# pins it. When the façade retires, the patches move to the module that reads the name.
import sys as _sys
import types as _types

_PACKAGE_MODULES = tuple(_sys.modules[f"shell.env.{_name}"] for _name in ("programs", "names", "search", "script", "places", "judge", "script_verdict", "git_egress"))


class _PropagatingFacade(_types.ModuleType):
    def __setattr__(self, name, value):
        for module in _PACKAGE_MODULES:
            if name in module.__dict__:
                module.__dict__[name] = value
        super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _PropagatingFacade

# ── dispatch ──────────────────────────────────────────────────────────────────────────
def grep_output(tool_input: dict, cwd: str) -> dict | None:
    path = tool_input.get("path")
    if isinstance(path, str) and path_is_protected(path, cwd):
        return _deny(_named_message(path))
    field = tool_input.get("glob")
    if field is not None and not isinstance(field, str):
        return _deny("Blocked: Grep's glob is not a string, so .env-class files cannot be "
                     "excluded from this search; pass glob as one string.")
    field = field or ""
    if not GREP_EXCLUSION or field.endswith(GREP_EXCLUSION):
        return None
    specific = {"hookEventName": "PreToolUse",
                "updatedInput": {**tool_input, "glob": f"{field} {GREP_EXCLUSION}".strip()}}
    if mentions_class(field):
        specific["additionalContext"] = GREP_HINT
    return {"hookSpecificOutput": specific}


def decide(payload: dict) -> dict | None:
    """The hook's output for one payload: a deny, a Grep rewrite, or None to allow."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    if tool in ("Read", "NotebookRead"):
        for key in ("file_path", "notebook_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str) and path_is_protected(value, cwd):
                return _deny(_named_message(value))
        return None
    if tool == "Grep":
        return grep_output(tool_input, cwd)
    if tool == "Bash":
        command = tool_input.get("command")
        reason = bash_verdict(command, cwd) if isinstance(command, str) else None
        if isinstance(reason, Notice):
            return {"systemMessage": str(reason),
                    "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "additionalContext": str(reason)}}
        return _deny(reason) if reason else None
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            return _emit(None)
    except ValueError:
        return _emit(_deny(PARSE_MESSAGE) if mentions_class(raw) else None)
    try:
        return _emit(decide(payload))
    except Exception as error:   # noqa: BLE001 - fail closed only where the class is named
        mentioned = mentions_class(json.dumps(payload.get("tool_input", "")))
        print(f"env_guard: internal error ({type(error).__name__}), "
              f"{'denying' if mentioned else 'allowing'}", file=sys.stderr)
        return _emit(_deny(PARSE_MESSAGE) if mentioned else None)


def _load_detector() -> tuple:
    """The commit check's secret detector for the push scan (shared.scrub, the same functions
    git_commit_check runs), loaded only when a push is checked: this hook runs before every
    Bash call, and nothing under hooks/shell imports the shared package itself."""
    from shared.scrub import ALLOWLIST_PRAGMA, pragma_notice, scan_added_lines
    return ALLOWLIST_PRAGMA, pragma_notice, scan_added_lines


_git_egress.load_detector = _load_detector


def _emit(output: dict | None) -> int:
    if not output:
        print(_ALLOW_OUTPUT)
        return 0
    print(json.dumps(output))
    specific = output["hookSpecificOutput"]
    if specific.get("permissionDecision") == "deny":
        print(specific["permissionDecisionReason"], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
