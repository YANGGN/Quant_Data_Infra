"""Copy only verified local presentation assets into an Atlas public payload."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from ..errors import ConflictError, ValidationError


_DASHBOARD_CSS_SHA256 = "9e080e57be2dd129eb5caa349edc189c1d1875072d635749c20ed18849c373b5"
_INTER_FONT_SHA256 = "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3"
_INTER_LICENSE_SHA256 = "262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a"
_STAGE6_ASSETS = (
    (
        "quant_data/dashboard/static/dashboard.css",
        "assets/dashboard.css",
        _DASHBOARD_CSS_SHA256,
    ),
    (
        "quant_data/dashboard/static/inter-variable.woff2",
        "assets/inter-variable.woff2",
        _INTER_FONT_SHA256,
    ),
    (
        "quant_data/dashboard/licenses/INTER-OFL-1.1.txt",
        "assets/INTER-OFL-1.1.txt",
        _INTER_LICENSE_SHA256,
    ),
)
_ATLAS_SOURCE_ASSETS = (
    ("index.html", "index.html"),
    ("assets/atlas.css", "assets/atlas.css"),
    ("assets/atlas.js", "assets/atlas.js"),
)


def _safe_project_file(project_root: Path, relative: str) -> Path:
    candidate = (project_root / relative).resolve(strict=True)
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValidationError("Atlas asset source escaped the project root") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValidationError("Atlas asset source is unavailable or unsafe")
    return candidate


def _write_new_file(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ConflictError("Atlas public asset target already exists")
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(destination, 0o644)
    except OSError as exc:
        raise ValidationError("Atlas public asset write failed") from exc


def _asset_record(relative: str, payload: bytes) -> dict[str, object]:
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _assert_local_site_source(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Atlas source asset must be UTF-8 text") from exc
    lowered = text.lower()
    if (
        "http://" in lowered
        or "https://" in lowered
        or re.search(r'''["']//[a-z0-9]''', lowered) is not None
    ):
        raise ValidationError("Atlas public source asset referenced a remote resource")


def copy_site_bundle(project_root: str | Path, payload_root: str | Path) -> tuple[dict[str, object], ...]:
    """Copy pinned dashboard assets and a complete local Atlas bundle if present."""

    project = Path(project_root).resolve(strict=True)
    target = Path(payload_root).resolve(strict=True)
    if not target.is_dir() or target.is_symlink():
        raise ValidationError("Atlas public payload root is unavailable or unsafe")

    records: list[dict[str, object]] = []
    for source_relative, target_relative, expected_sha256 in _STAGE6_ASSETS:
        source = _safe_project_file(project, source_relative)
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValidationError("Verified Stage 6 visual asset pin changed")
        destination = target / target_relative
        _write_new_file(destination, payload)
        records.append(_asset_record(target_relative, payload))

    source_root = (project / "sites" / "quant-data-atlas").resolve(strict=False)
    try:
        source_root.relative_to(project)
    except ValueError as exc:
        raise ValidationError("Atlas source root escaped the project root") from exc
    source_candidates = tuple(source_root / source for source, _ in _ATLAS_SOURCE_ASSETS)
    present = tuple(candidate.exists() for candidate in source_candidates)
    if any(present):
        if not all(present):
            raise ValidationError("Atlas site source bundle is incomplete")
        for (source_name, target_relative), source in zip(
            _ATLAS_SOURCE_ASSETS,
            source_candidates,
            strict=True,
        ):
            if not source.is_file() or source.is_symlink():
                raise ValidationError("Atlas site source asset is unavailable or unsafe")
            payload = source.read_bytes()
            _assert_local_site_source(payload)
            destination = target / target_relative
            _write_new_file(destination, payload)
            records.append(_asset_record(target_relative, payload))
    return tuple(sorted(records, key=lambda item: str(item["path"])))


__all__ = ("copy_site_bundle",)
