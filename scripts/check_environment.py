#!/usr/bin/env python3
"""Check a fixed development profile using package metadata only; never install."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
# Exclusive upper bounds identify the interpreters used for these baselines.
PROFILES = {
    "core": ((3, 11), (3, 13)),
    "calendar": ((3, 11), (3, 12)),
    "theta": ((3, 12), (3, 13)),
    "options-monitor": ((3, 12), (3, 13)),
}
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.+!-]*)")


def read_pins(path: Path, *, stack=()) -> dict[str, str]:
    """Read the repository's exact pins, including relative -r files."""
    path = path.resolve()
    if path in stack:
        raise ValueError("Cyclic requirements include")
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            child = line[3:].strip()
            if Path(child).name != child:
                raise ValueError("Requirements includes must be sibling filenames")
            entries = read_pins(path.parent / child, stack=(*stack, path))
        else:
            match = PIN.fullmatch(line)
            if not match:
                raise ValueError("Requirements must use exact version pins")
            entries = {re.sub(r"[-_.]+", "-", match[1]).lower(): match[2]}
        for name, version in entries.items():
            if name in pins and pins[name] != version:
                raise ValueError("Conflicting dependency pins: " + name)
            pins[name] = version
    return pins


def check(profile: str, *, root=ROOT, python=None, lookup=metadata.version):
    version = tuple(python or sys.version_info[:3])
    lower, upper = PROFILES[profile]
    python_ok = lower <= version[:2] < upper
    dependencies = []
    for name, expected in sorted(read_pins(Path(root) / "requirements" / (profile + ".txt")).items()):
        try:
            installed = lookup(name)
        except metadata.PackageNotFoundError:
            installed = None
        dependencies.append(dict(distribution=name, expected=expected, installed=installed,
            status="ok" if installed == expected else "missing" if installed is None else "version_mismatch"))
    return dict(profile=profile, python=".".join(map(str, version)), python_ok=python_ok,
        python_min=".".join(map(str, lower)), python_before=".".join(map(str, upper)),
        dependencies=dependencies, ok=python_ok and all(row["status"] == "ok" for row in dependencies))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="core")
    args = parser.parse_args(argv)
    try:
        report = check(args.profile)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(profile=args.profile, ok=False, error=str(error)), sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
