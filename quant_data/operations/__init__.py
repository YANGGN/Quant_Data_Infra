"""Offline health, backup/restore, and manual Stage 7 operations."""

from importlib import import_module

from .fixture_executor import (
    FixtureStepBinding,
    STAGE7_FIXTURE_STEP_BINDINGS,
    Stage7FixtureStepExecutor,
)
from .job_receipts import (
    JobRunReceipt,
    LockWaitReceipt,
    PrivateJobState,
    StepRunReceipt,
)
from .job_retry import JobStepError, RetryPolicy, retry_policy_for
from .job_runner import (
    DryRunPlan,
    PreparedStep,
    Stage7JobRunner,
    StepPublication,
    build_dry_run_plan,
)


_LAZY_EXPORTS = {
    "DEFAULT_TIMEOUT_SECONDS": (".backup", "DEFAULT_TIMEOUT_SECONDS"),
    "BackupCohort": (".backup", "BackupCohort"),
    "RestoreCohort": (".backup", "RestoreCohort"),
    "StoreCopyReceipt": (".backup", "StoreCopyReceipt"),
    "backup_all": (".backup", "backup_all"),
    "restore_all": (".backup", "restore_all"),
    "DatasetHealth": (".health", "DatasetHealth"),
    "HealthReport": (".health", "HealthReport"),
    "MigrationHealth": (".health", "MigrationHealth"),
    "StoreInspection": (".health", "StoreInspection"),
    "all_store_health": (".health", "all_store_health"),
    "health_all": (".health", "health_all"),
    "inspect_all": (".health", "inspect_all"),
    "inspect_all_stores": (".health", "inspect_all_stores"),
    "inspect_store": (".health", "inspect_store"),
}


def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "BackupCohort",
    "DryRunPlan",
    "FixtureStepBinding",
    "JobRunReceipt",
    "JobStepError",
    "LockWaitReceipt",
    "PreparedStep",
    "PrivateJobState",
    "RetryPolicy",
    "STAGE7_FIXTURE_STEP_BINDINGS",
    "Stage7FixtureStepExecutor",
    "Stage7JobRunner",
    "StepPublication",
    "StepRunReceipt",
    "DatasetHealth",
    "HealthReport",
    "MigrationHealth",
    "RestoreCohort",
    "StoreCopyReceipt",
    "StoreInspection",
    "all_store_health",
    "backup_all",
    "health_all",
    "inspect_all",
    "inspect_all_stores",
    "inspect_store",
    "restore_all",
    "build_dry_run_plan",
    "retry_policy_for",
]
