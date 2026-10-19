"""
Location: pact-plugin/hooks/egress_copies.py
Summary: The local files a copy client sends to a remote place, for egress_uploads to open
         and scan: scp and rsync sources, and the cloud storage and sync clients (aws s3,
         gsutil, gcloud storage, az storage, azcopy, rclone, s3cmd). A copy whose destination
         is local, or whose sources are all remote (a download), sends nothing. A directory
         source is returned as written; egress_uploads walks it within its budget and fails
         closed past it.
Used by: hooks/egress_uploads.py (upload_paths). Stdlib only, imports nothing.

Honest limit: options are read from the tables below. An option this module does not know
is taken as a flag without a value, so its value would be read as a source: a file that does
not exist then fails closed (the guard refuses), never open.
"""

from __future__ import annotations

SCP_VALUE_FLAGS = frozenset({"-c", "-D", "-F", "-i", "-J", "-l", "-o", "-P", "-S", "-X"})
RSYNC_VALUE_FLAGS = frozenset({
    "-e", "-f", "-T", "-B", "-M", "--rsh", "--exclude", "--include", "--filter", "--files-from",
    "--exclude-from", "--include-from", "--password-file", "--port", "--temp-dir", "--log-file",
    "--backup-dir", "--partial-dir", "--compare-dest", "--copy-dest", "--link-dest", "--chmod",
    "--chown", "--timeout", "--bwlimit", "--max-size", "--min-size", "--out-format", "--suffix",
    "--rsync-path", "--remote-option", "--sockopts", "--address", "--usermap", "--groupmap",
    "--info", "--debug", "--skip-compress", "--modify-window", "--contimeout", "--block-size",
    "--max-delete", "--iconv", "--checksum-choice", "--compress-choice", "--compress-level",
})

# Cloud clients: program -> (the words that select a copy, options that take a value).
# The copy words follow any group words (aws s3 cp, gcloud storage cp, az storage blob upload).
_AWS_VALUES = frozenset({
    "--profile", "--region", "--endpoint-url", "--exclude", "--include", "--acl", "--grants",
    "--storage-class", "--sse", "--sse-c", "--sse-c-key", "--sse-kms-key-id", "--content-type",
    "--cache-control", "--content-disposition", "--content-encoding", "--content-language",
    "--expires", "--metadata", "--metadata-directive", "--website-redirect", "--expected-size",
    "--page-size", "--source-region", "--request-payer", "--output", "--query", "--ca-bundle",
    "--color", "--cli-read-timeout", "--cli-connect-timeout", "--checksum-algorithm",
    "--copy-props", "--tagging",
})
_GSUTIL_VALUES = frozenset({"-o", "-h", "-u", "-a", "-j", "-z", "-L", "-s", "-x", "-i", "-p"})
_GCLOUD_VALUES = frozenset({
    "--project", "--account", "--configuration", "--billing-project", "--verbosity",
    "--impersonate-service-account", "--cache-control", "--content-type", "--storage-class",
    "--exclude", "--content-encoding", "--content-disposition", "--content-language",
    "--custom-metadata", "--predefined-acl", "--encryption-key", "--format",
})
_AZCOPY_VALUES = frozenset({"--include-pattern", "--exclude-pattern", "--log-level", "--from-to",
                            "--cap-mbps", "--block-size-mb", "--include-path", "--exclude-path"})
_RCLONE_VALUES = frozenset({
    "--config", "--exclude", "--include", "--filter", "--exclude-from", "--include-from",
    "--filter-from", "--files-from", "--transfers", "--checkers", "--bwlimit", "--log-file",
    "--log-level", "--max-age", "--min-age", "--max-size", "--min-size", "--backup-dir",
    "--suffix", "--user-agent", "--timeout", "--contimeout", "--retries",
})
_S3CMD_VALUES = frozenset({"-c", "--config", "--exclude", "--include", "--rexclude",
                           "--rinclude", "--exclude-from", "--acl-grant", "--mime-type",
                           "--storage-class", "--region", "--host", "--host-bucket"})
