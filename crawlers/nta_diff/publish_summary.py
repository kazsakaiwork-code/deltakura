#!/usr/bin/env python3
"""Build the published Bet-C summary: `data/published/nta/summary.json`.

This is the only Bet-C artefact that is ever committed. It carries counts and
code breakdowns, never a record: no corporate number, no name, no address.
The record-level store stays in the private data directory (`DELTAKURA_DATA_DIR`).

**One count, one authority.**
The publisher's file has a raw row count; our normalized store applies the
dedup rule (highest `sequence_number` wins per
`corporate_number|change_date`, `correct_flag=1` supersedes). Those two numbers
differ on a handful of days. Both are published, and which one is authoritative
is fixed here rather than decided again by each consumer:

    records      <- the deduplicated scan of the normalized store. AUTHORITY.
                    This is what the site, the RSS feed, the Worker and the MCP
                    server must all report.
    records_raw  <- the manifest's copy of the publisher's row count, kept so
                    the difference is explained instead of hidden.

`site/build.py` and `api/scripts/build-data.mjs` both read `records` from this
file, so the two public surfaces cannot disagree again.

Merge behaviour: days found in the normalized store are (re)computed; days that
are only in the existing summary are carried forward untouched. That is what
makes the nightly GitHub Actions run work, where the runner holds one night's
file and not the archive. A day carried forward keeps whatever basis it was
first computed with; a full rebuild on a machine holding the whole normalized
store is the way to revise history.

Every day carries its own `process_codes`, `kind_codes` and `prefectures`, and
the three top-level totals are the sums over the day table. (Until 2026-09-27
kind and prefecture totals were kept only as totals, and a nightly run on a
one-night store replaced the archive's totals with that night's: 89,724
prefecture rows became 2,101. Per-day breakdowns make a partial run additive.)

**Two published files, both committed.**

    data/published/nta/summary.json   counts, code breakdowns, provenance
    data/published/nta/manifest.csv   one row per collected day: file id, date,
                                      sha256, byte size, row counts, retrieval
                                      time. No record content.

The manifest is what makes the nightly GitHub Actions run cheap. The runner's
private store is thrown away with the runner, so without a committed record of
which days are already held the collector would re-fetch the whole 40-day
window every night. `collect.py` seeds itself from this file and fetches only
what is new (`crawlers/nta_diff/collect.py`, "already held").

**No build timestamp.** Neither file carries a wall clock: `source_version` is
a content hash of the summary itself, and `coverage.retrieved_at` is when the
publisher's newest file was actually fetched. The same inputs therefore produce
the same bytes, which is what lets CI assert that the committed artefacts match
a fresh build.

    python crawlers/nta_diff/publish_summary.py
    python crawlers/nta_diff/publish_summary.py --check     # exit 1 if stale
    python crawlers/nta_diff/publish_summary.py --out <path>
"""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import paths  # noqa: E402

SCHEMA = 1
DATASET = "nta_corporate_number_diff"
SOURCE_ID = "nta_diff"
SOURCE_URL = "https://www.houjin-bangou.nta.go.jp/download/sabun/"
LICENSE = (
    "公共データ利用規約(第1.0版) — commercial reuse and redistribution permitted "
    "with attribution; modifications must be declared."
)
ATTRIBUTION = (
    "出典：国税庁法人番号公表サイト（国税庁）"
    "（https://www.houjin-bangou.nta.go.jp/download/sabun/）"
)
MODIFICATION_NOTICE = (
    "国税庁法人番号公表サイトの日次差分ファイルを加工して作成しています"
    " / Derived by Deltakura from the National Tax Agency's daily diff files: "
    "parsed, deduplicated and aggregated to counts. "
    "Deltakura is an unofficial archive; it is not an official source."
)
#: The compact, committed collection manifest. Counts and integrity hashes only;
#: no corporate number, no name, no record of any kind.
PUBLISHED_MANIFEST_COLUMNS = [
    "file_date",
    "file_no",
    "raw_sha256",
    "raw_bytes",
    "records_raw",
    "records",
    "retrieved_at",
]

COUNT_RULE = (
    "`records` is the deduplicated count and is the single authority for every "
    "public surface. `records_raw` is the publisher's own row count as recorded "
    "in the collection manifest; it is larger on days where a later file "
    "superseded a row (highest sequence_number wins per "
    "corporate_number|change_date; correct_flag=1 supersedes)."
)


