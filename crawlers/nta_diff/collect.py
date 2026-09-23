#!/usr/bin/env python3
"""Bet C - 国税庁 法人番号公表サイト 差分データ nightly collector.

Why this exists: the publisher keeps only the past 40 days of daily diff files.
A night that is not collected is gone, and cannot be bought back. One request a
night, ~130 KB, builds an asset nobody else holds.

What it does, idempotently:

  1. GET the listing page once, read the CSRF token and the 40-day file table.
  2. For every file date not already in the manifest, POST the download form
     once (>= 2 s apart), and store the ZIP verbatim under
     data/nta/raw/<file_date>/.
  3. Parse the CSV inside into the Bet-C normalized schema and append it to
     a monthly normalized CSV under data/nta/normalized/.
  4. Update data/nta/manifest.csv.

Already-fetched dates are skipped without a request, so a re-run on the same
night costs exactly one HTTP request (the listing page).

**What "already fetched" means.** Two records are consulted:
the private manifest in the collection store, and the committed
`data/published/nta/manifest.csv`. The second matters because the store is
ephemeral on a CI runner: without it every nightly run would re-download the
whole 40-day window (~41 requests, ~5.5 MB) instead of the one new file. A
normal night therefore costs two requests - the listing page and one download.
`--backfill` ignores the committed record and re-fetches everything this
machine does not physically hold, which is how a local archive is rebuilt.

Usage:
    python crawlers/nta_diff/collect.py                # nightly: fetch what's new
    python crawlers/nta_diff/collect.py --backfill     # re-fetch whatever is not held locally
    python crawlers/nta_diff/collect.py --dry-run      # list what would be fetched
    python crawlers/nta_diff/collect.py --max-files 5  # cap this run
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import anonymize, http, jputil, paths, tos  # noqa: E402

SOURCE_ID = "nta_diff"
TOS_ROW = "法人番号公表サイト 差分データ"
LIST_URL = "https://www.houjin-bangou.nta.go.jp/download/sabun/index.html"
FILE_TYPE_CSV_UNICODE = "csv-unicode"
JST = timezone(timedelta(hours=9))

# The Bet-C field order, plus the shared fields every record carries.
NORMALIZED_COLUMNS = [
    "corporate_number",
    "process_code",
    "correct_flag",
    "update_date",
    "change_date",
    "sequence_number",
    "name",
    "name_image_id",
    "kind_code",
    "prefecture",
    "city",
    "street_number",
    "prefecture_code",
    "city_code",
    "post_code",
    "close_date",
    "close_cause",
    "successor_corporate_number",
    "change_cause",
    "assignment_date",
    "latest_flag",
    "furigana",
    "hidden_flag",
    "corporate_number_valid",
    "record_key",
    "file_date",
    "source_id",
    "source_url",
    "license",
    "attribution",
    "retrieved_at",
    "raw_hash",
]

# 30-column NTA layout. Index -> our field name; None means "not carried".
# Columns 17/18 (国外所在地 / イメージID) and 25-28 (English fields) are
# dropped: they add nothing to the diff product and the English address fields
# are the ones most likely to carry a transliterated personal name.
NTA_LAYOUT: List[Tuple[int, Optional[str]]] = [
    (0, "sequence_number"),
    (1, "corporate_number"),
    (2, "process_code"),
    (3, "correct_flag"),
    (4, "update_date"),
    (5, "change_date"),
    (6, "name"),
    (7, "name_image_id"),
    (8, "kind_code"),
    (9, "prefecture"),
    (10, "city"),
    (11, "street_number"),
    (12, None),   # 国内所在地イメージID
    (13, "prefecture_code"),
    (14, "city_code"),
    (15, "post_code"),
    (16, None),   # 国外所在地
    (17, None),   # 国外所在地イメージID
    (18, "close_date"),
    (19, "close_cause"),
    (20, "successor_corporate_number"),
    (21, "change_cause"),
    (22, "assignment_date"),
    (23, "latest_flag"),
    (24, None),   # 商号又は名称(英語表記)
    (25, None),   # 国内所在地(都道府県)(英語表記)
    (26, None),   # 国内所在地(市区町村丁目番地等)(英語表記)
    (27, None),   # 国外所在地(英語表記)
    (28, "furigana"),
    (29, "hidden_flag"),
]

MANIFEST_COLUMNS = [
    "file_date",
    "file_no",
    "file_name",
    "raw_path",
    "raw_bytes",
    "raw_sha256",
    "signature_stored",
    "records",
    "normalized_path",
    "retrieved_at",
    "status",
]


# --------------------------------------------------------------- page parse

_TOKEN_RE = re.compile(r'name="(jp\.go\.nta\.[^"]+)"\s+value="([^"]+)"')
_ROW_RE = re.compile(
    r'<tr[^>]*class="type(\d)_corpHistory\d+"[^>]*>\s*'
    r'<th[^>]*>\s*(.*?)\s*</th>\s*'
    r'<td[^>]*>\s*<a[^>]*onclick="return doDownload\((\d+)\);"[^>]*>(.*?)</a>',
    re.S,
)


def parse_listing(html: str) -> Tuple[str, str, List[Dict[str, str]]]:
    """Return (token_name, token_value, [{date, file_no, label}, ...])."""
    m = _TOKEN_RE.search(html)
    if not m:
        raise RuntimeError(
            "CSRF token not found on the NTA listing page; the page layout "
            "changed. Stop and re-verify before automating again."
        )
    token_name, token_value = m.group(1), m.group(2)

    # Restrict to the CSV/Unicode section so we never mix file types.
    start = html.find(f'id="{FILE_TYPE_CSV_UNICODE}"')
    if start < 0:
        raise RuntimeError("CSV/Unicode section not found on the NTA listing page")
    end = html.find('<h2 class="title" id="xml-unicode">', start)
    section = html[start : end if end > 0 else len(html)]

    files: List[Dict[str, str]] = []
    for row in _ROW_RE.finditer(section):
        wareki = re.sub(r"<[^>]+>", "", row.group(2)).strip()
        iso = jputil.wareki_to_iso(wareki)
        if not iso:
            continue
        files.append(
            {
                "date": iso,
                "file_no": row.group(3),
                "label": re.sub(r"<[^>]+>", "", row.group(4)).strip(),
            }
        )
    if not files:
        raise RuntimeError("no diff files parsed from the NTA listing page")
    files.sort(key=lambda f: f["date"], reverse=True)
    return token_name, token_value, files


# ----------------------------------------------------------------- manifest

def load_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["file_date"]: r for r in csv.DictReader(fh) if r.get("status") == "ok"}


def load_published_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    """Days recorded as collected in the COMMITTED manifest.

    `data/published/nta/manifest.csv` carries file id, date, sha256, byte size
    and row counts - no record content. It is the only memory a fresh CI runner
    has of what the project already holds, so it is what keeps the nightly job
    at one download instead of forty.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["file_date"]: r for r in csv.DictReader(fh) if r.get("file_date")}


