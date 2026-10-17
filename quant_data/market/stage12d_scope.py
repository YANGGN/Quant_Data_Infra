"""Closed authority scope for Stage 12D no-transfer market adoption.

This module reads only the explicitly supplied (or reviewed default) JSON
manifest.  It never opens a SQLite database, resolves a target path, reads an
environment variable, or contacts a provider.  Operations must revalidate the
immutable returned object immediately before use.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from ..errors import Issue, ValidationError
from ..json_codec import dumps_strict, loads_strict


STAGE12D_SCOPE_CONTRACT = "quant_data.stage12d_market_no_transfer_adoption_v1"
STAGE12D_SCOPE_VERSION = "1.0.0"
STAGE12D_RECEIPT_SCHEMA = "quant_data.stage12d_market_no_transfer_receipt_v1"
DEFAULT_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "stage12d_market_no_transfer_adoption_v1_scope.json"
)

# The semantic digest covers the canonical strict JSON mapping.  The source
# digest makes even semantically equivalent whitespace/source-byte drift fail
# closed before semantic parsing.
REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256 = (
    "83ff9be19997eb77c9af025b439adf9a977a42113ab8ea413125a1b88f77c981"
)
REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256 = (
    "2b20ad0cc5a62e12839c33cae81f3ef60e222535009df1caeda8ab2f93933e1e"
)

_MAX_SCOPE_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_OR_URL = re.compile(
    r"^(?:/|~(?:/|$)|[A-Za-z]:[\\/]|file:|[A-Za-z][A-Za-z0-9+.-]*://)"
)
_FORBIDDEN_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "header",
        "host",
        "password",
        "secret",
        "token",
        "uri",
        "url",
    }
)
_EXPECTED_CLOSED_CAPABILITIES = (
    "backup",
    "caller_path",
    "caller_sql",
    "credential",
    "database_copy",
    "database_move",
    "database_replace",
    "database_write",
    "env_configuration",
    "migration",
    "network",
    "promotion",
    "provider",
    "public_consumer",
    "registry_bump",
    "retained_source_reopen",
    "scheduler",
    "stage12e",
    "transfer",
)
_EXPECTED_STAMPS = ("main", "wal", "shm", "journal")
_EXPECTED_TOP_LEVEL = frozenset(
    {
        "access",
        "closed_capabilities",
        "contract",
        "expected_database",
        "receipt_policy",
        "registry",
        "stage12c",
        "stage12e",
        "target",
        "version",
    }
)
_EXPECTED_TARGET_PATH = "data/market.sqlite"
_EXPECTED_STAGE12C_COMPLETION_PATH = (
    "data/.stage12/market-v1/stage12c-20260813-20260814/completion.json"
)
_EXPECTED_PRIVATE_RECEIPT_ROOT = (
    "data/.stage12/market-v1/stage12d-no-transfer-adoption"
)


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _child(pointer: str, part: str | int) -> str:
    return f"{pointer}/{part}" if pointer else f"/{part}"


def _mapping(value: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error(pointer, "type", "Expected an object with string keys")
    return value


def _array(value: object, *, pointer: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(pointer, "type", "Expected an array")
    return value


def _closed(value: Mapping[str, Any], *, pointer: str, fields: frozenset[str]) -> None:
    if set(value) != fields:
        raise _error(pointer, "shape", "Object fields differ from the reviewed Stage 12D scope")


def _text(value: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _error(pointer, "type", "Expected bounded nonempty trimmed text")
    if _ABSOLUTE_OR_URL.fullmatch(value) is not None:
        raise _error(pointer, "operational_value", "Scope cannot contain an absolute path or URL")
    return value


def _exact_text(value: object, *, pointer: str, expected: str) -> str:
    parsed = _text(value, pointer=pointer, maximum=max(256, len(expected)))
    if parsed != expected:
        raise _error(pointer, "constant", "Value differs from the reviewed Stage 12D scope")
    return parsed


def _exact_int(value: object, *, pointer: str, expected: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _error(pointer, "constant", "Integer differs from the reviewed Stage 12D scope")
    return value


def _exact_bool(value: object, *, pointer: str, expected: bool) -> bool:
    if not isinstance(value, bool) or value is not expected:
        raise _error(pointer, "constant", "Boolean differs from the reviewed Stage 12D scope")
    return value


def _exact_none(value: object, *, pointer: str) -> None:
    if value is not None:
        raise _error(pointer, "constant", "Value must be the reviewed null invariant")
    return None


def _relative_path(value: object, *, pointer: str, expected: str) -> str:
    parsed = _exact_text(value, pointer=pointer, expected=expected)
    path = Path(parsed)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "\\" in parsed
        or parsed.endswith("/")
    ):
        raise _error(pointer, "path", "Path must be the reviewed project-relative declaration")
    return parsed


def _sha256_text(value: object, *, pointer: str, expected: str) -> str:
    parsed = _exact_text(value, pointer=pointer, expected=expected)
    if _SHA256.fullmatch(parsed) is None:
        raise _error(pointer, "sha256", "Expected a lowercase SHA-256 digest")
    return parsed


def _reject_unsafe_keys_and_values(value: object, *, pointer: str) -> None:
    """Reject unreviewed secret or transport declarations before shape parsing."""

    if isinstance(value, Mapping):
        for key, child_value in value.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Scope object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            child = _child(pointer, key)
            if normalized in _FORBIDDEN_KEYS:
                raise _error(child, "forbidden_key", "Scope cannot declare credentials or transport locators")
            _reject_unsafe_keys_and_values(child_value, pointer=child)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            _reject_unsafe_keys_and_values(child_value, pointer=_child(pointer, index))
    elif isinstance(value, str) and _ABSOLUTE_OR_URL.fullmatch(value) is not None:
        raise _error(pointer, "operational_value", "Scope cannot contain an absolute path or URL")


@dataclass(frozen=True, slots=True)
class Stage12DRegistryBinding:
    revision: str
    schema_version: str

    def manifest_mapping(self) -> dict[str, str]:
        return {"revision": self.revision, "schema_version": self.schema_version}


@dataclass(frozen=True, slots=True)
class Stage12DStage12CBinding:
    completion_receipt_path: str
    completion_receipt_sha256: str
    plan_sha256: str
    scope_file_sha256: str
    scope_semantic_sha256: str
    authorized_http_402: int
    closed: int
    published_complete: int
    successful_empty: int

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "completion_receipt_path": self.completion_receipt_path,
            "completion_receipt_sha256": self.completion_receipt_sha256,
            "ledger": {
                "authorized_http_402": self.authorized_http_402,
                "closed": self.closed,
                "published_complete": self.published_complete,
                "successful_empty": self.successful_empty,
            },
            "plan_sha256": self.plan_sha256,
            "scope_file_sha256": self.scope_file_sha256,
            "scope_semantic_sha256": self.scope_semantic_sha256,
        }


@dataclass(frozen=True, slots=True)
class Stage12DExpectedDatabase:
    availability_policy: str
    captures: int
    correction_sequence: int
    current_rows: int
    duplicate_current: int
    foreign_key_violations: int
    integrity: str
    invalid_current_pointer: int
    stage12c_captures: int
    stage12c_current_rows: int
    stage12c_version_rows: int
    supersession: None
    terminal_database_facts: int
    version_rows: int

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "availability_policy": self.availability_policy,
            "captures": self.captures,
            "correction_sequence": self.correction_sequence,
            "current_rows": self.current_rows,
            "duplicate_current": self.duplicate_current,
            "foreign_key_violations": self.foreign_key_violations,
            "integrity": self.integrity,
            "invalid_current_pointer": self.invalid_current_pointer,
            "stage12c_captures": self.stage12c_captures,
            "stage12c_current_rows": self.stage12c_current_rows,
            "stage12c_version_rows": self.stage12c_version_rows,
            "supersession": self.supersession,
            "terminal_database_facts": self.terminal_database_facts,
            "version_rows": self.version_rows,
        }


@dataclass(frozen=True, slots=True)
class Stage12DReceiptPolicy:
    create_mode: str
    max_proofs: int
    private_receipt_root: str
    receipt_format: str
    receipt_mode: str
    receipt_schema: str
    root_mode: str
    semantic_proof: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "create_mode": self.create_mode,
            "max_proofs": self.max_proofs,
            "private_receipt_root": self.private_receipt_root,
            "receipt_format": self.receipt_format,
            "receipt_mode": self.receipt_mode,
            "receipt_schema": self.receipt_schema,
            "root_mode": self.root_mode,
            "semantic_proof": self.semantic_proof,
        }


@dataclass(frozen=True, slots=True)
class Stage12DTargetPolicy:
    direct_regular_file: bool
    non_symlink: bool
    project_relative_path: str
    quiet_target: bool
    required_link_count: int
    stamp_components: tuple[str, ...]
    wal_and_rollback_journal: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "direct_regular_file": self.direct_regular_file,
            "non_symlink": self.non_symlink,
            "project_relative_path": self.project_relative_path,
            "quiet_target": self.quiet_target,
            "required_link_count": self.required_link_count,
            "stamp_components": list(self.stamp_components),
            "wal_and_rollback_journal": self.wal_and_rollback_journal,
        }


@dataclass(frozen=True, slots=True)
class Stage12DAccessPolicy:
    backup: bool
    caller_arguments: bool
    caller_path: bool
    caller_sql: bool
    checkpoint: bool
    connection_uri_query: str
    database_write_lock: bool
    generic_database_helper: bool
    journal_mode: bool
    normal_reader: bool
    query_only: bool
    restore: bool

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "backup": self.backup,
            "caller_arguments": self.caller_arguments,
            "caller_path": self.caller_path,
            "caller_sql": self.caller_sql,
            "checkpoint": self.checkpoint,
            "connection_uri_query": self.connection_uri_query,
            "database_write_lock": self.database_write_lock,
            "generic_database_helper": self.generic_database_helper,
            "journal_mode": self.journal_mode,
            "normal_reader": self.normal_reader,
            "query_only": self.query_only,
            "restore": self.restore,
        }


@dataclass(frozen=True, slots=True)
class Stage12DMarketNoTransferAdoptionScope:
    access: Stage12DAccessPolicy
    closed_capabilities: tuple[str, ...]
    contract: str
    expected_database: Stage12DExpectedDatabase
    receipt_policy: Stage12DReceiptPolicy
    registry: Stage12DRegistryBinding
    stage12c: Stage12DStage12CBinding
    stage12e_status: str
    target: Stage12DTargetPolicy
    version: str
    manifest_sha256: str
    source_file_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "access": self.access.manifest_mapping(),
            "closed_capabilities": list(self.closed_capabilities),
            "contract": self.contract,
            "expected_database": self.expected_database.manifest_mapping(),
            "receipt_policy": self.receipt_policy.manifest_mapping(),
            "registry": self.registry.manifest_mapping(),
            "stage12c": self.stage12c.manifest_mapping(),
            "stage12e": {"status": self.stage12e_status},
            "target": self.target.manifest_mapping(),
            "version": self.version,
        }


def _parse_access(value: object) -> Stage12DAccessPolicy:
    raw = _mapping(value, pointer="/access")
    _closed(
        raw,
        pointer="/access",
        fields=frozenset(
            {
                "backup",
                "caller_arguments",
                "caller_path",
                "caller_sql",
                "checkpoint",
                "connection_uri_query",
                "database_write_lock",
                "generic_database_helper",
                "journal_mode",
                "normal_reader",
                "query_only",
                "restore",
            }
        ),
    )
    return Stage12DAccessPolicy(
        backup=_exact_bool(raw["backup"], pointer="/access/backup", expected=False),
        caller_arguments=_exact_bool(
            raw["caller_arguments"], pointer="/access/caller_arguments", expected=False
        ),
        caller_path=_exact_bool(raw["caller_path"], pointer="/access/caller_path", expected=False),
        caller_sql=_exact_bool(raw["caller_sql"], pointer="/access/caller_sql", expected=False),
        checkpoint=_exact_bool(raw["checkpoint"], pointer="/access/checkpoint", expected=False),
        connection_uri_query=_exact_text(
            raw["connection_uri_query"],
            pointer="/access/connection_uri_query",
            expected="mode=ro&immutable=1",
        ),
        database_write_lock=_exact_bool(
            raw["database_write_lock"], pointer="/access/database_write_lock", expected=False
        ),
        generic_database_helper=_exact_bool(
            raw["generic_database_helper"],
            pointer="/access/generic_database_helper",
            expected=False,
        ),
        journal_mode=_exact_bool(
            raw["journal_mode"], pointer="/access/journal_mode", expected=False
        ),
        normal_reader=_exact_bool(
            raw["normal_reader"], pointer="/access/normal_reader", expected=False
        ),
        query_only=_exact_bool(raw["query_only"], pointer="/access/query_only", expected=True),
        restore=_exact_bool(raw["restore"], pointer="/access/restore", expected=False),
    )


def _parse_closed_capabilities(value: object) -> tuple[str, ...]:
    parsed = tuple(
        _text(item, pointer=f"/closed_capabilities/{index}")
        for index, item in enumerate(_array(value, pointer="/closed_capabilities"))
    )
    if parsed != _EXPECTED_CLOSED_CAPABILITIES or len(set(parsed)) != len(parsed):
        raise _error(
            "/closed_capabilities",
            "closed",
            "Closed capabilities differ from the reviewed Stage 12D boundary",
        )
    return parsed


def _parse_expected_database(value: object) -> Stage12DExpectedDatabase:
    raw = _mapping(value, pointer="/expected_database")
    _closed(
        raw,
        pointer="/expected_database",
        fields=frozenset(
            {
                "availability_policy",
                "captures",
                "correction_sequence",
                "current_rows",
                "duplicate_current",
                "foreign_key_violations",
                "integrity",
                "invalid_current_pointer",
                "stage12c_captures",
                "stage12c_current_rows",
                "stage12c_version_rows",
                "supersession",
                "terminal_database_facts",
                "version_rows",
            }
        ),
    )
    return Stage12DExpectedDatabase(
        availability_policy=_exact_text(
            raw["availability_policy"],
            pointer="/expected_database/availability_policy",
            expected="available_at_equals_captured_at",
        ),
        captures=_exact_int(raw["captures"], pointer="/expected_database/captures", expected=5_210),
        correction_sequence=_exact_int(
            raw["correction_sequence"],
            pointer="/expected_database/correction_sequence",
            expected=1,
        ),
        current_rows=_exact_int(
            raw["current_rows"], pointer="/expected_database/current_rows", expected=4_237_131
        ),
        duplicate_current=_exact_int(
            raw["duplicate_current"], pointer="/expected_database/duplicate_current", expected=0
        ),
        foreign_key_violations=_exact_int(
            raw["foreign_key_violations"],
            pointer="/expected_database/foreign_key_violations",
            expected=0,
        ),
        integrity=_exact_text(raw["integrity"], pointer="/expected_database/integrity", expected="ok"),
        invalid_current_pointer=_exact_int(
            raw["invalid_current_pointer"],
            pointer="/expected_database/invalid_current_pointer",
            expected=0,
        ),
        stage12c_captures=_exact_int(
            raw["stage12c_captures"],
            pointer="/expected_database/stage12c_captures",
            expected=619,
        ),
        stage12c_current_rows=_exact_int(
            raw["stage12c_current_rows"],
            pointer="/expected_database/stage12c_current_rows",
            expected=1_238,
        ),
        stage12c_version_rows=_exact_int(
            raw["stage12c_version_rows"],
            pointer="/expected_database/stage12c_version_rows",
            expected=1_238,
        ),
        supersession=_exact_none(raw["supersession"], pointer="/expected_database/supersession"),
        terminal_database_facts=_exact_int(
            raw["terminal_database_facts"],
            pointer="/expected_database/terminal_database_facts",
            expected=0,
        ),
        version_rows=_exact_int(
            raw["version_rows"], pointer="/expected_database/version_rows", expected=4_237_873
        ),
    )


def _parse_receipt_policy(value: object) -> Stage12DReceiptPolicy:
    raw = _mapping(value, pointer="/receipt_policy")
    _closed(
        raw,
        pointer="/receipt_policy",
        fields=frozenset(
            {
                "create_mode",
                "max_proofs",
                "private_receipt_root",
                "receipt_format",
                "receipt_mode",
                "receipt_schema",
                "root_mode",
                "semantic_proof",
            }
        ),
    )
    return Stage12DReceiptPolicy(
        create_mode=_exact_text(
            raw["create_mode"], pointer="/receipt_policy/create_mode", expected="O_EXCL"
        ),
        max_proofs=_exact_int(raw["max_proofs"], pointer="/receipt_policy/max_proofs", expected=2),
        private_receipt_root=_relative_path(
            raw["private_receipt_root"],
            pointer="/receipt_policy/private_receipt_root",
            expected=_EXPECTED_PRIVATE_RECEIPT_ROOT,
        ),
        receipt_format=_exact_text(
            raw["receipt_format"], pointer="/receipt_policy/receipt_format", expected="json"
        ),
        receipt_mode=_exact_text(
            raw["receipt_mode"], pointer="/receipt_policy/receipt_mode", expected="0600"
        ),
        receipt_schema=_exact_text(
            raw["receipt_schema"],
            pointer="/receipt_policy/receipt_schema",
            expected=STAGE12D_RECEIPT_SCHEMA,
        ),
        root_mode=_exact_text(
            raw["root_mode"], pointer="/receipt_policy/root_mode", expected="0700"
        ),
        semantic_proof=_exact_text(
            raw["semantic_proof"],
            pointer="/receipt_policy/semantic_proof",
            expected="path_secret_raw_body_free",
        ),
    )


def _parse_registry(value: object) -> Stage12DRegistryBinding:
    raw = _mapping(value, pointer="/registry")
    _closed(raw, pointer="/registry", fields=frozenset({"revision", "schema_version"}))
    return Stage12DRegistryBinding(
        revision=_exact_text(raw["revision"], pointer="/registry/revision", expected="2.14.0"),
        schema_version=_exact_text(
            raw["schema_version"], pointer="/registry/schema_version", expected="1.8.0"
        ),
    )


def _parse_stage12c(value: object) -> Stage12DStage12CBinding:
    raw = _mapping(value, pointer="/stage12c")
    _closed(
        raw,
        pointer="/stage12c",
        fields=frozenset(
            {
                "completion_receipt_path",
                "completion_receipt_sha256",
                "ledger",
                "plan_sha256",
                "scope_file_sha256",
                "scope_semantic_sha256",
            }
        ),
    )
    ledger = _mapping(raw["ledger"], pointer="/stage12c/ledger")
    _closed(
        ledger,
        pointer="/stage12c/ledger",
        fields=frozenset(
            {"authorized_http_402", "closed", "published_complete", "successful_empty"}
        ),
    )
    binding = Stage12DStage12CBinding(
        completion_receipt_path=_relative_path(
            raw["completion_receipt_path"],
            pointer="/stage12c/completion_receipt_path",
            expected=_EXPECTED_STAGE12C_COMPLETION_PATH,
        ),
        completion_receipt_sha256=_sha256_text(
            raw["completion_receipt_sha256"],
            pointer="/stage12c/completion_receipt_sha256",
            expected="0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88",
        ),
        plan_sha256=_sha256_text(
            raw["plan_sha256"],
            pointer="/stage12c/plan_sha256",
            expected="5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1",
        ),
        scope_file_sha256=_sha256_text(
            raw["scope_file_sha256"],
            pointer="/stage12c/scope_file_sha256",
            expected="24d8448c124cedb5deb745b50943291d955f05e7a95ac1d9747a7d51b6760226",
        ),
        scope_semantic_sha256=_sha256_text(
            raw["scope_semantic_sha256"],
            pointer="/stage12c/scope_semantic_sha256",
            expected="2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392",
        ),
        authorized_http_402=_exact_int(
            ledger["authorized_http_402"],
            pointer="/stage12c/ledger/authorized_http_402",
            expected=7,
        ),
        closed=_exact_int(ledger["closed"], pointer="/stage12c/ledger/closed", expected=629),
        published_complete=_exact_int(
            ledger["published_complete"],
            pointer="/stage12c/ledger/published_complete",
            expected=619,
        ),
        successful_empty=_exact_int(
            ledger["successful_empty"],
            pointer="/stage12c/ledger/successful_empty",
            expected=3,
        ),
    )
    if (
        binding.published_complete
        + binding.successful_empty
        + binding.authorized_http_402
        != binding.closed
    ):
        raise _error("/stage12c/ledger", "math", "Stage 12C ledger does not close exactly")
    return binding


def _parse_stage12e(value: object) -> str:
    raw = _mapping(value, pointer="/stage12e")
    _closed(raw, pointer="/stage12e", fields=frozenset({"status"}))
    return _exact_text(raw["status"], pointer="/stage12e/status", expected="closed")


def _parse_target(value: object) -> Stage12DTargetPolicy:
    raw = _mapping(value, pointer="/target")
    _closed(
        raw,
        pointer="/target",
        fields=frozenset(
            {
                "direct_regular_file",
                "non_symlink",
                "project_relative_path",
                "quiet_target",
                "required_link_count",
                "stamp_components",
                "wal_and_rollback_journal",
            }
        ),
    )
    stamp_components = tuple(
        _text(item, pointer=f"/target/stamp_components/{index}")
        for index, item in enumerate(_array(raw["stamp_components"], pointer="/target/stamp_components"))
    )
    if stamp_components != _EXPECTED_STAMPS or len(set(stamp_components)) != len(stamp_components):
        raise _error("/target/stamp_components", "closed", "Target stamps differ from the reviewed Stage 12D policy")
    return Stage12DTargetPolicy(
        direct_regular_file=_exact_bool(
            raw["direct_regular_file"], pointer="/target/direct_regular_file", expected=True
        ),
        non_symlink=_exact_bool(raw["non_symlink"], pointer="/target/non_symlink", expected=True),
        project_relative_path=_relative_path(
            raw["project_relative_path"],
            pointer="/target/project_relative_path",
            expected=_EXPECTED_TARGET_PATH,
        ),
        quiet_target=_exact_bool(raw["quiet_target"], pointer="/target/quiet_target", expected=True),
        required_link_count=_exact_int(
            raw["required_link_count"], pointer="/target/required_link_count", expected=1
        ),
        stamp_components=stamp_components,
        wal_and_rollback_journal=_exact_text(
            raw["wal_and_rollback_journal"],
            pointer="/target/wal_and_rollback_journal",
            expected="absent_or_zero",
        ),
    )


def _parse_scope(
    value: object,
    *,
    source_file_sha256: str,
) -> Stage12DMarketNoTransferAdoptionScope:
    raw = _mapping(value, pointer="/")
    _closed(raw, pointer="/", fields=_EXPECTED_TOP_LEVEL)
    stage12c = _parse_stage12c(raw["stage12c"])
    expected_database = _parse_expected_database(raw["expected_database"])
    if (
        expected_database.stage12c_captures != stage12c.published_complete
        or expected_database.stage12c_current_rows
        != stage12c.published_complete * 2
        or expected_database.stage12c_version_rows
        != stage12c.published_complete * 2
        or expected_database.stage12c_current_rows != expected_database.stage12c_version_rows
        or expected_database.current_rows < expected_database.stage12c_current_rows
        or expected_database.version_rows < expected_database.stage12c_version_rows
        or expected_database.captures < expected_database.stage12c_captures
    ):
        raise _error(
            "/expected_database",
            "math",
            "Stage 12D expected database counts do not bind the Stage 12C ledger",
        )
    scope = Stage12DMarketNoTransferAdoptionScope(
        access=_parse_access(raw["access"]),
        closed_capabilities=_parse_closed_capabilities(raw["closed_capabilities"]),
        contract=_exact_text(raw["contract"], pointer="/contract", expected=STAGE12D_SCOPE_CONTRACT),
        expected_database=expected_database,
        receipt_policy=_parse_receipt_policy(raw["receipt_policy"]),
        registry=_parse_registry(raw["registry"]),
        stage12c=stage12c,
        stage12e_status=_parse_stage12e(raw["stage12e"]),
        target=_parse_target(raw["target"]),
        version=_exact_text(raw["version"], pointer="/version", expected=STAGE12D_SCOPE_VERSION),
        manifest_sha256="",
        source_file_sha256=source_file_sha256,
    )
    if dumps_strict(raw) != dumps_strict(scope.manifest_mapping()):
        raise _error("/", "canonical", "Scope cannot be normalized to the reviewed closed schema")
    digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    if digest != REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256:
        raise _error("/", "immutable", "Scope differs from the reviewed immutable Stage 12D manifest")
    return replace(scope, manifest_sha256=digest)


def load_stage12d_market_no_transfer_adoption_scope(
    source: str | Path | None = None,
) -> Stage12DMarketNoTransferAdoptionScope:
    """Load only the exact reviewed Stage 12D no-transfer scope source."""

    if source is None:
        source_path = DEFAULT_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_PATH
    elif isinstance(source, bool) or not isinstance(source, (str, Path)):
        raise _error("/", "source", "A Stage 12D scope source must be a path")
    elif isinstance(source, str) and not source.strip():
        raise _error("/", "source", "A Stage 12D scope source must be a path")
    else:
        source_path = Path(source)
    try:
        payload = source_path.read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "source", "Stage 12D scope source is unavailable") from exc
    source_digest = hashlib.sha256(payload).hexdigest()
    if source_digest != REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256:
        raise _error(
            "/",
            "source_bytes",
            "Stage 12D scope source bytes differ from the reviewed manifest",
        )
    raw = loads_strict(payload, max_bytes=_MAX_SCOPE_BYTES)
    _reject_unsafe_keys_and_values(raw, pointer="")
    return _parse_scope(raw, source_file_sha256=source_digest)


def require_stage12d_market_no_transfer_adoption_scope(
    scope: object,
) -> Stage12DMarketNoTransferAdoptionScope:
    """Revalidate an in-memory Stage 12D scope before an operation uses it."""

    if not isinstance(scope, Stage12DMarketNoTransferAdoptionScope):
        raise ValidationError("Stage 12D requires a reviewed no-transfer market scope")
    try:
        digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValidationError("Stage 12D no-transfer market scope is invalid") from exc
    if (
        scope.manifest_sha256
        != REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256
        or scope.source_file_sha256
        != REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256
        or digest != REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256
    ):
        raise ValidationError("Stage 12D no-transfer market scope binding is invalid")
    return scope


__all__ = (
    "DEFAULT_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_PATH",
    "REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256",
    "REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256",
    "STAGE12D_RECEIPT_SCHEMA",
    "STAGE12D_SCOPE_CONTRACT",
    "STAGE12D_SCOPE_VERSION",
    "Stage12DAccessPolicy",
    "Stage12DExpectedDatabase",
    "Stage12DMarketNoTransferAdoptionScope",
    "Stage12DReceiptPolicy",
    "Stage12DRegistryBinding",
    "Stage12DStage12CBinding",
    "Stage12DTargetPolicy",
    "load_stage12d_market_no_transfer_adoption_scope",
    "require_stage12d_market_no_transfer_adoption_scope",
)