CLOUD_COPIES = {
    "aws": (("s3",), frozenset({"cp", "mv", "sync"}), _AWS_VALUES),
    "gsutil": ((), frozenset({"cp", "mv", "rsync"}), _GSUTIL_VALUES),
    "gcloud": (("storage",), frozenset({"cp", "mv", "rsync"}), _GCLOUD_VALUES),
    "azcopy": ((), frozenset({"copy", "cp", "sync"}), _AZCOPY_VALUES),
    "rclone": ((), frozenset({"copy", "copyto", "sync", "move", "moveto"}), _RCLONE_VALUES),
    "s3cmd": ((), frozenset({"put", "sync"}), _S3CMD_VALUES),
}
# az names the file it uploads with an option, not a positional.
AZ_FILE_OPTIONS = frozenset({"-f", "--file", "-s", "--source"})
CLOUD_PROGRAMS = frozenset(CLOUD_COPIES) | {"az"}


def is_remote(arg: str) -> bool:
    """scp/rsync/rclone: [user@]host:path, host::module or a URL (s3://, gs://, scp://).
    A path that starts with /, ./, ../ or ~ is local even when it holds a colon."""
    if "://" in arg:
        return True
    if arg.startswith(("/", "./", "../", "~")):
        return False
    head = arg.split("/", 1)[0]
    return ":" in head


def positionals(words: list[str], value_flags: frozenset) -> tuple[list[str], list[str]]:
    """(positional arguments, --exclude patterns) after the program word."""
    args, excludes, i = [], [], 1
    while i < len(words):
        word = words[i]
        if word == "--":
            args += words[i + 1:]
            break
        if word.startswith("--") and "=" in word:
            name, value = word.split("=", 1)
            if name == "--exclude":
                excludes.append(value)
            i += 1
        elif word in value_flags:
            if word == "--exclude" and i + 1 < len(words):
                excludes.append(words[i + 1])
            i += 2
        elif word.startswith("-") and len(word) > 1:
            i += 1
        else:
            args.append(word)
            i += 1
    return args, excludes


def copy_sources(words: list[str], value_flags: frozenset) -> tuple[list[str], list[str]]:
    """The local sources of a copy whose last positional is remote; none otherwise."""
    args, excludes = positionals(words, value_flags)
    if len(args) < 2 or not is_remote(args[-1]):
        return [], []
    return [arg for arg in args[:-1] if not is_remote(arg)], excludes


def _az_files(words: list[str]) -> list[str]:
    """az storage blob|file upload[-batch]: the --file / --source values."""
    if "storage" not in words or not any(word.startswith("upload") for word in words):
        return []
    found = []
    for i, word in enumerate(words):
        name, eq, value = word.partition("=")
        if eq and name in AZ_FILE_OPTIONS:
            found.append(value)
        elif word in AZ_FILE_OPTIONS and i + 1 < len(words):
            found.append(words[i + 1])
    return found


def cloud_sources(words: list[str]) -> tuple[list[str], list[str]]:
    """(local sources, exclude patterns) a cloud client uploads; ([], []) for any other use
    (a download, a listing, a group word this module does not know)."""
    program = words[0].rsplit("/", 1)[-1] if words else ""
    if program == "az":
        return _az_files(words), []
    entry = CLOUD_COPIES.get(program)
    if entry is None:
        return [], []
    groups, actions, values = entry
    args, _ = positionals(words, values)
    head = args[:len(groups) + 1]
    if tuple(head[:len(groups)]) != groups or len(head) <= len(groups) \
            or head[len(groups)] not in actions:
        return [], []
    rest = [words[0]] + words[words.index(head[-1]) + 1:]
    sources, excludes = copy_sources(rest, values)
    return sources, excludes
