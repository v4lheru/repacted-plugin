#!/usr/bin/env bash
# dev/clean-room/run.sh: install the plugin the way a stranger would and check it, with no model call.
#
# WHAT THIS DOES
#   Copies the tree into a scratch directory, re-runs itself under `env -i` with a scratch HOME and
#   CLAUDE_CONFIG_DIR and a PATH that holds only the tools it needs, and runs these steps in order,
#   stopping at the first failure:
#     preflight  python3 3.9+, git, perl and the claude CLI are present (and the CLI version, if pinned)
#     install    claude plugin marketplace add <tree copy>; claude plugin install repacted@<marketplace>
#     list       claude plugin list --json shows the plugin enabled at its plugin.json version
#     validate   claude plugin validate --strict on the installed cache copy and on the marketplace
#     purity     the shipped purity scan (public mode) over a byte-identical copy of the cache copy
#     init       repact-init --yes --repo twice: the second run says "nothing to do" and changes nothing
#     doctor     repact-doctor --full exits 0, no red check, amber only from the allow list below
#     hooks      every hooks/hooks.json entry point once, with a synthetic payload: exit 0
#     bin        every bin/ tool with --help: exit 0
#     uninstall  claude plugin uninstall; the plugin is gone from the list and the install record
#   The output of every step is also scanned: a Python traceback, the string "user-layer", a
#   home-style path (/Users/<x> or /home/<x>) outside this run's scratch root, or the invoking user's
#   real HOME anywhere fails the step. A summary prints at the end.
#
# USAGE
#   bash dev/clean-room/run.sh [options] TREE
#     TREE                 a staged export tree or a public checkout (holds .claude-plugin/marketplace.json)
#     --installed DIR      check an already installed plugin directory instead: no install, list or
#                          uninstall step, and TREE is not needed
#     --source SPEC        add this marketplace source instead of the tree copy (owner/repo or a URL);
#                          the only mode that uses the network
#     --expect-cli VER     fail unless `claude --version` reports VER
#     --plant KIND         control run: plant a bin/ tool that prints a traceback (KIND=traceback) or
#                          a home path (KIND=home-path) and exits 0; the run must then FAIL at `bin`
#     --keep               keep the scratch directory (it is kept anyway when a step fails)
#   Exit codes: 0 every step passed; 1 a step failed; 64 usage.
#
# ENVIRONMENT (read before the env -i re-exec, nothing else crosses it)
#   CLEANROOM_PYTHON       interpreter to use (default: python3 on PATH)
#   CLEANROOM_CLAUDE       claude CLI to use (default: claude on PATH)
#   TMPDIR                 where the scratch root is made (must not sit under HOME)
#
# NETWORK
#   None in the default mode: the marketplace is a local folder and the CLI's non-essential traffic
#   is switched off. --source fetches the marketplace, so it needs the network.
#
# Requires bash 3.2+ (the macOS system bash), python3 3.9+, git, perl (the per-command timeout) and
# the Claude Code CLI. Runs on macOS and Linux; CI runs it on ubuntu and macOS, and the Dockerfile
# beside this script runs it in a container (`make gate`).

set -u

PLUGIN_NAME="repacted"
STEP_TIMEOUT=300            # seconds per command
# repact-doctor check ids a fresh install may leave amber. Contract 10 allows semantic-search
# extras, Telegram not configured, no profile and KB off; today none of those is an amber check,
# so the list is empty, matching FRESH_AMBER_ALLOWED in tests/test_repact_doctor.py.
DOCTOR_AMBER_ALLOWED=""

die_usage() { echo "run.sh: $*" >&2; echo "usage: run.sh [--installed DIR] [--source SPEC] [--expect-cli VER] [--plant traceback|home-path] [--keep] TREE" >&2; exit 64; }

