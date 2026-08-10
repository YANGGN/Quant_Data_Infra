"""Offline-first Quant Data Infrastructure rebuild.

The accepted offline Stage 1 fixture slice, Stage 2 four-store foundation, and
bounded Stage 3 market/macro fixture implementation are present. Importing this
package performs no filesystem, database, network, or environment work.
"""

from .contracts import ExecutionContext, Observation, TimeSeries
from .errors import QuantDataError, ValidationError
from .stores import StoreMap, StoreRole

__all__ = [
    "ExecutionContext",
    "Observation",
    "QuantDataError",
    "StoreMap",
    "StoreRole",
    "TimeSeries",
    "ValidationError",
]

__version__ = "0.3.0"
