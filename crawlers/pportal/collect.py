#!/usr/bin/env python3
"""Bet A - 調達ポータル 落札実績オープンデータ collector.

National procurement award records, FY2013 onward, published by デジタル庁
under 政府標準利用規約(第2.0版) (CC BY 4.0 compatible). One request per file,
>= 2 s apart, conditional on ETag so a re-run of an unchanged year costs one
304.

Anonymisation is applied before anything is written: a winner without a
checksum-valid 法人番号 is masked (R2), whatever the name looks like, and the
title passes the R4 person-name detector. Raw ZIPs stay under data/pportal/raw/
which is git-ignored - they are not the product and must not reach a public
repository.

Usage:
    python crawlers/pportal/collect.py                      # all fiscal years, newest first
    python crawlers/pportal/collect.py --years 2026 2025    # only these
    python crawlers/pportal/collect.py --include-diff       # also the daily diff window
    python crawlers/pportal/collect.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import anonymize, http, jputil, paths, tos  # noqa: E402

import codes  # noqa: E402  (same directory)

SOURCE_ID = "pportal_awards"
TOS_ROW = "調達ポータル 落札実績オープンデータ"
LIST_URL = "https://www.p-portal.go.jp/pps-web-biz/UAB02/OAB0201"

# The Bet-A field order, then the shared fields.
NORMALIZED_COLUMNS = [
    "award_id",
    "procurement_item_no",
    "publisher_name",
    "publisher_code",
    "sector",
    "prefecture",
    "municipality",
    "method",
    "method_detail",
    "category_code",
    "title",
    "title_masked",
    "publish_date",
    "award_date",
    "fiscal_year",
    "amount_jpy",
    "predicted_price_jpy",
    "award_ratio",
    "bidder_count",
    "winner_type",
    "winner_name",
    "winner_masked",
    "corporate_number",
    "anonymise_flags",
    "source_id",
    "source_url",
    "license",
    "attribution",
    "retrieved_at",
    "raw_hash",
]

MANIFEST_COLUMNS = [
    "file_key", "kind", "file_name", "raw_path", "raw_bytes", "raw_sha256",
    "etag", "records", "normalized_path", "retrieved_at", "status",
]

_URL_VAR_RE = re.compile(r'var\s+uab02FileDownloadUrl\s*=\s*"([^"]+)"')
_ALL_RE = re.compile(r"doDownload\('(successful_bid_record_info_all_(\d{4})\.zip)'\)")
_DIFF_RE = re.compile(r"doDownload\('(successful_bid_record_info_diff_(\d{8})\.zip)'\)")


# ------------------------------------------------------------- page parse

def parse_listing(html: str) -> Dict[str, object]:
    m = _URL_VAR_RE.search(html)
    if not m:
        raise RuntimeError(
            "uab02FileDownloadUrl not found on the 調達ポータル listing page; "
            "the download mechanism changed. Re-verify before automating."
        )
    base = m.group(1)
    all_files = [
        {"file_name": a, "key": f"FY{y}", "kind": "all", "fiscal_year": int(y)}
        for a, y in dict.fromkeys(_ALL_RE.findall(html))
    ]
    diff_files = [
        {"file_name": a, "key": f"{d[:4]}-{d[4:6]}-{d[6:]}", "kind": "diff"}
        for a, d in dict.fromkeys(_DIFF_RE.findall(html))
    ]
    all_files.sort(key=lambda f: f["fiscal_year"], reverse=True)
    diff_files.sort(key=lambda f: f["key"], reverse=True)
    if not all_files:
        raise RuntimeError("no 全件 files parsed from the 調達ポータル listing page")
    return {"base": base, "all": all_files, "diff": diff_files}


# ---------------------------------------------------------------- parse

def _amount(raw: str) -> Optional[int]:
    """落札価格 is a decimal string; procurement amounts are whole yen."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(round(float(raw)))
    except ValueError:
        return None


