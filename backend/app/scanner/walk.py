"""The scanner's disk access — all of it blocking, all of it run in a thread pool.

One `os.scandir` per directory gives every entry's type and stat in the same
pass, so nothing is stat'ed twice. A directory's mtime is taken *before* it is
listed: a file landing while the listing runs moves the mtime past the
recorded one, and the next heal lists the directory again rather than missing
the file.

Filesystem timestamps come from a coarse clock (a few milliseconds a tick), so
a file added in the same tick the listing ran in leaves the directory's mtime
equal to the recorded one. A directory modified within `RACY_NS` of being
listed is marked `racy` and not trusted next time — the same trick git uses
for its index.
"""
from __future__ import annotations

import hashlib
import os
import stat
import time
from dataclasses import dataclass, field

SCAN_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
RACY_NS = 2_000_000_000


@dataclass(frozen=True, slots=True)
class FileStat:
    path: str
    size: int
    mtime_ns: int


@dataclass(slots=True)
class DirListing:
    path: str
    mtime_ns: int
    racy: bool = False
    files: list[FileStat] = field(default_factory=list)
    subdirs: list[str] = field(default_factory=list)


def is_image_name(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in SCAN_EXTENSIONS


def dir_mtime_ns(path: str) -> int | None:
    """The directory's mtime, or None when it is gone (or no longer a directory)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns if stat.S_ISDIR(st.st_mode) else None


def list_dir(path: str) -> DirListing | None:
    """Image files and subdirectories directly inside `path`; None when it is gone."""
    mtime = dir_mtime_ns(path)
    if mtime is None:
        return None
    out = DirListing(path=path, mtime_ns=mtime, racy=mtime > time.time_ns() - RACY_NS)
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        out.subdirs.append(entry.path)
                    elif is_image_name(entry.name) and entry.is_file():
                        st = entry.stat()
                        out.files.append(FileStat(entry.path, st.st_size, st.st_mtime_ns))
                except OSError:
                    # Vanished between the listing and the stat — it is not there.
                    continue
    except FileNotFoundError:
        return None
    return out


def read_and_hash(path: str) -> tuple[bytes, str]:
    """The file's bytes and their sha256 — the only time the scanner reads a file."""
    with open(path, "rb") as f:
        data = f.read()
    return data, hashlib.sha256(data).hexdigest()
