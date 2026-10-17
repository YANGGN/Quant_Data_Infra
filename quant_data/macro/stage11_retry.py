"""Narrow transient-request classification for the manual Stage 11 runner.

This module intentionally carries no provider response, credential, path, or
retry state.  Capture primitives convert only the reviewed transient transport
signals into :class:`Stage11TransientRequestError`; the manual runner owns the
fixed retry loop and its clock/sleeper behavior.
"""

from __future__ import annotations

import http.client
from typing import Final

from ..errors import StoreUnavailableError


STAGE11_MAX_REQUEST_ATTEMPTS: Final[int] = 3
STAGE11_RETRY_BACKOFF_SECONDS: Final[tuple[float, float]] = (1.0, 2.0)

_TRANSIENT_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"connection", "timeout", "http_429", "http_5xx"}
)
_SAFE_MESSAGES: Final[dict[str, str]] = {
    "connection": "Stage 11 provider connection was temporarily unavailable",
    "timeout": "Stage 11 provider request timed out",
    "http_429": "Stage 11 provider temporarily rate limited the request",
    "http_5xx": "Stage 11 provider was temporarily unavailable",
}


class Stage11TransientRequestError(StoreUnavailableError):
    """A credential/body/path-free transient Stage 11 capture failure.

    It remains a ``StoreUnavailableError`` so an exhausted retry retains the
    existing manual-runner unavailable exit behavior.  The category is frozen
    to a small reviewable vocabulary and never includes provider material.
    """

    __slots__ = ("category",)

    def __init__(self, category: str) -> None:
        if category not in _TRANSIENT_CATEGORIES:
            raise ValueError("Stage 11 transient category is unsupported")
        super().__init__(_SAFE_MESSAGES[category])
        self.category = category


def transient_error_from_transport_exception(
    error: BaseException,
) -> Stage11TransientRequestError:
    """Classify only the approved connection/timeout exception families.

    The original exception is intentionally not attached to the returned
    value.  Callers should raise the result with ``from None`` so a provider
    message cannot enter a receipt or rendered error path.
    """

    if not isinstance(error, (OSError, http.client.HTTPException)):
        raise TypeError("Stage 11 transient transport error is unsupported")
    return Stage11TransientRequestError(
        "timeout" if isinstance(error, TimeoutError) else "connection"
    )


def transient_error_for_http_status(status: object) -> Stage11TransientRequestError | None:
    """Return an error only for the exact retryable HTTP status classes."""

    if isinstance(status, bool) or not isinstance(status, int):
        return None
    if status == 429:
        return Stage11TransientRequestError("http_429")
    if 500 <= status <= 599:
        return Stage11TransientRequestError("http_5xx")
    return None


def raise_for_transient_http_status(status: object) -> None:
    """Raise before response-body or MIME handling for retryable statuses."""

    error = transient_error_for_http_status(status)
    if error is not None:
        raise error


__all__ = (
    "STAGE11_MAX_REQUEST_ATTEMPTS",
    "STAGE11_RETRY_BACKOFF_SECONDS",
    "Stage11TransientRequestError",
    "raise_for_transient_http_status",
    "transient_error_for_http_status",
    "transient_error_from_transport_exception",
)
