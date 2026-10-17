"""
Location: pact-plugin/hooks/shared/unattended.py
Summary: Unattended mode. The switch (a flag file in the session directory, else the
         REPACT_UNATTENDED environment variable) and the classifier that sorts the
         questions of one AskUserQuestion call into always-ask and default.
Used by: hooks/unattended_gate.py, hooks/session_init.py, bin/repact-unattended.

Standard library only and no import from the `shared` package. It writes no journal
row and reaches nothing outside the session directory: the callers journal.

THE SWITCH. A well-formed flag file wins over the environment, so `repact-unattended
off` works without a restart. No flag file: the environment decides. A flag that
cannot be read or parsed reads OFF, also when the environment says 1. OFF is the safe
direction: the question then reaches the terminal and nothing is chosen by default.

THE CLASSIFIER. It is lexical and it defaults only what it can show to be local and
reversible. A question is `default` only when ALL of this holds: its fields have the
documented types and no other, it is not multiSelect, its text is within the size cap and
plain (no character left outside ASCII after normalising, except punctuation, spaces, math
and currency signs), no word of its question, header, option labels or descriptions
matches ALWAYS_ASK, exactly one option label carries "(Recommended)", its question text
names a local class (LOCAL_CLASSES: naming, test scope, file layout), and every word of
its question, header and the recommended option is a LOCAL_WORDS word. A code token (an
identifier or a relative path) may stand only in the recommended option's label, alone
with its mark, and never one that leaves the worktree (absolute, ~, .., a URL).
Everything else is `always_ask`.

Known limit: the check reads words, not intent, and it fails closed both ways. A local
question phrased outside LOCAL_WORDS halts (one stopped feature until the owner is back),
and ALWAYS_ASK stays a veto, so a local question that names main.py, an MR, a PR, a
secret or a customer halts too. A local question can still lead to an outward act the
lead takes on its own; the default is never permission for that act
(reference/unattended-runs.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from typing import Any, Mapping, NamedTuple

ENV_NAME = "REPACT_UNATTENDED"
FLAG_NAME = "unattended.json"
GATE_NAME = "unattended_gate"

CLASS_ALWAYS_ASK = "always_ask"
CLASS_DEFAULT = "default"

_ENV_ON = frozenset({"1", "true", "yes", "on"})
_FLAG_MAX_BYTES = 4096

# The veto: a question naming one of these is always-ask even when it is in the local
# class (LOCAL_CLASSES below decides what defaults). Matched against the words
# ([a-z0-9]+) of the normalised text of every string of a question:
#   inside  a word that CONTAINS the stem (autodeploy, deleteWorkflow, dropdown), or
#           two neighbouring words that begin with it when joined (de-ploy, roll out)
#   starts  a word that BEGINS with the stem (forced, shipping, emails)
#   words   the whole word (post, but not postgres; prod, but not product)
#   with    every word of the group, anywhere in the same text (git + clean); here a
#           one-letter word counts alone too (branch + d in `branch -D -q`)
#   pairs   a word that BEGINS with a stem of the first set (or two neighbouring words
#           that do when joined: re-write) and, anywhere in the same text, a whole word
#           of the second set (rewrite + history, shared + database, scale + zero)
# Besides the verbs, the words name the targets of an outward act: a protected branch
# or remote (main, master, origin, remote), a merge or pull request (mr, pr), a forge CLI
# and an infrastructure tool, a customer-facing system, money, access and secrets. Any
# question that names one is always-ask, whatever its verb. The pairs catch an act on a
# shared or public resource and a rewrite of shared history, whose words alone are local.
# A false match costs one halt; a missing word lets the lead default into the act.
ALWAYS_ASK: Mapping[str, tuple[Any, ...]] = {
    "inside": (
        "merg", "delet", "deploy", "destroy", "transfer", "publish", "purg", "drop",
        "truncat", "remov", "overwrit", "uninstall", "rollback", "rollout", "golive",
        "push", "squash", "refund", "customer", "contractor", "stakeholder", "helpdesk",
        "crm", "jira", "gitlab", "github", "alembic", "credential", "confluence",
        "bigquery",
    ),
    "starts": (
        "force", "forcing", "ship", "releas", "send", "eras", "wipe", "wiping",
        "revok", "discard", "reset", "upload", "email", "mail", "submit", "submiss",
        "announc", "invit", "purchas", "migrat", "prun", "abandon", "archiv", "promot",
        "kill", "rotat", "grant", "invoic", "collaborator", "warehous",
    ),
    "words": (
        "post", "posts", "posted", "posting", "sent", "close", "closes", "closed",
        "closing", "prod", "production", "live", "rm", "pay", "pays", "paid", "paying",
        "buy", "n8n", "accept", "accepts", "accepted", "accepting", "land", "lands",
        "landed", "main", "master", "trunk", "origin", "upstream", "mr", "mrs", "pr",
        "prs", "gh", "glab", "helm", "kubectl", "k8s", "terraform", "tofu", "pulumi",
        "cluster", "remote", "remotes", "pod", "pods", "secret", "secrets", "share", "shares",
        "sharing", "slack", "tracker", "bq",
    ),
    "with": (
        ("git", "clean"), ("branch", "d"), ("apply", "plan"), ("move", "workflow"),
        ("move", "workflows"), ("pull", "request"), ("pull", "requests"),
        ("google", "doc"), ("google", "docs"), ("google", "sheet"), ("google", "sheets"),
        ("google", "drive"), ("google", "slides"),
    ),
    "pairs": (
        # rewriting shared history
        (("rewrit", "rebas", "amend", "reword", "rewind", "fixup", "revert"),
         ("history", "histories", "shared", "pushed", "published", "server", "servers")),
        # a shared or public resource
        (("shared",),
         ("branch", "branches", "database", "databases", "db", "drive", "doc", "docs",
          "document", "documents", "folder", "calendar", "channel", "bucket", "inbox",
          "repo", "repository", "sheet", "spreadsheet", "workspace", "environment", "env",
          "instance", "cluster")),
        (("public", "visibilit"),
         ("repo", "repos", "repository", "repositories", "project", "bucket", "gist",
          "link", "doc", "docs", "document", "drive", "folder", "site", "channel",
          "dataset", "workspace")),
        (("visibilit",), ("public", "internal", "private")),
        (("access",),
         ("bucket", "buckets", "repo", "repos", "repository", "workspace", "project",
          "drive", "folder", "doc", "docs", "sheet", "channel", "dataset", "database", "db",
          "cluster", "account", "org", "organisation", "organization", "vault", "instance")),
        # people outside: a client, a meeting
        (("notif", "messag", "reply", "repli", "contact", "tell", "inform", "remind", "dm"),
         ("client", "clients")),
        (("declin", "accept", "cancel", "schedul", "reschedul", "book"),
         ("meeting", "meetings", "calendar", "invite", "invites")),
        # money, tickets, pipelines, running services and shared schemas
        (("cancel",),
         ("subscription", "subscriptions", "order", "orders", "account", "membership",
          "invoice", "booking")),
        (("charg",), ("card", "cards", "customer", "client", "clients", "account", "overage")),
        (("credit",), ("note", "notes", "customer", "client", "clients", "account", "card")),
        (("activat", "deactivat", "enabl", "disabl", "stop", "paus", "unpaus"),
         ("workflow", "workflows", "service", "services", "instance", "instances", "cron",
          "webhook", "webhooks", "bot", "schedule", "trigger", "triggers")),
        (("updat", "insert", "alter", "backfill", "reload", "upsert", "execut"),
         ("postgres", "postgresql", "mysql", "supabase", "database", "databases", "db")),
        (("creat", "open", "file", "rais", "assign", "comment", "reply", "repli", "respond"),
         ("ticket", "tickets", "issue", "issues")),
        (("trigger", "retry", "rerun", "launch", "cancel", "kick"),
         ("pipeline", "pipelines")),
        (("restart", "scale", "drain", "cordon", "reboot"),
         ("pod", "pods", "cluster", "clusters", "node", "nodes", "service", "services",
          "deployment", "deployments", "replica", "replicas", "instance", "instances")),
        (("scale",), ("zero",)),
        (("upgrade", "downgrade", "stamp"),
         ("head", "heads", "database", "databases", "db", "schema", "schemas")),
    ),
}

# The local class. A question defaults only when its question text holds a word of one
# of these classes and every word the lead reads to act (question, header, and the label
# and description of the option marked "(Recommended)") is in LOCAL_WORDS. The list is
# closed on purpose: a word outside it halts, so an outward act needs no listed word to
# be refused. Keep it narrow: no people, places, systems, remotes, schedules or verbs that
# send, publish, pay or change access; a false halt costs one stopped feature.
LOCAL_CLASSES: Mapping[str, frozenset[str]] = {
    "naming": frozenset({
        "name", "names", "named", "naming", "rename", "renamed", "renaming", "call", "called",
        "identifier", "identifiers", "prefix", "suffix",
    }),
    "test_scope": frozenset({
        "test", "tests", "testing", "tested", "regression", "coverage", "assertion",
        "assertions", "fixture", "fixtures", "suite",
    }),
    "file_layout": frozenset({
        "file", "files", "folder", "folders", "directory", "directories", "module", "modules",
        "layout", "path", "paths",
    }),
}
LOCAL_WORDS: frozenset[str] = frozenset().union(*LOCAL_CLASSES.values(), {
    # function words
    "a", "an", "the", "this", "that", "these", "those", "it", "its", "them", "to", "of",
    "in", "on", "for", "with", "from", "into", "by", "as", "at", "or", "and", "not", "no",
    "yes", "if", "then", "than", "so", "be", "is", "are", "should", "would", "could", "can",
    "do", "does", "we", "i", "our", "my", "which", "what", "where", "how", "whether",
    "there", "here", "instead", "only", "also", "too", "just", "both", "each", "every",
    "all", "one", "two", "three", "more", "fewer", "less", "same", "other", "new", "old",
    "first", "second", "last", "whole", "rest", "current", "existing", "own", "per",
    # options
    "recommended", "option", "options",
    # what a local coding question is about
    "helper", "helpers", "function", "functions", "method", "methods", "class", "classes",
    "variable", "variables", "constant", "constants", "type", "types", "field", "fields",
    "parameter", "parameters", "argument", "arguments", "parser", "input", "inputs",
    "output", "outputs", "case", "cases", "edge", "empty", "unicode", "error", "errors",
    "unit", "units", "integration", "behaviour", "behavior", "code", "change", "changes",
    "check", "checks", "mock", "mocks", "stub", "stubs", "sample", "samples", "style",
    "convention", "word", "words", "touched", "local",
    # what is done to it, in the worktree
    "add", "use", "keep", "make", "put", "move", "split", "group", "extract", "place",
    "go", "cover", "covers", "run", "skip", "include", "exclude", "write", "mark", "match",
    "matches", "follow", "follows", "fit", "fits", "read", "reads", "prefer", "after",
    "before", "together", "apart", "short", "shorter", "long", "longer", "clear", "clearer",
    "simple", "simpler", "consistent",
})
_CLASS_WORDS: frozenset[str] = frozenset().union(*LOCAL_CLASSES.values())

MAX_QUESTIONS = 4            # the question tool takes 1 to 4
MAX_QUESTION_CHARS = 4000    # all strings of one question together
_MAX_NODES = 200             # values walked in one question

_RECOMMENDED = re.compile(r"\(\s*recommended\s*\)")
_WORD = re.compile(r"[a-z0-9]+")
_JOINERS = str.maketrans("", "", "'\u2019\u2018`")
_DIGIT_LETTERS = str.maketrans("013457", "oleast")
_OPTION_KEYS = ("label", "description")
_QUESTION_KEYS = ("question", "header", "options", "multiSelect")
_WRAPPERS = "`'\"()[]{}<>,;:!?*"
_CODE_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_CODE_SHAPE = re.compile(r"[_./]|[a-z][A-Z]|[A-Z]{2}[a-z]|[A-Za-z][0-9]|[0-9][A-Za-z]")
_OUTSIDE = re.compile(r"^[/~\\]|\.\.|://|@|\\")


class Mode(NamedTuple):
    on: bool
    cause: str   # flag | env | unset | flag_unreadable | flag_malformed


INVALID_CAUSES = frozenset({"flag_unreadable", "flag_malformed"})


class Verdict(NamedTuple):
    index: int   # 1-based position in the call
    cls: str     # CLASS_ALWAYS_ASK | CLASS_DEFAULT
    code: str    # why, a short code; never text of the question
    ref: str     # 12 hex of a sha256 over the normalised question; "" when it has none


# --- the switch -------------------------------------------------------------

def flag_path(session_dir: str) -> str:
    return os.path.join(session_dir, FLAG_NAME)


def _read_flag(session_dir: str) -> Mode | None:
    """The flag's verdict, or None when there is no flag file."""
    try:
        fd = os.open(flag_path(session_dir), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return Mode(False, "flag_unreadable")
    try:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return Mode(False, "flag_unreadable")
            raw = handle.read(_FLAG_MAX_BYTES + 1)
    except OSError:
        return Mode(False, "flag_unreadable")
    if len(raw) > _FLAG_MAX_BYTES:
        return Mode(False, "flag_malformed")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return Mode(False, "flag_malformed")
    value = data.get("unattended") if isinstance(data, dict) else None
    if not isinstance(value, bool):
        return Mode(False, "flag_malformed")
    return Mode(value, "flag")


def read_mode(session_dir: str, environ: Mapping[str, str] | None = None) -> Mode:
    """Whether unattended mode is ON, and what decided it. Never raises."""
    try:
        flag = _read_flag(session_dir) if session_dir and isinstance(session_dir, str) else None
    except Exception:  # noqa: BLE001 - a read that cannot finish reads OFF
        flag = Mode(False, "flag_unreadable")
    if flag is not None:
        return flag
    value = (os.environ if environ is None else environ).get(ENV_NAME)
    if isinstance(value, str) and value.strip().casefold() in _ENV_ON:
        return Mode(True, "env")
    return Mode(False, "unset")


def write_flag(session_dir: str, on: bool, at: str) -> str:
    """Write the flag atomically (0o600) and return its path. Raises OSError."""
    if not os.path.isdir(session_dir):
        raise NotADirectoryError(session_dir)
    body = json.dumps({"v": 1, "unattended": bool(on), "at": at, "by": "command"}) + "\n"
    fd, temp = tempfile.mkstemp(prefix=".unattended-", dir=session_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(temp, flag_path(session_dir))
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return flag_path(session_dir)


# --- the classifier ---------------------------------------------------------

def normalise(text: str) -> str:
    """NFKD, minus combining marks, format characters (zero-width and the like) and
    control characters; whitespace controls become a space; casefolded."""
    return _clean(text).casefold()


def _clean(text: str) -> str:
    """normalise() with the case kept, for telling an identifier from a word."""
    out = []
    for char in unicodedata.normalize("NFKD", text):
        category = unicodedata.category(char)
        if category in ("Mn", "Mc", "Me", "Cf"):
            continue
        if category == "Cc":
            if char in "\t\n\r\f\v":
                out.append(" ")
            continue
        out.append(char)
    return "".join(out)


def _is_plain(text: str) -> bool:
    """True when every character of the normalised text is ASCII, or punctuation, a
    separator, a math sign or a currency sign."""
    for char in text:
        if ord(char) < 128:
            continue
        category = unicodedata.category(char)
        if category[0] in "PZ" or category in ("Sm", "Sc"):
            continue
        return False
    return True


def _words(text: str) -> list[str]:
    """The words of a normalised text. An apostrophe joins (user's is one word) and a
    run of one-letter words is one word (m e r g e)."""
    words: list[str] = []
    run = ""
    for word in _WORD.findall(text.translate(_JOINERS)):
        if len(word) == 1:
            run += word
            continue
        if run:
            words.append(run)
            run = ""
        words.append(word)
    if run:
        words.append(run)
    return words


def _begins(words: list[str], stems: tuple[str, ...]) -> bool:
    """True when a word begins with a stem, or two neighbouring words do when joined
    and the first is shorter than the stem (re-write)."""
    if any(word.startswith(stems) for word in words):
        return True
    return any(
        (first + second).startswith(stem) and len(first) < len(stem)
        for first, second in zip(words, words[1:]) for stem in stems
    )


def names_act(text: str) -> bool:
    """True when the normalised text holds a word of the always-ask class."""
    inside, starts, whole = ALWAYS_ASK["inside"], ALWAYS_ASK["starts"], frozenset(ALWAYS_ASK["words"])
    words = _words(text)
    present = frozenset(words) | frozenset(_WORD.findall(text.translate(_JOINERS)))
    if any(present.issuperset(group) for group in ALWAYS_ASK["with"]):
        return True
    for stems, targets in ALWAYS_ASK["pairs"]:
        if not present.isdisjoint(targets) and _begins(words, stems):
            return True
    for word in words:
        forms = {word}
        if not word.isalpha() and not word.isdigit():
            forms.add(word.translate(_DIGIT_LETTERS))   # depl0y, de1ete
        for form in forms:
            if form in whole or form.startswith(starts) or any(stem in form for stem in inside):
                return True
    for first, second in zip(words, words[1:]):
        joined = first + second
        if any(joined.startswith(stem) and len(first) < len(stem) for stem in inside):
            return True
    return False


def _strings(value: Any) -> list[str] | None:
    """Every string under `value`, keys included; None when it is too large to walk."""
    found: list[str] = []
    stack = [value]
    seen = 0
    while stack:
        item = stack.pop()
        seen += 1
        if seen > _MAX_NODES:
            return None
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


def _shape_code(question: Any) -> str:
    """"" when the question has the documented shape, else the code of what is off."""
    if not isinstance(question, dict):
        return "not_a_question"
    text = question.get("question")
    if not isinstance(text, str) or not text.strip():
        return "non_string"
    if "header" in question and not isinstance(question["header"], str):
        return "non_string"
    multi = question.get("multiSelect", False)
    if not isinstance(multi, bool):
        return "non_string"
    if multi:
        return "multi_select"
    options = question.get("options")
    if not isinstance(options, list) or not options:
        return "options_missing"
    for option in options:
        if not isinstance(option, dict):
            return "non_string"
        label = option.get("label")
        if not isinstance(label, str) or not label.strip():
            return "non_string"
        if "description" in option and not isinstance(option["description"], str):
            return "non_string"
    return ""


def _local_words(text: str, code_ok: bool) -> tuple[list[str], str]:
    """(the words of a text, "") for the local check, or ([], code) when the text names a
    place outside the worktree, or holds a code token where none may stand. Where one may
    (`code_ok`), a text with a code token holds nothing else but the "(Recommended)" mark,
    so no verb stands beside a name: `Run notify.py` is not a name."""
    words: list[str] = []
    coded = False
    for token in _clean(text).split():
        core = token.strip(_WRAPPERS).rstrip(".")
        if _OUTSIDE.search(core):
            return [], "outside_worktree"
        if _CODE_TOKEN.fullmatch(core) and _CODE_SHAPE.search(core):
            if not code_ok:
                return [], "not_local"
            coded = True
            continue
        words.extend(_WORD.findall(core.casefold().translate(_JOINERS)))
    if coded and any(word != "recommended" for word in words):
        return [], "not_local"
    return words, ""


def _local_code(question: dict) -> str:
    """"" when the question is shown to be local and reversible, else the code of why
    not. Runs after every other check, so the option marked "(Recommended)" is one."""
    if any(key not in _QUESTION_KEYS for key in question) or any(
            key not in _OPTION_KEYS for option in question["options"] for key in option):
        return "not_local"
    taken = next(option for option in question["options"]
                 if _RECOMMENDED.search(normalise(option["label"])))
    words: list[str] = []
    for text, code_ok in ((question["question"], False), (question.get("header", ""), False),
                          (taken["label"], True), (taken.get("description", ""), False)):
        more, code = _local_words(text, code_ok)
        if code:
            return code
        words.extend(more)
    if _CLASS_WORDS.isdisjoint(_local_words(question["question"], False)[0]):
        return "no_local_class"
    if not all(word in LOCAL_WORDS or word.isdigit() for word in words):
        return "not_local"
    return ""


def _ref(question: Any) -> str:
    text = question.get("question") if isinstance(question, dict) else None
    if not isinstance(text, str):
        return ""
    if len(text) <= MAX_QUESTION_CHARS:
        text = normalise(text)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def classify_question(question: Any, index: int = 1) -> Verdict:
    """One question's verdict. `default` needs every check to pass."""
    ref = _ref(question)

    def ask(code: str) -> Verdict:
        return Verdict(index, CLASS_ALWAYS_ASK, code, ref)

    code = _shape_code(question)
    if code:
        return ask(code)
    strings = _strings(question)
    if strings is None or sum(len(item) for item in strings) > MAX_QUESTION_CHARS:
        return ask("too_long")
    texts = [normalise(item) for item in strings]
    if not all(_is_plain(text) for text in texts):
        return ask("non_ascii")
    if any(names_act(text) for text in texts):
        return ask("act_named")
    marked = sum(
        1 for option in question["options"]
        if _RECOMMENDED.search(normalise(option["label"]))
    )
    if marked == 0:
        return ask("no_recommended")
    if marked > 1:
        return ask("several_recommended")
    code = _local_code(question)
    if code:
        return ask(code)
    return Verdict(index, CLASS_DEFAULT, "recommended", ref)


def classify_call(tool_input: Any) -> list[Verdict]:
    """One verdict per question of an AskUserQuestion call, in order. A call that is
    not a list of 1 to 4 questions gets always-ask for all it holds. Text outside the
    questions that names an act makes every question always-ask. Never raises."""
    try:
        questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
        if not isinstance(questions, list) or not questions:
            return [Verdict(1, CLASS_ALWAYS_ASK, "questions_missing", "")]
        verdicts = [
            classify_question(item, number)
            for number, item in enumerate(questions[:MAX_QUESTIONS * 4], 1)
        ]
        code = "too_many" if len(questions) > MAX_QUESTIONS else _rest_code(tool_input)
        return all_always_ask(verdicts, code) if code else verdicts
    except Exception:  # noqa: BLE001 - a check that cannot finish never answers default
        return [Verdict(1, CLASS_ALWAYS_ASK, "check_error", "")]


def _rest_code(tool_input: dict) -> str:
    """"" when the call holds nothing of note outside its questions, else a code."""
    rest = _strings({key: value for key, value in tool_input.items() if key != "questions"})
    if rest is None or sum(len(item) for item in rest) > MAX_QUESTION_CHARS:
        return "too_long"
    texts = [normalise(item) for item in rest]
    if not all(_is_plain(text) for text in texts):
        return "non_ascii"
    return "act_named" if any(names_act(text) for text in texts) else ""


def all_always_ask(verdicts: list[Verdict], code: str, refs: frozenset[str] | None = None) -> list[Verdict]:
    """The verdicts with each `default` one turned into always-ask under `code`: every
    one, or only those whose ref is in `refs`."""
    return [
        item._replace(cls=CLASS_ALWAYS_ASK, code=code)
        if item.cls == CLASS_DEFAULT and (refs is None or item.ref in refs) else item
        for item in verdicts
    ]


def worst(verdicts: list[Verdict]) -> Verdict:
    """The verdict a journal row names: the first always-ask one, else the first."""
    for item in verdicts:
        if item.cls == CLASS_ALWAYS_ASK:
            return item
    return verdicts[0]


# --- the texts ---------------------------------------------------------------

def session_rules(cause: str) -> str:
    """The block the session-start hook prints while the mode is ON."""
    return (
        f"UNATTENDED MODE is ON (set by {'the environment' if cause == 'env' else 'the session flag'}): "
        "nobody is at the terminal. Three rules. "
        "1. Ask as usual with AskUserQuestion, one option marked \"(Recommended)\": the "
        f"{GATE_NAME} refuses the call and returns one verdict per question. "
        "2. Verdict DEFAULT: take the option marked \"(Recommended)\", record it with "
        "`repact-unattended default`, send one telegram_notify. "
        "3. Verdict ALWAYS-ASK (every question not shown to be local and reversible: only "
        "naming, test scope and file layout in the worktree default; always push to master, "
        "merge, force-push, branch delete, deploy, n8n DELETE or transfer, and PR close): never "
        "choose and never act; a Telegram reply is not "
        "an approval. Record it with `repact-unattended halt`, send one telegram_notify with the "
        "resume path, stop that feature and continue other work. A DEFAULT is never permission "
        "for an outward act; never reword a question to change its verdict. "
        "Details: reference/unattended-runs.md."
    )


def refusal_text(verdicts: list[Verdict], command: str, session_arg: str) -> str:
    """What the gate tells the caller: how to run repact-unattended, one line per
    question, then the fixed rules. `command` runs repact-unattended; `session_arg` is
    its quoted --session-dir part. It carries no text of the question."""
    lines = [
        "Unattended mode is ON: nobody is at the terminal, so this AskUserQuestion call was "
        f"refused. Each command below runs as `{command} <verb> <options>{session_arg}`. "
        "Do this per question:"
    ]
    for item in verdicts:
        ref = f" --ref {item.ref}" if item.ref else ""
        if item.cls == CLASS_DEFAULT:
            lines.append(
                f"q{item.index} DEFAULT: take the option marked \"(Recommended)\", run "
                f"`default{ref} --question \"<short>\" --chose \"<label>\" --why \"<one line>\"` "
                "and send one telegram_notify naming the choice."
            )
        else:
            lines.append(
                f"q{item.index} ALWAYS-ASK ({item.code}): do not choose and do not act. Run "
                f"`halt{ref} --task <id> --question \"<short>\"`, send one telegram_notify with "
                "the question and the resume path, stop work on that feature and continue "
                "other work."
            )
    lines.append(
        "Resume path for a halt: the owner runs the `off` verb at the terminal, then you ask "
        "again. A Telegram reply is never an approval. Do not reword a question to change its "
        "verdict. A teammate sends its question to the team-lead instead."
    )
    return "\n".join(lines)