# ---------------------------------------------------------------------------------------------
# Outer half: resolve the inputs, make the scratch root, then re-exec under env -i.
# ---------------------------------------------------------------------------------------------
if [ -z "${CLEANROOM_INNER:-}" ]; then
  TREE="" INSTALLED="" SOURCE="" EXPECT_CLI="" PLANT="" KEEP=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --installed) [ $# -ge 2 ] || die_usage "--installed needs a directory"; INSTALLED="$2"; shift 2 ;;
      --source) [ $# -ge 2 ] || die_usage "--source needs a value"; SOURCE="$2"; shift 2 ;;
      --expect-cli) [ $# -ge 2 ] || die_usage "--expect-cli needs a version"; EXPECT_CLI="$2"; shift 2 ;;
      --plant) [ $# -ge 2 ] || die_usage "--plant needs traceback or home-path"; PLANT="$2"; shift 2 ;;
      --keep) KEEP=1; shift ;;
      -h|--help) sed -n '2,48p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      -*) die_usage "unknown option $1" ;;
      *) [ -z "$TREE" ] || die_usage "one TREE only"; TREE="$1"; shift ;;
    esac
  done
  case "$PLANT" in ""|traceback|home-path) ;; *) die_usage "--plant takes traceback or home-path" ;; esac
  if [ -n "$INSTALLED" ]; then
    [ -d "$INSTALLED/.claude-plugin" ] || die_usage "$INSTALLED is not a plugin directory (no .claude-plugin/)"
    [ -z "$PLANT" ] || die_usage "--plant needs TREE mode (the installed directory is never written)"
    INSTALLED=$(cd "$INSTALLED" && pwd -P)
  elif [ -n "$TREE" ]; then
    [ -f "$TREE/.claude-plugin/marketplace.json" ] || die_usage "$TREE has no .claude-plugin/marketplace.json"
    TREE=$(cd "$TREE" && pwd -P)
  elif [ -z "$SOURCE" ]; then
    die_usage "give TREE, --installed DIR or --source SPEC"
  fi
  [ -z "$PLANT" ] || [ -n "$TREE" ] || die_usage "--plant needs TREE"

  PY=${CLEANROOM_PYTHON:-$(command -v python3 || true)}
  CL=${CLEANROOM_CLAUDE:-$(command -v claude || true)}
  [ -n "$PY" ] && [ -x "$PY" ] || { echo "FAIL preflight: python3 not found (set CLEANROOM_PYTHON)"; exit 1; }
  [ -n "$CL" ] && [ -x "$CL" ] || { echo "FAIL preflight: claude CLI not found (set CLEANROOM_CLAUDE)"; exit 1; }

  REAL_HOME=${HOME:-}
  BASE=${TMPDIR:-/tmp}
  SCRATCH=$(mktemp -d "${BASE%/}/repacted-cleanroom.XXXXXX") || { echo "FAIL preflight: mktemp"; exit 1; }
  SCRATCH=$(cd "$SCRATCH" && pwd -P)
  case "$SCRATCH/" in
    "$REAL_HOME"/*) echo "FAIL preflight: scratch root $SCRATCH sits under HOME; set TMPDIR elsewhere"; rm -rf -- "$SCRATCH"; exit 1 ;;
  esac

  # A tool directory holding only what the run needs, so Homebrew or user tools on the caller's
  # PATH (rtk, gh, a second python) cannot leak into the check.
  # Each entry is a one-line exec wrapper, not a symlink: npm and pnpm shims resolve their package
  # from their own path, which a symlink would move.
  mkdir -p "$SCRATCH/toolbin"
  wrap() { printf '#!/bin/sh\nexec "%s" "$@"\n' "$2" >"$SCRATCH/toolbin/$1"; chmod 755 "$SCRATCH/toolbin/$1"; }
  wrap python3 "$PY"
  wrap claude "$CL"
  for tool in git perl node; do
    path=$(command -v "$tool" || true)
    [ -n "$path" ] && wrap "$tool" "$path"
  done

  exec env -i \
    CLEANROOM_INNER=1 CLEANROOM_SCRATCH="$SCRATCH" CLEANROOM_REAL_HOME="$REAL_HOME" \
    CLEANROOM_TREE="$TREE" CLEANROOM_INSTALLED="$INSTALLED" CLEANROOM_SOURCE="$SOURCE" \
    CLEANROOM_EXPECT_CLI="$EXPECT_CLI" CLEANROOM_PLANT="$PLANT" CLEANROOM_KEEP="$KEEP" \
    HOME="$SCRATCH/home" CLAUDE_CONFIG_DIR="$SCRATCH/home/.claude" TMPDIR="$SCRATCH/tmp" \
    PATH="$SCRATCH/toolbin:/usr/bin:/bin" LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM=dumb \
    DISABLE_AUTOUPDATER=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 PYTHONDONTWRITEBYTECODE=1 REPACT_DOCTOR_NO_REFRESH=1 \
    bash "$0"
fi

# ---------------------------------------------------------------------------------------------
# Inner half: runs under env -i.
# ---------------------------------------------------------------------------------------------
SCRATCH=$CLEANROOM_SCRATCH
LOGS="$SCRATCH/logs"
PROJ="$SCRATCH/project"
mkdir -p "$HOME" "$CLAUDE_CONFIG_DIR" "$TMPDIR" "$LOGS" "$PROJ"
SUMMARY=""
START=$SECONDS

finish() {
  rc=$1
  echo
  echo "clean-room summary"
  printf '%s' "$SUMMARY"
  if [ "$rc" -eq 0 ]; then
    echo "RESULT: PASS ($((SECONDS - START))s)"
    if [ -z "$CLEANROOM_KEEP" ]; then rm -rf -- "$SCRATCH"; else echo "scratch kept: $SCRATCH"; fi
  else
    echo "RESULT: FAIL ($((SECONDS - START))s); logs kept in $LOGS"
  fi
  exit "$rc"
}

fail() {   # fail STEP REASON [LOG]
  SUMMARY="${SUMMARY}FAIL  $1: $2
"
  echo "FAIL $1: $2"
  if [ -n "${3:-}" ] && [ -f "$3" ]; then echo "---- last lines of $3"; tail -n 25 "$3"; echo "----"; fi
  finish 1
}

pass() { SUMMARY="${SUMMARY}ok    $1: $2
"; echo "ok   $1: $2"; }

# capped LOG CMD...: run CMD with stdin from /dev/null, append its output to LOG, return its exit code.
capped() {
  log=$1; shift
  { echo "\$ $*"; } >>"$log"
  perl -e 'alarm shift; exec @ARGV or exit 127' "$STEP_TIMEOUT" "$@" </dev/null >>"$log" 2>&1
}

# The output scan, run over every log. Prints a reason and exits 1 on the first leak.
SCAN_PY='
import os, re, sys
log, scratch, real_home = sys.argv[1], sys.argv[2], sys.argv[3]
allowed = {scratch, os.path.realpath(scratch)}
home_path = re.compile(r"(?<![\w.~-])(?:/Users|/home)/[^/\s\x27\"`:;,()<>]+[^\s\x27\"`:;,()<>]*")
text = open(log, encoding="utf-8", errors="replace").read()
for n, line in enumerate(text.splitlines(), 1):
    if "Traceback (most recent call last)" in line:
        sys.exit(f"Traceback at {os.path.basename(log)}:{n}")
    if "user-layer" in line:
        sys.exit(f"\"user-layer\" at {os.path.basename(log)}:{n}")
    if real_home not in ("", "/") and real_home in line:
        sys.exit(f"the caller'"'"'s real HOME at {os.path.basename(log)}:{n}")
    for m in home_path.finditer(line):
        if not any(m.group(0) == root or m.group(0).startswith(root + "/") for root in allowed):
            sys.exit(f"home-style path outside the scratch root at {os.path.basename(log)}:{n}")
'

scan() {   # scan STEP LOG
  reason=$(python3 -c "$SCAN_PY" "$2" "$SCRATCH" "$CLEANROOM_REAL_HOME" 2>&1) || fail "$1" "$reason" "$2"
}

json_get() {   # json_get FILE EXPR: print a value with python
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {"d": d}))' "$1" "$2"
}

# --- preflight ---------------------------------------------------------------------------------
L="$LOGS/preflight.log"
for tool in python3 git perl claude; do
  [ -x "$SCRATCH/toolbin/$tool" ] || fail preflight "$tool not found on the caller's PATH" "$L"
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail preflight "python3 is older than 3.9 ($(python3 -V 2>&1))"
capped "$L" claude --version || fail preflight "claude --version failed" "$L"
CLI_VER=$(sed -n 's/^\([0-9][0-9.]*\) (Claude Code).*/\1/p' "$L" | head -n 1)
[ -n "$CLI_VER" ] || fail preflight "could not read the claude version" "$L"
if [ -n "$CLEANROOM_EXPECT_CLI" ] && [ "$CLI_VER" != "$CLEANROOM_EXPECT_CLI" ]; then
  fail preflight "claude is $CLI_VER, expected $CLEANROOM_EXPECT_CLI" "$L"
