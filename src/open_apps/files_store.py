"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.

The shared file store: one folder tree on disk that the Files app shows and
the bank, shop, messenger and code editor write into.

A folder tree rather than database rows, because files here are meant to be
real: an uploaded PDF is the bytes the browser sent, a downloaded statement is
a file the raw route can serve to the native viewer, and the code editor's
workspace can simply *be* the ``Code`` folder instead of a synced copy.

Paths are store-relative POSIX strings (``"Documents/lease.pdf"``). Every one
goes through :func:`resolve`, which refuses ``..``, absolute paths and
symlinks pointing out of the root, so no form field can reach the host's disk.

Three rules shape the operations:

* **Nothing is overwritten silently.** Saving, moving, duplicating or
  restoring onto a taken name appends " (2)", " (3)", ... as macOS does.
  Rename is the exception: renaming onto a taken name is refused, the way a
  file manager refuses it, rather than quietly picking a different name than
  the one asked for.
* **Delete is reversible.** It moves the item into a hidden ``.trash/``
  folder and records where it came from and when (``deleted_on``, from the
  pinned ``today``). Restore, purge and empty-trash are separate, scoreable
  actions.
* **Retention is shown, never enforced.** :func:`days_left` counts down from
  30 days, but nothing is ever purged on a clock: an episode's state must not
  depend on how long it ran.

:func:`snapshot` is the ``/files_all`` scoring surface. It is sorted, carries
no modification times or trash-internal names, and leaves out the ``Code``
subtree -- ``/codeeditor_all`` is the only state source for those files, so
existing code tasks never see a spurious files change.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Optional, Union

CODE = "Code"
DEFAULT_FOLDERS = ("Documents", "Downloads", "Receipts", "Statements")
RETENTION_DAYS = 30

_TRASH = ".trash"
_META = ".meta"  # inside .trash: one JSON per trashed item, keyed by its trash name

# A fixed table, not ``mimetypes``: that module reads the host's mime.types,
# so the scoring surface would differ between a laptop and the cluster.
_KINDS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".py": "text/x-python",
}
_BINARY = "application/octet-stream"


@dataclass(frozen=True)
class Entry:
    """One file or folder, live or in Recently Deleted.

    For a trashed item ``path`` is where it came from (what the UI shows and
    what restore puts back), and ``trash_name`` is its id inside the trash --
    the handle :func:`restore`, :func:`purge` and :func:`read_trash` take.
    """

    path: str
    kind: str  # MIME type, or "folder"
    size: int
    sha256: Optional[str]
    deleted: bool = False
    deleted_on: Optional[str] = None
    trash_name: Optional[str] = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"


_root: Optional[Path] = None
_code_dir: Optional[Path] = None
_today: Optional[date] = None


def configure(root: Union[str, Path], code_dir: Union[str, Path, None], today: Union[str, date]) -> None:
    """Point the store at ``root`` (created if missing). ``code_dir`` is the
    code editor's workspace, shown as the ``Code`` folder; ``today`` is the
    pinned date deletions are stamped with. Does not clear anything -- see
    :func:`reset`."""
    global _root, _code_dir, _today
    _root = Path(root)
    _root.mkdir(parents=True, exist_ok=True)
    _code_dir = Path(code_dir) if code_dir is not None else None
    _today = today if isinstance(today, date) else date.fromisoformat(str(today))
    (_root / _TRASH / _META).mkdir(parents=True, exist_ok=True)


def reset() -> None:
    """Empty the store and recreate the default folders. Never touches the
    code workspace, which belongs to the code editor."""
    root = _require_root()
    for child in root.iterdir():
        _remove(child)
    (root / _TRASH / _META).mkdir(parents=True)
    for folder in DEFAULT_FOLDERS:
        (root / folder).mkdir()


# ---------------------------------------------------------------------------
# Paths.


def resolve(path: str) -> Path:
    """The on-disk location of store path ``path``, refusing anything that
    could escape the root: absolute paths, ``..``, the trash, and symlinks
    leading out. The target need not exist."""
    root = _require_root()
    text = str(path)
    if "\\" in text or text.startswith(("/", "~")):
        raise ValueError(f"not a store path: {path!r}")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"not a store path: {path!r}")
    if parts and parts[0] == _TRASH:
        raise ValueError(f"not a store path: {path!r}")
    if parts and parts[0] == CODE:
        if _code_dir is None:
            raise FileNotFoundError(path)
        base, rest = _code_dir, parts[1:]
    else:
        base, rest = root, parts
    base = base.resolve()
    target = base.joinpath(*rest).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"not a store path: {path!r}")
    return target


def exists(path: str) -> bool:
    return resolve(path).exists()


def kind_of(name: str) -> str:
    return _KINDS.get(PurePosixPath(name).suffix.lower(), _BINARY)


# ---------------------------------------------------------------------------
# Live files.


