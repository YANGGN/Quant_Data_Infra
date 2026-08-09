"""Verified synthetic fixture manifest access."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ValidationError
from .json_codec import loads_strict


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Fixture:
    id: str
    fixture_schema_version: str
    ingestion_family_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    identity_dataset_id: str | None
    store: str
    provider: str
    resource: Path
    resource_name: str
    sha256: str
    expected_semantic_identity: str
    byte_count: int
    captured_at: str
    test_fixture: bool
    promotable: bool
    expected_warnings: tuple[str, ...]
    request_scope: Mapping[str, Any]
    metadata: Mapping[str, Any]
    bytes: bytes


class FixtureManifest:
    def __init__(self, root: Path, raw: Mapping[str, Any]) -> None:
        self.root = root
        self.raw = raw
        self._entries = {entry["id"]: entry for entry in raw["fixtures"]}
        if len(self._entries) != len(raw["fixtures"]):
            raise ValidationError("Fixture IDs must be unique")

    @classmethod
    def load(cls, path: str | Path, *, project_root: str | Path) -> "FixtureManifest":
        root = Path(project_root).resolve(strict=True)
        manifest_path = Path(path)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        raw = loads_strict(manifest_path.read_bytes(), max_bytes=1024 * 1024)
        if not isinstance(raw, dict) or raw.get("schema_version") != "1.0.0":
            raise ValidationError("Unsupported fixture manifest")
        if set(raw) != {"schema_version", "fixtures"} or not isinstance(raw["fixtures"], list):
            raise ValidationError("Fixture manifest has an invalid shape")
        return cls(root, raw)

    def get(self, fixture_id: str) -> Fixture:
        try:
            entry = self._entries[fixture_id]
        except KeyError as exc:
            raise ValidationError("Unknown fixture ID") from exc
        required = {
            "id",
            "fixture_schema_version",
            "ingestion_family_id",
            "evidence_dataset_id",
            "canonical_dataset_id",
            "identity_dataset_id",
            "store",
            "provider",
            "resource",
            "sha256",
            "expected_semantic_identity",
            "byte_count",
            "captured_at",
            "test_fixture",
            "promotable",
            "expected_warnings",
            "request_scope",
            "metadata",
        }
        if set(entry) != required:
            raise ValidationError("Fixture manifest entry has an invalid shape")
        if entry["fixture_schema_version"] != "1.0.0":
            raise ValidationError("Fixture entry has an unsupported schema version")
        if entry["test_fixture"] is not True or entry["promotable"] is not False:
            raise ValidationError("Synthetic fixtures must be test-only and non-promotable")
        expected_warnings = entry["expected_warnings"]
        if not isinstance(expected_warnings, list) or not all(
            isinstance(warning, str) for warning in expected_warnings
        ):
            raise ValidationError("Fixture expected warnings must be a string array")
        if not isinstance(entry["expected_semantic_identity"], str) or not _SHA256.fullmatch(
            entry["expected_semantic_identity"]
        ):
            raise ValidationError("Fixture semantic identity must be a lowercase SHA-256 digest")
        relative = Path(entry["resource"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationError("Fixture resource path is unsafe")
        resource = (self.root / relative).resolve(strict=True)
        try:
            resource.relative_to(self.root)
        except ValueError as exc:
            raise ValidationError("Fixture resource escapes the project root") from exc
        payload = resource.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry["sha256"] or len(payload) != entry["byte_count"]:
            raise ValidationError("Fixture bytes do not match the reviewed manifest")
        return Fixture(
            id=entry["id"],
            fixture_schema_version=entry["fixture_schema_version"],
            ingestion_family_id=entry["ingestion_family_id"],
            evidence_dataset_id=entry["evidence_dataset_id"],
            canonical_dataset_id=entry["canonical_dataset_id"],
            identity_dataset_id=entry["identity_dataset_id"],
            store=entry["store"],
            provider=entry["provider"],
            resource=resource,
            resource_name=relative.as_posix(),
            sha256=digest,
            expected_semantic_identity=entry["expected_semantic_identity"],
            byte_count=len(payload),
            captured_at=entry["captured_at"],
            test_fixture=entry["test_fixture"],
            promotable=entry["promotable"],
            expected_warnings=tuple(expected_warnings),
            request_scope=entry["request_scope"],
            metadata=entry["metadata"],
            bytes=payload,
        )