def normalize_rows(
    payload: bytes,
    clearance: tos.Clearance,
    retrieved_at: str,
) -> List[Dict[str, object]]:
    text = payload.decode("utf-8-sig", errors="strict")
    out: List[Dict[str, object]] = []
    for cells in csv.reader(io.StringIO(text, newline="")):
        if len(cells) < 7:
            continue
        item_no, title_raw, award_date, price, ministry_cd, method_cd = (
            c.strip() for c in cells[:6]
        )
        winner_raw = cells[6].strip() if len(cells) > 6 else ""
        corp_no = cells[7].strip() if len(cells) > 7 else ""

        # R1/R2 - the only supported path from a raw winner to a record.
        winner = anonymize.anonymize_winner(winner_raw, corp_no)
        # R4 - the publisher's own 随意契約 titles occasionally name a person.
        title, title_masked = anonymize.mask_free_text(title_raw)

        amount = _amount(price)
        award_id = hashlib.sha1(
            "|".join(
                (
                    ministry_cd,
                    award_date,
                    jputil.normalize_key(title_raw),
                    str(amount if amount is not None else ""),
                )
            ).encode("utf-8")
        ).hexdigest()

        rec: Dict[str, object] = {
            "award_id": award_id,
            "procurement_item_no": item_no,
            "publisher_name": codes.ministry_name(ministry_cd),
            "publisher_code": ministry_cd,
            "sector": codes.sector_name(ministry_cd),
            # National procurement: the publisher provides no location field.
            "prefecture": "",
            "municipality": "",
            "method": codes.method_bucket(method_cd),
            "method_detail": codes.method_detail(method_cd),
            "category_code": "",          # no 業種 in this dataset
            "title": title,
            "title_masked": "Y" if title_masked else "N",
            "publish_date": "",           # not published
            "award_date": award_date,
            "fiscal_year": jputil.fiscal_year(award_date) or "",
            "amount_jpy": amount if amount is not None else "",
            "predicted_price_jpy": "",    # not published -> 落札率 uncomputable
            "award_ratio": "",
            "bidder_count": "",           # not published
            "source_id": SOURCE_ID,
            "source_url": LIST_URL,
            "license": clearance.license,
            "attribution": clearance.attribution,
            "retrieved_at": retrieved_at,
            "raw_hash": http.sha256_hex("|".join(cells).encode("utf-8")),
        }
        rec.update(winner.as_dict())

        # Hard guard: nothing reaches disk carrying a masked winner's name.
        if rec["winner_type"] == anonymize.MASKED_INDIVIDUAL and rec["winner_name"]:
            raise RuntimeError("masked winner still carries a name; refusing to write")

        # None and "" both mean "the publisher does not provide this"; store one
        # of them so a consumer never has to distinguish.
        out.append({c: ("" if rec.get(c) is None else rec.get(c)) for c in NORMALIZED_COLUMNS})
    return out


