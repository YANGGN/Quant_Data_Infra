"""Explicit command-line entry point for the offline Stage 1 harness."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .errors import QuantDataError
from .json_codec import dumps_strict
from .stage1 import compare_clean_rebuilds, run_clean_rebuild


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m quant_data")
    parser.add_argument(
        "--project-root",
        required=True,
        help="Explicit project root containing the registry and reviewed fixtures",
    )
    parser.add_argument(
        "--store-root",
        required=True,
        help="Explicit empty root for four temporary Stage 1 SQLite stores",
    )
    parser.add_argument(
        "--second-store-root",
        help="Optional second empty root; when supplied, require equal clean rebuild evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.second_store_root:
            evidence = compare_clean_rebuilds(
                project_root=arguments.project_root,
                first_store_root=arguments.store_root,
                second_store_root=arguments.second_store_root,
            )
        else:
            evidence = run_clean_rebuild(
                project_root=arguments.project_root,
                store_root=arguments.store_root,
            )
    except QuantDataError as exc:
        print(dumps_strict({"error": exc.to_dict()}), file=sys.stderr)
        return 2
    print(dumps_strict(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
