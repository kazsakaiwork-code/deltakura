#!/usr/bin/env python3
"""Nightly wrapper for the Bet-C collector.

Runs `collect.run()` and appends exactly one line to `data/nta/run_log.csv`,
whatever happens - a crash, a network outage and a quiet no-op all leave a row,
because a silently missing night is the one failure mode this product cannot
recover from.

This is the entry point the scheduler calls. It takes no arguments and needs no
working directory: every path is derived from this file's own location.

    python crawlers/nta_diff/run_nightly.py

Exit code is 0 on success and 1 on failure, so a scheduler can alert on it.
"""

from __future__ import annotations

import csv
import io
import contextlib
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import paths  # noqa: E402

import collect  # noqa: E402  (same directory)

JST = timezone(timedelta(hours=9))

RUN_LOG_COLUMNS = [
    "run_started_jst",
    "run_finished_jst",
    "duration_sec",
    "status",
    "files_fetched",
    "records",
    "window_missing",
    "manifest_total",
    "note",
]


def _append_run_log(row: dict) -> Path:
    path = paths.DATA / "nta" / "run_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUN_LOG_COLUMNS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in RUN_LOG_COLUMNS})
    return path


def main() -> int:
    started = datetime.now(JST)
    captured = io.StringIO()
    status = "ok"
    note = ""
    rc = 0

    try:
        with contextlib.redirect_stdout(captured):
            rc = collect.run([])
        if rc != 0:
            status = "error"
            note = f"collector returned {rc}"
    except Exception as exc:  # noqa: BLE001 - a nightly job never dies silently
        status = "error"
        note = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        rc = 1

    output = captured.getvalue()
    print(output, end="")

    # Pull the counters out of the collector's own summary line.
    files_fetched = records = window_missing = ""
    for line in output.splitlines():
        if "done     :" in line:
            parts = line.split("done     :", 1)[1].split(",")
            try:
                files_fetched = parts[0].strip().split()[0]
                records = parts[1].strip().split()[0]
                window_missing = parts[2].strip().split()[0]
            except IndexError:
                pass

    manifest_total = ""
    try:
        manifest_total = str(len(collect.load_manifest(paths.DATA / "nta" / "manifest.csv")))
    except OSError:
        pass

    finished = datetime.now(JST)
    log_path = _append_run_log(
        {
            "run_started_jst": started.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_finished_jst": finished.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_sec": f"{(finished - started).total_seconds():.1f}",
            "status": status,
            "files_fetched": files_fetched,
            "records": records,
            "window_missing": window_missing,
            "manifest_total": manifest_total,
            "note": note,
        }
    )
    print(f"[nta_diff] run log  : {paths.rel(log_path)} ({status})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