def write_normalized(records: List[Dict[str, object]], key: str) -> Path:
    """One gzipped CSV per fiscal year; dedup on award_id."""
    by_year: Dict[object, Dict[str, Dict[str, object]]] = {}
    for rec in records:
        by_year.setdefault(rec["fiscal_year"] or "unknown", {})[rec["award_id"]] = rec

    out_dir = paths.data_dir("pportal", "normalized")
    last: Optional[Path] = None
    for year, rows in by_year.items():
        out_path = out_dir / f"pportal_awards_FY{year}.csv.gz"
        merged: Dict[str, Dict[str, object]] = {}
        if out_path.exists():
            with gzip.open(out_path, "rt", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    merged[row["award_id"]] = row
        merged.update(rows)
        with gzip.open(out_path, "wt", encoding="utf-8", newline="", compresslevel=9) as fh:
            writer = csv.DictWriter(fh, fieldnames=NORMALIZED_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for award_id in sorted(merged):
                writer.writerow(merged[award_id])
        last = out_path
    return last or (out_dir / f"pportal_awards_{key}.csv.gz")


# ------------------------------------------------------------- manifest

def load_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["file_key"]: r for r in csv.DictReader(fh)}


def write_manifest(path: Path, rows: Dict[str, Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow({c: rows[key].get(c, "") for c in MANIFEST_COLUMNS})


# ---------------------------------------------------------- re-normalize

def _renormalize(raw_root: Path, manifest: Dict[str, Dict[str, str]],
                 manifest_path: Path, clearance: tos.Clearance) -> int:
    """Rebuild the normalized store from the ZIPs already on disk.

    A parser or anonymisation change must never cost the publisher a request.
    The `retrieved_at` recorded in the manifest is preserved, because that is
    when the data was actually obtained.
    """
    zips = sorted(raw_root.rglob("*.zip"))
    if not zips:
        print(f"[{SOURCE_ID}] no raw ZIPs under {raw_root}; nothing to re-normalize")
        return 1
    by_name = {row.get("file_name", ""): row for row in manifest.values()}
    total = masked = 0
    for path in zips:
        kind = "all" if path.parent.name == "all" else "diff"
        key = (
            f"FY{path.stem.rsplit('_', 1)[-1]}" if kind == "all"
            else path.stem.rsplit("_", 1)[-1]
        )
        prior = by_name.get(path.name) or manifest.get(key, {})
        retrieved_at = prior.get("retrieved_at") or http.utc_now_iso()
        payload = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if not csv_name:
                continue
            records = normalize_rows(zf.read(csv_name), clearance, retrieved_at)
        normalized_path = write_normalized(records, key)
        n_masked = sum(
            1 for r in records if r["winner_type"] == anonymize.MASKED_INDIVIDUAL
        )
        manifest[key] = {
            **prior,
            "file_key": key,
            "kind": kind,
            "file_name": path.name,
            "raw_path": paths.rel(path),
            "raw_bytes": str(len(payload)),
            "raw_sha256": http.sha256_hex(payload),
            "records": str(len(records)),
            "normalized_path": paths.rel(normalized_path),
            "retrieved_at": retrieved_at,
            "status": "ok",
        }
        total += len(records)
        masked += n_masked
        print(f"[{SOURCE_ID}] {key}: {len(records):>6} records ({n_masked} masked) "
              f"re-normalized from disk")
    write_manifest(manifest_path, manifest)
    print(f"[{SOURCE_ID}] done     : {len(zips)} file(s) re-normalized, "
          f"{total} records, {masked} masked winners, 0 requests")
    return 0


# ------------------------------------------------------------------ run

def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="調達ポータル 落札実績 collector (Bet A)")
    ap.add_argument("--years", nargs="*", type=int, default=None,
                    help="fiscal years to fetch (default: all, newest first)")
    ap.add_argument("--include-diff", action="store_true",
                    help="also fetch the daily 差分 window (retained ~2 months upstream)")
    ap.add_argument("--refetch", action="store_true",
                    help="ignore the manifest and re-request every selected file")
    ap.add_argument("--from-raw", action="store_true",
                    help="re-normalize the ZIPs already in data/pportal/raw/ "
                         "without contacting the publisher. Use after a parser "
                         "or anonymisation change.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--daily-cap", type=int, default=80)
    args = ap.parse_args(argv)

    clearance = tos.clear(TOS_ROW)
    tos.require_publishable(clearance)

    manifest_path = paths.DATA / "pportal" / "manifest.csv"
    manifest = load_manifest(manifest_path)
    raw_root = paths.data_dir("pportal", "raw")

    if args.from_raw:
        return _renormalize(raw_root, manifest, manifest_path, clearance)

    session = http.PoliteSession(
        state_path=paths.state_dir("pportal") / "http_state.json",
        daily_cap=args.daily_cap,
    )

    print(f"[{SOURCE_ID}] source   : {clearance.source}")
    print(f"[{SOURCE_ID}] licence  : {clearance.license}")
    print(f"[{SOURCE_ID}] robots   : {session.robots_verdict(LIST_URL)}")

    page = session.get(LIST_URL, accept="text/html", conditional=False)
    listing = parse_listing(page.text())
    base = str(listing["base"])
    print(f"[{SOURCE_ID}] download : {base}...")

    wanted: List[Dict[str, object]] = list(listing["all"])  # type: ignore[arg-type]
    if args.years:
        wanted = [f for f in wanted if f["fiscal_year"] in set(args.years)]
    if args.include_diff:
        wanted += list(listing["diff"])  # type: ignore[arg-type]

    print(f"[{SOURCE_ID}] listing  : {len(listing['all'])} 全件 file(s), "
          f"{len(listing['diff'])} 差分 file(s); {len(wanted)} selected")

    if args.dry_run:
        for f in wanted:
            seen = "already collected" if f["key"] in manifest else "would fetch"
            print(f"    {seen}: {f['file_name']}")
        session.save_state()
        return 0

    fetched = 0
    total_records = 0
    masked = 0
    for entry in wanted:
        key = str(entry["key"])
        file_name = str(entry["file_name"])
        if key in manifest and manifest[key].get("status") == "ok" and not args.refetch:
            # 全件 files are re-published monthly, so a year already collected is
            # still refreshed by --refetch; a diff file never changes.
            if entry["kind"] == "diff":
                continue
        url = base + file_name
        try:
            resp = session.get(url, accept="application/octet-stream")
        except http.NotModified:
            print(f"[{SOURCE_ID}] {key}: unchanged (304)")
            continue
        except (http.DailyCapReached, http.HostPaused) as exc:
            print(f"[{SOURCE_ID}] stopping: {exc}")
            break
        except RuntimeError as exc:
            print(f"[{SOURCE_ID}] {key}: FAILED {exc}")
            manifest[key] = {"file_key": key, "kind": str(entry["kind"]),
                             "file_name": file_name, "status": f"error: {exc}",
                             "retrieved_at": http.utc_now_iso()}
            continue

        if not resp.body.startswith(b"PK"):
            print(f"[{SOURCE_ID}] {key}: not a ZIP; skipped")
            continue

        sub = "all" if entry["kind"] == "all" else "diff"
        raw_path = raw_root / sub / file_name
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(resp.body)

        with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
            csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if not csv_name:
                print(f"[{SOURCE_ID}] {key}: no CSV inside; skipped")
                continue
            payload = zf.read(csv_name)

        records = normalize_rows(payload, clearance, resp.retrieved_at)
        normalized_path = write_normalized(records, key)
        n_masked = sum(
            1 for r in records if r["winner_type"] == anonymize.MASKED_INDIVIDUAL
        )

        manifest[key] = {
            "file_key": key,
            "kind": str(entry["kind"]),
            "file_name": file_name,
            "raw_path": paths.rel(raw_path),
            "raw_bytes": str(len(resp.body)),
            "raw_sha256": resp.sha256,
            "etag": resp.headers.get("etag", ""),
            "records": str(len(records)),
            "normalized_path": paths.rel(normalized_path),
            "retrieved_at": resp.retrieved_at,
            "status": "ok",
        }
        fetched += 1
        total_records += len(records)
        masked += n_masked
        print(f"[{SOURCE_ID}] {key}: {len(records):>6} records "
              f"({n_masked} masked), {len(resp.body):>8} bytes raw")

    write_manifest(manifest_path, manifest)
    session.save_state()
    print(f"[{SOURCE_ID}] done     : {fetched} file(s), {total_records} records, "
          f"{masked} masked winners")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
