"""Frozen evidence for the reviewed Stage 10 HTTP 402 response transition.

The provider mislabeled a plain-text entitlement response as JSON after the
first 614 dated-history requests had closed.  The transition receipt binds
that exact first response and pending intent.  Once authorized, only the same
status/content-type/byte-count/body-digest fingerprint may classify later
scoped windows as entitlement-unavailable; every different 402 still stops.
"""

from typing import Final


TRANSITION_CONTRACT: Final = (
    "quant_data.stage10_history_extension_execution_transition"
)
TRANSITION_VERSION: Final = "1.0.0"
TRANSITION_FILENAME: Final = "execution-transition-402.json"
TRANSITION_AUTHORIZATION: Final = "user_authorized_skip_failed_tickers_and_continue"
TRANSITION_TERMINAL_REASON: Final = "operator_authorized_entitlement_unavailable"

OLD_EXECUTION_REVISION: Final = (
    "a5fc2a6ff5d5eb8f5ddffffe09e5a7fb9a527ff323fe5725d4cee9a8bea36feb"
)
PLAN_SHA256: Final = (
    "364ae14f62e9a43621f6f2153dd9194780f403b142dbdfd349637a9dd4952a2d"
)
CLOSED_REQUEST_COUNT: Final = 614
PENDING_ORDINAL: Final = 615
PENDING_SYMBOL: Final = "^AXJO"
PENDING_WINDOW_ID: Final = "1990-1994"
PENDING_FROM: Final = "1990-01-01"
PENDING_TO: Final = "1994-12-31"
PENDING_INTENT_SHA256: Final = (
    "aef5b50ba98a6a2cb01812b02cfe76f6cf7b2e730b8382c60e716ec9dca4e9cf"
)
RESPONSE_SHA256: Final = (
    "38e6a6ea2ed189c5d4cab610c93eefc962b31fffdae06dd65390b90d7c0cff7c"
)
RESPONSE_BYTE_COUNT: Final = 215
HTTP_STATUS: Final = 402
CONTENT_TYPE: Final = "application/json"


__all__ = (
    "CLOSED_REQUEST_COUNT",
    "CONTENT_TYPE",
    "HTTP_STATUS",
    "OLD_EXECUTION_REVISION",
    "PENDING_FROM",
    "PENDING_INTENT_SHA256",
    "PENDING_ORDINAL",
    "PENDING_SYMBOL",
    "PENDING_TO",
    "PENDING_WINDOW_ID",
    "PLAN_SHA256",
    "RESPONSE_BYTE_COUNT",
    "RESPONSE_SHA256",
    "TRANSITION_AUTHORIZATION",
    "TRANSITION_CONTRACT",
    "TRANSITION_FILENAME",
    "TRANSITION_TERMINAL_REASON",
    "TRANSITION_VERSION",
)
