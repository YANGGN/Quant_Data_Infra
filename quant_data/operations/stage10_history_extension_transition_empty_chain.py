"""Frozen successor-transition and authorization-chain bindings for Stage 10.

This module contains no provider, credential, store, or journal I/O.  It
describes the one append-only successor transition needed after the reviewed
known-listed-empty receipt was written.  The runner validates the actual
immutable receipt files before it builds the compact chain value below; the
repopulation layer then requires that compact value whenever an operator
terminal outcome appears in a ledger.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from ..json_codec import dumps_strict


TRANSITION_CONTRACT: Final = (
    "quant_data.stage10_history_extension_execution_transition"
)
TRANSITION_VERSION: Final = "1.0.0"
TRANSITION_FILENAME: Final = "execution-transition-empty-chain-hardening.json"
TRANSITION_AUTHORIZATION: Final = "user_authorized_skip_failed_tickers_and_continue"

# The immutable receipt already on the approved isolated target was produced
# from this source revision.  It is the sole predecessor a successor receipt
# may extend; a future current execution revision is deliberately calculated
# from the frozen source tree at authorization time.
OLD_EXECUTION_REVISION: Final = (
    "47ff33b61f84554f96eda93d6cf924fe8eaa5fbcb29a511d5a8b26089b7923a2"
)
PLAN_SHA256: Final = (
    "364ae14f62e9a43621f6f2153dd9194780f403b142dbdfd349637a9dd4952a2d"
)
PRIOR_402_TRANSITION_SHA256: Final = (
    "726125269749572560f46bef6816565205fd321332d76e82029e518b1de8bf4c"
)
KNOWN_LISTED_EMPTY_TRANSITION_SHA256: Final = (
    "e432801c39782cd84f5b2460ee08a89dd2030a32f2100fefb6de066bfd488269"
)
LIVE_EXECUTION_REVISION: Final = (
    "a5fc2a6ff5d5eb8f5ddffffe09e5a7fb9a527ff323fe5725d4cee9a8bea36feb"
)
LIVE_MANIFEST_SHA256: Final = (
    "472407a3085094cc9bdb028fa672556a13ce51c10299240acf68c875bf267a65"
)
CLOSED_REQUEST_COUNT: Final = 4_603
PENDING_ORDINAL: Final = 4_604
PENDING_SYMBOL: Final = "EUV"
PENDING_WINDOW_ID: Final = "2025-2026"
PENDING_FROM: Final = "2025-01-01"
PENDING_TO: Final = "2026-08-12"
PENDING_INTENT_ID: Final = (
    "stage10_history_extension_intent_7be0e3c6c7b921defdaa4e2f07b68b64"
)
PENDING_INTENT_SHA256: Final = (
    "f072c7076eb5812fb7fa2c8fad8ac5757e3893c402cf02461b5a70b06b81d34c"
)
PENDING_RESPONSE_SHA256: Final = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)
PENDING_RESPONSE_BYTE_COUNT: Final = 2
PENDING_HTTP_STATUS: Final = 200
PENDING_CONTENT_TYPE: Final = "application/json"
PREFIX_LEDGER_SHA256: Final = (
    "817a6bac5d54f3518d0785401e7c622f25708a7f7c5cac79faf317de28c8ebbb"
)
PREFIX_RAW_SIDECAR_MANIFEST_SHA256: Final = (
    "e853a4eb181b84e1ea4b34288f08246bb362a9429ab8a54f5fd875f9dabd97b1"
)

AUTHORIZATION_CHAIN_CONTRACT: Final = (
    "quant_data.stage10_history_extension_authorization_chain"
)
AUTHORIZATION_CHAIN_VERSION: Final = "1.0.0"
AUTHORIZATION_PROOF_CONTRACT: Final = (
    "quant_data.stage10_history_extension_authorization_proof"
)
AUTHORIZATION_PROOF_VERSION: Final = "1.0.0"
_SHA256_LENGTH: Final = 64
_SHA256_CHARS: Final = frozenset("0123456789abcdef")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()

def _plain(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    raise ValueError("Stage 10 history-extension authorization proof is invalid")


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and set(value).issubset(_SHA256_CHARS)
    )


def stage10_history_extension_execution_revision() -> str:
    """Return the current exact execution-source revision."""

    from ..market.stage10_scope import load_stage10_market_scope
    from ..registry import (
        CANONICAL_REGISTRY_PATH,
        load_registry,
        stage10_registry_profile,
    )

    project_root = Path(__file__).resolve().parents[2]
    relative_paths = tuple(
        sorted(
            str(path.relative_to(project_root))
            for path in (project_root / "quant_data").rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".sql"}
        )
    ) + (
        "config/stage10_market_scope.json",
        "config/system_registry.json",
        "scripts/run_stage10_history_extension_live.py",
    )
    sources: list[dict[str, str]] = []
    for relative in relative_paths:
        path = project_root / relative
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_size > 16 * 1024 * 1024
            ):
                raise OSError("invalid source")
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(
                "Stage 10 history-extension execution source binding is unavailable"
            ) from exc
        sources.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    cohort_registry = load_registry(
        CANONICAL_REGISTRY_PATH, project_root=project_root, environment={}
    )
    registry = stage10_registry_profile(cohort_registry)
    scope = load_stage10_market_scope(
        project_root / "config" / "stage10_market_scope.json"
    )
    return _sha256_json(
        {
            "contract": "quant_data.stage10_history_extension_execution_source",
            "sources": sources,
            "stage10_registry_source_sha256": registry.source_sha256,
            "cohort_registry_source_sha256": cohort_registry.source_sha256,
            "scope_manifest_sha256": scope.manifest_sha256,
        }
    )


def _reference(value: object, *, expected_sha256: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("Stage 10 history-extension authorization receipt is invalid")
    if (
        value.get("contract") != TRANSITION_CONTRACT
        or value.get("transition_sha256") != expected_sha256
    ):
        raise ValueError("Stage 10 history-extension authorization receipt is invalid")
    return {
        "contract": TRANSITION_CONTRACT,
        "transition_sha256": expected_sha256,
    }


def build_stage10_history_extension_authorization_chain(
    prior_402_transition: Mapping[str, object],
    known_listed_empty_transition: Mapping[str, object],
) -> dict[str, object]:
    """Return the canonical compact chain after receipt-file verification.

    Callers must provide receipts that were independently re-read and
    validated from immutable files.  This helper retains only their immutable
    digests so private candidate evidence never copies provider body bytes.
    """

    material = {
        "contract": AUTHORIZATION_CHAIN_CONTRACT,
        "known_listed_empty_transition": _reference(
            known_listed_empty_transition,
            expected_sha256=KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
        ),
        "prior_402_transition": _reference(
            prior_402_transition,
            expected_sha256=PRIOR_402_TRANSITION_SHA256,
        ),
        "version": AUTHORIZATION_CHAIN_VERSION,
    }
    return {**material, "sha256": _sha256_json(material)}


def validate_stage10_history_extension_authorization_chain(
    value: object,
    *,
    required: bool,
) -> dict[str, object] | None:
    """Validate the exact two-receipt chain required by operator outcomes."""

    if value is None:
        if required:
            raise ValueError("Stage 10 history-extension authorization chain is missing")
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Stage 10 history-extension authorization chain is invalid")
    required_keys = {
        "contract",
        "known_listed_empty_transition",
        "prior_402_transition",
        "sha256",
        "version",
    }
    if set(value) != required_keys:
        raise ValueError("Stage 10 history-extension authorization chain is invalid")
    material = {
        "contract": value.get("contract"),
        "known_listed_empty_transition": value.get("known_listed_empty_transition"),
        "prior_402_transition": value.get("prior_402_transition"),
        "version": value.get("version"),
    }
    if (
        material["contract"] != AUTHORIZATION_CHAIN_CONTRACT
        or material["version"] != AUTHORIZATION_CHAIN_VERSION
        or not isinstance(value.get("sha256"), str)
        or len(value["sha256"]) != _SHA256_LENGTH
        or value["sha256"] != _sha256_json(material)
    ):
        raise ValueError("Stage 10 history-extension authorization chain is invalid")
    expected = build_stage10_history_extension_authorization_chain(
        material["prior_402_transition"],  # type: ignore[arg-type]
        material["known_listed_empty_transition"],  # type: ignore[arg-type]
    )
    if dict(value) != expected:
        raise ValueError("Stage 10 history-extension authorization chain is invalid")
    return expected

def validate_stage10_history_extension_successor_transition(
    value: object,
) -> dict[str, object]:
    """Validate the complete sanitized successor receipt without filesystem I/O."""

    if not isinstance(value, Mapping):
        raise ValueError("Stage 10 history-extension successor receipt is invalid")
    plain = _plain(value)
    if not isinstance(plain, dict):
        raise ValueError("Stage 10 history-extension successor receipt is invalid")
    required = {
        "authorization",
        "authorization_chain",
        "closed_request_count",
        "contract",
        "live_manifest_sha256",
        "new_execution_revision",
        "old_execution_revision",
        "pending",
        "plan_sha256",
        "prefix_ledger_sha256",
        "prefix_raw_sidecar_manifest_sha256",
        "response",
        "terminal_reason",
        "transition_sha256",
        "version",
    }
    try:
        chain = validate_stage10_history_extension_authorization_chain(
            plain.get("authorization_chain"),
            required=True,
        )
    except ValueError as exc:
        raise ValueError(
            "Stage 10 history-extension successor receipt is invalid"
        ) from exc
    expected_pending = {
        "from": PENDING_FROM,
        "intent_id": PENDING_INTENT_ID,
        "intent_sha256": PENDING_INTENT_SHA256,
        "ordinal": PENDING_ORDINAL,
        "symbol": PENDING_SYMBOL,
        "to": PENDING_TO,
        "window_id": PENDING_WINDOW_ID,
    }
    expected_response = {
        "content_type": PENDING_CONTENT_TYPE,
        "http_status": PENDING_HTTP_STATUS,
        "response_byte_count": PENDING_RESPONSE_BYTE_COUNT,
        "response_sha256": PENDING_RESPONSE_SHA256,
    }
    if (
        set(plain) != required
        or plain.get("authorization") != TRANSITION_AUTHORIZATION
        or chain is None
        or plain.get("authorization_chain") != chain
        or plain.get("closed_request_count") != CLOSED_REQUEST_COUNT
        or plain.get("contract") != TRANSITION_CONTRACT
        or plain.get("live_manifest_sha256") != LIVE_MANIFEST_SHA256
        or not _sha256(plain.get("new_execution_revision"))
        or plain.get("new_execution_revision")
        != stage10_history_extension_execution_revision()
        or plain.get("old_execution_revision") != OLD_EXECUTION_REVISION
        or plain.get("pending") != expected_pending
        or plain.get("plan_sha256") != PLAN_SHA256
        or plain.get("prefix_ledger_sha256") != PREFIX_LEDGER_SHA256
        or plain.get("prefix_raw_sidecar_manifest_sha256")
        != PREFIX_RAW_SIDECAR_MANIFEST_SHA256
        or plain.get("response") != expected_response
        or plain.get("terminal_reason")
        != "operator_authorized_known_listed_empty"
        or plain.get("version") != TRANSITION_VERSION
        or not _sha256(plain.get("transition_sha256"))
    ):
        raise ValueError("Stage 10 history-extension successor receipt is invalid")
    material = {
        key: item for key, item in plain.items() if key != "transition_sha256"
    }
    if plain["transition_sha256"] != _sha256_json(material):
        raise ValueError("Stage 10 history-extension successor receipt is invalid")
    return plain


def build_stage10_history_extension_authorization_proof(
    successor_transition: Mapping[str, object],
) -> dict[str, object]:
    """Build evidence only after validating the complete successor receipt."""

    successor = validate_stage10_history_extension_successor_transition(
        successor_transition
    )
    material: dict[str, object] = {
        "contract": AUTHORIZATION_PROOF_CONTRACT,
        "successor_transition": successor,
        "version": AUTHORIZATION_PROOF_VERSION,
    }
    return {**material, "sha256": _sha256_json(material)}


def validate_stage10_history_extension_authorization_proof(
    value: object,
    *,
    required: bool,
) -> dict[str, object] | None:
    """Validate receipt-derived authorization at every evidence boundary."""

    if value is None:
        if required:
            raise ValueError("Stage 10 history-extension authorization proof is missing")
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Stage 10 history-extension authorization proof is invalid")
    plain = _plain(value)
    if not isinstance(plain, dict) or set(plain) != {
        "contract",
        "sha256",
        "successor_transition",
        "version",
    }:
        raise ValueError("Stage 10 history-extension authorization proof is invalid")
    material = {
        "contract": plain.get("contract"),
        "successor_transition": plain.get("successor_transition"),
        "version": plain.get("version"),
    }
    if (
        material["contract"] != AUTHORIZATION_PROOF_CONTRACT
        or material["version"] != AUTHORIZATION_PROOF_VERSION
        or not _sha256(plain.get("sha256"))
        or plain["sha256"] != _sha256_json(material)
    ):
        raise ValueError("Stage 10 history-extension authorization proof is invalid")
    expected = build_stage10_history_extension_authorization_proof(
        plain["successor_transition"],  # type: ignore[arg-type]
    )
    if plain != expected:
        raise ValueError("Stage 10 history-extension authorization proof is invalid")
    return expected



__all__ = (
    "AUTHORIZATION_CHAIN_CONTRACT",
    "AUTHORIZATION_CHAIN_VERSION",
    "AUTHORIZATION_PROOF_CONTRACT",
    "AUTHORIZATION_PROOF_VERSION",
    "CLOSED_REQUEST_COUNT",
    "KNOWN_LISTED_EMPTY_TRANSITION_SHA256",
    "LIVE_EXECUTION_REVISION",
    "LIVE_MANIFEST_SHA256",
    "OLD_EXECUTION_REVISION",
    "PENDING_CONTENT_TYPE",
    "PENDING_FROM",
    "PENDING_HTTP_STATUS",
    "PENDING_INTENT_ID",
    "PENDING_INTENT_SHA256",
    "PENDING_ORDINAL",
    "PENDING_RESPONSE_BYTE_COUNT",
    "PENDING_RESPONSE_SHA256",
    "PENDING_SYMBOL",
    "PENDING_TO",
    "PENDING_WINDOW_ID",
    "PLAN_SHA256",
    "PREFIX_LEDGER_SHA256",
    "PREFIX_RAW_SIDECAR_MANIFEST_SHA256",
    "PRIOR_402_TRANSITION_SHA256",
    "TRANSITION_AUTHORIZATION",
    "TRANSITION_CONTRACT",
    "TRANSITION_FILENAME",
    "TRANSITION_VERSION",
    "build_stage10_history_extension_authorization_chain",
    "build_stage10_history_extension_authorization_proof",
    "stage10_history_extension_execution_revision",
    "validate_stage10_history_extension_authorization_chain",
    "validate_stage10_history_extension_authorization_proof",
    "validate_stage10_history_extension_successor_transition",
)
