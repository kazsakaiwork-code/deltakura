#!/usr/bin/env python3
"""Bet A - first statistics table over the 調達ポータル award archive.

Grain: 都道府県 x 業種-proxy x 年度.

Two honest substitutions are forced by the source (see data/pportal/README.md):

  都道府県  the dataset is national procurement and carries no location field.
            The only prefecture obtainable is the *winner's registered*
            prefecture, joined from the NTA 法人番号 registry on
            `corporate_number` and on nothing else. It answers
            "whose companies win national contracts", not "where the work is".
            A masked individual has no corporate number, so it has no
            prefecture, and lands in 不明.

  業種      the dataset carries no 業種 and no product classification. The
            closest category available is the procuring body: 府省 bucketed
            into a coarse `sector` (crawlers/pportal/codes.py).

落札率 needs 予定価格, which this source does not publish at all, so the
ratio columns are emitted and are empty for every row. They exist so the
municipal sources that *do* publish 予定価格 can fill them without a schema
change.

    python crawlers/pportal/stats.py
    python crawlers/pportal/stats.py --min-bucket 3
"""

from __future__ import annotations

import argparse
import csv
import gzip
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import anonymize, jputil, paths  # noqa: E402

#: R6b - the guard on the suppression counter.
#:
#: `n_awards_suppressed_in_group` counts awards held back by R6, summed one
#: level coarser than the suppressed bucket: per (fiscal_year, sector), never
#: per prefecture. That is only safe while several prefectures are missing from
#: the group, because "exactly one prefecture is absent from this group" would
#: name the suppressed bucket by elimination. Measured over the current table
#: the floor is 9 absent prefectures, but that is a property of today's
#: coverage, not of the design: as Bet A gains per-prefecture completeness a
#: group could reach one. So the count is emitted only when at least this many
#: prefectures are absent from the group, and is blank otherwise.
MIN_ABSENT_PREFECTURES = 3


def suppression_disclosure(count: int, n_absent_prefectures: int,
                           minimum: int = MIN_ABSENT_PREFECTURES):
    """The publishable value of `n_awards_suppressed_in_group`.

    Zero is always publishable: it says nothing about anybody. A non-zero count
    is published only when the ambiguity set is at least `minimum` prefectures
    wide; otherwise it is blanked, because the group itself would point at the
    suppressed bucket.
    """
    if not count:
        return 0
    if n_absent_prefectures < minimum:
        return ""
    return count

STATS_COLUMNS = [
    "fiscal_year",
    "prefecture",
    "prefecture_code",
    "prefecture_basis",
    "category_axis",
    "sector",
    "n_awards",
    "n_corporate",
    "n_masked_individual",
    # Provenance, constant within a file except n_awards_suppressed_in_group,
    # which is constant within a (fiscal_year, sector) group. They are carried
    # in the CSV so that every consumer of the PUBLISHED layer can state the
    # same reconciliation without reaching into the private record store: the
    # site and the Worker build from data/published/ alone.
    "n_awards_suppressed_in_group",
    "records_parsed",
    "records_normalized",
    "retrieved_at",
    "amount_min_jpy",
    "amount_q1_jpy",
    "amount_median_jpy",
    "amount_q3_jpy",
    "amount_max_jpy",
    "amount_sum_jpy",
    "award_ratio_median",
    "award_ratio_q1",
    "award_ratio_q3",
    "award_ratio_coverage",
    "source_id",
    "license",
    "attribution",
]

PREFECTURE_BASIS = "winner_registered_nta"
CATEGORY_AXIS = "府省由来セクター（業種は原データに存在しない）"


def load_lookup() -> Dict[str, Tuple[str, str]]:
    """corporate_number -> (prefecture_code, prefecture)."""
    path = paths.DATA / "nta" / "lookup" / "corp_location.csv.gz"
    if not path.exists():
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["corporate_number"]] = (
                row.get("prefecture_code", ""),
                row.get("prefecture") or jputil.prefecture_from_code(
                    row.get("prefecture_code", "")
                ),
            )
    return out


def quartiles(values: List[int]) -> Tuple[int, int, int]:
    """(q1, median, q3), inclusive method, integer yen."""
    ordered = sorted(values)
    median = statistics.median(ordered)
    if len(ordered) < 2:
        return int(median), int(median), int(median)
    try:
        q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    except statistics.StatisticsError:
        q1 = q3 = median
    return int(round(q1)), int(round(median)), int(round(q3))


