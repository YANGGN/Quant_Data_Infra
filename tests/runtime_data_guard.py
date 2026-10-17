"""Metadata-only project-data guards for explicit-root offline tests.

The test suite may run beside an ignored local ``data/`` directory. These
helpers inspect filesystem metadata only: they never open a SQLite database,
hash file contents, or follow directory symlinks while walking the tree.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat


_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


@dataclass(frozen=True)
class RuntimeDataEntry:
    """One no-follow metadata record from the project ``data/`` tree."""

    path: str
    entry_type: str
    resolved_target: str
    link_target: str | None
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int
    hard_link_count: int


@dataclass(frozen=True)
class RuntimeDataSnapshot:
    """Metadata sufficient to detect changes without reading file contents."""

    root_present: bool
    entries: tuple[RuntimeDataEntry, ...]
    sqlite_sidecars: tuple[tuple[str, tuple[tuple[str, bool], ...]], ...]


def _entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    return f"other:{stat.S_IFMT(mode):o}"


def _metadata_entry(path: Path, relative_path: Path, metadata: os.stat_result) -> RuntimeDataEntry:
    is_link = stat.S_ISLNK(metadata.st_mode)
    return RuntimeDataEntry(
        path=relative_path.as_posix(),
        entry_type=_entry_type(metadata.st_mode),
        resolved_target=os.path.realpath(path),
        link_target=os.readlink(path) if is_link else None,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        hard_link_count=metadata.st_nlink,
    )


def _walk_metadata_tree(
    path: Path,
    relative_path: Path,
    entries: list[tuple[RuntimeDataEntry, Path]],
) -> None:
    """Capture a deterministic tree without traversing directory symlinks."""

    metadata = os.lstat(path)
    entry = _metadata_entry(path, relative_path, metadata)
    entries.append((entry, path))
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return
    with os.scandir(path) as directory:
        children = sorted(directory, key=lambda child: child.name)
    for child in children:
        _walk_metadata_tree(
            Path(child.path),
            relative_path / child.name,
            entries,
        )


def snapshot_project_data(project_root: Path) -> RuntimeDataSnapshot:
    """Return a no-content-read snapshot of ``project_root / 'data'``.

    ``lexists`` distinguishes an absent root from a dangling symlink. Every
    present entry is recorded using ``lstat`` so a symlink's own identity and
    target are both visible without recursively scanning outside the tree.
    """

    data_root = project_root / "data"
    if not os.path.lexists(data_root):
        return RuntimeDataSnapshot(
            root_present=False,
            entries=(),
            sqlite_sidecars=(),
        )

    entries_with_paths: list[tuple[RuntimeDataEntry, Path]] = []
    _walk_metadata_tree(data_root, Path("."), entries_with_paths)
    sqlite_sidecars = tuple(
        (
            entry.path,
            tuple(
                (suffix, os.path.lexists(f"{path}{suffix}"))
                for suffix in _SQLITE_SIDECAR_SUFFIXES
            ),
        )
        for entry, path in entries_with_paths
        if entry.path.endswith(".sqlite")
    )
    return RuntimeDataSnapshot(
        root_present=True,
        entries=tuple(entry for entry, _ in entries_with_paths),
        sqlite_sidecars=sqlite_sidecars,
    )


def assert_project_data_unchanged(
    before: RuntimeDataSnapshot,
    *,
    project_root: Path,
) -> None:
    """Fail if an explicit-root test touched the real project data tree."""

    after = snapshot_project_data(project_root)
    if after != before:
        raise AssertionError(
            "Project data changed during an explicit-root offline test: "
            f"before={before!r}; after={after!r}"
        )
