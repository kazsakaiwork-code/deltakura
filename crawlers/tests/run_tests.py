#!/usr/bin/env python3
"""Runnable test suite for the Deltakura collectors. No pytest required.

    python crawlers/tests/run_tests.py
    python crawlers/tests/run_tests.py -v
    python crawlers/tests/run_tests.py anonymize        # only matching modules

Every test is a module-level function whose name starts with `test_`. Nothing
here touches the network; the HTTP layer is exercised against a stub session and
the parsers against fixtures. Exit code 0 means every rule this project states
in prose is also enforced in code.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import traceback
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:  # keep Japanese assertion messages readable on a cp932 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)                 # type: ignore[union-attr]
    return module


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run the crawler test suite")
    ap.add_argument("pattern", nargs="?", default="",
                    help="only run test modules whose name contains this")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    files = sorted(p for p in HERE.glob("test_*.py") if args.pattern in p.stem)
    if not files:
        print(f"no test modules match {args.pattern!r}")
        return 1

    passed = 0
    failures: List[Tuple[str, str]] = []
    started = time.monotonic()

    for path in files:
        module = load(path)
        names = sorted(n for n in dir(module) if n.startswith("test_"))
        print(f"\n{path.stem}  ({len(names)} test(s))")
        for name in names:
            fn = getattr(module, name)
            if not callable(fn):
                continue
            try:
                fn()
            except Exception:  # noqa: BLE001 - a test runner reports, it does not raise
                failures.append((f"{path.stem}.{name}", traceback.format_exc()))
                print(f"  FAIL  {name}")
            else:
                passed += 1
                if args.verbose:
                    print(f"  ok    {name}")

    elapsed = time.monotonic() - started
    print(f"\n{'-' * 62}")
    if failures:
        for label, tb in failures:
            print(f"\nFAILED {label}\n{tb}")
        print(f"{passed} passed, {len(failures)} FAILED in {elapsed:.1f}s")
        return 1
    print(f"{passed} passed, 0 failed in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