def write_manifest(path: Path, rows: Dict[str, Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow({c: rows[key].get(c, "") for c in MANIFEST_COLUMNS})


# ------------------------------------------------------------------- parse

def parse_diff_csv(raw: bytes, file_date: str, clearance: tos.Clearance,
                   retrieved_at: str, source_url: str) -> List[Dict[str, str]]:
    text = raw.decode("utf-8-sig", errors="strict")
    reader = csv.reader(io.StringIO(text, newline=""))
    out: List[Dict[str, str]] = []
    for cells in reader:
        if not cells or len(cells) < 24:
            continue
        rec: Dict[str, str] = {}
        for idx, field_name in NTA_LAYOUT:
            if field_name is None or idx >= len(cells):
                continue
            rec[field_name] = cells[idx].strip()

        # R3 belt-and-braces: if a future layout introduces a personal field
        # name, refuse the row rather than write it.
        for key in rec:
            if anonymize.is_personal_field(key):
                raise RuntimeError(
                    f"{file_date}: field {key!r} matches the personal-field "
                    "pattern; refusing to normalize (rule R3)"
                )

        number = rec.get("corporate_number", "")
        rec["corporate_number_valid"] = (
            "Y" if anonymize.is_valid_corporate_number(number) else "N"
        )
        # The Bet-C record key
        rec["record_key"] = "|".join(
            (number, rec.get("change_date", ""), rec.get("sequence_number", ""))
        )
        rec["file_date"] = file_date
        rec["source_id"] = SOURCE_ID
        rec["source_url"] = source_url
        rec["license"] = clearance.license
        rec["attribution"] = clearance.attribution
        rec["retrieved_at"] = retrieved_at
        rec["raw_hash"] = http.sha256_hex("|".join(cells).encode("utf-8"))
        out.append({c: rec.get(c, "") for c in NORMALIZED_COLUMNS})
    return out


def append_normalized(records: List[Dict[str, str]], file_date: str) -> Path:
    """Append to a gzipped monthly CSV; highest sequence_number wins on a
    re-run (the Bet-C dedup rule)."""
    month = file_date[:7]
    out_dir = paths.data_dir("nta", "normalized")
    out_path = out_dir / f"nta_diff_{month}.csv.gz"

    existing: Dict[str, Dict[str, str]] = {}
    if out_path.exists():
        with gzip.open(out_path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                existing[row["record_key"]] = row

    for rec in records:
        key = rec["record_key"]
        prior = existing.get(key)
        if prior is None:
            existing[key] = rec
            continue
        # corrections supersede; otherwise the highest sequence_number wins
        if rec.get("correct_flag") == "1" and prior.get("correct_flag") != "1":
            existing[key] = rec
        elif _as_int(rec.get("sequence_number")) > _as_int(prior.get("sequence_number")):
            existing[key] = rec

    with gzip.open(out_path, "wt", encoding="utf-8", newline="", compresslevel=9) as fh:
        writer = csv.DictWriter(fh, fieldnames=NORMALIZED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(existing):
            writer.writerow(existing[key])
    return out_path


def _as_int(value: Optional[str]) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return -1


# -------------------------------------------------------------------- run

def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NTA 法人番号 差分 collector (Bet C)")
    ap.add_argument("--backfill", action="store_true",
                    help="ignore the committed manifest and fetch every windowed "
                         "file this machine does not physically hold")
    ap.add_argument("--max-files", type=int, default=None,
                    help="stop after this many downloads")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be fetched and exit")
    ap.add_argument("--daily-cap", type=int, default=60,
                    help="per-host request budget for this UTC day (default 60)")
    args = ap.parse_args(argv)

    clearance = tos.clear(TOS_ROW)           # fail-closed; raises if not Y
    tos.require_publishable(clearance)

    raw_root = paths.data_dir("nta", "raw")
    manifest_path = paths.DATA / "nta" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    published_manifest = load_published_manifest(
        paths.PUBLISHED / "nta" / "manifest.csv"
    )
    # Days we already hold: physically in this store, plus - unless the caller
    # asked for a backfill - the days the committed manifest records as
    # collected. A fresh runner has an empty store and a full published
    # manifest, which is exactly the case this exists for.
    held = dict(manifest)
    if not args.backfill:
        for day, row in published_manifest.items():
            held.setdefault(day, row)

    session = http.PoliteSession(
        state_path=paths.state_dir("nta") / "http_state.json",
        daily_cap=args.daily_cap,
    )

    print(f"[{SOURCE_ID}] source   : {clearance.source}")
    print(f"[{SOURCE_ID}] licence  : {clearance.license}")
    print(f"[{SOURCE_ID}] robots   : {session.robots_verdict(LIST_URL)}")
    print(f"[{SOURCE_ID}] manifest : {len(manifest)} file(s) in this store, "
          f"{len(published_manifest)} in the committed manifest, "
          f"{len(held)} treated as already held"
          f"{' (--backfill: committed manifest ignored)' if args.backfill else ''}")

    try:
        page = session.get(LIST_URL, accept="text/html", conditional=False)
    except http.NotModified:
        print(f"[{SOURCE_ID}] listing unchanged; nothing to do")
        session.save_state()
        return 0

    token_name, token_value, files = parse_listing(page.text())
    print(f"[{SOURCE_ID}] window   : {len(files)} file(s), "
          f"{files[-1]['date']} .. {files[0]['date']}")

    # Every file in the window that we do not already hold. A gap is
    # unrecoverable once it leaves the 40-day window, so the run repairs the
    # whole window rather than only the newest night: if collection was down for
    # a week, the next run catches up in one pass. On a normal night there is
    # exactly one such file. The per-host daily cap is the real brake.
    pending = [f for f in files if f["date"] not in held]
    if args.max_files:
        pending = pending[: args.max_files]
    pending.sort(key=lambda f: f["date"])

    print(f"[{SOURCE_ID}] pending  : {len(pending)} file(s) to fetch")
    if args.dry_run:
        for f in pending:
            print(f"    would fetch {f['date']}  fileNo={f['file_no']}  {f['label']}")
        session.save_state()
        return 0

    fetched = 0
    total_records = 0
    for entry in pending:
        file_date = entry["date"]
        try:
            resp = session.post(
                LIST_URL,
                data={
                    token_name: token_value,
                    "event": "download",
                    "selDlFileNo": entry["file_no"],
                },
                accept="application/octet-stream",
            )
        except (http.DailyCapReached, http.HostPaused) as exc:
            print(f"[{SOURCE_ID}] stopping: {exc}")
            break
        except RuntimeError as exc:
            print(f"[{SOURCE_ID}] {file_date}: FAILED {exc}")
            manifest[file_date] = {
                "file_date": file_date, "file_no": entry["file_no"],
                "status": f"error: {exc}", "retrieved_at": http.utc_now_iso(),
            }
            continue

        if not resp.body.startswith(b"PK"):
            print(f"[{SOURCE_ID}] {file_date}: response is not a ZIP "
                  f"({resp.headers.get('content-type')}); skipped")
            continue

        day_dir = raw_root / file_date
        day_dir.mkdir(parents=True, exist_ok=True)
        raw_path = day_dir / f"diff_{file_date.replace('-', '')}.zip"
        raw_path.write_bytes(resp.body)   # already a compressed container

        with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
            names = zf.namelist()
            csv_name = next((n for n in names if n.lower().endswith(".csv")), None)
            sig_name = next((n for n in names if n.lower().endswith(".asc")), None)
            if not csv_name:
                print(f"[{SOURCE_ID}] {file_date}: no CSV inside the ZIP; skipped")
                continue
            payload = zf.read(csv_name)

        records = parse_diff_csv(
            payload, file_date, clearance, resp.retrieved_at, LIST_URL
        )
        normalized_path = append_normalized(records, file_date)

        manifest[file_date] = {
            "file_date": file_date,
            "file_no": entry["file_no"],
            "file_name": csv_name,
            "raw_path": paths.rel(raw_path),
            "raw_bytes": str(len(resp.body)),
            "raw_sha256": resp.sha256,
            "signature_stored": "Y" if sig_name else "N",
            "records": str(len(records)),
            "normalized_path": paths.rel(normalized_path),
            "retrieved_at": resp.retrieved_at,
            "status": "ok",
        }
        fetched += 1
        total_records += len(records)
        print(f"[{SOURCE_ID}] {file_date}: {len(records):>5} records, "
              f"{len(resp.body):>7} bytes raw")

    write_manifest(manifest_path, manifest)
    session.save_state()

    held.update(manifest)
    missing = [f["date"] for f in files if f["date"] not in held]
    print(f"[{SOURCE_ID}] done     : {fetched} file(s) fetched, "
          f"{total_records} records, {len(missing)} still missing in the window")
    if missing and not args.dry_run:
        print(f"[{SOURCE_ID}] missing  : {', '.join(missing[:10])}"
              f"{' ...' if len(missing) > 10 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