def _scan_normalized(normalized_dir: Path):
    daily = collections.Counter()
    daily_process = collections.defaultdict(collections.Counter)
    process = collections.Counter()
    daily_kinds = collections.defaultdict(collections.Counter)
    daily_prefs = collections.defaultdict(collections.Counter)
    for path in sorted(normalized_dir.glob("*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                day = row.get("file_date") or ""
                if not day:
                    continue
                daily[day] += 1
                daily_process[day][row.get("process_code") or "unknown"] += 1
                process[row.get("process_code") or "unknown"] += 1
                daily_kinds[day][row.get("kind_code") or "unknown"] += 1
                if row.get("prefecture"):
                    daily_prefs[day][row["prefecture"]] += 1
    return daily, daily_process, process, daily_kinds, daily_prefs


def _read_manifest(manifest_path: Path) -> Dict[str, dict]:
    if not manifest_path.exists():
        return {}
    out: Dict[str, dict] = {}
    with manifest_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") not in ("", "ok"):
                continue
            day = row.get("file_date")
            if day:
                out[day] = row
    return out


def _int(value, default=0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build(data_dir: Path, previous: Optional[dict] = None) -> dict:
    nta_root = data_dir / "nta"
    manifest = _read_manifest(nta_root / "manifest.csv")
    daily, daily_process, process, daily_kinds, daily_prefs = _scan_normalized(
        nta_root / "normalized"
    )
    previous_days = set(((previous or {}).get("days") or {}))

    days: Dict[str, dict] = {}
    for day, entry in ((previous or {}).get("days") or {}).items():
        days[day] = dict(entry)

    for day in sorted(set(daily) | set(manifest)):
        entry = dict(days.get(day) or {})
        man = manifest.get(day)
        if man:
            entry.update({
                "file_no": man.get("file_no", ""),
                "records_raw": _int(man.get("records")),
                "raw_bytes": _int(man.get("raw_bytes")),
                "raw_sha256": man.get("raw_sha256", ""),
                "signature_stored": man.get("signature_stored", ""),
                "retrieved_at": man.get("retrieved_at", ""),
                "status": man.get("status", "ok"),
            })
        if day in daily:
            entry["records"] = daily[day]
            entry["basis"] = "deduplicated-scan"
            entry["process_codes"] = dict(sorted(daily_process[day].items()))
            entry["kind_codes"] = dict(sorted(daily_kinds[day].items()))
            entry["prefectures"] = dict(sorted(daily_prefs[day].items()))
        elif "records" not in entry:
            # No normalized rows for this day and nothing carried forward: the
            # manifest count is all there is. Say so rather than pass it off as
            # a deduplicated number.
            entry["records"] = entry.get("records_raw", 0)
            entry["basis"] = "manifest-only"
            entry.setdefault("process_codes", {})
            entry.setdefault("kind_codes", {})
            entry.setdefault("prefectures", {})
        days[day] = entry

    ordered = {day: days[day] for day in sorted(days)}
    day_list = list(ordered)

    # Totals are recomputed from the merged day table, so a day carried forward
    # from a previous run still counts.
    total = sum(_int(d.get("records")) for d in ordered.values())
    total_raw = sum(_int(d.get("records_raw")) for d in ordered.values())
    raw_bytes = sum(_int(d.get("raw_bytes")) for d in ordered.values())
    retrieved_at = max((d.get("retrieved_at") or "") for d in ordered.values()) if ordered else ""

    merged_process = collections.Counter()
    for entry in ordered.values():
        for code, n in (entry.get("process_codes") or {}).items():
            merged_process[code] += _int(n)

    def summed(field):
        total = collections.Counter()
        for entry in ordered.values():
            for key, n in (entry.get(field) or {}).items():
                total[key] += _int(n)
        return total

    if all("kind_codes" in e and "prefectures" in e for e in ordered.values()):
        # Every day carries its breakdown: the totals are the sums, whatever
        # part of the archive this run could scan.
        kind_totals = summed("kind_codes")
        pref_totals = summed("prefectures")
    else:
        # A summary written before per-day breakdowns existed: its totals cover
        # the days it already held; add only the days this run saw first.
        kind_totals = collections.Counter({k: _int(v) for k, v in ((previous or {}).get("kind_codes") or {}).items()})
        pref_totals = collections.Counter({k: _int(v) for k, v in ((previous or {}).get("prefectures") or {}).items()})
        for day in daily:
            if day not in previous_days:
                kind_totals.update(daily_kinds[day])
                pref_totals.update(daily_prefs[day])

    return {
        "schema": SCHEMA,
        "dataset": DATASET,
        # Filled in by _stamp_version() below: a hash of this file's own
        # content, not the time it was written. Two runs over the same
        # collection produce the same bytes.
        "source_version": "",
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "modification_notice": MODIFICATION_NOTICE,
        "count_rule": COUNT_RULE,
        "contains_records": False,
        "coverage": {
            "from": day_list[0] if day_list else None,
            "to": day_list[-1] if day_list else None,
            "days_with_data": len(day_list),
            "records": total,
            "records_raw": total_raw,
            "records_deduplicated_out": total_raw - total,
            "raw_bytes": raw_bytes,
            "retrieved_at": retrieved_at,
            "upstream_retention_days": 40,
        },
        "days": ordered,
        "process_codes": dict(sorted(merged_process.items())),
        "kind_codes": dict(sorted(kind_totals.items())),
        "prefectures": dict(sorted(pref_totals.items())),
    }


def _stamp_version(summary: dict) -> dict:
    """Set `source_version` to a hash of everything else in the summary.

    Deterministic provenance: it changes when the data changes and not when the
    clock moves, so `--check` and `git diff` mean what they say.
    """
    summary["source_version"] = ""
    body = {k: v for k, v in summary.items() if k != "source_version"}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    summary["source_version"] = f"sha256-{digest}"
    return summary


def _dump(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def manifest_csv(summary: dict) -> str:
    """The committed collection manifest, derived from the summary's day table.

    One row per day whose publisher file we hold, carrying only what a later run
    (or a reader checking our integrity claims) needs: which file, when it was
    fetched, how many bytes, its sha256, and the two row counts. No record.
    """
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=PUBLISHED_MANIFEST_COLUMNS,
                            lineterminator="\n")
    writer.writeheader()
    for day in sorted(summary.get("days") or {}):
        entry = summary["days"][day]
        if not entry.get("file_no"):
            continue
        writer.writerow({
            "file_date": day,
            "file_no": entry.get("file_no", ""),
            "raw_sha256": entry.get("raw_sha256", ""),
            "raw_bytes": _int(entry.get("raw_bytes")),
            "records_raw": _int(entry.get("records_raw")),
            "records": _int(entry.get("records")),
            "retrieved_at": entry.get("retrieved_at", ""),
        })
    return out.getvalue()


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=None,
                    help="private data directory (default: $DELTAKURA_DATA_DIR or ../data)")
    ap.add_argument("--out", default=None,
                    help="output path (default data/published/nta/summary.json)")
    ap.add_argument("--check", action="store_true",
                    help="compare instead of writing; exit 1 when stale")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else paths.DATA
    out_path = Path(args.out) if args.out else (paths.published_dir("nta") / "summary.json")

    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError:
            previous = None

    summary = _stamp_version(build(data_dir, previous))
    text = _dump(summary)
    manifest_text = manifest_csv(summary)
    manifest_path = out_path.parent / "manifest.csv"

    if not summary["days"]:
        print(f"[nta-summary] no data under {paths.rel(data_dir)} and no previous summary; "
              "nothing written")
        return 1

    if args.check:
        for path, wanted in ((out_path, text), (manifest_path, manifest_text)):
            if not path.exists():
                print(f"[nta-summary] STALE: {paths.rel(path)} does not exist")
                return 1
            # Byte-for-byte: nothing in either file is allowed to move on its own.
            if path.read_text(encoding="utf-8") != wanted:
                print(f"[nta-summary] STALE: {paths.rel(path)} differs from a fresh build")
                return 1
        print(f"[nta-summary] {paths.rel(out_path)} and {paths.rel(manifest_path)} are current")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # LF on every platform: these bytes are committed and compared in CI.
    out_path.write_text(text, encoding="utf-8", newline="\n")
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")
    cov = summary["coverage"]
    print(f"[nta-summary] {paths.rel(out_path)}: {cov['days_with_data']} day(s), "
          f"{cov['records']:,} records (deduplicated; publisher rows "
          f"{cov['records_raw']:,}, {cov['records_deduplicated_out']:,} superseded), "
          f"{len(text):,} bytes")
    print(f"[nta-summary] {paths.rel(manifest_path)}: "
          f"{manifest_text.count(chr(10)) - 1} collected day(s), "
          f"version {summary['source_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