def save(folder: str, name: str, data: Union[bytes, str]) -> str:
    """Write ``data`` as ``folder/name`` (the folder is created if missing)
    and return the store path, with a " (n)" suffix if the name was taken."""
    _check_name(name)
    target_dir = resolve(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _free_name(target_dir, name)
    target.write_bytes(data.encode() if isinstance(data, str) else data)
    return _join(folder, target.name)


def read(path: str) -> bytes:
    target = resolve(path)
    if not target.is_file():
        raise FileNotFoundError(path)
    return target.read_bytes()


def make_folder(parent: str, name: str) -> str:
    _check_name(name)
    parent_dir = resolve(parent)
    if not parent_dir.is_dir():
        raise FileNotFoundError(parent)
    target = _free_name(parent_dir, name)
    target.mkdir()
    return _join(parent, target.name)


def list_folder(folder: str = "") -> list[Entry]:
    """The folder's direct children, folders first, then by name
    case-insensitively. Hidden names and the trash never appear; the root
    shows ``Code`` when a code workspace is configured."""
    target = resolve(folder)
    if not target.exists():
        raise FileNotFoundError(folder)
    if not target.is_dir():
        raise NotADirectoryError(folder)
    entries = [
        _entry(child, _join(folder, child.name))
        for child in target.iterdir()
        if not child.name.startswith(".") and not child.is_symlink()
    ]
    if _is_root(folder) and _code_dir is not None:
        entries.append(Entry(path=CODE, kind="folder", size=0, sha256=None))
    return sorted(entries, key=lambda e: (not e.is_folder, e.name.casefold(), e.name))


def rename(path: str, new_name: str) -> str:
    _check_name(new_name)
    _check_movable(path)
    source = _existing(path)
    if new_name == source.name:
        return _norm(path)
    target = source.with_name(new_name)
    # On a case-insensitive disk "a.txt" -> "A.txt" finds the file itself.
    if target.exists() and not target.samefile(source):
        raise FileExistsError(_join(_parent(path), new_name))
    source.rename(target)
    return _join(_parent(path), new_name)


def move(path: str, folder: str) -> str:
    _check_movable(path)
    source = _existing(path)
    target_dir = resolve(folder)
    if not target_dir.is_dir():
        raise FileNotFoundError(folder)
    if source.is_dir() and (target_dir == source or source in target_dir.parents):
        raise ValueError(f"cannot move {path!r} into itself")
    if target_dir == source.parent:
        return _norm(path)
    target = _free_name(target_dir, source.name)
    shutil.move(str(source), str(target))
    return _join(folder, target.name)


def duplicate(path: str) -> str:
    source = _existing(path)
    target = _free_name(source.parent, source.name)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copyfile(source, target)
    return _join(_parent(path), target.name)


# ---------------------------------------------------------------------------
# Recently Deleted.


def delete(path: str, deleted_on: Union[str, date, None] = None) -> str:
    """Move ``path`` into Recently Deleted and return its trash name.
    ``deleted_on`` defaults to the pinned today; seeding passes an earlier
    date to put an entry close to its 30 days."""
    _check_movable(path)
    source = _existing(path)
    stamp = deleted_on if deleted_on is not None else _require_today()
    stamp = stamp.isoformat() if isinstance(stamp, date) else date.fromisoformat(str(stamp)).isoformat()
    trash = _trash_dir()
    target = _free_name(trash, source.name)
    shutil.move(str(source), str(target))
    meta = {"original_path": _norm(path), "deleted_on": stamp}
    (trash / _META / f"{target.name}.json").write_text(json.dumps(meta, sort_keys=True))
    return target.name


def list_trash() -> list[Entry]:
    """Every trashed item, by original path then trash name."""
    trash = _trash_dir()
    entries = []
    for item in trash.iterdir():
        if item.name.startswith("."):
            continue
        meta = _read_meta(item.name)
        base = _entry(item, meta["original_path"])
        entries.append(
            Entry(
                path=base.path,
                kind=base.kind,
                size=base.size,
                sha256=base.sha256,
                deleted=True,
                deleted_on=meta["deleted_on"],
                trash_name=item.name,
            )
        )
    return sorted(entries, key=lambda e: (e.path, e.deleted_on or "", e.trash_name))


def read_trash(trash_name: str) -> bytes:
    item = _trash_item(trash_name)
    if not item.is_file():
        raise IsADirectoryError(trash_name)
    return item.read_bytes()


def restore(trash_name: str) -> str:
    """Put a trashed item back where it came from, recreating its folder if
    that was deleted too, and return its path (" (n)" if the place is taken)."""
    item = _trash_item(trash_name)
    original = PurePosixPath(_read_meta(trash_name)["original_path"])
    parent = str(original.parent) if str(original.parent) != "." else ""
    parent_dir = resolve(parent)
    parent_dir.mkdir(parents=True, exist_ok=True)
    target = _free_name(parent_dir, original.name)
    shutil.move(str(item), str(target))
    (_trash_dir() / _META / f"{trash_name}.json").unlink()
    return _join(parent, target.name)


def purge(trash_name: str) -> None:
    """Delete one trashed item permanently."""
    _remove(_trash_item(trash_name))
    (_trash_dir() / _META / f"{trash_name}.json").unlink()


def empty_trash() -> int:
    """Delete everything in Recently Deleted; returns how many items went."""
    names = [e.trash_name for e in list_trash()]
    for name in names:
        purge(name)
    return len(names)


def days_left(entry: Entry) -> int:
    """Days until the 30-day mark, counted to the pinned today, never below 0.
    Display only: nothing is removed when it reaches 0."""
    if entry.deleted_on is None:
        raise ValueError(f"{entry.path!r} is not deleted")
    elapsed = (_require_today() - date.fromisoformat(entry.deleted_on)).days
    return max(0, RETENTION_DAYS - elapsed)


# ---------------------------------------------------------------------------
# Scoring surface.


def snapshot() -> dict:
    """The ``/files_all`` payload: every live file, every empty folder, and
    every file in Recently Deleted under its original path. Sorted by
    ``(path, deleted, deleted_on, sha256)``; no times, no trash names, no
    ``Code``."""
    root = _require_root()
    rows = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith(".") or folder.is_symlink():
            continue
        rows.extend(_walk(folder, folder.name, deleted_on=None))
    for item in _trash_dir().iterdir():
        if item.name.startswith("."):
            continue
        meta = _read_meta(item.name)
        if _in_code(meta["original_path"]):
            continue
        rows.extend(_walk(item, meta["original_path"], deleted_on=meta["deleted_on"]))
    rows.sort(key=lambda r: (r["path"], r["deleted"], r["deleted_on"] or "", r["sha256"] or ""))
    return {"files": rows}


def _walk(node: Path, path: str, deleted_on: Optional[str]) -> list[dict]:
    if node.is_dir():
        children = [c for c in node.iterdir() if not c.name.startswith(".") and not c.is_symlink()]
        if not children:
            return [_row(path, "folder", 0, None, deleted_on)]
        rows = []
        for child in children:
            rows.extend(_walk(child, f"{path}/{child.name}", deleted_on))
        return rows
    data = node.read_bytes()
    return [_row(path, kind_of(node.name), len(data), hashlib.sha256(data).hexdigest(), deleted_on)]


def _row(path, kind, size, sha256, deleted_on) -> dict:
    return {
        "path": path,
        "kind": kind,
        "size": size,
        "sha256": sha256,
        "deleted": deleted_on is not None,
        "deleted_on": deleted_on,
    }


# ---------------------------------------------------------------------------
# Helpers.


def _require_root() -> Path:
    if _root is None:
        raise RuntimeError("files_store.configure() has not been called")
    return _root


def _require_today() -> date:
    if _today is None:
        raise RuntimeError("files_store.configure() has not been called")
    return _today


def _trash_dir() -> Path:
    return _require_root() / _TRASH


def _trash_item(trash_name: str) -> Path:
    if "/" in trash_name or "\\" in trash_name or trash_name.startswith(".") or not trash_name:
        raise ValueError(f"not a trash entry: {trash_name!r}")
    item = _trash_dir() / trash_name
    if not item.exists():
        raise FileNotFoundError(trash_name)
    return item


def _read_meta(trash_name: str) -> dict:
    return json.loads((_trash_dir() / _META / f"{trash_name}.json").read_text())


def _check_name(name: str) -> None:
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise ValueError(f"not a valid name: {name!r}")


def _check_movable(path: str) -> None:
    if _norm(path) in ("", CODE):
        raise ValueError(f"{path!r} cannot be moved, renamed or deleted")


def _existing(path: str) -> Path:
    target = resolve(path)
    if not target.exists():
        raise FileNotFoundError(path)
    return target


def _free_name(directory: Path, name: str) -> Path:
    """``directory/name``, or the first free ``stem (n)suffix`` after it."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    pure = PurePosixPath(name)
    stem, suffix = (pure.stem, pure.suffix) if pure.suffix and pure.stem else (name, "")
    n = 2
    while (directory / f"{stem} ({n}){suffix}").exists():
        n += 1
    return directory / f"{stem} ({n}){suffix}"


def _entry(node: Path, path: str) -> Entry:
    if node.is_dir():
        return Entry(path=path, kind="folder", size=0, sha256=None)
    data = node.read_bytes()
    return Entry(path=path, kind=kind_of(node.name), size=len(data), sha256=hashlib.sha256(data).hexdigest())


def _remove(node: Path) -> None:
    if node.is_dir() and not node.is_symlink():
        shutil.rmtree(node)
    else:
        node.unlink()


def _norm(path: str) -> str:
    return "/".join(p for p in str(path).split("/") if p not in ("", "."))


def _join(folder: str, name: str) -> str:
    folder = _norm(folder)
    return f"{folder}/{name}" if folder else name


def _parent(path: str) -> str:
    parts = _norm(path).split("/")
    return "/".join(parts[:-1])


def _is_root(folder: str) -> bool:
    return _norm(folder) == ""


def _in_code(path: str) -> bool:
    return _norm(path).split("/")[0] == CODE
