"""Explicit command-line entry point for deterministic offline stage gates."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .errors import QuantDataError
from .json_codec import dumps_strict
from .stage1 import (
    compare_clean_rebuilds as compare_clean_stage1_rebuilds,
    run_clean_rebuild as run_clean_stage1_rebuild,
)
from .stage2 import compare_clean_stage2_rebuilds, run_clean_stage2_rebuild
from .stage3 import compare_clean_stage3_rebuilds, run_clean_stage3_rebuild
from .stage4 import compare_clean_stage4_rebuilds, run_clean_stage4_rebuild
from .stage5 import compare_clean_stage5_rebuilds, run_clean_stage5_rebuild
from .stage6 import compare_clean_stage6_rebuilds, run_clean_stage6_rebuild


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m quant_data")
    parser.add_argument(
        "--stage",
        required=True,
        choices=("stage1", "stage2", "stage3", "stage4", "stage5", "stage6"),
        help="Offline acceptance gate to execute",
    )
    parser.add_argument(
        "--project-root",
        required=True,
        help="Explicit project root containing the registry and reviewed fixtures",
    )
    parser.add_argument(
        "--store-root",
        required=True,
        help="Explicit empty store root (Stage 1) or work root (Stages 2 through 6)",
    )
    parser.add_argument(
        "--second-store-root",
        help="Optional second empty root; require equal path-free rebuild evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.stage == "stage1" and arguments.second_store_root:
            evidence = compare_clean_stage1_rebuilds(
                project_root=arguments.project_root,
                first_store_root=arguments.store_root,
                second_store_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage1":
            evidence = run_clean_stage1_rebuild(
                project_root=arguments.project_root,
                store_root=arguments.store_root,
            )
        elif arguments.stage == "stage2" and arguments.second_store_root:
            evidence = compare_clean_stage2_rebuilds(
                project_root=arguments.project_root,
                first_work_root=arguments.store_root,
                second_work_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage2":
            evidence = run_clean_stage2_rebuild(
                project_root=arguments.project_root,
                work_root=arguments.store_root,
            )
        elif arguments.stage == "stage3" and arguments.second_store_root:
            evidence = compare_clean_stage3_rebuilds(
                project_root=arguments.project_root,
                first_work_root=arguments.store_root,
                second_work_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage3":
            evidence = run_clean_stage3_rebuild(
                project_root=arguments.project_root,
                work_root=arguments.store_root,
            )
        elif arguments.stage == "stage4" and arguments.second_store_root:
            evidence = compare_clean_stage4_rebuilds(
                project_root=arguments.project_root,
                first_work_root=arguments.store_root,
                second_work_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage4":
            evidence = run_clean_stage4_rebuild(
                project_root=arguments.project_root,
                work_root=arguments.store_root,
            )
        elif arguments.stage == "stage5" and arguments.second_store_root:
            evidence = compare_clean_stage5_rebuilds(
                project_root=arguments.project_root,
                first_work_root=arguments.store_root,
                second_work_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage5":
            evidence = run_clean_stage5_rebuild(
                project_root=arguments.project_root,
                work_root=arguments.store_root,
            )
        elif arguments.stage == "stage6" and arguments.second_store_root:
            evidence = compare_clean_stage6_rebuilds(
                project_root=arguments.project_root,
                first_work_root=arguments.store_root,
                second_work_root=arguments.second_store_root,
            )
        elif arguments.stage == "stage6":
            evidence = run_clean_stage6_rebuild(
                project_root=arguments.project_root,
                work_root=arguments.store_root,
            )
        else:  # pragma: no cover - argparse enforces the closed stage inventory
            raise AssertionError("Unsupported stage selection")
    except QuantDataError as exc:
        print(dumps_strict({"error": exc.to_dict()}), file=sys.stderr)
        return 2
    print(dumps_strict(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
