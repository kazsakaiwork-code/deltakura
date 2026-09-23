#!/usr/bin/env python3
"""NTA 法人番号 全件データ - the baseline the diffs are applied to, and the
anonymisation backstop for Bet A.

Bet C is the mechanism that decides whether a Bet-A winner is a 法人 or a
masked individual. Bet C may be joined to Bet A on `corporate_number` and on
nothing else. This script builds exactly that join table.

The nationwide Unicode dump is ~255 MB zipped. It is streamed to disk, read
once, and reduced to a lookup of `corporate_number -> 都道府県 / 市区町村 /
法人種別`. By default the lookup is filtered to the corporate numbers that
actually appear in a normalized store, which turns a 5.8M-row registry into a
file measured in tens of kilobytes.

    # build the lookup Bet A needs, keeping the raw dump
    python crawlers/nta_diff/zenken.py --needed-from data/pportal/normalized

    # build it, then delete the 255 MB raw dump
    python crawlers/nta_diff/zenken.py --needed-from data/pportal/normalized --discard-raw

    # the whole registry (large: tens of MB gzipped)
    python crawlers/nta_diff/zenken.py --all
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import io
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import anonymize, http, paths, tos  # noqa: E402

import collect  # noqa: E402

TOS_ROW = "法人番号公表サイト 全件データ"
LIST_URL = "https://www.houjin-bangou.nta.go.jp/download/zenken/index.html"
LOOKUP_COLUMNS = [
    "corporate_number", "prefecture_code", "prefecture", "city",
    "kind_code", "close_date", "source_id", "retrieved_at",
]

_NATIONWIDE_RE = re.compile(
    r'<th scope="row">全国</th>.*?doDownload\((\d+)\);.*?>([^<]*)</a>', re.S
)


def _find_nationwide(html: str) -> tuple[str, str, str]:
    """Return (token_name, token_value, file_no) for the Unicode CSV 全国 file."""
    token = collect._TOKEN_RE.search(html)
    if not token:
        raise RuntimeError("CSRF token not found on the 全件 page")
    start = html.find('id="csv-unicode"')
    end = html.find('id="xml-unicode"', start)
    if start < 0:
        raise RuntimeError("CSV/Unicode section not found on the 全件 page")
    m = _NATIONWIDE_RE.search(html[start : end if end > 0 else len(html)])
    if not m:
        raise RuntimeError("nationwide 全件 download link not found")
    print(f"[nta_zenken] file     : 全国 CSV/Unicode, {m.group(2).strip()}")
    return token.group(1), token.group(2), m.group(1)


def _needed_numbers(sources: Iterable[str]) -> Set[str]:
    needed: Set[str] = set()
    for source in sources:
        target = Path(source)
        files = (
            sorted(target.glob("*.csv.gz")) if target.is_dir()
            else [Path(p) for p in glob.glob(source)]
        )
        for path in files:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", newline="") as fh:  # type: ignore[operator]
                for row in csv.DictReader(fh):
                    value = row.get("corporate_number") or ""
                    if value:
                        needed.add(value)
    return needed


def run(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="NTA 法人番号 全件 -> Bet-A join table")
    ap.add_argument("--needed-from", nargs="*", default=None,
                    help="normalized store(s) whose corporate_number column "
                         "defines which registry rows to keep")
    ap.add_argument("--all", action="store_true",
                    help="keep every corporation (large output)")
    ap.add_argument("--discard-raw", action="store_true",
                    help="delete the ~255 MB dump once the lookup is built")
    ap.add_argument("--reuse-raw", action="store_true",
                    help="skip the download and reuse the dump already on disk")
    ap.add_argument("--daily-cap", type=int, default=10)
    args = ap.parse_args(argv)

    if not args.all and not args.needed_from:
        ap.error("pass --needed-from <store> or --all")

    clearance = tos.clear(TOS_ROW)
    tos.require_publishable(clearance)

    raw_path = paths.data_dir("nta", "raw", "zenken") / "houjin_zenken_unicode.zip"

    if not args.reuse_raw or not raw_path.exists():
        session = http.PoliteSession(
            state_path=paths.state_dir("nta") / "http_state.json",
            daily_cap=args.daily_cap,
        )
        print(f"[nta_zenken] source   : {clearance.source}")
        print(f"[nta_zenken] robots   : {session.robots_verdict(LIST_URL)}")
        page = session.get(LIST_URL, accept="text/html", conditional=False)
        token_name, token_value, file_no = _find_nationwide(page.text())
        print("[nta_zenken] download : streaming to disk, this takes a few minutes")
        resp = session.post_to_file(
            LIST_URL,
            {token_name: token_value, "event": "download", "selDlFileNo": file_no},
            raw_path,
            accept="application/octet-stream",
        )
        session.save_state()
        print(f"[nta_zenken] stored   : {getattr(resp, 'streamed_bytes', 0):,} bytes")
        retrieved_at = resp.retrieved_at
    else:
        print(f"[nta_zenken] reusing  : {raw_path.name} "
              f"({raw_path.stat().st_size:,} bytes)")
        retrieved_at = http.utc_now_iso()

    needed = set() if args.all else _needed_numbers(args.needed_from or [])
    if not args.all:
        print(f"[nta_zenken] filter   : {len(needed):,} corporate number(s) wanted")

    out_dir = paths.data_dir("nta", "lookup")
    out_path = out_dir / "corp_location.csv.gz"
    kept = scanned = 0

    with zipfile.ZipFile(raw_path) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(member) as binary, gzip.open(
            out_path, "wt", encoding="utf-8", newline="", compresslevel=9
        ) as out:
            writer = csv.DictWriter(out, fieldnames=LOOKUP_COLUMNS, lineterminator="\n")
            writer.writeheader()
            reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
            for cells in reader:
                scanned += 1
                if len(cells) < 24:
                    continue
                number = cells[1].strip()
                if not args.all and number not in needed:
                    continue
                if not anonymize.is_valid_corporate_number(number):
                    continue
                writer.writerow(
                    {
                        "corporate_number": number,
                        "prefecture_code": cells[13].strip(),
                        "prefecture": cells[9].strip(),
                        "city": cells[10].strip(),
                        "kind_code": cells[8].strip(),
                        "close_date": cells[18].strip(),
                        "source_id": "nta_zenken",
                        "retrieved_at": retrieved_at,
                    }
                )
                kept += 1
                if scanned % 1_000_000 == 0:
                    print(f"[nta_zenken] scanned  : {scanned:,} rows, kept {kept:,}")

    print(f"[nta_zenken] lookup   : {kept:,} row(s) from {scanned:,} registry rows "
          f"-> {paths.rel(out_path)} "
          f"({out_path.stat().st_size:,} bytes)")

    if args.discard_raw:
        size = raw_path.stat().st_size
        raw_path.unlink()
        print(f"[nta_zenken] discarded: raw dump removed ({size:,} bytes freed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
