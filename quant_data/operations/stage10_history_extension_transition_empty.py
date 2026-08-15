"""Frozen evidence for the reviewed known-listed empty-response transition.

The Stage 10 history extension stopped after the first 4,603 durable outcomes
because EUV's final dated window returned an exact HTTP 200 JSON empty array
despite immutable evidence that the instrument was already listed inside that
window.  This constants-only module binds the prior transition, exact pending
intent, and exact retained response.  Once authorized, the same empty-response
fingerprint may be classified as a failed window only when current/base facts
independently prove the requested ticker was listed by that window's end.
"""

from typing import Final


TRANSITION_CONTRACT: Final = (
    "quant_data.stage10_history_extension_execution_transition"
)
TRANSITION_VERSION: Final = "1.0.0"
TRANSITION_FILENAME: Final = "execution-transition-known-listed-empty.json"
TRANSITION_AUTHORIZATION: Final = "user_authorized_skip_failed_tickers_and_continue"
TRANSITION_TERMINAL_REASON: Final = "operator_authorized_known_listed_empty"

OLD_EXECUTION_REVISION: Final = (
    "039813d054b6d0df45a8945da1e1c1def49dd9546649bace41ef712dee862d4e"
)
PLAN_SHA256: Final = (
    "364ae14f62e9a43621f6f2153dd9194780f403b142dbdfd349637a9dd4952a2d"
)
PRIOR_TRANSITION_SHA256: Final = (
    "726125269749572560f46bef6816565205fd321332d76e82029e518b1de8bf4c"
)
CLOSED_REQUEST_COUNT: Final = 4_603
PENDING_ORDINAL: Final = 4_604
PENDING_SYMBOL: Final = "EUV"
PENDING_WINDOW_ID: Final = "2025-2026"
PENDING_FROM: Final = "2025-01-01"
PENDING_TO: Final = "2026-08-12"
PENDING_INTENT_SHA256: Final = (
    "f072c7076eb5812fb7fa2c8fad8ac5757e3893c402cf02461b5a70b06b81d34c"
)
RESPONSE_SHA256: Final = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)
RESPONSE_BYTE_COUNT: Final = 2
HTTP_STATUS: Final = 200
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
    "PRIOR_TRANSITION_SHA256",
    "RESPONSE_BYTE_COUNT",
    "RESPONSE_SHA256",
    "TRANSITION_AUTHORIZATION",
    "TRANSITION_CONTRACT",
    "TRANSITION_FILENAME",
    "TRANSITION_TERMINAL_REASON",
    "TRANSITION_VERSION",
)
