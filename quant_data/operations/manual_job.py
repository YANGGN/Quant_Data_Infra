"""Restricted manual wrapper for offline Stage 7 fixture rehearsals."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from ..errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..fixtures import FixtureManifest
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stage1 import explicit_store_map
from ..stores import StoreMap
from .fixture_executor import Stage7FixtureStepExecutor
from .health import inspect_all_stores
from .job_receipts import PrivateJobState, validate_marker_period
from .job_retry import JobStepError
from .job_runner import Stage7JobRunner, build_dry_run_plan


class _ArgumentFailure(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure("invalid manual job arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python3 -m quant_data.operations.manual_job",
        description="Run one disabled offline Stage 7 fixture rehearsal",
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--store-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--marker-period")
    return parser


def _explicit_root(value: object, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be an explicit absolute directory")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ValidationError(f"{field_name} must be an explicit absolute directory")
    resolved = candidate.resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=False):
        raise ValidationError(f"{field_name} is too broad")
    return resolved


def _write_json(stream: TextIO, value: object) -> None:
    stream.write(dumps_strict(value))
    stream.write("\n")
    stream.flush()


def _error_payload(code: str, exit_code: int) -> dict[str, object]:
    return {
        "contract": "quant_data.stage7_manual_error",
        "contract_version": "1.0.0",
        "error": {
            "code": code,
            "message": "manual offline job request failed",
        },
        "exit_code": exit_code,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    registry_loader: Callable[..., Registry] = load_registry,
    fixture_manifest_loader: Callable[..., FixtureManifest] = FixtureManifest.load,
    store_map_factory: Callable[[str | Path], StoreMap] = explicit_store_map,
    state_factory: Callable[..., PrivateJobState] = PrivateJobState,
    executor_factory: Callable[..., Stage7FixtureStepExecutor] = Stage7FixtureStepExecutor,
    runner_factory: Callable[..., Stage7JobRunner] = Stage7JobRunner,
    health_check: Callable[[StoreMap, Registry], object] = inspect_all_stores,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Execute one registered fixture-only job and preserve its aggregate exit."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        project_root = _explicit_root(arguments.project_root, "project_root")
        store_root = _explicit_root(arguments.store_root, "store_root")
        state_root = _explicit_root(arguments.state_root, "state_root")
        registry = registry_loader(
            project_root / CANONICAL_REGISTRY_PATH,
            project_root=project_root,
            environment={},
        )
        try:
            job = registry.job(arguments.job)
        except RegistryError as exc:
            raise ValidationError("Unknown manual job request") from exc
        marker_period = arguments.marker_period
        marker_policy = str(job.success_marker)
        if marker_period is not None:
            marker_period = validate_marker_period(marker_period)
            if marker_policy != "monthly_full_success_only":
                raise ValidationError(
                    "marker period is only valid for a monthly success-marker job"
                )

        store_map = store_map_factory(store_root)
        if arguments.dry_run:
            plan = build_dry_run_plan(registry, job.id, store_map)
            _write_json(output, plan.to_primitive())
            return 0

        health_check(store_map, registry)
        manifest = fixture_manifest_loader(
            project_root / "tests" / "fixtures" / "manifest.json",
            project_root=project_root,
        )
        private_state = state_factory(state_root)
        executor = executor_factory(store_map, manifest)
        runner = runner_factory(
            registry,
            store_map,
            private_state,
            executor,
            monotonic=monotonic,
            sleeper=sleeper,
        )
        receipt = runner.run(job.id, marker_period=marker_period)
        exit_code = receipt.aggregate_exit_code
        _write_json(
            output,
            {
                "contract": "quant_data.stage7_manual_result",
                "contract_version": "1.0.0",
                "job_id": receipt.job_id,
                "semantic_outcome": receipt.semantic_outcome,
                "aggregate_exit_code": exit_code,
                "receipt": receipt.to_primitive(),
            },
        )
        return exit_code
    except _ArgumentFailure:
        exit_code, code = 64, "invalid_arguments"
    except JobStepError as exc:
        exit_code, code = exc.exit_code, exc.code
    except ValidationError:
        exit_code, code = 64, "invalid_request"
    except StoreUnavailableError:
        exit_code, code = 69, "store_unavailable"
    except OSError:
        exit_code, code = 74, "local_io"
    except ConflictError:
        exit_code, code = 75, "temporary_conflict"
    except (RegistryError, MigrationError, ResourceLimitError):
        exit_code, code = 70, "internal_contract"
    except Exception:
        exit_code, code = 70, "internal_failure"
    _write_json(errors, _error_payload(code, exit_code))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
