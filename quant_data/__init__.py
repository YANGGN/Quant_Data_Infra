"""Offline-first Quant Data Infrastructure rebuild.

Only the accepted Stage 1 fixture slice is implemented.  Importing this
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

__version__ = "0.1.0"
