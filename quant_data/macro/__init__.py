"""Offline macro fixture ingestion and read-only query contracts through Stage 3."""

from .repository import MacroSeriesQuery, MacroSeriesRepository
from .rtdsm_fixture import MacroFixtureImporter
from .stage3_fixture_importers import MacroStage3FixtureImporter
from .stage3_repository import (
    MacroStage3RecessionQuery,
    MacroStage3Repository,
    MacroStage3SeriesQuery,
)

__all__ = (
    "MacroFixtureImporter",
    "MacroSeriesQuery",
    "MacroSeriesRepository",
    "MacroStage3FixtureImporter",
    "MacroStage3RecessionQuery",
    "MacroStage3Repository",
    "MacroStage3SeriesQuery",
)
