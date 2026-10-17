"""The machine-local project registry: ${CLAUDE_PLUGIN_DATA}/projects.json (D10 §13 P3, §15.3).

Location: pact-plugin/bin/_tasks/registry.py. Design: the design notes.
One file lists every main checkout where `repact-task init` ran (and the bootstrap's init offer),
plus the atlas's own state: whether the first-write backup was taken, and per page the regions
the atlas has ever written (a missing region that was written before is a user deletion).

Shape (version 1):
    {"version": 1,
     "checkouts": {"<realpath of the main checkout>": {
         "repo_path", "project_id", "slug", "init_at",
         "bootstrap": {"offered", "answer"} | null,
         "last_known": {"name", "what", "stack", "status", "last_active", "atlas"} | null}},
     "atlas": {"backup": {"path", "sha256", "at", "files"} | null,
               "pages": {"<slug>" | "_index": {"regions_written": [...], "deleted": bool}}}}

Every write holds an fcntl lock on projects.json.lock and replaces the file atomically. A file
that cannot be parsed is never overwritten: RegistryError, and the caller reports it.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

BIN_DIR = Path(__file__).resolve().parent.parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
import _repact_common as common  # noqa: E402

VERSION = 1
FILENAME = "projects.json"
INDEX_PAGE = "_index"
FILE_MODE = 0o600


class RegistryError(Exception):
    """The registry exists but cannot be read or parsed; nothing is written over it."""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path() -> Path:
    return common.plugin_data_dir() / FILENAME


def _empty() -> dict:
    return {"version": VERSION, "checkouts": {}, "atlas": {"backup": None, "pages": {}}}


def load() -> dict:
    """The registry, or an empty one when the file does not exist yet."""
    target = path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as err:
        raise RegistryError(f"cannot read the project registry {target}: {err}") from err
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise RegistryError(f"the project registry {target} is not a version {VERSION} registry")
    data.setdefault("checkouts", {})
    atlas = data.setdefault("atlas", {})
    atlas.setdefault("backup", None)
    atlas.setdefault("pages", {})
    return data


@contextmanager
def _locked() -> Iterator[None]:
    lock = path().with_name(FILENAME + ".lock")
    with open(lock, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(data: dict) -> None:
    target = path()
    tmp = target.with_name(f".{FILENAME}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)


def update(change: Callable[[dict], None]) -> dict:
    """Apply `change` to the registry under the lock and write it back; returns the new registry."""
    with _locked():
        data = load()
        change(data)
        _write(data)
        return data


def key(main) -> str:
    return os.path.realpath(str(main))


def entry(main) -> Optional[dict]:
    return load()["checkouts"].get(key(main))


def checkout_row(data: dict, main) -> dict:
    """The checkout's row inside `data` (created empty when absent); for use inside update()."""
    row = data["checkouts"].setdefault(key(main), {
        "repo_path": key(main), "project_id": None, "slug": None, "init_at": None,
        "bootstrap": None, "last_known": None})
    return row


def record_bootstrap(main, answer: str) -> dict:
    """The bootstrap's one-time init offer and the user's answer, for this checkout."""
    def change(data):
        checkout_row(data, main)["bootstrap"] = {"offered": now(), "answer": answer}
    return update(change)


def record_init(main, project_id: str, slug: str) -> dict:
    """`repact-task init` ran here: the checkout is registered with its project id and slug."""
    def change(data):
        row = checkout_row(data, main)
        row["project_id"], row["slug"] = project_id, slug
        row["init_at"] = row.get("init_at") or now()
    return update(change)


def set_known(main, slug: str, known: dict) -> dict:
    """After an atlas run: the slug the page now lives under and the values it showed."""
    def change(data):
        row = checkout_row(data, main)
        row["slug"], row["last_known"] = slug, known
    return update(change)


def forget(slug: str) -> int:
    """Drop every checkout registered under `slug`; returns how many were dropped."""
    dropped = []

    def change(data):
        for name, row in list(data["checkouts"].items()):
            if row.get("slug") == slug:
                dropped.append(name)
                del data["checkouts"][name]
    update(change)
    return len(dropped)


# -- the atlas's own state (written by vault-note project-page) --------------------------------

def backup_record() -> Optional[dict]:
    return load()["atlas"]["backup"]


def set_backup(record: dict) -> dict:
    def change(data):
        data["atlas"]["backup"] = record
    return update(change)


def regions_written(page: str) -> set:
    return set((load()["atlas"]["pages"].get(page) or {}).get("regions_written") or [])


def add_regions_written(page: str, regions) -> dict:
    def change(data):
        record = data["atlas"]["pages"].setdefault(page, {"regions_written": []})
        record["regions_written"] = sorted(set(record["regions_written"]) | set(regions))
    return update(change)


def page_deleted(page: str) -> bool:
    """The user deleted the whole page the atlas had written: it stays deleted until --restore."""
    return bool((load()["atlas"]["pages"].get(page) or {}).get("deleted"))


def set_page_deleted(page: str, deleted: bool) -> dict:
    def change(data):
        data["atlas"]["pages"].setdefault(page, {"regions_written": []})["deleted"] = deleted
    return update(change)


def rename_page(old: str, new: str) -> dict:
    def change(data):
        pages = data["atlas"]["pages"]
        if old in pages:
            pages[new] = pages.pop(old)
    return update(change)