fi
git init -q "$PROJ" >>"$L" 2>&1 || fail preflight "git init of the scratch project failed" "$L"
printf 'clean-room project\n' >"$PROJ/README.md"
scan preflight "$L"
pass preflight "python $(python3 -c 'import platform; print(platform.python_version())'), claude $CLI_VER, $(uname -s)"

# --- install -----------------------------------------------------------------------------------
PLUGIN_DIR=""
MKT=""
if [ -n "$CLEANROOM_INSTALLED" ]; then
  PLUGIN_DIR=$CLEANROOM_INSTALLED
  pass install "skipped: checking the installed directory given"
else
  L="$LOGS/install.log"
  if [ -n "$CLEANROOM_SOURCE" ]; then
    MKT_SOURCE=$CLEANROOM_SOURCE
  else
    MKT_SOURCE="$SCRATCH/tree"
    cp -R "$CLEANROOM_TREE" "$MKT_SOURCE" || fail install "could not copy the tree to scratch"
    rm -rf -- "$MKT_SOURCE/.git"
    if [ -n "$CLEANROOM_PLANT" ]; then
      rel=$(json_get "$MKT_SOURCE/.claude-plugin/marketplace.json" \
        '[p["source"] for p in d["plugins"] if p["name"] == "'"$PLUGIN_NAME"'"][0]')
      planted="$MKT_SOURCE/$rel/bin/zz-cleanroom-planted"
      if [ "$CLEANROOM_PLANT" = traceback ]; then
        printf '%s\n' '#!/usr/bin/env python3' 'import sys' \
          'print("Traceback (most recent call last):", file=sys.stderr)' \
          'print("PlantedError: clean-room control", file=sys.stderr)' >"$planted"
      else
        printf '%s\n' '#!/usr/bin/env python3' 'print("/" + "Users" + "/planted-control/notes.txt")' >"$planted"
      fi
      chmod 755 "$planted"
      echo "control: planted bin/zz-cleanroom-planted ($CLEANROOM_PLANT); the bin step must fail"
    fi
  fi
  capped "$L" claude plugin marketplace add "$MKT_SOURCE" || fail install "marketplace add failed" "$L"
  MKT=$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