def float_quartiles(values: List[float]) -> Tuple[float, float, float]:
    """(q1, median, q3) for 落札率-style ratios, rounded to 4 places."""
    ordered = sorted(values)
    median = statistics.median(ordered)
    if len(ordered) < 2:
        return round(median, 4), round(median, 4), round(median, 4)
    q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    return round(q1, 4), round(median, 4), round(q3, 4)


def load_collection_provenance() -> Tuple[int, str]:
    """(publisher rows parsed, latest retrieved_at) from the private manifest.

    Returns (0, "") when the private collection store is not present, which is
    the normal case for a standalone checkout of the public repository.
    """
    manifest = paths.DATA / "pportal" / "manifest.csv"
    if not manifest.exists():
        return 0, ""
    parsed = 0
    retrieved = ""
    with manifest.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "ok") != "ok":
                continue
            try:
                parsed += int(row.get("records") or 0)
            except ValueError:
                pass
            retrieved = max(retrieved, row.get("retrieved_at") or "")
    return parsed, retrieved


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bet-A statistics v0")
    ap.add_argument("--min-bucket", type=int, default=anonymize.MIN_BUCKET_SIZE,
                    help="R6 threshold for masked individuals per bucket")
    ap.add_argument("--out", default=None,
                    help="output path (default data/published/pportal/stats_v0.csv)")
    args = ap.parse_args(argv)

    normalized_dir = paths.DATA / "pportal" / "normalized"
    files = sorted(normalized_dir.glob("*.csv.gz"))
    if not files:
        print("[stats] no normalized data; run crawlers/pportal/collect.py first")
        return 1

    lookup = load_lookup()
    print(f"[stats] join table : {len(lookup):,} corporate number(s) "
          f"{'(missing - every row falls to 不明)' if not lookup else ''}")

    amounts: Dict[Tuple, List[int]] = defaultdict(list)
    ratios: Dict[Tuple, List[float]] = defaultdict(list)
    n_corporate: Dict[Tuple, int] = defaultdict(int)
    n_masked: Dict[Tuple, int] = defaultdict(int)
    license_seen = attribution_seen = source_seen = ""
    total_rows = unresolved = 0

    for path in files:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                total_rows += 1
                license_seen = license_seen or row.get("license", "")
                attribution_seen = attribution_seen or row.get("attribution", "")
                source_seen = source_seen or row.get("source_id", "")

                number = row.get("corporate_number") or ""
                pref_code, pref = lookup.get(number, ("", jputil.UNKNOWN))
                if not number or pref == jputil.UNKNOWN:
                    unresolved += 1

                key = (row.get("fiscal_year", ""), pref, pref_code, row.get("sector", ""))

                if row.get("winner_type") == anonymize.MASKED_INDIVIDUAL:
                    n_masked[key] += 1
                elif row.get("winner_type") == anonymize.CORPORATE:
                    n_corporate[key] += 1

                raw_amount = row.get("amount_jpy") or ""
                if raw_amount:
                    try:
                        amounts[key].append(int(float(raw_amount)))
                    except ValueError:
                        pass
                raw_ratio = row.get("award_ratio") or ""
                if raw_ratio:
                    try:
                        ratios[key].append(float(raw_ratio))
                    except ValueError:
                        pass

    keys = set(amounts) | set(n_corporate) | set(n_masked)
    rows: List[Dict[str, object]] = []
    suppressed = 0
    suppressed_awards = 0
    # Awards held back by R6, summed per (fiscal_year, sector). Published one
    # level coarser than the suppressed bucket itself, so no bucket is
    # reconstructible, and only as a count.
    suppressed_in_group: Dict[Tuple[str, str], int] = defaultdict(int)
    # R6b: how wide the ambiguity set is. `published_prefectures` is what a
    # reader of the published table can see in a group; everything else in the
    # dataset's prefecture universe is absent from it and could be the
    # suppressed bucket.
    published_prefectures: Dict[Tuple[str, str], set] = defaultdict(set)
    all_prefectures = {key[1] for key in keys}
    for key in keys:
        fiscal_year, pref, _code, sector = key
        if anonymize.bucket_is_publishable(n_masked[key], minimum=args.min_bucket):
            published_prefectures[(fiscal_year, sector)].add(pref)
        else:
            suppressed_in_group[(fiscal_year, sector)] += n_corporate[key] + n_masked[key]

    withheld_groups = sum(
        1
        for group, count in suppressed_in_group.items()
        if count
        and suppression_disclosure(
            count, len(all_prefectures) - len(published_prefectures[group])
        ) == ""
    )

    parsed_total, retrieved_at = load_collection_provenance()
    normalized_total = total_rows
    if not parsed_total:
        # No private manifest next to this checkout: the publisher's own row
        # count is not knowable here. Report the normalized total rather than
        # inventing a number.
        parsed_total = normalized_total

    for key in sorted(keys, key=lambda k: (k[0], k[3], k[1])):
        fiscal_year, pref, pref_code, sector = key
        masked = n_masked[key]
        corporate = n_corporate[key]
        n_awards = corporate + masked

        # R6: a bucket holding 1..min-1 masked individuals is suppressed.
        if not anonymize.bucket_is_publishable(masked, minimum=args.min_bucket):
            suppressed += 1
            suppressed_awards += n_awards
            continue

        values = amounts[key]
        if values:
            q1, median, q3 = quartiles(values)
            amount_stats = {
                "amount_min_jpy": min(values),
                "amount_q1_jpy": q1,
                "amount_median_jpy": median,
                "amount_q3_jpy": q3,
                "amount_max_jpy": max(values),
                "amount_sum_jpy": sum(values),
            }
        else:
            amount_stats = {c: "" for c in STATS_COLUMNS if c.startswith("amount_")}

        ratio_values = ratios[key]
        if ratio_values:
            rq1, rmedian, rq3 = float_quartiles(ratio_values)
            ratio_stats = {
                "award_ratio_q1": rq1,
                "award_ratio_median": rmedian,
                "award_ratio_q3": rq3,
                "award_ratio_coverage": round(len(ratio_values) / n_awards, 4) if n_awards else "",
            }
        else:
            ratio_stats = {
                "award_ratio_q1": "", "award_ratio_median": "",
                "award_ratio_q3": "", "award_ratio_coverage": 0,
            }

        row: Dict[str, object] = {
            "fiscal_year": fiscal_year,
            "prefecture": pref,
            "prefecture_code": pref_code,
            "prefecture_basis": PREFECTURE_BASIS,
            "category_axis": CATEGORY_AXIS,
            "sector": sector,
            "n_awards": n_awards,
            "n_corporate": corporate,
            "n_masked_individual": masked,
            "n_awards_suppressed_in_group": suppression_disclosure(
                suppressed_in_group.get((fiscal_year, sector), 0),
                len(all_prefectures) - len(published_prefectures[(fiscal_year, sector)]),
            ),
            "records_parsed": parsed_total,
            "records_normalized": normalized_total,
            "retrieved_at": retrieved_at,
            "source_id": source_seen,
            "license": license_seen,
            "attribution": attribution_seen,
        }
        row.update(amount_stats)
        row.update(ratio_stats)
        rows.append({c: row.get(c, "") for c in STATS_COLUMNS})

    # R5: an aggregate must not be keyed by a party's name.
    anonymize.assert_not_a_directory(rows, context="pportal stats_v0")

    # stats_v0.csv is a PUBLISHED aggregate: it is the one Bet-A file
    # committed to the repository. The per-award normalized store it is built
    # from stays in the private data directory.
    out_path = Path(args.out) if args.out else (paths.published_dir("pportal") / "stats_v0.csv")
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[stats] input      : {total_rows:,} award record(s) in {len(files)} file(s)")
    print(f"[stats] unresolved : {unresolved:,} record(s) with no registered "
          f"prefecture (masked individuals and unmatched numbers)")
    print(f"[stats] suppressed : {suppressed} bucket(s) / {suppressed_awards} award(s) "
          f"under R6 (fewer than {args.min_bucket} masked individuals)")
    print(f"[stats] R6b guard  : {withheld_groups} group(s) had their suppression "
          f"count blanked (fewer than {MIN_ABSENT_PREFECTURES} prefectures absent "
          f"of {len(all_prefectures)})")
    print(f"[stats] written    : {paths.rel(out_path)} "
          f"({len(rows):,} row(s), {out_path.stat().st_size:,} bytes)")
    print("[stats] 落札率     : empty for every row - 予定価格 is not published "
          "by this source")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
