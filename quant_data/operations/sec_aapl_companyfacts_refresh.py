"""Manual-only, bounded SEC AAPL submissions and CompanyFacts refresh.

This operation owns the live-provider boundary for the first company-data
slice.  Its scope is deliberately fixed: one AAPL CIK, one SEC submissions
request, and one SEC CompanyFacts request.  Both bounded responses are
validated in memory before the company-domain parser/publisher is invoked, so
no provider work can occur while the company writer holds its physical lock.

The SEC requires a declared User-Agent.  The two components are read by name
from the established credential resolver and are used only for the request
headers; they are never persisted or emitted in receipts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import stat
import sys
from time import monotonic
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, StoreRole, resolve_store_map


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
COMPANY_STORE: Final = PROJECT_ROOT / "data" / "company.sqlite"
AAPL_CIK: Final = "0000320193"
SEC_SUBMISSIONS_URL: Final = (
    "https://data.sec.gov/submissions/CIK0000320193.json"
)
SEC_COMPANYFACTS_URL: Final = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
)
REQUEST_CAP: Final = 2
TIMEOUT_SECONDS: Final = 60
MAX_RUN_SECONDS: Final = 120
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_JSON_MEDIA_TYPE: Final = "application/json"
_VERSION: Final = "1.0.0"
_SENSITIVE_KEY_PARTS: Final = frozenset(
    {
        "agent",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "email",
        "header",
        "password",
        "secret",
        "token",
        "user_agent",
        "username",
    }
)
_MAX_SAFE_STRING: Final = 512
_MAX_SAFE_ITEMS: Final = 64
_MAX_SAFE_DEPTH: Final = 4


@dataclass(frozen=True, slots=True)
class SecCompanyFactsTransportResponse:
    """One direct SEC response held only until it is parsed and published."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