names = [k for k in d if k != "anthropic-plugin-directory"]
print(names[0] if len(names) == 1 else "")' "$CLAUDE_CONFIG_DIR/plugins/known_marketplaces.json" 2>>"$L")
  [ -n "$MKT" ] || fail install "could not read the added marketplace name" "$L"
  capped "$L" claude plugin install "$PLUGIN_NAME@$MKT" || fail install "plugin install failed" "$L"
  scan install "$L"
  pass install "$PLUGIN_NAME@$MKT from $( [ -n "$CLEANROOM_SOURCE" ] && echo "$CLEANROOM_SOURCE" || echo "the tree copy")"

  # --- list ------------------------------------------------------------------------------------
  L="$LOGS/list.log"
  claude plugin list --json </dev/null >"$SCRATCH/list.json" 2>>"$L" || fail list "plugin list --json failed" "$L"
  cat "$SCRATCH/list.json" >>"$L"
  PLUGIN_DIR=$(python3 -c '
import json, sys
rows = [r for r in json.load(open(sys.argv[1])) if r.get("id") == sys.argv[2]]
print(rows[0]["installPath"] if len(rows) == 1 and rows[0].get("enabled") is True else "")' \
    "$SCRATCH/list.json" "$PLUGIN_NAME@$MKT" 2>>"$L")
  [ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] || fail list "$PLUGIN_NAME@$MKT is not listed as enabled" "$L"
  LISTED=$(json_get "$SCRATCH/list.json" '[r["version"] for r in d if r["id"] == "'"$PLUGIN_NAME@$MKT"'"][0]')
  DECLARED=$(json_get "$PLUGIN_DIR/.claude-plugin/plugin.json" 'd["version"]')
  [ "$LISTED" = "$DECLARED" ] || fail list "listed version $LISTED differs from plugin.json $DECLARED" "$L"
  scan list "$L"
  pass list "enabled at $LISTED"
fi
VERSION=$(json_get "$PLUGIN_DIR/.claude-plugin/plugin.json" 'd["version"]')
export CLAUDE_PLUGIN_ROOT="$PLUGIN_DIR"
export CLAUDE_PLUGIN_DATA="$CLAUDE_CONFIG_DIR/plugins/data/$PLUGIN_NAME-${MKT:-cleanroom}"
mkdir -p "$CLAUDE_PLUGIN_DATA"

# --- validate ----------------------------------------------------------------------------------
L="$LOGS/validate.log"
capped "$L" claude plugin validate --strict "$PLUGIN_DIR" || fail validate "validate --strict failed on the installed copy" "$L"
if [ -z "$CLEANROOM_INSTALLED" ] && [ -z "$CLEANROOM_SOURCE" ]; then
  capped "$L" claude plugin validate --strict "$SCRATCH/tree" || fail validate "validate --strict failed on the marketplace" "$L"
fi
scan validate "$L"
pass validate "installed copy$( [ -z "$CLEANROOM_INSTALLED$CLEANROOM_SOURCE" ] && echo " and marketplace")"

# --- purity (design gate G3 on the bytes a user gets) ------------------------------------------
L="$LOGS/purity.log"
# The scanner's core root is the directory holding pact-plugin/, and its baseline paths start
# with pact-plugin/; the cache copy is a bare version directory, so scan a copy under that name.
mkdir -p "$SCRATCH/g3"
cp -R "$PLUGIN_DIR" "$SCRATCH/g3/pact-plugin" || fail purity "could not copy the installed directory"
diff -r "$PLUGIN_DIR" "$SCRATCH/g3/pact-plugin" >>"$L" 2>&1 || fail purity "the copy differs from the installed directory" "$L"
[ -f "$SCRATCH/g3/pact-plugin/tests/contract/_purity.py" ] || fail purity "the installed copy has no tests/contract/_purity.py" "$L"
# The baseline also lists files outside the plugin directory (the marketplace manifest, docs),
# which an install never copies, so the comparison is limited to the files that did ship.
PURITY_PY='
import sys
sys.path.insert(0, "tests/contract")
import _purity as p
ctx = p.context_from_env()
shipped = set(p.core_files())
result = p.compare(p.scan_tree(ctx), p.load_baseline(), ctx, paths=shipped)
for label, items in (("NEW", result.new), ("STALE", result.stale)):
    for cls, count in sorted(p._counts(items).items()):
        print(label, cls, count)
summary = "%d new, %d stale over %d shipped files (mode %s)" % (len(result.new), len(result.stale), len(shipped), ctx.mode)
print("purity: " + summary)
sys.exit(1 if result.new or result.stale else 0)
'
( cd "$SCRATCH/g3/pact-plugin" && export REPACT_PURITY_NO_DENYLIST=1 \
    && capped "$L" python3 -c "$PURITY_PY" ) || fail purity "$(grep '^purity:' "$L" | tail -n 1)" "$L"
scan purity "$L"
pass purity "$(grep '^purity:' "$L" | tail -n 1 | sed 's/^purity: //')"

# --- init --------------------------------------------------------------------------------------
L="$LOGS/init.log"
snapshot() { ( cd "$SCRATCH" && find home/.claude/settings.json* project -path project/.git -prune -o -type f -print 2>/dev/null \
  | LC_ALL=C sort | while read -r f; do printf '%s ' "$f"; cksum <"$f"; done ) }
( cd "$PROJ" && capped "$L" python3 "$PLUGIN_DIR/bin/repact-init" --yes --repo "$PROJ" --no-doctor ) \
  || fail init "first repact-init run failed" "$L"
FIRST=$(snapshot)
: >"$SCRATCH/init2.log"
( cd "$PROJ" && capped "$SCRATCH/init2.log" python3 "$PLUGIN_DIR/bin/repact-init" --yes --repo "$PROJ" --no-doctor ) \
  || { cat "$SCRATCH/init2.log" >>"$L"; fail init "second repact-init run failed" "$L"; }
cat "$SCRATCH/init2.log" >>"$L"
grep -qi "nothing to do" "$SCRATCH/init2.log" || fail init "second run did not say \"nothing to do\"" "$L"
[ "$FIRST" = "$(snapshot)" ] || fail init "second run changed files" "$L"
scan init "$L"
pass init "applied once, second run: nothing to do"

# --- doctor ------------------------------------------------------------------------------------
L="$LOGS/doctor.log"
( cd "$PROJ" && capped "$L" python3 "$PLUGIN_DIR/bin/repact-doctor" --full ) || fail doctor "repact-doctor --full exited non-zero" "$L"
grep -q '^execution: ' "$L" || fail doctor "no execution: line" "$L"
grep -Eq '^host [^ ]+: (tested|untested) \(last tested [0-9.]+\)' "$L" || fail doctor "no host compat line" "$L"
DOCTOR_JSON="$CLAUDE_PLUGIN_DATA/doctor.json"
[ -f "$DOCTOR_JSON" ] || fail doctor "no doctor.json under CLAUDE_PLUGIN_DATA" "$L"
verdict=$(python3 -c '
import json, sys
checks = json.load(open(sys.argv[1]))["checks"]
allowed = set(sys.argv[2].split())
red = sorted(c["id"] for c in checks if c["state"] == "red")
amber = sorted(c["id"] for c in checks if c["state"] == "amber" and c["id"] not in allowed)
if red or amber:
    sys.exit("red: %s; amber off the allow list: %s" % (", ".join(red) or "none", ", ".join(amber) or "none"))
print("%d checks, no red, no amber off the allow list" % len(checks))' "$DOCTOR_JSON" "$DOCTOR_AMBER_ALLOWED" 2>&1) \
  || fail doctor "$verdict" "$L"
scan doctor "$L"
pass doctor "$verdict; $(grep '^execution: ' "$L" | head -n 1)"

# --- hooks -------------------------------------------------------------------------------------
L="$LOGS/hooks.log"
HOOKS_PY='
import json, os, re, subprocess, sys
root, proj, scratch, timeout = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
spec = json.load(open(os.path.join(root, "hooks", "hooks.json")))["hooks"]
target = os.path.join(proj, "README.md")
transcript = os.path.join(scratch, "transcript.jsonl")
open(transcript, "a").close()
inputs = {
    "Bash": {"command": "ls"},
    "Read": {"file_path": target},
    "Edit": {"file_path": target, "old_string": "clean-room", "new_string": "clean room"},
    "Write": {"file_path": target, "content": "clean-room project\n"},
    "MultiEdit": {"file_path": target, "edits": []},
    "NotebookEdit": {"notebook_path": os.path.join(proj, "n.ipynb"), "new_source": ""},
    "NotebookRead": {"notebook_path": os.path.join(proj, "n.ipynb")},
    "Grep": {"pattern": "clean", "path": proj},
    "WebFetch": {"url": "https://example.com/", "prompt": "title"},
    "Artifact": {"file_path": target},
    "Agent": {"description": "probe", "prompt": "probe", "subagent_type": "general-purpose"},
    "Task": {"description": "probe", "prompt": "probe", "subagent_type": "general-purpose"},
    "TaskCreate": {"subject": "probe", "description": "probe"},
    "TaskUpdate": {"taskId": "1", "status": "in_progress"},
    "AskUserQuestion": {"questions": [{"question": "ok?", "header": "probe", "multiSelect": False,
                                       "options": [{"label": "yes", "description": "y"},
                                                   {"label": "no", "description": "n"}]}]},
    "mcp__cleanroom__noop": {},
}
extra = {
    "SessionStart": {"source": "startup"},
    "SessionEnd": {"reason": "other"},
    "UserPromptSubmit": {"prompt": "hello"},
    "PreCompact": {"trigger": "manual", "custom_instructions": ""},
    "PostCompact": {"trigger": "manual"},
    "Stop": {"stop_hook_active": False},
    "SubagentStop": {"stop_hook_active": False, "agent_id": "a-cleanroom", "agent_type": "general-purpose"},
    "SubagentStart": {"agent_id": "a-cleanroom", "agent_type": "general-purpose"},
    "TaskCompleted": {"task_id": "1", "task_subject": "probe"},
    "TeammateIdle": {"teammate_name": "probe", "team_name": "cleanroom"},
}
def tool_for(matcher):
    if not matcher or matcher == "*":
        return None
    if re.fullmatch(r"[A-Za-z_|]+", matcher):
        return matcher.split("|")[0]
    return "mcp__cleanroom__noop" if re.search(matcher, "mcp__cleanroom__noop") else None
count, bad = 0, []
for event, groups in spec.items():
    for group in groups:
        tool = tool_for(group.get("matcher"))
        for hook in group.get("hooks", []):
            payload = {"session_id": "00000000-0000-4000-8000-00000000c1ea", "transcript_path": transcript,
                       "cwd": proj, "hook_event_name": event, "permission_mode": "default"}
            payload.update(extra.get(event, {}))
            if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
                name = tool or "Bash"
                payload.update(tool_name=name, tool_input=inputs.get(name, {}), tool_use_id="toolu_cleanroom")
                if event == "PostToolUse":
                    payload["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
                if event == "PostToolUseFailure":
                    payload["error"] = "exit 1"
            label = event + ("[" + tool + "]" if tool else "")
            cmd = hook["command"]
            print("$ " + label + ": " + cmd, flush=True)
            try:
                run = subprocess.run(["sh", "-c", cmd], input=json.dumps(payload), cwd=proj, timeout=timeout,
                                     capture_output=True, text=True)
                code = run.returncode
                sys.stdout.write(run.stdout + run.stderr)
            except subprocess.TimeoutExpired:
                code = "timeout"
            print("exit " + str(code), flush=True)
            count += 1
            if code != 0:
                bad.append(label + " exit " + str(code))
if bad:
    sys.exit("; ".join(bad[:5]))
print("%d entry points" % count)
'
( cd "$PROJ" && python3 -c "$HOOKS_PY" "$PLUGIN_DIR" "$PROJ" "$SCRATCH" "$STEP_TIMEOUT" ) >"$L" 2>"$SCRATCH/hooks.err" \
  || { cat "$SCRATCH/hooks.err" >>"$L"; fail hooks "$(tail -n 1 "$SCRATCH/hooks.err")" "$L"; }
cat "$SCRATCH/hooks.err" >>"$L"
scan hooks "$L"
pass hooks "$(tail -n 1 "$L") ran with exit 0"

# --- bin ---------------------------------------------------------------------------------------
L="$LOGS/bin.log"
n=0
for f in "$PLUGIN_DIR"/bin/*; do
  name=${f##*/}
  case "$name" in _*|*.json|*.py) continue ;; esac   # private helpers, data and importable modules
  [ -f "$f" ] && [ -x "$f" ] || continue
  ( cd "$PROJ" && capped "$L" "$f" --help ) || fail bin "$name --help exited non-zero" "$L"
  n=$((n + 1))
done
[ "$n" -gt 0 ] || fail bin "no executable tool found in bin/" "$L"
scan bin "$L"
pass bin "$n tools, --help exit 0"

# --- uninstall ---------------------------------------------------------------------------------
if [ -n "$MKT" ]; then
  L="$LOGS/uninstall.log"
  capped "$L" claude plugin uninstall "$PLUGIN_NAME@$MKT" || fail uninstall "plugin uninstall failed" "$L"
  claude plugin list --json </dev/null >"$SCRATCH/list2.json" 2>>"$L" || fail uninstall "plugin list --json failed" "$L"
  cat "$SCRATCH/list2.json" >>"$L"
  python3 -c '
import json, sys
listed = [r for r in json.load(open(sys.argv[1])) if r.get("id") == sys.argv[2]]
record = json.load(open(sys.argv[3])).get("plugins", {}).get(sys.argv[2])
sys.exit("still listed" if listed else "still in installed_plugins.json" if record else 0)' \
    "$SCRATCH/list2.json" "$PLUGIN_NAME@$MKT" "$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json" >>"$L" 2>&1 \
    || fail uninstall "$(tail -n 1 "$L")" "$L"
  scan uninstall "$L"
  pass uninstall "gone from the list and the install record"
fi

finish 0