class SecCompanyFactsTransport(Protocol):
    """Injectable one-request transport with no retry semantics."""

    def request(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> SecCompanyFactsTransportResponse: ...


class StdlibSecCompanyFactsTransport:
    """One bounded HTTPS GET that follows no redirects."""

    def request(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> SecCompanyFactsTransportResponse:
        if (
            not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(headers, Mapping)
            or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in headers.items()
            )
        ):
            raise ValidationError("SEC AAPL provider request is invalid")
        _validate_sec_url(url)
        parsed = urlsplit(url)
        try:
            connection = http.client.HTTPSConnection(
                parsed.netloc, timeout=timeout_seconds
            )
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("SEC AAPL provider transport failed") from exc
        try:
            connection.request("GET", parsed.path, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError(
                        "SEC AAPL provider length is invalid"
                    ) from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError(
                        "SEC AAPL provider response exceeds its byte bound"
                    )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError(
                    "SEC AAPL provider response exceeds its byte bound"
                )
            status = int(response.status)
            return SecCompanyFactsTransportResponse(
                status=status,
                media_type=response.getheader("Content-Type") or "",
                body=body,
                redirected=(
                    300 <= status < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("SEC AAPL provider transport failed") from exc
        finally:
            connection.close()


ParseAndPublish = Callable[..., object]
CredentialReader = Callable[..., str]


@dataclass(frozen=True, slots=True)
class SecAaplCompanyFactsReport:
    """Small credential-free result for the fixed two-request operation."""

    requested: int
    response_bytes: int
    receipt: Mapping[str, Any]

    def mapping(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "response_bytes": self.response_bytes,
            "receipt": dict(self.receipt),
        }


def _validate_sec_url(value: object) -> str:
    if value not in {SEC_SUBMISSIONS_URL, SEC_COMPANYFACTS_URL}:
        raise ValidationError("SEC AAPL provider URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "data.sec.gov"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError("SEC AAPL provider URL is invalid")
    return value


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("SEC AAPL source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("SEC AAPL source media type is invalid")
    return result


def _validate_response(
    response: object, *, max_bytes: int
) -> SecCompanyFactsTransportResponse:
    """Require a bounded direct JSON response before any parser can run."""

    if (
        not isinstance(max_bytes, int)
        or max_bytes <= 0
        or max_bytes > MAX_RESPONSE_BYTES
    ):
        raise ValidationError("SEC AAPL response bound is invalid")
    if not isinstance(response, SecCompanyFactsTransportResponse):
        raise StoreUnavailableError("SEC AAPL transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("SEC AAPL provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("SEC AAPL provider response is outside policy")
    if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("SEC AAPL provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("SEC AAPL provider response body is invalid")
    if len(response.body) > max_bytes:
        raise ResourceLimitError("SEC AAPL provider response exceeds its byte bound")
    try:
        json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("SEC AAPL provider JSON is invalid") from exc
    return response


def _credential_component(value: object, *, kind: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 254
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ValidationError("SEC User-Agent credential is missing or invalid")
    if kind == "email":
        local, separator, domain = value.partition("@")
        if (
            separator != "@"
            or not local
            or not domain
            or "@" in domain
            or "." not in domain
        ):
            raise ValidationError("SEC User-Agent credential is missing or invalid")
    elif kind != "name":  # pragma: no cover - internal invariant
        raise ValidationError("SEC User-Agent credential is missing or invalid")
    return value


def _utcnow(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("SEC AAPL capture time must include an offset")
    return value.astimezone(timezone.utc)


def _safe_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise ValidationError("SEC AAPL publication receipt is invalid")
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValidationError("SEC AAPL publication receipt is invalid")
    if value.casefold() in _SENSITIVE_KEY_PARTS:
        raise ValidationError("SEC AAPL publication receipt is invalid")
    return value


def _safe_value(
    value: object, *, forbidden_text: tuple[str, ...], depth: int = 0
) -> Any:
    if depth > _MAX_SAFE_DEPTH:
        raise ValidationError("SEC AAPL publication receipt is invalid")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("SEC AAPL publication receipt is invalid")
        return value
    if isinstance(value, str):
        if (
            len(value) > _MAX_SAFE_STRING
            or "\x00" in value
            or any(secret and secret in value for secret in forbidden_text)
        ):
            raise ValidationError("SEC AAPL publication receipt is invalid")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("SEC AAPL publication receipt is invalid")
        return {
            _safe_key(key): _safe_value(
                item, forbidden_text=forbidden_text, depth=depth + 1
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("SEC AAPL publication receipt is invalid")
        return [
            _safe_value(item, forbidden_text=forbidden_text, depth=depth + 1)
            for item in value
        ]
    raise ValidationError("SEC AAPL publication receipt is invalid")


def _receipt_mapping(
    value: object, *, forbidden_text: tuple[str, ...]
) -> dict[str, Any]:
    mapping_method = getattr(value, "to_primitive", None)
    candidate = mapping_method() if callable(mapping_method) else value
    if not isinstance(candidate, Mapping):
        raise ValidationError("SEC AAPL publication receipt is invalid")
    safe = _safe_value(candidate, forbidden_text=forbidden_text)
    if not isinstance(safe, dict):  # pragma: no cover - defensive narrowing
        raise ValidationError("SEC AAPL publication receipt is invalid")
    return safe


def _require_bound_target(
    *,
    project_root: Path,
    company_store: Path,
    stores: StoreMap,
    canonical: bool,
) -> None:
    """Keep this operation bound to its one company-store target."""

    try:
        root = project_root.resolve(strict=True)
        target = company_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = company_store.lstat()
        mapped = stores.path(StoreRole.COMPANY).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("SEC AAPL company target is unavailable") from exc
    if (
        root != project_root
        or target != company_store
        or mapped != target
        or project_root.is_symlink()
        or company_store.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISREG(target_info.st_mode)
        or target_info.st_nlink != 1
        or company_store != project_root / "data" / "company.sqlite"
    ):
        raise ValidationError("SEC AAPL company target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or company_store != COMPANY_STORE:
            raise ValidationError("SEC AAPL canonical target binding is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("SEC AAPL fixtures must use a temporary root") from exc


class SecAaplCompanyFactsRunner:
    """Fetch the fixed SEC pair before one parser/publisher call."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        company_store: str | Path,
        stores: StoreMap,
        registry: object,
        parse_and_publish: ParseAndPublish,
        transport: SecCompanyFactsTransport,
        credential_environment: Mapping[str, str],
        credential_reader: CredentialReader = read_project_credential,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_clock: Callable[[], float] = monotonic,
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._company_store = Path(company_store)
        self._stores = stores
        self._registry = registry
        self._parse_and_publish = parse_and_publish
        self._transport = transport
        self._credential_environment = credential_environment
        self._credential_reader = credential_reader
        self._utcnow = utcnow
        self._monotonic = monotonic_clock
        if not isinstance(self._stores, StoreMap):
            raise ValidationError("SEC AAPL runner dependencies are invalid")
        _require_bound_target(
            project_root=self._project_root,
            company_store=self._company_store,
            stores=self._stores,
            canonical=_canonical,
        )
        if (
            self._registry is None
            or not callable(self._parse_and_publish)
            or not callable(getattr(self._transport, "request", None))
            or not isinstance(self._credential_environment, Mapping)
            or not callable(self._credential_reader)
            or not callable(self._utcnow)
            or not callable(self._monotonic)
        ):
            raise ValidationError("SEC AAPL runner dependencies are invalid")

    def _read_user_agent(self) -> tuple[str, tuple[str, ...]]:
        try:
            name = self._credential_reader(
                project_root=self._project_root,
                name="SEC_USER_AGENT_NAME",
                environment=self._credential_environment,
            )
            email = self._credential_reader(
                project_root=self._project_root,
                name="SEC_USER_AGENT_EMAIL",
                environment=self._credential_environment,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("SEC User-Agent credential is missing or invalid") from exc
        safe_name = _credential_component(name, kind="name")
        safe_email = _credential_component(email, kind="email")
        user_agent = f"{safe_name} {safe_email}"
        if len(user_agent) > 512:
            raise ValidationError("SEC User-Agent credential is missing or invalid")
        return user_agent, (safe_name, safe_email, user_agent)

    def _request(
        self, *, url: str, user_agent: str, max_bytes: int, deadline: float
    ) -> SecCompanyFactsTransportResponse:
        timeout_seconds = min(TIMEOUT_SECONDS, int(deadline - self._monotonic()))
        if timeout_seconds < 1:
            raise ResourceLimitError("SEC AAPL capture exceeded 120 seconds")
        response = self._transport.request(
            url=url,
            headers={"Accept": _JSON_MEDIA_TYPE, "User-Agent": user_agent},
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        validated = _validate_response(response, max_bytes=max_bytes)
        if self._monotonic() > deadline:
            raise ResourceLimitError("SEC AAPL capture exceeded 120 seconds")
        return validated

    def run(self) -> SecAaplCompanyFactsReport:
        """Issue the two fixed requests once, then publish their parsed bundle."""

        deadline = self._monotonic() + MAX_RUN_SECONDS
        user_agent, forbidden_text = self._read_user_agent()
        submissions = self._request(
            url=SEC_SUBMISSIONS_URL,
            user_agent=user_agent,
            max_bytes=MAX_TOTAL_RESPONSE_BYTES,
            deadline=deadline,
        )
        remaining = MAX_TOTAL_RESPONSE_BYTES - len(submissions.body)
        if remaining <= 0:
            raise ResourceLimitError("SEC AAPL responses exceed their byte bound")
        companyfacts = self._request(
            url=SEC_COMPANYFACTS_URL,
            user_agent=user_agent,
            max_bytes=remaining,
            deadline=deadline,
        )
        captured_at = _utcnow(self._utcnow())
        # This is intentionally the first point at which the company publisher
        # can acquire its physical write lock: both SEC requests are complete.
        receipt = self._parse_and_publish(
            stores=self._stores,
            registry=self._registry,
            submissions_body=submissions.body,
            companyfacts_body=companyfacts.body,
            captured_at=captured_at,
            deadline=deadline,
        )
        return SecAaplCompanyFactsReport(
            requested=REQUEST_CAP,
            response_bytes=len(submissions.body) + len(companyfacts.body),
            receipt=_receipt_mapping(receipt, forbidden_text=forbidden_text),
        )


def _canonical_dependencies() -> tuple[object, StoreMap]:
    """Load only the reviewed registry and fixed canonical store map."""

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    stores = resolve_store_map(
        registry,
        project_root=PROJECT_ROOT,
        environment={},
    )
    if stores.path(StoreRole.COMPANY) != COMPANY_STORE.resolve(strict=False):
        raise ValidationError("SEC AAPL canonical company target is invalid")
    return registry, stores


def _domain_api() -> ParseAndPublish:
    """Bind the reviewed live SEC parser/publisher only at execution time."""

    from ..company.sec_companyfacts import run_sec_aapl_companyfacts

    return run_sec_aapl_companyfacts


def populate_sec_aapl_companyfacts_live() -> SecAaplCompanyFactsReport:
    """Run the fixed manual AAPL SEC collection once, with no retry loop."""

    registry, stores = _canonical_dependencies()
    return SecAaplCompanyFactsRunner(
        project_root=PROJECT_ROOT,
        company_store=COMPANY_STORE,
        stores=stores,
        registry=registry,
        parse_and_publish=_domain_api(),
        transport=StdlibSecCompanyFactsTransport(),
        credential_environment=os.environ,
        _canonical=True,
    ).run()


class _ArgumentFailure(Exception):
    """Sanitized rejection for any caller-selected operation scope."""


def _run() -> dict[str, Any]:
    return populate_sec_aapl_companyfacts_live().mapping()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the one fixed collection and emit a compact credential-free receipt."""

    try:
        if argv is not None and tuple(argv):
            raise _ArgumentFailure
        report = _run()
        safe_report = _safe_value(report, forbidden_text=())
        if not isinstance(safe_report, dict):  # pragma: no cover - narrowing
            raise ValidationError("SEC AAPL publication receipt is invalid")
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except ResourceLimitError:
        code, name = 74, "local_io"
    except (RegistryError, ConflictError):
        code, name = 75, "temporary_conflict"
    except Exception:
        code, name = 70, "internal_failure"
    else:
        sys.stdout.write(
            dumps_strict(
                {
                    "contract": "quant_data.sec_aapl_companyfacts_receipt",
                    "receipt": safe_report,
                    "version": _VERSION,
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.sec_aapl_companyfacts_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))


__all__ = (
    "AAPL_CIK",
    "COMPANY_STORE",
    "MAX_RESPONSE_BYTES",
    "MAX_TOTAL_RESPONSE_BYTES",
    "PROJECT_ROOT",
    "REQUEST_CAP",
    "SEC_COMPANYFACTS_URL",
    "SEC_SUBMISSIONS_URL",
    "SecAaplCompanyFactsReport",
    "SecAaplCompanyFactsRunner",
    "SecCompanyFactsTransport",
    "SecCompanyFactsTransportResponse",
    "StdlibSecCompanyFactsTransport",
    "populate_sec_aapl_companyfacts_live",
)
