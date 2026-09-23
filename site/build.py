#!/usr/bin/env python3
"""Deltakura static site generator.

Renders the whole public site into ``site/public/`` from the normalized data
stores in ``data/``. Standard library only: no framework, no build server, no
paid service, no network access. Output is plain HTML/CSS/JS that Firebase
Hosting serves as static files.

    python site/build.py            # build into site/public/
    python site/build.py --clean    # wipe site/public/ first
    python site/build.py --check    # build, then assert the invariants below

Invariants the build enforces (a failure is a build failure, not a warning):

  * every page has a <title> and a meta description in both languages;
  * every page that shows source-derived numbers carries the publisher's
    attribution string, the upstream licence name and a modification notice;
  * every data page carries a schema.org Dataset JSON-LD block;
  * no page is larger than 100 KB (a Firebase Spark-plan constraint);
  * no email address, input[type=email] or mailto: link exists anywhere -
    this project sends and collects no email at all;
  * the total build stays under 50 MB.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import html
import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

SITE_DIR = Path(__file__).resolve().parent
REPO = SITE_DIR.parent
OUT = SITE_DIR / "public"

# The site builds from the repository's own published data. These
# three files are committed; everything else is optional enrichment.
PUBLISHED = REPO / "data" / "published"
PPORTAL_STATS = PUBLISHED / "pportal" / "stats_v0.csv"
NTA_SUMMARY = PUBLISHED / "nta" / "summary.json"

# The private collection store: publisher originals, the per-record normalized
# layer, manifests. Never committed. `DELTAKURA_DATA_DIR` points at it and
# defaults to `../data` relative to the repository root. When it is absent the
# build still succeeds: pooled quartiles fall back to the documented estimator
# and every page says so, and Bet-A provenance falls back to what the published
# files themselves state.
PRIVATE_DATA = Path(
    os.environ.get("DELTAKURA_DATA_DIR", "").strip() or (REPO.parent / "data")
).expanduser().resolve()
PPORTAL_NORMALIZED = PRIVATE_DATA / "pportal" / "normalized"
PPORTAL_MANIFEST = PRIVATE_DATA / "pportal" / "manifest.csv"

# --------------------------------------------------------------------------
# Placeholders that need a real value once the accounts exist.
# Search for "TODO" to find every one of them.
# --------------------------------------------------------------------------

BASE_URL = "https://deltakura-signals.web.app"  # TODO: the Firebase project id
# One public monorepo: code, published data, issues and removal requests all
# live at github.com/kazsakaiwork-code/deltakura. There is no GitHub organisation;
# every "GitHub" link points at the repository itself.
GITHUB_REPO = "https://github.com/kazsakaiwork-code/deltakura"
GITHUB_ORG = GITHUB_REPO
GITHUB_ISSUES = GITHUB_REPO + "/issues"
GITHUB_CORE = GITHUB_REPO + "/tree/main/crawlers"
GITHUB_SITE = GITHUB_REPO + "/tree/main/site"
INTENT_ENDPOINT = ""  # TODO: https://intent.deltakura.workers.dev/v0/intent

# --------------------------------------------------------------------------
# The intent contract
# --------------------------------------------------------------------------
#
# These are the product ids the Worker accepts: DEFAULT_PRODUCTS in
# api/src/routes/intent.ts. A button carrying anything else is answered with
# 400 unknown_product, and intent_rate_14d - one of the two go-live conditions -
# reads zero. `intent_button()` refuses to
# render an id that is not in this tuple, and api/test/intent-contract.test.ts
# drives the real Worker with the ids parsed out of this file, so the two sides
# cannot drift apart again.
INTENT_PRODUCTS = (
    "bet_a_report",
    "bet_a_consultant_plan",
    "bet_a_api",
    "bet_b_watchlist",
    "bet_b_api",
    "bet_c_registry_diff",
    "pricing_paid_plans",
)

BRAND = "Deltakura"
BRAND_JA = "デルタ蔵"

LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

PPORTAL_ATTRIB = "出典：調達ポータル（https://www.p-portal.go.jp/）"
NTA_ATTRIB = (
    "出典：国税庁法人番号公表サイト（国税庁）"
    "（https://www.houjin-bangou.nta.go.jp/download/sabun/）"
)
# 公共データ利用規約(第1.0版) requires BOTH a source indication and a statement
# that the data was modified. Every Bet-C surface, including every feed item,
# carries this.
NTA_LICENSE_NAME = "公共データ利用規約(第1.0版)"
NTA_MODIFIED = (
    "国税庁法人番号公表サイトの日次差分ファイルを加工して利用しています"
    " / Modified from the source: parsed, deduplicated and aggregated to counts by Deltakura."
)
NTA_FEED_NOTICE = f"{NTA_ATTRIB}｜{NTA_LICENSE_NAME}｜{NTA_MODIFIED}"

# 政府標準利用規約(第2.0版) asks for the same two things of the procurement
# data: name the licence, and say the data is used in modified form.
PPORTAL_LICENSE_NAME = "政府標準利用規約(第2.0版)"
PPORTAL_MODIFIED = (
    "調達ポータルの落札実績オープンデータを加工して利用しています"
    " / Modified from the source: parsed, deduplicated, anonymised and aggregated"
    " to counts by Deltakura."
)

# --------------------------------------------------------------------------
# Reference tables
# --------------------------------------------------------------------------

# 13 coarse sectors derived from the procuring 府省 code by crawlers/pportal.
# NOTE: the sector describes WHO BOUGHT, not what was sold (data/pportal/README.md
# caveat 3). The English label says so.
SECTORS = {
    "内閣・内政": ("cabinet", "Cabinet & domestic administration"),
    "総務・情報通信": ("soumu-ict", "Internal affairs & telecommunications"),
    "警察・法務・公安": ("police-justice", "Police, justice & public security"),
    "外交": ("mofa", "Foreign affairs"),
    "財務・金融": ("mof-fsa", "Finance & financial services"),
    "教育・文化・スポーツ": ("mext", "Education, culture & sport"),
    "厚生労働": ("mhlw", "Health, labour & welfare"),
    "農林水産": ("maff", "Agriculture, forestry & fisheries"),
    "経済産業": ("meti", "Economy, trade & industry"),
    "国土交通・運輸": ("mlit", "Land, infrastructure, transport & tourism"),
    "環境・原子力": ("env-nuclear", "Environment & nuclear"),
    "防衛": ("mod", "Defense"),
    "立法・司法・会計検査": ("legislature-judiciary-audit", "Legislature, judiciary & board of audit"),
}

PREF_EN = {
    "01": "Hokkaido", "02": "Aomori", "03": "Iwate", "04": "Miyagi",
    "05": "Akita", "06": "Yamagata", "07": "Fukushima", "08": "Ibaraki",
    "09": "Tochigi", "10": "Gunma", "11": "Saitama", "12": "Chiba",
    "13": "Tokyo", "14": "Kanagawa", "15": "Niigata", "16": "Toyama",
    "17": "Ishikawa", "18": "Fukui", "19": "Yamanashi", "20": "Nagano",
    "21": "Gifu", "22": "Shizuoka", "23": "Aichi", "24": "Mie",
    "25": "Shiga", "26": "Kyoto", "27": "Osaka", "28": "Hyogo",
    "29": "Nara", "30": "Wakayama", "31": "Tottori", "32": "Shimane",
    "33": "Okayama", "34": "Hiroshima", "35": "Yamaguchi", "36": "Tokushima",
    "37": "Kagawa", "38": "Ehime", "39": "Kochi", "40": "Fukuoka",
    "41": "Saga", "42": "Nagasaki", "43": "Kumamoto", "44": "Oita",
    "45": "Miyazaki", "46": "Kagoshima", "47": "Okinawa", "": "Unknown",
}

# NTA 差分 処理区分 (publisher spec). Only codes actually present are rendered.
PROCESS_CODES = {
    "01": ("新規", "New registration"),
    "11": ("商号又は名称の変更", "Trade-name change"),
    "12": ("国内所在地の変更", "Domestic address change"),
    "13": ("国外所在地の変更", "Overseas address change"),
    "21": ("登記記録の閉鎖等", "Registry record closed"),
    "22": ("登記記録の復活等", "Registry record reinstated"),
    "71": ("吸収合併", "Absorption-type merger"),
    "72": ("吸収合併無効", "Merger invalidated"),
    "81": ("清算の結了等", "Liquidation completed"),
    "99": ("削除", "Deletion"),
}

KIND_CODES = {
    "101": ("国の機関", "National government body"),
    "201": ("地方公共団体", "Local government body"),
    "301": ("株式会社", "Kabushiki-kaisha (joint-stock company)"),
    "302": ("有限会社", "Yugen-kaisha (limited company)"),
    "303": ("合名会社", "General partnership company"),
    "304": ("合資会社", "Limited partnership company"),
    "305": ("合同会社", "Godo-kaisha (LLC)"),
    "399": ("その他の設立登記法人", "Other incorporated entity"),
    "401": ("外国会社等", "Foreign company etc."),
    "499": ("その他", "Other"),
}

# Column documentation for the Bet C schema sample. Values in the sample row are
# ILLUSTRATIVE, not a real record: the page's job is to show the shape.
NTA_SCHEMA = [
    ("corporate_number", "13桁の法人番号", "13-digit corporate number", "1234567890123"),
    ("process_code", "処理区分（新規/変更/閉鎖 …）", "Change type (new / change / closed ...)", "12"),
    ("correct_flag", "訂正フラグ（1 なら訂正）", "Correction flag (1 = correction)", "0"),
    ("update_date", "差分ファイルに載った日", "Date the diff file carried it", "2026-09-02"),
    ("change_date", "変更が生じた日", "Date the change took effect", "2026-04-01"),
    ("sequence_number", "同一変更日の連番", "Sequence within the same change date", "1"),
    ("name", "法人の商号・名称（法人のみ）", "Corporate name (corporations only)", "株式会社サンプル"),
    ("kind_code", "法人種別コード", "Entity-type code", "301"),
    ("prefecture", "本店所在地 都道府県", "Registered prefecture", "東京都"),
    ("city", "本店所在地 市区町村", "Registered municipality", "千代田区"),
    ("street_number", "本店所在地 丁目番地", "Registered street address", "霞が関1-1-1"),
    ("post_code", "郵便番号", "Postal code", "1000013"),
    ("close_date", "登記記録の閉鎖日", "Registry closure date", ""),
    ("close_cause", "閉鎖事由", "Closure cause", ""),
    ("successor_corporate_number", "承継先法人番号", "Successor corporate number", ""),
    ("assignment_date", "法人番号指定年月日", "Corporate-number assignment date", "2015-10-05"),
    ("latest_flag", "最新レコードか", "Whether this is the latest record", "1"),
    ("corporate_number_valid", "チェックディジット検証結果", "Check-digit verification result", "Y"),
    ("record_key", "corporate_number|change_date|sequence_number", "Primary key", "1234567890123|2026-04-01|1"),
    ("file_date", "取得した差分ファイルの日付", "Date of the diff file we fetched", "2026-09-02"),
    ("source_id / source_url / license / attribution", "出典・ライセンス（全レコードに付与）", "Provenance, carried on every record", "nta_diff / ... / 公共データ利用規約(第1.0版) / 出典：国税庁…"),
    ("retrieved_at / raw_hash", "取得時刻（UTC）と原本のハッシュ", "Retrieval time (UTC) and source hash", "2026-09-21T14:07:01Z / 377dd6…"),
]

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def e(s) -> str:
    """HTML-escape."""
    return html.escape("" if s is None else str(s), quote=True)


def yen(n) -> str:
    if n is None or n == "":
        return "—"
    return "¥{:,}".format(int(round(float(n))))


def num(n) -> str:
    if n is None or n == "":
        return "—"
    return "{:,}".format(int(n))


def pct(x, digits=1) -> str:
    return f"{x * 100:.{digits}f}%"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def t(lang: str, ja: str, en: str) -> str:
    return ja if lang == "ja" else en


# --------------------------------------------------------------------------
# Quantile estimation over pooled buckets
# --------------------------------------------------------------------------
#
# stats_v0.csv is aggregated at 年度 x 都道府県 x セクター. A sector x fiscal-year
# page pools every prefecture bucket of that sector and year, so the pooled
# median cannot be read off the file: only each bucket's five order statistics
# (min, Q1, median, Q3, max) and its count survive.
#
# The normalized award store (data/pportal/normalized/) still holds one row per
# award, so the pooled quantiles ARE computable exactly, and that is what the
# build does: `load_award_amounts()` reads the per-fiscal-year files and computes
# q1/median/q3 with the same inclusive method stats_v0.csv used per bucket.
#
# The estimator below is the fallback for when that store is not present (for
# example a checkout carrying only stats_v0.csv). It treats each bucket as a
# piecewise-linear CDF through its five order statistics, mixes the buckets by
# award count, and inverts the mixture. Measured against the exact FY2025
# figures it runs 1.5-17% high, because award amounts are right-skewed inside
# every quartile segment and linear interpolation does not know that. So when it
# is used, the page says so with a dagger and the JSON sets `estimated: true`.
# We never print an estimate as if it were measured.


def _bucket_cdf(points, x: float) -> float:
    if x < points[0][0]:
        return 0.0
    c = 0.0
    for i in range(len(points) - 1):
        v0, p0 = points[i]
        v1, p1 = points[i + 1]
        if x >= v1:
            c = p1
        elif x >= v0:
            if v1 > v0:
                return p0 + (p1 - p0) * (x - v0) / (v1 - v0)
            return p1
    return c


def pooled_quantile(buckets, p: float):
    """buckets: list of (weight, [min, q1, med, q3, max]). Returns integer yen."""
    total = sum(w for w, _ in buckets)
    if total <= 0:
        return None
    pts = [
        (w, [(v, q) for v, q in zip(vals, (0.0, 0.25, 0.5, 0.75, 1.0))])
        for w, vals in buckets
    ]
    lo = min(vals[0] for _, vals in buckets)
    hi = max(vals[4] for _, vals in buckets)
    if hi <= lo:
        return int(round(lo))

    def mixture(x: float) -> float:
        return sum(w * _bucket_cdf(pp, x) for w, pp in pts) / total

    for _ in range(80):
        mid = (lo + hi) / 2.0
        if mixture(mid) < p:
            lo = mid
        else:
            hi = mid
    return int(round((lo + hi) / 2.0))


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


def load_award_amounts():
    """Exact per-(fiscal_year, sector) award amounts from the normalized store.

    Returns {} when the store is absent, in which case the caller falls back to
    the mixture estimator and every page says its quantiles are estimated.
    """
    files = sorted(glob.glob(str(PPORTAL_NORMALIZED / "*.csv.gz")))
    if not files:
        return {}
    groups = collections.defaultdict(list)
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    amount = int(round(float(r["amount_jpy"])))
                except (KeyError, TypeError, ValueError):
                    continue
                groups[(int(r["fiscal_year"]), r["sector"])].append(amount)

    out = {}
    for key, amounts in groups.items():
        amounts.sort()
        n = len(amounts)
        if n >= 2:
            q1, q2, q3 = statistics.quantiles(amounts, n=4, method="inclusive")
        else:
            q1 = q2 = q3 = amounts[0]
        out[key] = {
            "n_awards": n,
            "amount_sum_jpy": sum(amounts),
            "amount_min_jpy": amounts[0],
            "amount_max_jpy": amounts[-1],
            "amount_q1_jpy": int(round(q1)),
            "amount_median_jpy": int(round(q2)),
            "amount_q3_jpy": int(round(q3)),
        }
    return out


# Filled by load_bet_a() from stats_v0.csv: the counts that must be identical
# wherever this site is built. The private record store may refine a quantile;
# it may never change a published count.
BET_A_PROVENANCE = {"records_parsed": 0, "records_normalized": 0, "retrieved_at": ""}


def load_bet_a():
    """Group stats_v0.csv into {(fiscal_year, sector): [prefecture buckets]}."""
    rows = []
    with PPORTAL_STATS.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    if rows:
        BET_A_PROVENANCE.update({
            "records_parsed": int(rows[0].get("records_parsed") or 0),
            "records_normalized": int(rows[0].get("records_normalized") or 0),
            "retrieved_at": rows[0].get("retrieved_at") or "",
        })

    suppressed_in_group = {}
    groups = collections.OrderedDict()
    for r in rows:
        key = (int(r["fiscal_year"]), r["sector"])
        suppressed_in_group[key] = int(r.get("n_awards_suppressed_in_group") or 0)
        groups.setdefault(key, []).append(
            {
                "prefecture": r["prefecture"],
                "prefecture_code": r["prefecture_code"],
                "n_awards": int(r["n_awards"]),
                "n_corporate": int(r["n_corporate"]),
                "n_masked_individual": int(r["n_masked_individual"]),
                "amount_min_jpy": int(r["amount_min_jpy"]),
                "amount_q1_jpy": int(r["amount_q1_jpy"]),
                "amount_median_jpy": int(r["amount_median_jpy"]),
                "amount_q3_jpy": int(r["amount_q3_jpy"]),
                "amount_max_jpy": int(r["amount_max_jpy"]),
                "amount_sum_jpy": int(r["amount_sum_jpy"]),
                "award_ratio_coverage": float(r["award_ratio_coverage"] or 0),
            }
        )

    exact = load_award_amounts()

    pages = []
    for (fy, sector), buckets in groups.items():
        buckets.sort(key=lambda b: (-b["n_awards"], b["prefecture_code"] or "zz"))
        n_buckets_awards = sum(b["n_awards"] for b in buckets)

        page = {
            "fiscal_year": fy,
            "sector": sector,
            "sector_slug": SECTORS[sector][0],
            "sector_en": SECTORS[sector][1],
            "buckets": buckets,
            "n_buckets": len(buckets),
            # what the prefecture table below adds up to, i.e. after suppression
            "n_awards_in_table": n_buckets_awards,
            "n_corporate": sum(b["n_corporate"] for b in buckets),
            "n_masked_individual": sum(b["n_masked_individual"] for b in buckets),
            "award_ratio_coverage": max(
                (b["award_ratio_coverage"] for b in buckets), default=0.0
            ),
        }

        # Pooled quantiles are the ONLY thing the private record store is
        # allowed to change: it holds one row per award, so q1/median/q3 are
        # computable exactly instead of estimated. Counts and sums always come
        # from the published table, so a build in CI and a build here produce
        # the same numbers (see the estimator note above).
        ex = exact.get((fy, sector))
        if ex:
            page.update(
                {
                    "amount_sum_jpy": sum(b["amount_sum_jpy"] for b in buckets),
                    "amount_min_jpy": min(b["amount_min_jpy"] for b in buckets),
                    "amount_max_jpy": max(b["amount_max_jpy"] for b in buckets),
                    "amount_q1_jpy": ex["amount_q1_jpy"],
                    "amount_median_jpy": ex["amount_median_jpy"],
                    "amount_q3_jpy": ex["amount_q3_jpy"],
                    "quantiles_exact": True,
                }
            )
        else:
            weighted = [
                (
                    b["n_awards"],
                    [
                        b["amount_min_jpy"],
                        b["amount_q1_jpy"],
                        b["amount_median_jpy"],
                        b["amount_q3_jpy"],
                        b["amount_max_jpy"],
                    ],
                )
                for b in buckets
            ]
            page.update(
                {
                    "amount_sum_jpy": sum(b["amount_sum_jpy"] for b in buckets),
                    "amount_min_jpy": min(b["amount_min_jpy"] for b in buckets),
                    "amount_max_jpy": max(b["amount_max_jpy"] for b in buckets),
                    "amount_q1_jpy": pooled_quantile(weighted, 0.25),
                    "amount_median_jpy": pooled_quantile(weighted, 0.50),
                    "amount_q3_jpy": pooled_quantile(weighted, 0.75),
                    "quantiles_exact": len(buckets) == 1,
                }
            )
        # Awards held back from the prefecture table by R6, from the published
        # table's own column rather than by differencing against a private store.
        page["n_awards_suppressed"] = suppressed_in_group.get((fy, sector), 0)
        page["n_awards"] = page["n_awards_in_table"] + page["n_awards_suppressed"]
        pages.append(page)

    pages.sort(key=lambda p: (p["sector_slug"], p["fiscal_year"]))
    return pages


def published_retrieved_at() -> str:
    """"Data as of" when the private collection manifest is not available.

    Falls back to the published procurement table's own `source_retrieved_at`,
    which the source CSV records and the builder copies through. It is a
    property of the data, not of the machine that built the site, so two
    checkouts of the same commit say the same thing.
    """
    js = PUBLISHED / "pportal" / "procurement-stats.json"
    if js.exists():
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
            return payload.get("source_retrieved_at") or ""
        except (ValueError, OSError):
            pass
    return ""


def load_bet_a_provenance(pages):
    """Provenance, and the two counts that are NOT the same number.

    Three counts, deliberately kept apart because they are not the same number:

      records_parsed      rows in the publisher's files (data/pportal/manifest.csv)
      records_normalized  rows surviving deduplication in data/pportal/normalized/
      records             awards these pages actually aggregate

    The last is the smallest, because a fiscal year x prefecture x sector bucket
    holding one or two masked individuals is dropped entirely (rule R6) so a
    rare sole proprietor cannot be re-identified by elimination. Public-facing
    text uses the aggregated count; the gap is stated, never smoothed over.
    """
    # All four numbers come from the published table (stats_v0.csv), so they do
    # not depend on whether the private record store is next to this checkout.
    parsed = BET_A_PROVENANCE["records_parsed"]
    normalized = BET_A_PROVENANCE["records_normalized"]
    retrieved = BET_A_PROVENANCE["retrieved_at"] or published_retrieved_at()
    aggregated = sum(p["n_awards"] for p in pages)

    files = 0
    if PPORTAL_MANIFEST.exists():
        with PPORTAL_MANIFEST.open(encoding="utf-8", newline="") as f:
            files = sum(1 for _ in csv.DictReader(f))

    return {
        "files": files,
        "records_parsed": parsed,
        "records_normalized": normalized,
        "records": aggregated,
        "deduplicated": parsed - normalized,
        # awards held back from the published prefecture breakdowns by rule R6
        "suppressed": sum(p["n_awards_suppressed"] for p in pages),
        "retrieved_at": retrieved,
    }


def load_bet_c():
    """Daily counts and code breakdowns for Bet C, from the published summary.

    `data/published/nta/summary.json` is the single authority for every Bet-C
    number on every public surface. Its per-day
    `records` field is the DEDUPLICATED count produced by the dedup rule;
    `records_raw` is the publisher's own row count, kept only so the difference
    can be explained. The Worker reads the same file, so the site, the RSS feed
    and the API cannot report three different totals again.

    Nothing record-level is read here, and nothing record-level is in that file.
    """
    if not NTA_SUMMARY.exists():
        raise SystemExit(
            f"missing {NTA_SUMMARY.relative_to(REPO).as_posix()}. Build it with: "
            "python crawlers/nta_diff/publish_summary.py"
        )
    summary = json.loads(NTA_SUMMARY.read_text(encoding="utf-8"))
    days_map = summary.get("days") or {}
    days = sorted(days_map)

    daily = {d: int(days_map[d].get("records") or 0) for d in days}
    daily_raw = {d: int(days_map[d].get("records_raw") or 0) for d in days}
    daily_process = {d: dict(days_map[d].get("process_codes") or {}) for d in days}
    coverage = summary.get("coverage") or {}

    return {
        "days": days,
        "daily": daily,
        "daily_raw": daily_raw,
        "daily_process": daily_process,
        "process": dict(sorted((summary.get("process_codes") or {}).items())),
        "kinds": dict(sorted((summary.get("kind_codes") or {}).items())),
        "prefectures": collections.Counter(summary.get("prefectures") or {}),
        "total": int(coverage.get("records") or sum(daily.values())),
        "total_raw": int(coverage.get("records_raw") or 0),
        "superseded": int(coverage.get("records_deduplicated_out") or 0),
        "n_files": int(coverage.get("days_with_data") or len(days)),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "retrieved_at": coverage.get("retrieved_at") or "",
        "bytes_raw": int(coverage.get("raw_bytes") or 0),
        "license": summary.get("license", ""),
        "attribution": summary.get("attribution", ""),
        "modification_notice": summary.get("modification_notice", ""),
        "count_rule": summary.get("count_rule", ""),
    }


# --------------------------------------------------------------------------
# Page shell
# --------------------------------------------------------------------------

CSS = """/* Deltakura - one stylesheet, no external assets, no webfonts, no trackers. */
:root{
  color-scheme: light dark;
  --bg:#ffffff; --bg-soft:#f6f7f9; --bg-card:#ffffff;
  --fg:#16191d; --fg-muted:#5b6470; --fg-faint:#848d99;
  --line:#e2e6ea; --line-strong:#c9d0d8;
  --accent:#1f5f8b; --accent-fg:#ffffff; --accent-soft:#e8f0f6;
  --warn-bg:#fdf6e3; --warn-line:#e6d5a8; --warn-fg:#6b551a;
  --bar:#4a7fa5;
  --radius:10px;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: system-ui, -apple-system, "Segoe UI", "Hiragino Kaku Gothic ProN", "Hiragino Sans",
          "Noto Sans JP", "Yu Gothic UI", Meiryo, sans-serif;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#101317; --bg-soft:#161a20; --bg-card:#171b21;
    --fg:#e6e9ed; --fg-muted:#a3acb8; --fg-faint:#7b8592;
    --line:#252b33; --line-strong:#39414b;
    --accent:#7fb2d4; --accent-fg:#0d1116; --accent-soft:#1a2630;
    --warn-bg:#241f12; --warn-line:#4a3f22; --warn-fg:#dcc98a;
    --bar:#6699bd;
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--fg);
  font-family:var(--sans); font-size:16px; line-height:1.7;
  font-feature-settings:"palt" 1;
}
.wrap{max-width:60rem; margin:0 auto; padding:0 16px}
a{color:var(--accent); text-underline-offset:2px}
a:hover{text-decoration-thickness:2px}

header.site{border-bottom:1px solid var(--line); background:var(--bg-soft)}
header.site .wrap{display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:center; padding-top:.7rem; padding-bottom:.7rem}
.brand{font-weight:700; letter-spacing:.02em; text-decoration:none; color:var(--fg); font-size:1.05rem}
.brand span{color:var(--fg-faint); font-weight:400; margin-left:.4rem; font-size:.85rem}
nav.site{display:flex; flex-wrap:wrap; gap:.15rem .9rem; margin-left:auto; font-size:.9rem}
nav.site a{color:var(--fg-muted); text-decoration:none}
nav.site a:hover,nav.site a[aria-current]{color:var(--fg); text-decoration:underline}
.lang{font-size:.85rem; border:1px solid var(--line-strong); border-radius:999px; padding:.1rem .6rem; text-decoration:none; color:var(--fg-muted)}

main{padding:1.5rem 0 3rem}
h1{font-size:1.65rem; line-height:1.35; margin:.2rem 0 .6rem; letter-spacing:.01em}
h2{font-size:1.2rem; margin:2.2rem 0 .6rem; padding-bottom:.25rem; border-bottom:1px solid var(--line)}
h3{font-size:1rem; margin:1.4rem 0 .4rem}
p{margin:.6rem 0}
.lede{font-size:1.05rem; color:var(--fg-muted); margin-bottom:1.2rem}
small,.small{font-size:.85rem}
.muted{color:var(--fg-muted)}
.faint{color:var(--fg-faint)}
code,kbd{font-family:var(--mono); font-size:.88em; background:var(--bg-soft); border:1px solid var(--line); border-radius:4px; padding:.05em .35em}
pre{background:var(--bg-soft); border:1px solid var(--line); border-radius:var(--radius); padding:.8rem 1rem; overflow-x:auto; font-family:var(--mono); font-size:.85rem; line-height:1.55}
pre code{background:none; border:0; padding:0}
hr{border:0; border-top:1px solid var(--line); margin:2rem 0}

.status{display:flex; flex-wrap:wrap; gap:.35rem .9rem; align-items:baseline;
  font-size:.85rem; color:var(--fg-muted); background:var(--bg-soft);
  border:1px solid var(--line); border-radius:var(--radius); padding:.55rem .85rem; margin:1rem 0}
.status b{color:var(--fg); font-weight:600}
.dot{display:inline-block; width:.5rem; height:.5rem; border-radius:50%; background:var(--bar); margin-right:.35rem; vertical-align:baseline}

.grid{display:grid; gap:1rem; grid-template-columns:1fr}
@media(min-width:44rem){.grid.c3{grid-template-columns:repeat(3,1fr)} .grid.c2{grid-template-columns:repeat(2,1fr)}}
.card{border:1px solid var(--line); border-radius:var(--radius); background:var(--bg-card); padding:1rem 1.1rem; display:flex; flex-direction:column}
.card h3{margin-top:0}
.card p{font-size:.93rem}
.card .spacer{flex:1}
.tag{display:inline-block; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase;
  border:1px solid var(--line-strong); border-radius:999px; padding:.05rem .55rem; color:var(--fg-muted); margin-bottom:.5rem}
.tag.live{border-color:var(--accent); color:var(--accent); background:var(--accent-soft)}
.tag.soon{border-color:var(--warn-line); color:var(--warn-fg); background:var(--warn-bg)}

.stats{display:grid; gap:.6rem; grid-template-columns:repeat(2,1fr); margin:1.2rem 0}
@media(min-width:44rem){.stats{grid-template-columns:repeat(4,1fr)}}
.stat{border:1px solid var(--line); border-radius:var(--radius); padding:.7rem .8rem; background:var(--bg-soft)}
.stat .k{font-size:.78rem; color:var(--fg-muted); display:block; line-height:1.4}
.stat .v{font-size:1.25rem; font-weight:650; font-variant-numeric:tabular-nums; letter-spacing:-.01em; display:block; margin-top:.15rem}
.stat .u{font-size:.78rem; color:var(--fg-faint); display:block}

.tablewrap{overflow-x:auto; border:1px solid var(--line); border-radius:var(--radius); margin:1rem 0}
table{border-collapse:collapse; width:100%; font-size:.88rem}
caption{text-align:left; padding:.7rem .85rem; color:var(--fg-muted); font-size:.85rem; border-bottom:1px solid var(--line)}
th,td{padding:.45rem .7rem; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap}
thead th{background:var(--bg-soft); font-weight:600; font-size:.82rem; color:var(--fg-muted); position:sticky; top:0}
tbody tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right; font-variant-numeric:tabular-nums}
tbody tr:hover td{background:var(--bg-soft)}

.note{border:1px solid var(--warn-line); background:var(--warn-bg); color:var(--warn-fg);
  border-radius:var(--radius); padding:.8rem 1rem; margin:1.1rem 0; font-size:.9rem}
.note p{margin:.35rem 0}
.note strong{color:inherit}
.attrib{font-size:.82rem; color:var(--fg-muted); border-left:3px solid var(--line-strong);
  padding:.3rem 0 .3rem .8rem; margin:1rem 0; word-break:break-all}

button.intent{
  font:inherit; font-size:.9rem; cursor:pointer; border:1px solid var(--accent);
  background:var(--accent); color:var(--accent-fg); border-radius:var(--radius);
  padding:.45rem 1rem; margin-top:.6rem; align-self:flex-start;
}
button.intent:hover{filter:brightness(1.08)}
button.intent[disabled]{background:var(--accent-soft); color:var(--accent); cursor:default; filter:none}
button.intent[data-variant="quiet"]{background:transparent; color:var(--accent)}
.intent-why{font-size:.8rem; color:var(--fg-faint); margin-top:.4rem}

figure.chart{margin:1.2rem 0; border:1px solid var(--line); border-radius:var(--radius); padding:1rem .6rem .4rem; background:var(--bg-card)}
figure.chart svg{display:block; width:100%; height:auto}
figure.chart figcaption{font-size:.82rem; color:var(--fg-muted); padding:.5rem .6rem 0}
.axis{stroke:var(--line-strong); stroke-width:1}
.tick{fill:var(--fg-faint); font-size:10px; font-family:var(--sans)}
.bar{fill:var(--bar)}
.bar:hover{fill:var(--accent)}
.baseline{stroke:var(--line); stroke-width:1; stroke-dasharray:2 3}

ul.clean{list-style:none; padding:0; margin:.6rem 0}
ul.clean li{padding:.28rem 0 .28rem 1.1rem; position:relative; font-size:.93rem}
ul.clean li::before{content:"–"; position:absolute; left:0; color:var(--fg-faint)}
ul.no li::before{content:"\\00d7"; color:var(--warn-fg)}
ul.yes li::before{content:"\\2713"; color:var(--accent)}

.yearnav{display:flex; flex-wrap:wrap; gap:.3rem; margin:.6rem 0 1.2rem}
.yearnav a,.yearnav span{font-size:.85rem; font-variant-numeric:tabular-nums; border:1px solid var(--line);
  border-radius:6px; padding:.15rem .5rem; text-decoration:none; color:var(--fg-muted); background:var(--bg-card)}
.yearnav a:hover{border-color:var(--accent); color:var(--accent)}
.yearnav span[aria-current]{background:var(--accent); color:var(--accent-fg); border-color:var(--accent)}

footer.site{border-top:1px solid var(--line); background:var(--bg-soft); padding:1.5rem 0 2.5rem; font-size:.85rem; color:var(--fg-muted)}
footer.site a{color:var(--fg-muted)}
footer.site .cols{display:grid; gap:1rem; grid-template-columns:1fr}
@media(min-width:44rem){footer.site .cols{grid-template-columns:2fr 1fr 1fr}}
footer.site h4{margin:0 0 .3rem; font-size:.82rem; color:var(--fg); letter-spacing:.04em; text-transform:uppercase}
footer.site ul{list-style:none; margin:0; padding:0}
footer.site li{padding:.12rem 0}
.skip{position:absolute; left:-9999px}
.skip:focus{position:static; display:inline-block; padding:.4rem .8rem; background:var(--accent); color:var(--accent-fg)}
"""

JS = """/* Deltakura intent button. No cookies, no email, no third party.

   It records that someone asked to be notified about a product, and that a page
   carrying such a button was seen. Nothing else.

   CONTRACT with the Worker (api/src/routes/intent.ts). The POST body is exactly

       {"product": <allowlisted id>, "kind": "click" | "view", "client_id": <str>}

   `product` must be one of the Worker's allowlisted ids or the request is
   refused with 400 unknown_product, so every id in the markup is validated at
   build time against INTENT_PRODUCTS in site/build.py, which is asserted
   against the Worker's own list by api/test/intent-contract.test.ts.

   `kind` is why the Gate-1 metric exists at all: intent_rate_14d is
   clicks / views, so a page that never reports a view has no denominator. One
   view per product per page load; the Worker de-duplicates per client and day.

   `client_id` is a random string in localStorage. It is not an identifier of a
   person, it never leaves this origin except as an opaque string in this POST,
   and the Worker stores only a salted hash of it. If the endpoint is not
   configured yet, the button still confirms locally and posts nothing. */
(function () {
  "use strict";
  var CFG = window.DELTAKURA || {};
  var KEY = "deltakura.cid";
  var SENT = "deltakura.intent.";

  function clientId() {
    try {
      var v = localStorage.getItem(KEY);
      if (!v) {
        v = (Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)).slice(0, 22);
        localStorage.setItem(KEY, v);
      }
      return v;
    } catch (err) { return "no-storage-client-id"; }
  }

  function post(product, kind) {
    if (!CFG.intentEndpoint) return;
    try {
      fetch(CFG.intentEndpoint, {
        method: "POST",
        mode: "cors",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product: product,
          kind: kind,
          client_id: clientId()
        })
      }).catch(function () { /* counting is best-effort; never block the reader */ });
    } catch (err) { /* ignore */ }
  }

  function already(product) {
    try { return localStorage.getItem(SENT + product) === "1"; } catch (err) { return false; }
  }

  function remember(product) {
    try { localStorage.setItem(SENT + product, "1"); } catch (err) { /* private mode */ }
  }

  function settle(btn) {
    btn.disabled = true;
    btn.textContent = btn.getAttribute("data-done") || "Recorded";
  }

  function onClick(ev) {
    var btn = ev.currentTarget;
    var product = btn.getAttribute("data-intent");
    settle(btn);
    if (already(product)) return;
    remember(product);
    post(product, "click");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll("button.intent[data-intent]");
    var viewed = {};
    for (var i = 0; i < buttons.length; i++) {
      var product = buttons[i].getAttribute("data-intent");
      if (!viewed[product]) {
        viewed[product] = true;
        post(product, "view");
      }
      if (already(product)) settle(buttons[i]);
      buttons[i].addEventListener("click", onClick);
    }
  });
})();
"""


def nav_items(lang: str):
    p = f"/{lang}/"
    return [
        (p, t(lang, "ホーム", "Home")),
        (p + "bet-a/", t(lang, "落札実績", "Tender awards")),
        (p + "bet-c/", t(lang, "法人番号 差分", "Registry diff")),
        (p + "pricing.html", t(lang, "料金", "Pricing")),
        (p + "privacy.html", t(lang, "プライバシー・方法論", "Privacy & method")),
    ]


def render_page(
    *,
    lang: str,
    path: str,
    title: str,
    desc_ja: str,
    desc_en: str,
    body: str,
    alt_path: str,
    jsonld=None,
    is_data_page: bool = False,
):
    """Return (relative output path, html string)."""
    canonical = BASE_URL + path
    alt = BASE_URL + alt_path
    desc = desc_ja if lang == "ja" else desc_en
    other = "en" if lang == "ja" else "ja"

    if alt_path == path:
        # A page with no counterpart in the other language (404, /data/) links
        # only to itself; emitting a second hreflang for the same URL is a lie.
        alternates = f'<link rel="alternate" hreflang="{lang}" href="{e(canonical)}">'
    else:
        alternates = (
            f'<link rel="alternate" hreflang="{lang}" href="{e(canonical)}">\n'
            f'<link rel="alternate" hreflang="{other}" href="{e(alt)}">'
        )

    blocks = ""
    for obj in jsonld or []:
        blocks += (
            '<script type="application/ld+json">'
            + json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            + "</script>\n"
        )

    nav = "".join(
        '<a href="{}"{}>{}</a>'.format(
            e(href), ' aria-current="page"' if href == path else "", e(label)
        )
        for href, label in nav_items(lang)
    )

    footer_note_ja = (
        "Deltakura は公開データの非公式アーカイブです。公式データではありません。"
        "収集・正規化の過程で誤りが生じ得ます。重要な判断の前に必ず出典の原本をご確認ください。"
    )
    footer_note_en = (
        "Deltakura is an unofficial archive of public data. It is not an official source; "
        "collection and parsing can introduce errors. Check the original source before relying on a number."
    )

    doc = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta name="description:ja" content="{e(desc_ja)}">
<meta name="description:en" content="{e(desc_en)}">
<link rel="canonical" href="{e(canonical)}">
{alternates}
<link rel="alternate" hreflang="x-default" href="{e(BASE_URL)}/">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:locale" content="{'ja_JP' if lang == 'ja' else 'en_US'}">
<meta name="robots" content="index,follow,max-snippet:-1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<link rel="stylesheet" href="/assets/style.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M8 2l6 11H2z' fill='%231f5f8b'/%3E%3C/svg%3E">
<link rel="alternate" type="application/rss+xml" title="Deltakura Registry Diff (weekly)" href="/feeds/nta-diff.xml">
{blocks}</head>
<body>
<a class="skip" href="#main">{e(t(lang, "本文へスキップ", "Skip to content"))}</a>
<header class="site"><div class="wrap">
<a class="brand" href="/{lang}/">{BRAND}<span>{BRAND_JA}</span></a>
<nav class="site" aria-label="{e(t(lang, 'メインナビゲーション', 'Main navigation'))}">{nav}</nav>
<a class="lang" href="{e(alt_path)}" hreflang="{other}" rel="alternate">{"English" if lang == "ja" else "日本語"}</a>
</div></header>
<main id="main"><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap"><div class="cols">
<div>
<h4>{BRAND}</h4>
<p class="small">{e(t(lang, footer_note_ja, footer_note_en))}</p>
<p class="small">{e(t(lang, "コード: MIT / 当社が生成した集計データ: CC BY 4.0。元データのライセンスは各ページの出典表記をご確認ください。", "Code: MIT. Our derived aggregates: CC BY 4.0. Upstream licences are named in each page's attribution."))}</p>
</div>
<div>
<h4>{e(t(lang, "データ", "Data"))}</h4>
<ul>
<li><a href="/{lang}/bet-a/">{e(t(lang, "落札実績 統計", "Tender award statistics"))}</a></li>
<li><a href="/{lang}/bet-c/">{e(t(lang, "法人番号 差分アーカイブ", "Registry diff archive"))}</a></li>
<li><a href="/feeds/nta-diff.xml">{e(t(lang, "RSS フィード", "RSS feed"))}</a></li>
<li><a href="/data/">{e(t(lang, "JSON エンドポイント", "JSON endpoints"))}</a></li>
</ul>
</div>
<div>
<h4>{e(t(lang, "運営", "Project"))}</h4>
<ul>
<li><a href="{e(GITHUB_ORG)}" rel="noopener">GitHub</a></li>
<li><a href="/{lang}/privacy.html">{e(t(lang, "プライバシー・方法論", "Privacy & methodology"))}</a></li>
<li><a href="/{lang}/pricing.html">{e(t(lang, "料金（準備中）", "Pricing (coming soon)"))}</a></li>
<li><a href="{e(GITHUB_ISSUES)}" rel="noopener">{e(t(lang, "お問い合わせ（GitHub Issues のみ）", "Contact (GitHub Issues only)"))}</a></li>
</ul>
</div>
</div></div></footer>
<script src="/assets/config.js"></script>
<script src="/assets/intent.js" defer></script>
</body>
</html>
"""
    return doc


# --------------------------------------------------------------------------
# Reusable fragments
# --------------------------------------------------------------------------


def intent_button(lang: str, product: str, label_ja: str, label_en: str, quiet=False):
    # Fail the build rather than ship a button the Worker will refuse with
    # 400 unknown_product. See INTENT_PRODUCTS.
    if product not in INTENT_PRODUCTS:
        raise SystemExit(
            f"intent_button: {product!r} is not in the Worker's allowlist. "
            f"Allowed: {', '.join(INTENT_PRODUCTS)}"
        )
    done = t(lang, "記録しました", "Recorded")
    why_ja = (
        "クリック数のみを記録します。メールアドレスは収集しません（このサイトに入力欄はありません）。"
        "更新は RSS / JSON フィードと MCP サーバーでお届けします。"
    )
    why_en = (
        "This records a click and nothing else. We collect no email address — there is no "
        "input field on this site. Updates arrive through the RSS/JSON feeds and the MCP server."
    )
    return (
        '<button class="intent" type="button" data-intent="{p}" data-done="{d}"{q}>{l}</button>'
        '<p class="intent-why">{w}</p>'
    ).format(
        p=e(product),
        d=e(done),
        q=' data-variant="quiet"' if quiet else "",
        l=e(t(lang, label_ja, label_en)),
        w=e(t(lang, why_ja, why_en)),
    )


def status_line(lang: str, bet_a_prov, bet_c, built_at: str):
    return (
        '<p class="status"><span><span class="dot"></span><b>{lbl}</b></span>'
        "<span>{a_lbl}: <b>{a}</b></span>"
        "<span>{c_lbl}: <b>{c}</b></span>"
        "<span>{b_lbl}: <b>{b}</b></span></p>"
    ).format(
        lbl=e(t(lang, "データ最終更新", "Data last updated")),
        a_lbl=e(t(lang, "落札実績", "Tender awards")),
        a=e(bet_a_prov["retrieved_at"][:10]),
        c_lbl=e(t(lang, "法人番号 差分", "Registry diff")),
        c=e(bet_c["last_day"] or "—"),
        b_lbl=e(t(lang, "ページ生成", "Site built")),
        b=e(built_at[:10]),
    )


def source_notices(lang: str):
    """The two upstream sources, one line each, carrying all three parts.

    Source indication + licence name + modification notice. Both upstream
    licences require all three, so no surface that shows a figure derived
    from them may carry fewer. The data pages spell the same three parts out
    in their own words; every other surface uses these lines, and check()
    fails the build on any generated file that shows a headline figure or an
    attribution string without them.
    """
    return [
        PPORTAL_ATTRIB
        + t(
            lang,
            "（政府標準利用規約 第2.0版。出典データを加工して利用しています：解析・重複排除・匿名化のうえ件数に集計。）",
            " (政府標準利用規約 v2.0; used in modified form: parsed, deduplicated,"
            " anonymised and aggregated to counts by Deltakura.)",
        ),
        NTA_ATTRIB
        + t(
            lang,
            "（公共データ利用規約 第1.0版。正規化・重複排除・集計の加工をしています。）",
            " (公共データ利用規約 v1.0; used in modified form: normalised,"
            " deduplicated and aggregated to counts by Deltakura.)",
        ),
    ]


def attribution_block(lang: str, lines):
    head = t(lang, "出典表記", "Attribution")
    items = "".join(f"<div>{e(x)}</div>" for x in lines)
    return f'<div class="attrib"><strong>{e(head)}</strong>{items}</div>'


def table(caption: str, headers, rows, aligns=None):
    aligns = aligns or [""] * len(headers)
    th = "".join(
        '<th{}>{}</th>'.format(' class="n" scope="col"' if a == "n" else ' scope="col"', h)
        for h, a in zip(headers, aligns)
    )
    body = ""
    for r in rows:
        tds = "".join(
            '<td{}>{}</td>'.format(' class="n"' if a == "n" else "", c)
            for c, a in zip(r, aligns)
        )
        body += f"<tr>{tds}</tr>"
    cap = f"<caption>{caption}</caption>" if caption else ""
    return (
        f'<div class="tablewrap"><table>{cap}<thead><tr>{th}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def stat_grid(items):
    cells = "".join(
        '<div class="stat"><span class="k">{k}</span><span class="v">{v}</span>'
        '<span class="u">{u}</span></div>'.format(k=e(k), v=e(v), u=e(u))
        for k, v, u in items
    )
    return f'<div class="stats">{cells}</div>'


# --------------------------------------------------------------------------
# SVG bar chart (no JS, no chart library)
# --------------------------------------------------------------------------


def bar_chart_svg(days, values, lang, height=190):
    """Daily counts as a bar chart. Sized in a viewBox so it scales to any width."""
    n = len(days)
    if n == 0:
        return ""
    W, H = 720, height
    pad_l, pad_r, pad_t, pad_b = 44, 8, 12, 34
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    vmax = max(values)
    # round the axis top up to a nice number
    step = 10 ** (len(str(int(vmax))) - 1)
    top = int((vmax // step + 1) * step)
    slot = plot_w / n
    bw = max(2.0, slot * 0.72)

    bars = []
    for i, (d, v) in enumerate(zip(days, values)):
        h = plot_h * v / top
        x = pad_l + i * slot + (slot - bw) / 2
        y = pad_t + plot_h - h
        label = t(lang, f"{d}: {v:,} 件", f"{d}: {v:,} records")
        bars.append(
            f'<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{bw:.2f}" height="{h:.2f}" '
            f'rx="1"><title>{e(label)}</title></rect>'
        )

    ticks = []
    for frac in (0, 0.5, 1.0):
        v = top * frac
        y = pad_t + plot_h - plot_h * frac
        cls = "axis" if frac == 0 else "baseline"
        ticks.append(f'<line class="{cls}" x1="{pad_l}" y1="{y:.2f}" x2="{W - pad_r}" y2="{y:.2f}"/>')
        ticks.append(
            f'<text class="tick" x="{pad_l - 6}" y="{y + 3.5:.2f}" text-anchor="end">{int(v):,}</text>'
        )

    # x labels: first day of each month plus the last day
    xlabels = []
    seen_month = set()
    for i, d in enumerate(days):
        month = d[:7]
        show = month not in seen_month or i == n - 1
        if month not in seen_month:
            seen_month.add(month)
        if show:
            x = pad_l + i * slot + slot / 2
            anchor = "end" if i == n - 1 else "middle"
            xlabels.append(
                f'<text class="tick" x="{x:.2f}" y="{H - pad_b + 15:.2f}" '
                f'text-anchor="{anchor}">{e(d[5:])}</text>'
            )

    title = t(
        lang,
        f"{days[0]} から {days[-1]} までの1日あたりの差分レコード数",
        f"Registry diff records per publication day, {days[0]} to {days[-1]}",
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="xMidYMid meet" '
        f'aria-label="{e(title)}"><title>{e(title)}</title>'
        + "".join(ticks)
        + "".join(bars)
        + "".join(xlabels)
        + f'<text class="tick" x="{pad_l - 6}" y="{pad_t - 2}" text-anchor="end">'
        + e(t(lang, "件", "recs"))
        + "</text></svg>"
    )


# --------------------------------------------------------------------------
# Page builders
# --------------------------------------------------------------------------


def build_home(lang, bet_a_pages, bet_a_prov, bet_c, built_at):
    path = f"/{lang}/"
    alt = "/en/" if lang == "ja" else "/ja/"
    fy_max = max(p["fiscal_year"] for p in bet_a_pages)
    fy_min = min(p["fiscal_year"] for p in bet_a_pages)
    n_pages = len(bet_a_pages)

    title = t(
        lang,
        "Deltakura（デルタ蔵）— 日本の公開データの履歴アーカイブ",
        "Deltakura — an open-methodology archive of Japanese public-data histories",
    )
    desc_ja = (
        "匿名運営・方法論公開の公開データアーカイブ。国の落札実績統計（FY2013–FY2026）と"
        "国税庁 法人番号の日次差分を、出典とライセンスを明記して蓄積しています。"
        "メールアドレスは一切集めません。更新は RSS / JSON / MCP で配信します。"
    )
    desc_en = (
        "An anonymous-by-design, open-methodology archive of Japanese public-data histories: "
        "national tender-award statistics (FY2013-FY2026) and the daily corporate-number registry "
        "diff, each carrying its source and licence. No email is ever collected; updates ship as "
        "RSS, JSON and MCP."
    )

    lede_ja = (
        "国や公的機関が公開するデータの<strong>履歴</strong>を保存する、小さな独立プロジェクトです。"
        "公開された瞬間のスナップショットは誰でも取れますが、消えた後の履歴は取り戻せません。"
        "そこだけを、方法を全部公開したうえで積み上げています。"
    )
    lede_en = (
        "A small independent project that keeps the <strong>histories</strong> of Japanese public "
        "data. Anyone can take today's snapshot; nobody can go back for the part the publisher has "
        "already deleted. That gap is the whole product, and the method behind it is public."
    )

    products = []

    # --- Bet A
    products.append(
        '<div class="card">'
        + '<span class="tag live">{}</span>'.format(e(t(lang, "公開中", "Live")))
        + "<h3>{}</h3>".format(
            e(t(lang, "落札実績 統計（国の調達）", "Tender award statistics (national procurement)"))
        )
        + "<p>{}</p>".format(
            e(
                t(
                    lang,
                    f"調達ポータルの落札実績オープンデータから作った、府省セクター × 年度の統計 {n_pages} ページ。"
                    f"FY{fy_min}–FY{fy_max}、{bet_a_prov['records']:,} 件の落札を集計しています。"
                    "落札者名の一覧は作りません（統計のみ）。",
                    f"{n_pages} statistics pages, one per purchasing sector and fiscal year, built from the "
                    f"調達ポータル open award dataset: FY{fy_min}-FY{fy_max}, {bet_a_prov['records']:,} awards. "
                    "Statistics only — no winner directory is built.",
                )
            )
        )
        + '<div class="spacer"></div>'
        + '<p class="small"><a href="/{}/bet-a/">{}</a></p>'.format(
            lang, e(t(lang, "統計を見る →", "Browse the statistics →"))
        )
        + intent_button(lang, "bet_a_report", "更新を受け取る", "Notify me")
        + "</div>"
    )

    # --- Bet C
    products.append(
        '<div class="card">'
        + '<span class="tag live">{}</span>'.format(e(t(lang, "公開中", "Live")))
        + "<h3>{}</h3>".format(
            e(t(lang, "法人番号 差分アーカイブ", "Corporate registry diff archive"))
        )
        + "<p>{}</p>".format(
            e(
                t(
                    lang,
                    f"国税庁が 40 日で消す日次差分ファイルを、毎晩1回だけ取得して保存しています。"
                    f"現在 {bet_c['n_files']} 日分・{bet_c['total']:,} レコード。法人のみで、個人の氏名は扱いません。",
                    f"The National Tax Agency keeps its daily corporate-registry diff files for 40 days. "
                    f"We fetch one file a night and keep them. {bet_c['n_files']} days and "
                    f"{bet_c['total']:,} records so far. Corporations only — never an individual's name.",
                )
            )
        )
        + '<div class="spacer"></div>'
        + '<p class="small"><a href="/{}/bet-c/">{}</a> · <a href="/feeds/nta-diff.xml">RSS</a></p>'.format(
            lang, e(t(lang, "アーカイブを見る →", "Browse the archive →"))
        )
        + intent_button(lang, "bet_c_registry_diff", "更新を受け取る", "Notify me")
        + "</div>"
    )

    # --- Bet B placeholder. The sources ARE cleared (reuse_allowed=Y in the
    #     clearance matrix shipped with the collectors); what holds publication
    #     back is the separate go-live approval, enforced by BET_B_LIVE=false.
    #     Say that, because the matrix is in the same repository as this page.
    products.append(
        '<div class="card">'
        + '<span class="tag soon">{}</span>'.format(e(t(lang, "準備中", "Not published")))
        + "<h3>{}</h3>".format(
            e(t(lang, "日本 採用開始インデックス", "Japan hiring first-seen index"))
        )
        + "<p>{}</p>".format(
            e(
                t(
                    lang,
                    "「どの企業が、いつ、日本で採用を始めたか」の初出日インデックス。"
                    "取得元の利用条件は確認済みで、条件付きで再利用できることまでは分かっています。"
                    "公開そのものが別途の承認事項のため、データは1件も公開していません。",
                    "A first-seen index of which companies started hiring in Japan and when. "
                    "The sources' reuse terms have been reviewed and permit reuse under conditions. "
                    "Publication is a separate approval and has not been given, so not one record "
                    "is published.",
                )
            )
        )
        + '<div class="spacer"></div>'
        + '<p class="small faint">{}</p>'.format(
            e(
                t(
                    lang,
                    "状態: 利用条件は確認済み・公開は承認待ち（公開データなし）",
                    "Status: source terms reviewed; publication pending approval, no data published",
                )
            )
        )
        + intent_button(lang, "bet_b_watchlist", "公開されたら知りたい", "Tell me when it opens")
        + "</div>"
    )

    not_sold = t(
        lang,
        """<ul class="clean no">
<li>個人（個人事業主を含む）の氏名・屋号の一覧。取り込み時点で匿名化しており、そもそも保有していません。</li>
<li>落札者ディレクトリ。法人であっても、氏名で検索できる形のものは作りません。</li>
<li>メールアドレスの収集、メール配信、営業メール。このサイトに入力欄はありません。</li>
<li>「この金額で入札すべき」といった助言。統計は出しますが、税務・法務・行政手続の助言は行いません。</li>
<li>公式データとしての保証。ここは非公式アーカイブです。</li>
<li>クローラーのコード。MIT で公開しており、売り物ではありません。</li>
</ul>""",
        """<ul class="clean no">
<li>A list of individuals, including sole proprietors. They are masked at ingestion, so we do not hold one.</li>
<li>A winner directory. Not even for corporations, if it would be searchable by name.</li>
<li>Email addresses, newsletters or outbound mail. There is no input field on this site.</li>
<li>Advice such as "you should bid X". We publish statistics, not tax, legal or administrative advice.</li>
<li>Any warranty that this is official data. It is an unofficial archive.</li>
<li>The crawler code — it is MIT-licensed and public, not a product.</li>
</ul>""",
    )

    sold = t(
        lang,
        """<ul class="clean yes">
<li>消えた後の履歴。国税庁が 40 日で削除する差分を、削除後も参照できる形で保持します。</li>
<li>動き続ける処理。毎晩の取得・正規化・出典付与を、利用者が運用せずに済む形にしています。</li>
<li>検証できる方法論。収集コードも正規化済みデータも公開リポジトリにあります。</li>
<li>機械向けの入口。RSS / JSON エンドポイント / MCP サーバーで、人間にもエージェントにも同じデータを返します。</li>
</ul>""",
        """<ul class="clean yes">
<li>History after deletion: the diffs the National Tax Agency removes after 40 days, still readable here.</li>
<li>A process that keeps running: nightly collection, normalisation and attribution that you never operate.</li>
<li>A method you can check: the collector code and the normalised data are both in public repositories.</li>
<li>A machine-facing door: RSS, JSON endpoints and an MCP server return the same data to people and to agents.</li>
</ul>""",
    )

    channels = t(
        lang,
        f"""<p>更新のお知らせに<strong>メールアドレスは使いません</strong>。特定電子メール法は広告メールに送信者の実名と住所の表示を求めますが、この事業は運営者の身元を公開しない設計なので、メールという手段自体を持ちません。代わりに次の経路があります。</p>
<ul class="clean">
<li><a href="/feeds/nta-diff.xml">RSS フィード</a> — 法人番号 差分の週次サマリ。リーダーに登録するだけです。</li>
<li><a href="/data/">JSON エンドポイント</a> — 各統計ページに対応する機械可読ファイル。</li>
<li>MCP サーバー / npm パッケージ — 公開後、<a href="{GITHUB_ORG}" rel="noopener">GitHub</a> から辿れます（準備中）。</li>
<li>各製品の「更新を受け取る」ボタン — クリック数だけを記録します。連絡先は取得しません。</li>
</ul>""",
        f"""<p>We do not use email for updates, ever. Japan's 特定電子メール法 requires a real sender name and postal address on advertising email, and this project does not publish an operator identity — so the channel simply does not exist here. Instead:</p>
<ul class="clean">
<li><a href="/feeds/nta-diff.xml">RSS feed</a> — weekly summary of the registry diff. Point a reader at it.</li>
<li><a href="/data/">JSON endpoints</a> — a machine-readable file behind every statistics page.</li>
<li>MCP server and npm package — linked from <a href="{GITHUB_ORG}" rel="noopener">GitHub</a> once published.</li>
<li>The "Notify me" button on each product — it records a click and asks for no contact detail.</li>
</ul>""",
    )

    body = f"""
<h1>{e(t(lang, "消える前の日本の公開データを、方法ごと保存する", "Keeping Japanese public data before it disappears — method included"))}</h1>
<p class="lede">{t(lang, lede_ja, lede_en)}</p>
{status_line(lang, bet_a_prov, bet_c, built_at)}

<h2>{e(t(lang, "3つのデータセット", "Three datasets"))}</h2>
<div class="grid c3">{''.join(products)}</div>

<h2>{e(t(lang, "売っているもの / 売っていないもの", "What we sell, and what we do not"))}</h2>
<div class="grid c2">
<div class="card"><h3>{e(t(lang, "提供するもの", "What is here"))}</h3>{sold}</div>
<div class="card"><h3>{e(t(lang, "提供しないもの", "What we do NOT sell"))}</h3>{not_sold}</div>
</div>

<h2>{e(t(lang, "更新の受け取り方（メールは使いません）", "How updates reach you (never by email)"))}</h2>
{channels}

<h2>{e(t(lang, "誰が運営しているのか", "Who runs this"))}</h2>
<p>{e(t(lang, "Deltakura（デルタ蔵）というブランド名で運営している小規模なデータプロジェクトです。運営者個人の情報は公開していません。その代わり、検証できるものを全部公開しています。収集コード、正規化済みデータ、このサイトの生成スクリプト、匿名化ルール、出典とライセンス。判断材料は身元ではなく、方法だと考えています。", "Deltakura is a small independent data project. We do not publish personal details about the operator. What we publish instead is everything you would need to check the work: the collector code, the normalised data, the script that generates this site, the anonymisation rules, and the source and licence of every number. The method is the thing to judge, not the identity."))}</p>
<p><a href="{e(GITHUB_ORG)}" rel="noopener">{e(t(lang, "GitHub でコードとデータを見る", "See the code and data on GitHub"))}</a> · <a href="/{lang}/privacy.html">{e(t(lang, "プライバシーと方法論", "Privacy and methodology"))}</a> · <a href="{e(GITHUB_ISSUES)}" rel="noopener">{e(t(lang, "連絡先は GitHub Issues のみ", "Contact: GitHub Issues only"))}</a></p>

{attribution_block(lang, source_notices(lang))}
"""

    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": BRAND,
            "alternateName": BRAND_JA,
            "url": BASE_URL + "/",
            "inLanguage": ["ja", "en"],
            "description": desc_ja if lang == "ja" else desc_en,
            "publisher": {
                "@type": "Organization",
                "name": BRAND,
                "url": GITHUB_ORG,
                "description": t(
                    lang,
                    "日本の公開データの履歴を、方法論を公開したまま匿名で運営するアーカイブ。",
                    "An anonymous-by-design, open-methodology archive of Japanese public-data histories.",
                ),
            },
        }
    ]
    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
        jsonld=jsonld,
    )


def bet_a_page_path(lang, slug, fy):
    return f"/{lang}/bet-a/{slug}-fy{fy}.html"


def bet_a_json_path(slug, fy):
    return f"/data/bet-a/{slug}-fy{fy}.json"


BET_A_CAVEATS = {
    "ja": [
        (
            "予定価格が公表されていないため、落札率は算出できません。",
            "この出典（国の調達ポータル落札実績）は8列しかなく、予定価格はその中にありません。"
            "落札率・参考価格を名乗る数字はこのデータからは作れません。",
        ),
        (
            "都道府県は「落札者の登記上の所在地」であり、実際に業務が行われた場所ではありません。",
            "元データに所在地の列は存在しません。法人番号を国税庁の登記情報に突き合わせて得た本店所在地です。"
            "東京本社の企業が沖縄の案件を落札すれば東京都に計上されます。"
            "この軸が答えるのは「どこの企業が国の契約を取っているか」です。",
        ),
        (
            "セクターは「どの府省が買ったか」であり、業種ではありません。",
            "元データに業種コードも商品分類もありません。"
            "「防衛」は防衛省が発注したという意味で、落札者が防衛関連企業という意味ではありません。",
        ),
        (
            "「不明」は個人事業主が集まる区分です。",
            "法人番号を持たない落札者は取り込み時点で匿名化され、都道府県を持ちません。",
        ),
        (
            "件数の少ないバケットは公開集計から除外しています。",
            "匿名化された個人事業主が1〜2者しかいない 都道府県 × セクター × 年度 の区分は、"
            "消去法での再識別を防ぐため全体を非公開にしています（全体で34バケット・49件）。",
        ),
        (
            "FY2013–FY2015 は制度の立ち上げ期です。",
            "FY2013 の全件ファイルに含まれるレコードは1件だけで、これは収集の失敗ではなく公表元のデータそのままです。"
            "調達量の測定値として読まないでください。",
        ),
    ],
    "en": [
        (
            "No 予定価格 is published, so there is no 落札率 here.",
            "The source dataset has eight columns and a predicted price is not one of them. "
            "Any 'award ratio' or 'reference price' product would have to come from elsewhere.",
        ),
        (
            "Prefecture is the winner's registered address, not where the work happened.",
            "The source has no location field at all. The prefecture comes from joining the corporate "
            "number to the national registry, which gives the head office. A Tokyo-registered company "
            "winning work in Okinawa counts as Tokyo. This axis answers 'whose companies win national "
            "contracts', and it over-weights Tokyo the way any head-office measure does.",
        ),
        (
            "The sector says who bought, not what was sold.",
            "There is no industry code and no product classification in the source. 'Defense' means the "
            "Ministry of Defense was the buyer, not that the supplier is a defence contractor.",
        ),
        (
            "'Unknown' is where sole proprietors land.",
            "A winner without a corporate number is masked at ingestion and therefore carries no prefecture.",
        ),
        (
            "Thin buckets are suppressed from the public aggregate.",
            "A prefecture x sector x year bucket holding only one or two masked individuals is dropped "
            "entirely so that a rare sole proprietor cannot be re-identified by elimination "
            "(34 buckets, 49 awards across the whole archive).",
        ),
        (
            "FY2013-FY2015 is the system's ramp-up.",
            "The FY2013 full-year file contains a single record. That is the publisher's own data, not a "
            "collection failure; do not read those years as a measurement of procurement volume.",
        ),
    ],
}

# Added only to a page whose quantiles could not be computed exactly (the
# normalized award store was absent at build time).
ESTIMATE_CAVEAT = {
    "ja": (
        "中央値・四分位は推定値です（†）。",
        "ビルド時に1件単位の正規化済みデータが無かったため、集計済みバケットから推定しています。"
        "各都道府県バケットの5点（最小・Q1・中央値・Q3・最大）を区分線形の分布とみなし、"
        "件数で重み付けして合成した分布から求めた値です。実測値との比較では 1.5〜17% 高めに出ます。"
        "件数・合計・最小・最大は正確な値です。",
    ),
    "en": (
        "Median and quartiles on this page are estimated (†).",
        "The per-award normalized store was not available at build time, so they are derived from "
        "the pre-aggregated buckets: each prefecture bucket's five order statistics treated as a "
        "piecewise-linear distribution, mixed by award count and inverted. Measured against the "
        "exact figures this method runs 1.5-17% high. Counts, totals, minimum and maximum are exact.",
    ),
}

SUPPRESSION_CAVEAT_PAGE = {
    "ja": (
        "下の都道府県表の合計は、上の件数より {n} 件少なくなります。",
        "再識別防止のため非公開にしたバケットの分です。上の集計値はその {n} 件を含んだ正確な値、"
        "表は非公開分を除いた内訳です。",
    ),
    "en": (
        "The prefecture table below adds up to {n} fewer awards than the count above.",
        "Those are the awards in suppressed buckets. The figures above include them and are exact; "
        "the table is the breakdown with the suppressed buckets removed.",
    ),
}


def bet_a_caveats(lang, page=None, any_estimated=False):
    items = list(BET_A_CAVEATS[lang])
    estimated = (not page["quantiles_exact"]) if page is not None else any_estimated
    if estimated:
        items.insert(3, ESTIMATE_CAVEAT[lang])
    if page is not None and page["n_awards_suppressed"] > 0:
        head, body = SUPPRESSION_CAVEAT_PAGE[lang]
        n = f"{page['n_awards_suppressed']:,}"
        items.insert(4 if estimated else 3, (head.format(n=n), body.format(n=n)))
    return items


def caveat_block(lang, items):
    inner = "".join(
        f"<p><strong>{e(h)}</strong> {e(b)}</p>" for h, b in items
    )
    head = t(lang, "この数字を使う前に", "Read this before using these numbers")
    return f'<div class="note"><p><strong>{e(head)}</strong></p>{inner}</div>'


def build_bet_a_index(lang, pages, bet_a_prov, built_at):
    path = f"/{lang}/bet-a/"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/bet-a/"

    years = sorted({p["fiscal_year"] for p in pages})
    by_sector = collections.OrderedDict()
    for p in pages:
        by_sector.setdefault(p["sector"], {})[p["fiscal_year"]] = p

    # grid: one row per sector, one column per fiscal year
    headers = [t(lang, "セクター（発注元府省）", "Sector (purchasing ministry)")] + [
        f"FY{y}" for y in years
    ] + [t(lang, "合計", "Total")]
    aligns = [""] + ["n"] * len(years) + ["n"]
    rows = []
    for sector, ymap in sorted(by_sector.items(), key=lambda kv: SECTORS[kv[0]][0]):
        slug, en = SECTORS[sector]
        label = e(sector) if lang == "ja" else f"{e(en)}<br><span class='faint small'>{e(sector)}</span>"
        cells = [label]
        total = 0
        for y in years:
            p = ymap.get(y)
            if not p:
                cells.append('<span class="faint">—</span>')
                continue
            total += p["n_awards"]
            cells.append(
                '<a href="{}">{}</a>'.format(e(bet_a_page_path(lang, slug, y)), num(p["n_awards"]))
            )
        cells.append(f"<strong>{num(total)}</strong>")
        rows.append(cells)

    totals = [t(lang, "合計", "Total")]
    grand = 0
    for y in years:
        s = sum(p["n_awards"] for p in pages if p["fiscal_year"] == y)
        grand += s
        totals.append(f"<strong>{num(s)}</strong>")
    totals.append(f"<strong>{num(grand)}</strong>")
    rows.append(totals)

    caption = t(
        lang,
        f"年度 × セクター の落札件数。セルをクリックすると、その年度・セクターの統計ページに移動します（全 {len(pages)} ページ）。",
        f"Award counts by fiscal year and purchasing sector. Each cell links to that sector-year's "
        f"statistics page ({len(pages)} pages in total).",
    )

    title = t(
        lang,
        "国の落札実績 統計 — 年度 × 発注府省セクター | Deltakura",
        "Japanese national tender awards — statistics by fiscal year and purchasing sector | Deltakura",
    )
    desc_ja = (
        f"調達ポータルの落札実績オープンデータ {bet_a_prov['records']:,} 件を、年度 × 発注府省セクターで集計した "
        f"{len(pages)} ページの統計索引。件数・落札価格の中央値と四分位・落札者所在地の上位都道府県。"
        "予定価格は公表されていないため落札率は含みません。"
    )
    desc_en = (
        f"Index of {len(pages)} statistics pages built from {bet_a_prov['records']:,} Japanese national "
        "procurement awards: counts, median and quartile award prices, and the top prefectures of "
        "registered winners, by fiscal year and purchasing sector. No award ratio: the source publishes "
        "no predicted price."
    )

    body = f"""
<h1>{e(t(lang, "国の落札実績 統計", "National tender award statistics"))}</h1>
<p class="lede">{e(t(lang, "調達ポータルが公開している国の落札実績オープンデータ（FY" + str(min(years)) + "–FY" + str(max(years)) + "、" + f"{bet_a_prov['records']:,}" + " 件）を、年度と発注府省セクターで集計したものです。落札者名の一覧は作らず、統計だけを公開します。", "Built from the open award dataset published by 調達ポータル (FY" + str(min(years)) + "-FY" + str(max(years)) + ", " + f"{bet_a_prov['records']:,}" + " awards), aggregated by fiscal year and purchasing sector. Statistics only; no winner directory is built."))}</p>

{stat_grid([
    (t(lang, "集計した落札件数", "Awards aggregated"), f"{bet_a_prov['records']:,}", t(lang, "FY" + str(min(years)) + "–FY" + str(max(years)), "FY" + str(min(years)) + "-FY" + str(max(years)))),
    (t(lang, "統計ページ", "Statistics pages"), f"{len(pages)}", t(lang, "年度 × セクター", "fiscal year x sector")),
    (t(lang, "セクター", "Sectors"), f"{len(by_sector)}", t(lang, "発注府省から導出", "derived from the buying ministry")),
    (t(lang, "内訳を非公開にした件数", "Held back from breakdowns"), f"{bet_a_prov['suppressed']:,}", t(lang, "再識別防止のため", "to prevent re-identification")),
])}

{table(caption, headers, rows, aligns)}

{caveat_block(lang, bet_a_caveats(lang, any_estimated=any(not p["quantiles_exact"] for p in pages)))}

<h2>{e(t(lang, "機械向け", "For machines"))}</h2>
<p>{e(t(lang, "各統計ページには同じ内容の JSON があります。索引は次のとおりです。", "Every statistics page has a JSON twin. The index lists them all."))}</p>
<pre><code>GET {e(BASE_URL)}/data/bet-a/index.json
GET {e(BASE_URL)}/data/bet-a/&lt;sector-slug&gt;-fy&lt;year&gt;.json</code></pre>

{attribution_block(lang, [
    PPORTAL_ATTRIB + t(lang, "（政府標準利用規約 第2.0版。加工して利用しています。）", " (政府標準利用規約 v2.0; used in modified form.)"),
    NTA_ATTRIB + t(lang, "（公共データ利用規約 第1.0版。落札者の登記所在地の突合にのみ使用）", " (公共データ利用規約 v1.0; used only to resolve the winner's registered prefecture)"),
])}
"""

    jsonld = [
        {
            "@context": "https://schema.org",
            "@type": "DataCatalog",
            "name": t(lang, "国の落札実績 統計", "Japanese national tender award statistics"),
            "url": BASE_URL + path,
            "inLanguage": lang,
            "description": desc_ja if lang == "ja" else desc_en,
            "license": LICENSE_URL,
            "isAccessibleForFree": True,
            "provider": {"@type": "Organization", "name": BRAND, "url": GITHUB_ORG},
            "spatialCoverage": {"@type": "Place", "name": "Japan"},
            "temporalCoverage": f"{min(years)}-04-01/{max(years) + 1}-03-31",
        }
    ]
    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
        jsonld=jsonld,
        is_data_page=True,
    )


def build_bet_a_page(lang, page, years_for_sector, bet_a_prov, built_at):
    slug = page["sector_slug"]
    fy = page["fiscal_year"]
    path = bet_a_page_path(lang, slug, fy)
    alt = bet_a_page_path("en" if lang == "ja" else "ja", slug, fy)
    json_url = bet_a_json_path(slug, fy)

    sector_label = page["sector"] if lang == "ja" else page["sector_en"]
    fy_label = t(lang, f"{fy}年度", f"FY{fy}")
    period = f"{fy}-04-01 / {fy + 1}-03-31"

    dagger = "†" if not page["quantiles_exact"] else ""
    mean = page["amount_sum_jpy"] / page["n_awards"] if page["n_awards"] else 0

    headers = [
        t(lang, "都道府県（落札者の登記所在地）", "Prefecture (winner's registered address)"),
        t(lang, "件数", "Awards"),
        t(lang, "構成比", "Share"),
        t(lang, "中央値", "Median"),
        t(lang, "第1四分位", "Q1"),
        t(lang, "第3四分位", "Q3"),
        t(lang, "合計", "Total"),
        t(lang, "法人", "Corporate"),
        t(lang, "匿名化", "Masked"),
    ]
    aligns = ["", "n", "n", "n", "n", "n", "n", "n", "n"]
    rows = []
    for b in page["buckets"]:
        name = b["prefecture"]
        if lang == "en":
            name = PREF_EN.get(b["prefecture_code"], b["prefecture"])
            if b["prefecture"] == "不明":
                name = "Unknown (masked sole proprietors)"
        elif b["prefecture"] == "不明":
            name = "不明（匿名化された個人事業主）"
        share = b["n_awards"] / page["n_awards_in_table"] if page["n_awards_in_table"] else 0
        rows.append(
            [
                e(name),
                num(b["n_awards"]),
                pct(share),
                yen(b["amount_median_jpy"]),
                yen(b["amount_q1_jpy"]),
                yen(b["amount_q3_jpy"]),
                yen(b["amount_sum_jpy"]),
                num(b["n_corporate"]),
                num(b["n_masked_individual"]),
            ]
        )

    table_note_ja = (
        f"合計 {page['n_awards_in_table']:,} 件"
        + (
            f"（上の {page['n_awards']:,} 件のうち、再識別防止で非公開にした {page['n_awards_suppressed']:,} 件を除く）"
            if page["n_awards_suppressed"]
            else ""
        )
        + "。構成比はこの表の合計に対する割合です。"
    )
    table_note_en = (
        f"{page['n_awards_in_table']:,} awards"
        + (
            f" — the {page['n_awards']:,} above minus {page['n_awards_suppressed']:,} in suppressed buckets"
            if page["n_awards_suppressed"]
            else ""
        )
        + ". Shares are of this table's total."
    )
    caption = t(
        lang,
        "落札者の登記上の所在地別。" + table_note_ja + "各行の中央値・四分位はその都道府県内の正確な値です。",
        "By the winner's registered prefecture. " + table_note_en
        + " Each row's median and quartiles are exact within that prefecture.",
    )

    top = page["buckets"][:5]
    top_txt = "、".join(
        f"{b['prefecture']}（{b['n_awards']:,}件）" for b in top
    ) if lang == "ja" else ", ".join(
        f"{PREF_EN.get(b['prefecture_code'], b['prefecture'])} ({b['n_awards']:,})" for b in top
    )

    yearnav = "".join(
        (
            f'<span aria-current="page">FY{y}</span>'
            if y == fy
            else f'<a href="{e(bet_a_page_path(lang, slug, y))}">FY{y}</a>'
        )
        for y in years_for_sector
    )

    title = t(
        lang,
        f"{sector_label} {fy_label} の落札実績統計 — 件数・落札価格の中央値 | Deltakura",
        f"{sector_label}, FY{fy}: Japanese national tender award statistics | Deltakura",
    )
    desc_ja = (
        f"{fy}年度に{page['sector']}系の府省が発注した国の調達 {page['n_awards']:,} 件の落札統計。"
        f"落札価格の中央値 {yen(page['amount_median_jpy'])}、合計 {yen(page['amount_sum_jpy'])}。"
        f"落札者の登記所在地上位は {top_txt}。予定価格が非公表のため落札率は含みません。"
    )
    desc_en = (
        f"Statistics for {page['n_awards']:,} FY{fy} Japanese national procurement awards bought by "
        f"{page['sector_en'].lower()} bodies: median award price {yen(page['amount_median_jpy'])}, "
        f"total {yen(page['amount_sum_jpy'])}, top registered winner prefectures {top_txt}. "
        "No award ratio — the source publishes no predicted price."
    )

    ratio_note = t(
        lang,
        "落札率: 算出不可（予定価格が公表されていないため）",
        "Award ratio: not available (no predicted price is published)",
    )

    body = f"""
<p class="small muted"><a href="/{lang}/bet-a/">{e(t(lang, "落札実績 統計", "Tender award statistics"))}</a> / {e(sector_label)} / {e(fy_label)}</p>
<h1>{e(sector_label)} · {e(fy_label)}</h1>
<p class="lede">{e(t(lang, f"{fy}年度に{page['sector']}系の府省が発注した国の調達契約 {page['n_awards']:,} 件の落札統計です。セクターは「どの府省が買ったか」であり、業種ではありません。", f"Statistics for the {page['n_awards']:,} national procurement contracts bought by {page['sector_en'].lower()} bodies in FY{fy}. The sector says who bought, not what was sold."))}</p>
<nav class="yearnav" aria-label="{e(t(lang, "年度", "Fiscal year"))}">{yearnav}</nav>

{stat_grid([
    (t(lang, "落札件数", "Awards"), num(page["n_awards"]), t(lang, f"{page['n_buckets']} 都道府県区分", f"across {page['n_buckets']} prefecture buckets")),
    (t(lang, "落札価格 中央値", "Median award price") + dagger, yen(page["amount_median_jpy"]), t(lang, f"四分位 {yen(page['amount_q1_jpy'])} – {yen(page['amount_q3_jpy'])}", f"Q1-Q3 {yen(page['amount_q1_jpy'])} - {yen(page['amount_q3_jpy'])}")),
    (t(lang, "落札総額", "Total awarded"), yen(page["amount_sum_jpy"]), t(lang, f"平均 {yen(mean)}", f"mean {yen(mean)}")),
    (t(lang, "最小 / 最大", "Min / max"), yen(page["amount_min_jpy"]), t(lang, f"最大 {yen(page['amount_max_jpy'])}", f"max {yen(page['amount_max_jpy'])}")),
])}

<p class="small muted">{e(t(lang, "落札者の内訳: 法人 ", "Winner composition: "))}{num(page['n_corporate'])}{e(t(lang, " 者 / 匿名化された個人事業主 ", " corporate winners / "))}{num(page['n_masked_individual'])}{e(t(lang, " 者。", " masked sole proprietors."))}
{" " + e(ratio_note)}{(" · " + e(t(lang, "†中央値・四分位は推定値（下記参照）", "† median and quartiles are estimated, see below"))) if dagger else ""}</p>

<h2>{e(t(lang, "落札者の所在地上位", "Top prefectures of registered winners"))}</h2>
<p>{e(t(lang, "件数の多い順。ここでいう所在地は落札者の本店登記地であり、業務が行われた場所ではありません。", "Ordered by award count. 'Prefecture' is where the winner is registered, not where the work was done."))}</p>
{table(caption, headers, rows, aligns)}

{caveat_block(lang, bet_a_caveats(lang, page))}

<h2>{e(t(lang, "この数字を機械で読む", "Read these numbers with a machine"))}</h2>
<pre><code>GET {e(BASE_URL + json_url)}</code></pre>
<p class="small">{e(t(lang, "ページに表示されている値と同一の内容に、算出方法と出典・ライセンスを添えて返します。", "Returns exactly the values on this page, plus the method, the source and the licence."))}</p>

{attribution_block(lang, [
    PPORTAL_ATTRIB + t(lang, "（政府標準利用規約 第2.0版。加工して利用しています。）", " (政府標準利用規約 v2.0; used in modified form.)"),
    NTA_ATTRIB + t(lang, "（落札者の登記所在地の突合にのみ使用）", " (used only to resolve the winner's registered prefecture)"),
])}
"""

    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": t(
            lang,
            f"国の落札実績統計 — {page['sector']} · {fy}年度",
            f"Japanese national tender award statistics — {page['sector_en']}, FY{fy}",
        ),
        "description": desc_ja if lang == "ja" else desc_en,
        "url": BASE_URL + path,
        "identifier": f"deltakura:bet-a:{slug}:fy{fy}",
        "inLanguage": lang,
        "license": LICENSE_URL,
        "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": BRAND, "url": GITHUB_ORG},
        "publisher": {"@type": "Organization", "name": BRAND, "url": GITHUB_ORG},
        "temporalCoverage": period,
        "spatialCoverage": {"@type": "Place", "name": "Japan"},
        "keywords": [
            "落札実績", "調達", "公共調達", "オープンデータ",
            "public procurement", "tender awards", "Japan", page["sector"], page["sector_en"],
        ],
        "measurementTechnique": t(
            lang,
            "調達ポータルの年度別全件ファイルを正規化し、年度 × セクターで集計。中央値・四分位は"
            "1件単位の落札価格から inclusive 法で算出（実測値）。都道府県別内訳は落札者の法人番号を"
            "国税庁の登記情報に突合して得た本店所在地による。"
            if page["quantiles_exact"]
            else "集計済みバケットからの推定値（区分線形分布の件数加重合成）。",
            "Normalised from the publisher's per-fiscal-year full files and aggregated by fiscal year "
            "and sector. Median and quartiles are computed from the individual award amounts using the "
            "inclusive method. The prefecture breakdown comes from joining the winner's corporate "
            "number to the national registry, which yields the registered head office."
            if page["quantiles_exact"]
            else "Estimated from pre-aggregated buckets by mixing piecewise-linear distributions "
            "weighted by award count.",
        ),
        "variableMeasured": [
            {"@type": "PropertyValue", "name": "n_awards", "value": page["n_awards"]},
            {"@type": "PropertyValue", "name": "amount_median_jpy", "value": page["amount_median_jpy"], "unitText": "JPY"},
            {"@type": "PropertyValue", "name": "amount_sum_jpy", "value": page["amount_sum_jpy"], "unitText": "JPY"},
        ],
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": BASE_URL + json_url,
            }
        ],
        "isBasedOn": [
            {
                "@type": "Dataset",
                "name": "調達ポータル 落札実績",
                "url": "https://www.p-portal.go.jp/",
                "license": "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0",
            },
            {
                "@type": "Dataset",
                "name": "国税庁 法人番号公表サイト",
                "url": "https://www.houjin-bangou.nta.go.jp/",
            },
        ],
        "citation": PPORTAL_ATTRIB,
        "creativeWorkStatus": "Published",
    }

    payload = {
        "schema_version": "0.1",
        "endpoint": json_url,
        "page_url": {"ja": BASE_URL + bet_a_page_path("ja", slug, fy),
                     "en": BASE_URL + bet_a_page_path("en", slug, fy)},
        "dataset": "bet-a-national-tender-awards",
        "fiscal_year": fy,
        "fiscal_year_period": period,
        "sector": {"ja": page["sector"], "en": page["sector_en"], "slug": slug,
                   "basis": "derived from the purchasing ministry code; it says who bought, not what was sold"},
        "totals": {
            "n_awards": page["n_awards"],
            "n_awards_in_prefecture_breakdown": page["n_awards_in_table"],
            "n_awards_suppressed": page["n_awards_suppressed"],
            "n_corporate_winners": page["n_corporate"],
            "n_masked_individual_winners": page["n_masked_individual"],
            "n_prefecture_buckets": page["n_buckets"],
            "amount_sum_jpy": page["amount_sum_jpy"],
            "amount_mean_jpy": int(round(mean)),
            "amount_min_jpy": page["amount_min_jpy"],
            "amount_max_jpy": page["amount_max_jpy"],
        },
        "amount_quantiles_jpy": {
            "q1": page["amount_q1_jpy"],
            "median": page["amount_median_jpy"],
            "q3": page["amount_q3_jpy"],
            "estimated": not page["quantiles_exact"],
            "method": (
                "Computed from the individual award amounts in the normalized store using the "
                "inclusive quartile method."
                if page["quantiles_exact"]
                else "ESTIMATE: n-weighted mixture of per-prefecture five-point "
                "(min/q1/median/q3/max) piecewise-linear CDFs, inverted by bisection. Used only "
                "when the per-award store is unavailable at build time; measured against exact "
                "figures it runs 1.5-17% high. Counts, sums, min and max remain exact."
            ),
        },
        "award_ratio": {
            "available": False,
            "reason": "The source dataset publishes no 予定価格 (predicted price), so 落札率 cannot be computed.",
        },
        "prefectures": [
            {
                "prefecture_ja": b["prefecture"],
                "prefecture_en": PREF_EN.get(b["prefecture_code"], b["prefecture"]),
                "prefecture_code": b["prefecture_code"] or None,
                "basis": "winner_registered_nta",
                "n_awards": b["n_awards"],
                "n_corporate_winners": b["n_corporate"],
                "n_masked_individual_winners": b["n_masked_individual"],
                "amount_min_jpy": b["amount_min_jpy"],
                "amount_q1_jpy": b["amount_q1_jpy"],
                "amount_median_jpy": b["amount_median_jpy"],
                "amount_q3_jpy": b["amount_q3_jpy"],
                "amount_max_jpy": b["amount_max_jpy"],
                "amount_sum_jpy": b["amount_sum_jpy"],
                "quantiles_exact": True,
            }
            for b in page["buckets"]
        ],
        "caveats": [
            "予定価格が公表されていないため落札率は存在しない / no predicted price is published, so there is no award ratio",
            "都道府県は落札者の登記所在地であり履行地ではない / prefecture is the winner's registered address, not the place of performance",
            "セクターは発注府省由来であり業種ではない / sector is derived from the buying ministry, not an industry code",
            "個人事業主は取り込み時点で匿名化され都道府県を持たない（不明に集計） / sole proprietors are masked at ingestion and carry no prefecture",
            "匿名化個人が1〜2者のバケットは再識別防止のため非公開 / buckets with one or two masked individuals are suppressed",
            "FY2013-FY2015は公表元の立ち上げ期 / FY2013-FY2015 is the publisher's ramp-up period",
        ],
        "provenance": {
            "source_id": "pportal_awards",
            "source_name": "調達ポータル 落札実績",
            "source_url": "https://www.p-portal.go.jp/",
            "license": "政府標準利用規約(第2.0版) — commercial reuse permitted with source indication",
            "attribution": PPORTAL_ATTRIB,
            "prefecture_join_source": "国税庁法人番号公表サイト",
            "prefecture_join_attribution": NTA_ATTRIB,
            "modified": (
                "出典データを加工して利用しています / Modified from the source:"
                " normalised, aggregated and anonymised by Deltakura"
            ),
            "retrieved_at": bet_a_prov["retrieved_at"],
            "derived_aggregates_license": "CC BY 4.0",
        },
        "generated_at": built_at,
    }

    return (
        path,
        alt,
        render_page(
            lang=lang,
            path=path,
            title=title,
            desc_ja=desc_ja,
            desc_en=desc_en,
            body=body,
            alt_path=alt,
            jsonld=[dataset],
            is_data_page=True,
        ),
        payload,
    )


def build_bet_c(lang, bet_c, built_at):
    path = f"/{lang}/bet-c/"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/bet-c/"
    days = bet_c["days"]
    values = [bet_c["daily"][d] for d in days]
    avg = sum(values) / len(values) if values else 0

    chart = bar_chart_svg(days, values, lang)

    proc_rows = []
    for code, n in sorted(bet_c["process"].items(), key=lambda kv: -kv[1]):
        ja, en = PROCESS_CODES.get(code, (f"コード {code}", f"code {code}"))
        proc_rows.append(
            [
                f"<code>{e(code)}</code> {e(ja if lang == 'ja' else en)}",
                num(n),
                pct(n / bet_c["total"]),
            ]
        )
    kind_rows = []
    for code, n in sorted(bet_c["kinds"].items(), key=lambda kv: -kv[1]):
        ja, en = KIND_CODES.get(code, (f"コード {code}", f"code {code}"))
        kind_rows.append(
            [
                f"<code>{e(code)}</code> {e(ja if lang == 'ja' else en)}",
                num(n),
                pct(n / bet_c["total"]),
            ]
        )

    schema_rows = [
        [
            f"<code>{e(col)}</code>",
            e(ja if lang == "ja" else en),
            f'<span class="faint">{e(sample) if sample else "—"}</span>',
        ]
        for col, ja, en, sample in NTA_SCHEMA
    ]

    why_ja = """<p>国税庁の法人番号公表サイトは、登記の変更を1日1本の差分ファイルで出します。そのファイルは<strong>約40日で消えます</strong>。全件ファイルは毎月出ますが、それは「いまの状態」であって「いつ何が変わったか」ではありません。つまり、</p>
<ul class="clean">
<li>40日より前に「どの法人がいつ設立されたか」「いつ本店を移したか」「いつ登記記録が閉鎖されたか」は、取り直せません。</li>
<li>スナップショットを何枚並べても、間に起きた変更は復元できません（同じ日に2回変われば1回に見えます）。</li>
<li>だから、毎晩1回・約130KB の取得を、途切れさせずに続けることそのものが資産になります。</li>
</ul>
<p>このアーカイブは、その取得を続けた結果です。欠けた日は欠けたと表示します。</p>"""
    why_en = """<p>The National Tax Agency publishes one diff file per working day and <strong>deletes it after about 40 days</strong>. A monthly full dump exists, but it describes the present state, not when each thing changed. So:</p>
<ul class="clean">
<li>Beyond the 40-day window, when a company was registered, when it moved its head office, and when its registry record was closed cannot be fetched again.</li>
<li>Stacking snapshots does not recover it: two changes inside one interval look like one.</li>
<li>Which makes one 130 KB request a night, never missed, the entire asset.</li>
</ul>
<p>This archive is the result of making that request. Missing days are shown as missing.</p>"""

    gaps_ja = (
        "差分ファイルは土日・祝日・12/29〜1/3 には作られません。"
        "その日が欠けているのは収集失敗ではなく公表元の暦です。"
    )
    gaps_en = (
        "No diff file is produced on weekends, public holidays or 12/29-01/03. "
        "A missing day there is the publisher's calendar, not a collection failure."
    )

    title = t(
        lang,
        "法人番号 差分アーカイブ — 40日で消える日次差分を保存する | Deltakura",
        "Corporate registry diff archive — keeping the daily file that vanishes in 40 days | Deltakura",
    )
    desc_ja = (
        f"国税庁 法人番号公表サイトが約40日で削除する日次差分ファイルを、毎晩1回取得して保存しています。"
        f"現在 {bet_c['n_files']} 日分・{bet_c['total']:,} レコード（{bet_c['first_day']}〜{bet_c['last_day']}）。"
        "対象は法人のみで、個人の氏名は一切扱いません。週次 RSS と JSON で配信。"
    )
    desc_en = (
        f"The National Tax Agency deletes its daily corporate-registry diff after about 40 days. We fetch "
        f"one file a night and keep it: {bet_c['n_files']} days and {bet_c['total']:,} records so far "
        f"({bet_c['first_day']} to {bet_c['last_day']}). Corporations only, never an individual's name. "
        "Weekly RSS and JSON."
    )

    body = f"""
<h1>{e(t(lang, "法人番号 差分アーカイブ", "Corporate registry diff archive"))}</h1>
<p class="lede">{e(t(lang, "国税庁 法人番号公表サイトの日次差分を、消える前に保存しています。法人のみ。個人の氏名は保有しません。", "We keep the National Tax Agency's daily corporate-registry diff before it is deleted. Corporations only; we hold no individual's name."))}</p>

{stat_grid([
    (t(lang, "保存済みレコード", "Records kept"), f"{bet_c['total']:,}", t(lang, f"{bet_c['n_files']} 公表日分", f"over {bet_c['n_files']} publication days")),
    (t(lang, "収録期間", "Coverage"), f"{bet_c['first_day']}", t(lang, f"→ {bet_c['last_day']}", f"to {bet_c['last_day']}")),
    (t(lang, "1日あたり平均", "Average per day"), f"{avg:,.0f}", t(lang, "レコード", "records")),
    (t(lang, "上流の保持期間", "Upstream retention"), "40", t(lang, "日で削除される", "days, then deleted")),
])}

<h2>{e(t(lang, "なぜ40日が重要なのか", "Why the 40-day window matters"))}</h2>
{t(lang, why_ja, why_en)}
{intent_button(lang, "bet_c_registry_diff", "更新を受け取る", "Notify me")}

<h2>{e(t(lang, "1日あたりの差分件数", "Records per publication day"))}</h2>
<figure class="chart">
{chart}
<figcaption>{e(t(lang, f"{bet_c['first_day']} 〜 {bet_c['last_day']} の公表日ごとの差分レコード数（{len(days)} 日分）。{gaps_ja}", f"Diff records per publication day, {bet_c['first_day']} to {bet_c['last_day']} ({len(days)} days). {gaps_en}"))}</figcaption>
</figure>

<h2>{e(t(lang, "変更の内訳", "What changed"))}</h2>
<div class="grid c2">
<div>{table(t(lang, "処理区分別", "By change type"), [t(lang, "処理区分", "Change type"), t(lang, "件数", "Records"), t(lang, "構成比", "Share")], proc_rows, ["", "n", "n"])}</div>
<div>{table(t(lang, "法人種別", "By entity type"), [t(lang, "法人種別", "Entity type"), t(lang, "件数", "Records"), t(lang, "構成比", "Share")], kind_rows, ["", "n", "n"])}</div>
</div>

<h2>{e(t(lang, "正規化後のスキーマ", "The normalised schema"))}</h2>
<p>{e(t(lang, "公表元の CSV はヘッダなし30列です。英字転記4列と国外所在地の画像ID 2列は、取り込み時に捨てています（差分として価値がなく、転記された人名が混入し得る列だからです）。下の値は形を示すためのサンプルで、実在のレコードではありません。", "The publisher's CSV is 30 columns with no header. Six are dropped at parse: the four English-transliteration fields and the two overseas-address image ids, which add nothing to a diff and are the ones most likely to carry a transliterated personal name. The values below are illustrative, not a real record."))}</p>
{table("", [t(lang, "列", "Column"), t(lang, "意味", "Meaning"), t(lang, "サンプル値", "Illustrative value")], schema_rows, ["", "", ""])}
<p class="small">{e(t(lang, "主キーは corporate_number|change_date|sequence_number。訂正フラグ付きのレコードが同一キーの先行レコードを上書きし、それ以外は連番の大きいほうが残ります。", "Primary key: corporate_number|change_date|sequence_number. A record flagged as a correction supersedes an earlier one with the same key; otherwise the highest sequence number wins."))}</p>

<div class="note">
<p><strong>{e(t(lang, "個人情報は含まれません。", "There is no personal data here."))}</strong>
{e(t(lang, "法人番号は法人と公的機関に対して指定されるもので、個人事業主には指定されません。差分の30列レイアウトには代表者名の列がありません。さらにパーサーは、担当者・氏名・連絡先などに一致する列名を見つけたら書き込まずに失敗します。将来レイアウトが変わっても、黙って公開されることはありません。", "Corporate numbers are issued to corporations and public bodies, not to sole proprietors, and the 30-column diff layout carries no representative-person field. On top of that, the parser refuses any column whose name matches a personal-field pattern, so a future layout change fails loudly instead of quietly publishing a name."))}</p>
</div>

<h2>{e(t(lang, "受け取り方", "How to take it"))}</h2>
<ul class="clean">
<li><a href="/feeds/nta-diff.xml">/feeds/nta-diff.xml</a> — {e(t(lang, "週次 RSS。1週ぶんの日次件数サマリを1アイテムで配信します。", "Weekly RSS: one item per ISO week, carrying that week's daily counts."))}</li>
<li><a href="/data/bet-c/daily.json">/data/bet-c/daily.json</a> — {e(t(lang, "日次件数と処理区分内訳の JSON。", "Daily counts and the change-type breakdown, as JSON."))}</li>
<li><a href="{e(GITHUB_CORE)}" rel="noopener">{e(t(lang, "収集スクリプト（MIT）", "The collector (MIT)"))}</a> — {e(t(lang, "毎晩2リクエスト（一覧ページと当日分のダウンロード）。robots.txt 遵守、同一ホストへ2秒以上の間隔。", "Two requests a night: the listing page and that day's file. robots.txt honoured, at least 2 s between requests to the same host."))}</li>
</ul>

{attribution_block(lang, [NTA_ATTRIB + t(lang, "（公共データ利用規約 第1.0版。正規化・重複排除・再エンコードの加工をしています。）", " (公共データ利用規約 v1.0; used in modified form: normalised, deduplicated and re-encoded.)")])}
"""

    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": t(
            lang,
            "法人番号 差分アーカイブ（国税庁 日次差分の保全）",
            "Japanese corporate-number registry diff archive",
        ),
        "description": desc_ja if lang == "ja" else desc_en,
        "url": BASE_URL + path,
        "identifier": "deltakura:bet-c:nta-diff",
        "inLanguage": lang,
        "license": LICENSE_URL,
        "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": BRAND, "url": GITHUB_ORG},
        "publisher": {"@type": "Organization", "name": BRAND, "url": GITHUB_ORG},
        "temporalCoverage": f"{bet_c['first_day']}/{bet_c['last_day']}",
        "spatialCoverage": {"@type": "Place", "name": "Japan"},
        "keywords": ["法人番号", "登記", "差分", "corporate number", "registry diff", "Japan", "open data"],
        "measurementTechnique": t(
            lang,
            "公表元の日次差分ZIP（CSV/Unicode）を1日1回取得し、30列レイアウトから24列に正規化。"
            "主キーで重複排除し、訂正レコードを優先。個人名が入り得る列は取り込み時に削除。",
            "One nightly fetch of the publisher's daily diff ZIP (CSV/Unicode), normalised from the "
            "30-column layout to 24 columns, deduplicated on the primary key with corrections taking "
            "precedence, and with every column that could carry a personal name dropped at parse.",
        ),
        "variableMeasured": [
            {"@type": "PropertyValue", "name": "records", "value": bet_c["total"]},
            {"@type": "PropertyValue", "name": "publication_days", "value": bet_c["n_files"]},
        ],
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": BASE_URL + "/data/bet-c/daily.json",
            },
            {
                "@type": "DataDownload",
                "encodingFormat": "application/rss+xml",
                "contentUrl": BASE_URL + "/feeds/nta-diff.xml",
            },
        ],
        "isBasedOn": {
            "@type": "Dataset",
            "name": "国税庁 法人番号公表サイト 差分データ",
            "url": "https://www.houjin-bangou.nta.go.jp/download/sabun/",
        },
        "citation": NTA_ATTRIB,
        "creativeWorkStatus": "Published",
    }
    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
        jsonld=[dataset],
        is_data_page=True,
    )


def build_pricing(lang, built_at):
    path = f"/{lang}/pricing.html"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/pricing.html"

    title = t(
        lang,
        "料金（準備中） | Deltakura",
        "Pricing (coming soon) | Deltakura",
    )
    desc_ja = (
        "Deltakura の有料レイヤーの予定価格。現在は準備中で、決済は開いていません。"
        "無料レイヤー（ページ・RSS・JSON・MCP）は今後も無料のまま提供します。"
    )
    desc_en = (
        "Planned pricing for the Deltakura paid layer. Nothing is on sale yet and there is no checkout. "
        "The free layer — pages, RSS, JSON and MCP — stays free."
    )

    free_rows = [
        [
            e(t(lang, "落札実績 統計", "Tender award statistics")),
            e(t(lang, "全 169 ページ + JSON エンドポイント", "All 169 pages plus their JSON endpoints")),
            e(t(lang, "無料・認証不要", "Free, no key")),
        ],
        [
            e(t(lang, "法人番号 差分", "Registry diff")),
            e(t(lang, "日次件数・週次 RSS・JSON", "Daily counts, weekly RSS, JSON")),
            e(t(lang, "無料・認証不要", "Free, no key")),
        ],
        [
            e(t(lang, "MCP サーバー / npm", "MCP server / npm")),
            e(t(lang, "読み取り専用ツール（準備中）", "Read-only tools (not published yet)")),
            e(t(lang, "無料", "Free")),
        ],
        [
            e(t(lang, "収集コード", "Collector code")),
            e(t(lang, "クローラー・正規化・匿名化・テスト", "Crawlers, normalisation, anonymiser, tests")),
            "MIT",
        ],
    ]

    paid_rows = [
        [
            e(t(lang, "落札実績レポート（単発）", "Award statistics report (one-off)")),
            e(t(lang, "業種 × 都道府県の統計レポート", "Statistics report per industry x prefecture")),
            "JPY 2,980 – 9,800",
            '<span class="tag soon">' + e(t(lang, "要 自治体データ", "blocked on municipal data")) + "</span>",
        ],
        [
            e(t(lang, "コンサルタント向けプラン", "Consultant plan")),
            e(t(lang, "CSV エクスポート + Webhook", "CSV export plus webhooks")),
            e(t(lang, "JPY 9,800 / 月（税込）", "JPY 9,800 / month (tax incl.)")),
            '<span class="tag soon">' + e(t(lang, "準備中", "Coming soon")) + "</span>",
        ],
        [
            e(t(lang, "採用ウォッチリスト", "Hiring watchlist")),
            e(t(lang, "最大200ドメイン、Slack / Discord Webhook", "Up to 200 domains, Slack / Discord webhook")),
            e(t(lang, "JPY 4,350 / 月（USD 29）", "JPY 4,350 / month (USD 29)")),
            '<span class="tag soon">' + e(t(lang, "Bet B 未公開", "Bet B not published")) + "</span>",
        ],
        [
            e(t(lang, "API / MCP キー", "API / MCP key")),
            e(t(lang, "従量制の読み取り API", "Metered read API")),
            "USD 79",
            '<span class="tag soon">' + e(t(lang, "準備中", "Coming soon")) + "</span>",
        ],
        [
            e(t(lang, "Apify Actor（従量課金）", "Apify Actor (pay per event)")),
            e(t(lang, "変更イベント単位。変更がなければ 0 円。", "Charged per change event; zero when nothing changed.")),
            e(t(lang, "USD 0.005 – 0.02 / イベント", "USD 0.005 - 0.02 per event")),
            '<span class="tag soon">' + e(t(lang, "準備中", "Coming soon")) + "</span>",
        ],
    ]

    honest_ja = """<p><strong>先に正直なところを書きます。</strong>上の表の1行目「落札実績レポート」は、<em>いま公開している国の落札実績データからは作れません</em>。国の調達ポータルは予定価格を公表しておらず、落札率も参考価格も算出できないからです。この行が売り物になるのは、予定価格を公表している自治体のデータを追加できたときだけです。できなければ、この行は消します。</p>
<p>3行目の採用ウォッチリストも同じです。取得元の利用条件は確認済みですが、公開そのものが別途の承認事項で、データを1件も公開していません。値付けも仮のものです。</p>"""
    honest_en = """<p><strong>The honest part first.</strong> The first row above — award statistics reports — <em>cannot be built from the national data on this site</em>. The national procurement portal publishes no predicted price, so there is no award ratio and no reference price to sell. That row becomes real only if municipal sources that do publish a predicted price can be added. If they cannot, the row gets deleted rather than quietly redefined.</p>
<p>The same applies to the hiring watchlist: its sources' reuse terms have been reviewed, but publication is a separate approval that has not been given and not one record is published, so its price is provisional too.</p>"""

    terms_ja = f"""<ul class="clean">
<li><strong>販売者</strong>: 決済が開く際は、海外の Merchant of Record（Polar / Apify）が販売者になります。返金もそちらが実行します。</li>
<li><strong>返金</strong>: 単発購入は14日以内であれば理由を問わず返金。サブスクリプションはいつでも解約でき、当期分の日割り返金はありません。従量課金は提供済みの分について返金できません。</li>
<li><strong>適格請求書</strong>: 販売者が国外事業者のため、日本の適格請求書（登録番号付き）は発行できません。仕入税額控除の可否は貴社の税務顧問にご確認ください。</li>
<li><strong>無料レイヤー</strong>: 有料化後も、このサイトのページ・RSS・JSON・MCP は無料のまま残します。有料化のために無料機能を削ることはしません。</li>
<li><strong>メール</strong>: 購入手続きに伴う領収書等は Merchant of Record が送ります。Deltakura からメールを送ることは、購入後も一切ありません。</li>
</ul>"""
    terms_en = f"""<ul class="clean">
<li><strong>Seller.</strong> When checkout opens, a merchant of record (Polar / Apify) is the seller and executes every refund.</li>
<li><strong>Refunds.</strong> One-off purchases: 14 days, no questions asked. Subscriptions: cancel any time, no pro-rata refund of the current period. Metered usage already served is not refundable.</li>
<li><strong>Japanese qualified invoices (適格請求書).</strong> The seller is a foreign business, so a registration-numbered invoice cannot be issued. Ask your tax adviser whether the input credit applies to you.</li>
<li><strong>The free layer stays free.</strong> Pages, RSS, JSON and MCP will not be taken away to make room for a paid tier.</li>
<li><strong>Email.</strong> Receipts come from the merchant of record. Deltakura itself sends no email, before or after a purchase.</li>
</ul>"""

    body = f"""
<h1>{e(t(lang, "料金", "Pricing"))} <span class="tag soon">{e(t(lang, "準備中", "Coming soon"))}</span></h1>
<p class="lede">{e(t(lang, "決済はまだ開いていません。購入リンクもありません。ここにあるのは、有料レイヤーを開くときに提示する予定の価格です。先に見えているほうがフェアだと考えて出しています。", "Nothing is on sale and there is no checkout link on this page. These are the prices we intend to open with, published early because it seems fairer than pricing in private."))}</p>

<h2>{e(t(lang, "無料で続けるもの", "What stays free"))}</h2>
{table("", [t(lang, "対象", "What"), t(lang, "内容", "Contents"), t(lang, "価格", "Price")], free_rows)}

<h2>{e(t(lang, "有料レイヤー（予定）", "Paid layer (planned)"))}</h2>
{table(t(lang, "いずれも準備中です。購入はできません。", "All of these are unavailable; nothing can be bought today."), [t(lang, "プラン", "Plan"), t(lang, "内容", "Contents"), t(lang, "予定価格", "Planned price"), t(lang, "状態", "Status")], paid_rows, ["", "", "n", ""])}

<div class="note">{t(lang, honest_ja, honest_en)}</div>

<h2>{e(t(lang, "有料化したときの条件", "The terms, when it opens"))}</h2>
{t(lang, terms_ja, terms_en)}

<h2>{e(t(lang, "開いたら知りたい方へ", "If you want to know when it opens"))}</h2>
<p>{e(t(lang, "メールアドレスは受け取りません。下のボタンはクリック数だけを記録します。実際のお知らせは RSS と GitHub のリリースで流します。", "We take no email address. The button below records a click and nothing else; the actual announcement goes out through the RSS feed and GitHub releases."))}</p>
{intent_button(lang, "pricing_paid_plans", "有料プランが開いたら知りたい", "Tell me when paid plans open")}
"""

    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
    )


def build_privacy(lang, bet_a_prov, bet_c, built_at):
    path = f"/{lang}/privacy.html"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/privacy.html"

    title = t(
        lang,
        "プライバシーと方法論 — Cookie なし・メールなし | Deltakura",
        "Privacy and methodology — no cookies, no email | Deltakura",
    )
    desc_ja = (
        "Deltakura のプライバシー方針と方法論。アクセス解析のビーコンを読み込まない作り、"
        "メールアドレスを収集しない設計、"
        "出典とライセンス、匿名化ルール、法人からの削除依頼の受け付け方。連絡先は GitHub Issues のみです。"
    )
    desc_en = (
        "Deltakura's privacy statement and methodology: no analytics beacon, a design that collects no "
        "email address, the sources and their licences, the anonymisation rules, and how a company asks for "
        "a removal. Contact is GitHub Issues only."
    )

    sources_rows = [
        [
            e("調達ポータル 落札実績"),
            '<a href="https://www.p-portal.go.jp/" rel="noopener nofollow">p-portal.go.jp</a>',
            e("政府標準利用規約(第2.0版)"),
            e(t(lang, "商用利用可・出典表示が必要", "Commercial reuse permitted with source indication")),
            e(t(lang, f"{bet_a_prov['records']:,} 件", f"{bet_a_prov['records']:,} awards")),
        ],
        [
            e("国税庁 法人番号公表サイト 差分"),
            '<a href="https://www.houjin-bangou.nta.go.jp/download/sabun/" rel="noopener nofollow">houjin-bangou.nta.go.jp</a>',
            e("公共データ利用規約(第1.0版)"),
            e(t(lang, "商用利用・再配布可・出典表示と改変の明示が必要", "Commercial reuse and redistribution permitted; attribution and a modification notice required")),
            e(t(lang, f"{bet_c['total']:,} レコード", f"{bet_c['total']:,} records")),
        ],
        [
            e(t(lang, "採用ボード（Bet B）", "Job boards (Bet B)")),
            e("—"),
            e(t(lang, "確認済み（条件付き再利用可）", "reviewed; reuse permitted under conditions")),
            e(t(lang, "公開は承認待ちのため公開データなし", "Publication pending approval; nothing published")),
            e("0"),
        ],
    ]

    analytics_ja = f"""<h3>計測について</h3>
<p>Cookie を設置しません。ログインもアカウントもありません。他サイトを横断する追跡も行いません。</p>
<ul class="clean">
<li><strong>アクセス数は数えていません</strong>: アクセス解析のビーコンはこのサイトに1つも読み込まれていません。読み込まれるスクリプトは自前の2本（<code>/assets/config.js</code> と <code>/assets/intent.js</code>）だけで、配信時の Content-Security-Policy も同一オリジンのスクリプトしか許可していません。今後 Cookie を使わない計測（Cloudflare Web Analytics）を導入する場合は、このページの記述と CSP を先に更新し、同じ変更の中でのみ有効化します。</li>
<li><strong>「更新を受け取る」ボタン</strong>: クリック数を数えるために、ブラウザの localStorage にランダムな文字列を1つ保存します。二重カウントを防ぐためだけのもので、個人と結びつく情報ではなく、他のサイトからは読めません。プライベートウィンドウでは保存されず、その場合もページは正常に動きます。現在は送信先が設定されていないため、クリックはブラウザ内で確認されるだけで、どこにも送信されません。</li>
<li><strong>フィードの購読数も数えていません</strong>: RSS は静的ファイルとして配信しており、アクセスログを集計する仕組みは動いていません。将来 JSON フィードを自前のエンドポイントから配信する場合は、リーダーのユーザーエージェントと、日ごとに変わるソルトで /16 に丸めたうえでハッシュ化した IP を当日の集計にのみ使い、集計後に破棄します。生の IP を保存することはありません。その場合も、このページを先に更新します。</li>
<li><strong>入力欄がありません</strong>: このサイトにフォームは1つもありません。メールアドレス、氏名、会社名、いずれも受け取る手段がありません。</li>
</ul>
<h3>メールを使わない理由</h3>
<p>特定電子メール法は広告メールに送信者の実名と住所の表示を義務づけ、GDPR 第13条は EU の購読者に対して管理者の明示を求めます。この事業は運営者の身元を公開しない設計なので、そのどちらも満たせません。だから<strong>メールという手段を最初から持たない</strong>ことにしました。ニュースレターも、1回限りのお知らせも、営業メールも送りません。購入が始まった後の領収書は、販売者である Merchant of Record が送ります。</p>"""

    analytics_en = f"""<h3>Measurement</h3>
<p>No cookies are set. There is no login and no account, and nothing here follows you to another site.</p>
<ul class="clean">
<li><strong>Page visits are not counted.</strong> No analytics beacon of any kind is loaded on this site. The only scripts served are two self-hosted files (<code>/assets/config.js</code> and <code>/assets/intent.js</code>), and the Content-Security-Policy sent with every page allows scripts from this origin only. If cookie-less analytics (Cloudflare Web Analytics) is added later, this page and the CSP are updated first, in the same change that switches it on.</li>
<li><strong>The "Notify me" button</strong> stores one random string in your browser's localStorage so a second click is not counted twice. It is not tied to a person, cannot be read by another site, and is simply absent in a private window — the page still works. No endpoint is configured today, so the click is confirmed in your browser and sent nowhere.</li>
<li><strong>Feed subscribers are not counted either.</strong> The RSS feed is a static file and nothing here aggregates access logs. If a JSON feed is later served from an endpoint of our own, it will use the reader's user agent plus a hash of the IP truncated to /16 with a salt that rotates daily, for that day's aggregate only, and then discard it. A raw IP address would never be stored — and this page would be updated before that ships.</li>
<li><strong>There is no input field.</strong> This site has no form at all — no way to submit an email address, a name or a company.</li>
</ul>
<h3>Why there is no email</h3>
<p>Japan's 特定電子メール法 requires a real sender name and postal address on advertising email, and GDPR Art. 13 requires a named controller for EU subscribers. A project that does not publish an operator identity cannot satisfy either. So the channel does not exist here: no newsletter, no one-off announcement, no outbound sales mail. Once purchases open, receipts come from the merchant of record, which is the seller.</p>"""

    method_ja = """<h3>匿名化</h3>
<p>取り込みの時点で、次のルールを機械的に適用します。人が判断する余地はありません。</p>
<ul class="clean">
<li>チェックディジットが正しい13桁の法人番号を持つ落札者は、名称をそのまま保持します。その名称は国が法人番号公表サイトで公開しているものです。</li>
<li>法人番号を持たない落札者は、名前がどれだけ法人らしく見えても匿名化します。「ヤマダ印刷」も「山田太郎商店」も同じ扱いです。チェックディジットが合わない番号も同様に匿名化します。</li>
<li>担当者・氏名・連絡先・電話・メールに相当する列は、正規化の前に捨てます。公開物に到達する経路がありません。</li>
<li>落札案件の件名は人名検出器を通し、一致したら件名ごと差し替えます。</li>
<li>落札者ディレクトリは作りません。氏名で検索できる集計は、コード側で拒否します。</li>
<li>匿名化された個人が1〜2者しかいない区分（都道府県 × セクター × 年度）は、消去法による再識別を防ぐため、区分ごと公開集計から外します。</li>
</ul>
<h3>数字の作り方</h3>
<p>各ページの中央値と四分位のうち、セクター全体の値は推定値で、† を付けています。元データが 年度 × 都道府県 × セクター で集計済みのため、セクター全体の中央値は直接は読み出せません。各都道府県バケットの5点（最小・Q1・中央値・Q3・最大）を区分線形の分布とみなし、件数で重み付けして合成した分布を二分法で反転して求めています。件数・合計・最小・最大、および都道府県ごとの中央値・四分位は正確な値です。</p>
<h3>公式データではありません</h3>
<p>ここは公開情報を機械的に集めた非公式アーカイブです。収集漏れも解析誤りも起こり得ます。重要な判断の前には、各ページの出典 URL から原本をご確認ください。無保証です。</p>"""

    method_en = """<h3>Anonymisation</h3>
<p>These rules are applied mechanically at ingestion. No judgement call is involved.</p>
<ul class="clean">
<li>A winner with a checksum-valid 13-digit corporate number keeps its name — that name is published by the state on the corporate-number site.</li>
<li>A winner without one is masked, however corporate the name looks. A number whose check digit fails is masked as well, and flagged.</li>
<li>Columns equivalent to contact person, personal name, phone or email are dropped before normalisation. There is no path by which they reach anything published.</li>
<li>Contract titles pass a person-name detector; a match replaces the title.</li>
<li>No winner directory is built. An aggregate keyed by a party's name is refused in code, not by policy.</li>
<li>A prefecture x sector x year bucket holding only one or two masked individuals is dropped from the public aggregate so nobody can be re-identified by elimination.</li>
</ul>
<h3>How the numbers are made</h3>
<p>On each statistics page, the sector-wide median and quartiles are estimates and carry a dagger. The source statistics are pre-aggregated at fiscal year x prefecture x sector, so a sector-wide median cannot be read off the file. Each prefecture bucket's five order statistics are treated as a piecewise-linear distribution, mixed by award count, and the mixture is inverted by bisection. Counts, totals, minimum, maximum, and each prefecture's own median and quartiles are exact.</p>
<h3>This is not an official source</h3>
<p>It is an unofficial archive built by automated collection of public information. Gaps and parsing errors are possible. Before relying on a number, open the source URL given on the page. Provided without warranty.</p>"""

    removal_ja = f"""<p>掲載されている数値は、国が公開している調達・登記のオープンデータから機械的に集計したものです。個人情報は保有していませんが、それでも次のような依頼は受け付けます。</p>
<ul class="clean">
<li><strong>誤りの指摘</strong>: 集計や突合に誤りがある場合。出典の原本と食い違う箇所をお知らせください。</li>
<li><strong>個人が特定できる情報の指摘</strong>: 匿名化の漏れを見つけた場合。<strong>24時間以内</strong>に該当箇所を非公開にし、原因を調査します。これは最優先で扱います。</li>
<li><strong>法人からの削除依頼</strong>: 法人名や法人番号に関する掲載について。ただし、出典が国の公開データであるため、削除できる範囲には限りがあります。判断と対応内容は依頼ごとに公開の Issue に記録します。</li>
</ul>
<p>受け付けは <a href="{e(GITHUB_ISSUES)}" rel="noopener">GitHub Issues</a> のみです。メールでの受付はありません（上記のとおりメールの手段を持たないためです）。件名に「削除依頼」または「REMOVAL」と入れていただくと優先します。通常2営業日以内に応答します。</p>"""

    removal_en = f"""<p>Everything here is aggregated mechanically from open procurement and registry data published by the Japanese state. We hold no personal data, and we still accept these requests:</p>
<ul class="clean">
<li><strong>Report an error</strong> in an aggregate or a join. Point at where it disagrees with the source.</li>
<li><strong>Report identifiable information.</strong> If you find a gap in the anonymisation, we take the item down <strong>within 24 hours</strong> and investigate. This is handled ahead of everything else.</li>
<li><strong>Removal request from a company</strong> regarding a corporate name or corporate number. Because the upstream source is state-published open data, what can be removed is limited; the decision and what was done are recorded in the public issue.</li>
</ul>
<p>Requests go through <a href="{e(GITHUB_ISSUES)}" rel="noopener">GitHub Issues</a> only. There is no email intake, for the reason given above. Put "REMOVAL" or 「削除依頼」 in the title and it is prioritised. We reply within two business days.</p>"""

    body = f"""
<h1>{e(t(lang, "プライバシーと方法論", "Privacy and methodology"))}</h1>
<p class="lede">{e(t(lang, "Cookie を置かず、メールアドレスを集めず、個人の氏名を扱いません。どれも方針である前に、作りのレベルでそうなっています。", "No cookies, no email addresses, no individuals' names. Each of those is a property of how this is built, not only a policy."))}</p>

<h2>{e(t(lang, "プライバシー", "Privacy"))}</h2>
{t(lang, analytics_ja, analytics_en)}

<h2>{e(t(lang, "出典とライセンス", "Sources and licences"))}</h2>
{table(t(lang, "収集しているのはこの3つだけです。利用条件が確認できない取得元からは、1件も取り込みません。", "These are the only sources. Nothing is ingested from a source whose reuse terms cannot be verified."), [t(lang, "出典", "Source"), "URL", t(lang, "ライセンス", "Licence"), t(lang, "条件", "Terms"), t(lang, "収録量", "Volume")], sources_rows)}
<p class="small">{e(t(lang, "当社が生成した集計データは CC BY 4.0、収集コードは MIT で公開します。再配布の際は各ページの出典表記をそのままお使いください。", "Our derived aggregates are CC BY 4.0 and the collector code is MIT. When you redistribute, carry the attribution string shown on the page."))}</p>
{attribution_block(lang, source_notices(lang))}

<h2>{e(t(lang, "方法論", "Methodology"))}</h2>
{t(lang, method_ja, method_en)}

<h2>{e(t(lang, "収集のしかた", "How the collection behaves"))}</h2>
<ul class="clean">
<li>{e(t(lang, "robots.txt を取得して従います。ホストごとに24時間ごとに再確認します。", "robots.txt is fetched and obeyed, re-checked every 24 hours per host."))}</li>
<li>{e(t(lang, "同一ホストへのリクエストは2秒以上あけ、並列化しません。", "At least 2 seconds between requests to the same host, with no parallelism."))}</li>
<li>{e(t(lang, "連絡先 URL を含む識別可能な User-Agent を必ず送ります。ブラウザを装うことはしません。", "Every request carries an identifying User-Agent with a contact URL. We never spoof a browser."))}</li>
<li>{e(t(lang, "条件付きリクエストとローカルキャッシュを使い、変わっていないものは取りに行きません。", "Conditional requests and a local cache mean an unchanged file is not re-fetched."))}</li>
<li>{e(t(lang, "ログインしません。アクセス制限を回避しません。CAPTCHA を解きません。", "We never log in, never bypass an access control, and never solve a CAPTCHA."))}</li>
<li>{e(t(lang, "取得元から停止のご連絡をいただいた場合、24時間以内に当該ホストへの収集を止めます。", "If a source asks us to stop, we halt collection from that host within 24 hours."))}</li>
</ul>

<h2>{e(t(lang, "訂正・削除のご依頼", "Corrections and removals"))}</h2>
{t(lang, removal_ja, removal_en)}

<h2>{e(t(lang, "連絡先", "Contact"))}</h2>
<p>{e(t(lang, "連絡手段は GitHub Issues のみです。", "GitHub Issues is the only channel."))} <a href="{e(GITHUB_ISSUES)}" rel="noopener">{e(GITHUB_ISSUES)}</a></p>
<p class="small muted">{e(t(lang, "運営者個人の氏名・住所・連絡先は公開していません。決済が始まった際の販売者は海外の Merchant of Record です。このページの内容は法的助言ではありません。", "We do not publish the operator's name, address or personal contact details. When payments open, the seller of record is a foreign merchant of record. Nothing on this page is legal advice."))}</p>
<p class="small faint">{e(t(lang, "最終更新: ", "Last updated: "))}{e(built_at[:10])}{e(t(lang, "（UTC。このページは毎回のビルドで生成されるため、日付はビルド日です。公開データの取得日は上の表と各統計ページに記載しています。）", " UTC (this page is regenerated by every build, so the date is the build date; the retrieval dates of the source data are in the table above and on the statistics pages)"))}</p>
"""

    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
    )


def build_root(bet_a_pages, bet_c, built_at):
    n = len(bet_a_pages)
    body = f"""
<h1>Deltakura <span class="faint">デルタ蔵</span></h1>
<p class="lede">An anonymous-by-design, open-methodology archive of Japanese public-data histories.<br>
日本の公開データの履歴を、方法論を公開したまま蓄積するアーカイブです。</p>
<div class="grid c2">
<div class="card">
<h3>日本語</h3>
<p>国の落札実績統計（{n} ページ）と、国税庁 法人番号の日次差分アーカイブ（{bet_c['total']:,} レコード）。出典・ライセンス・匿名化ルールをすべて公開しています。メールアドレスは集めません。</p>
<div class="spacer"></div>
<p><a href="/ja/">日本語のサイトへ →</a></p>
</div>
<div class="card">
<h3>English</h3>
<p>National tender-award statistics ({n} pages) and a daily corporate-registry diff archive ({bet_c['total']:,} records), each carrying its source, its licence and the anonymisation rules applied. No email is ever collected.</p>
<div class="spacer"></div>
<p><a href="/en/">Go to the English site →</a></p>
</div>
</div>
<p class="small muted"><a href="/feeds/nta-diff.xml">RSS</a> · <a href="/data/">JSON</a> · <a href="{e(GITHUB_ORG)}" rel="noopener">GitHub</a></p>
{attribution_block("ja", source_notices("ja"))}
"""
    return render_page(
        lang="ja",
        path="/",
        title="Deltakura / デルタ蔵 — Japanese public-data histories",
        desc_ja="日本の公開データの履歴を、方法論を公開したまま蓄積する匿名運営のアーカイブ。落札実績統計と法人番号 差分。",
        desc_en="An anonymous-by-design, open-methodology archive of Japanese public-data histories: tender award statistics and the corporate registry diff.",
        body=body,
        alt_path="/en/",
    )


# --------------------------------------------------------------------------
# Feed, sitemap, robots, data index
# --------------------------------------------------------------------------


def rfc822(d: dt.date) -> str:
    return dt.datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=dt.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )


def build_feed(bet_c, built_at):
    """Weekly RSS: one item per ISO week, carrying that week's daily counts."""
    weeks = collections.OrderedDict()
    for d in bet_c["days"]:
        day = dt.date.fromisoformat(d)
        y, w, _ = day.isocalendar()
        weeks.setdefault((y, w), []).append(day)

    items = []
    for (y, w), days in sorted(weeks.items(), reverse=True):
        monday = min(days) - dt.timedelta(days=min(days).weekday())
        sunday = monday + dt.timedelta(days=6)
        total = sum(bet_c["daily"][d.isoformat()] for d in days)
        proc = collections.Counter()
        for d in days:
            for code, n in bet_c["daily_process"][d.isoformat()].items():
                proc[code] += n

        lines = [
            f"<p>{monday.isoformat()} – {sunday.isoformat()}: "
            f"<strong>{total:,}</strong> registry-diff records over {len(days)} publication "
            f"{'day' if len(days) == 1 else 'days'} "
            f"/ 公表日 {len(days)} 日分・{total:,} レコード。</p>",
            "<ul>",
        ]
        for d in sorted(days):
            lines.append(
                f"<li>{d.isoformat()}: {bet_c['daily'][d.isoformat()]:,}</li>"
            )
        lines.append("</ul><p>")
        parts = []
        for code, n in sorted(proc.items(), key=lambda kv: -kv[1]):
            ja, en = PROCESS_CODES.get(code, (code, code))
            parts.append(f"{en} / {ja}: {n:,}")
        lines.append("; ".join(parts))
        lines.append(f"</p><p>{NTA_FEED_NOTICE}</p>")
        desc = "".join(lines)

        guid = f"{BASE_URL}/feeds/nta-diff.xml#{y}-W{w:02d}"
        items.append(
            "<item>"
            f"<title>{e(f'週次サマリ / Weekly summary {y}-W{w:02d}: {total:,} records over {len(days)} publication day' + ('' if len(days) == 1 else 's'))}</title>"
            f"<link>{e(BASE_URL)}/ja/bet-c/</link>"
            f"<guid isPermaLink=\"false\">{e(guid)}</guid>"
            f"<pubDate>{rfc822(sunday)}</pubDate>"
            f"<description>{e(desc)}</description>"
            f"<category>{e('法人番号 / corporate registry')}</category>"
            "</item>"
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>Deltakura — 法人番号 差分アーカイブ / Corporate registry diff (weekly)</title>
<link>{e(BASE_URL)}/ja/bet-c/</link>
<atom:link href="{e(BASE_URL)}/feeds/nta-diff.xml" rel="self" type="application/rss+xml"/>
<description>{e("国税庁 法人番号公表サイトの日次差分件数の週次サマリ。個別の法人名は配信しません。 / Weekly summary of daily record counts in the Japanese National Tax Agency corporate-number registry diff. No individual records, no names.")}</description>
<language>ja</language>
<generator>Deltakura site build</generator>
<lastBuildDate>{e(dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"))}</lastBuildDate>
<copyright>{e("Derived aggregates CC BY 4.0. " + NTA_FEED_NOTICE)}</copyright>
<ttl>1440</ttl>
{chr(10).join(items)}
</channel>
</rss>
"""
    return xml


def build_sitemap(paths, built_at):
    urls = "".join(
        f"<url><loc>{e(BASE_URL + p)}</loc><lastmod>{built_at[:10]}</lastmod></url>"
        for p in sorted(paths)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + urls
        + "</urlset>\n"
    )


def build_robots():
    return f"""# Deltakura. Everything here is meant to be read, indexed and cited,
# by people and by machines alike.
User-agent: *
Allow: /

Sitemap: {BASE_URL}/sitemap.xml
"""


def build_llms_txt(bet_a_pages, bet_a_prov, bet_c, built_at):
    return f"""# Deltakura ({BRAND_JA})

> An anonymous-by-design, open-methodology archive of Japanese public-data
> histories. Unofficial. Every number carries its source, its licence and its
> retrieval date. Derived aggregates are CC BY 4.0; please keep the attribution
> string, the licence name and the modification notice together when you quote
> them.

Every figure below is derived from government open data used in modified form.
Quote the three lines that belong to a figure - source, licence, modification -
as one unit:

- {PPORTAL_ATTRIB}
  Licence: {PPORTAL_LICENSE_NAME}. {PPORTAL_MODIFIED}
- {NTA_ATTRIB}
  Licence: {NTA_LICENSE_NAME}. {NTA_MODIFIED}

Data last updated: awards {bet_a_prov['retrieved_at'][:10]}, registry diff {bet_c['last_day']}.
Site built: {built_at[:10]} (UTC, like every date and timestamp on this site).

## Datasets

- National tender award statistics: {bet_a_prov['records']:,} awards, FY2013-FY2026,
  {len(bet_a_pages)} pages at /ja/bet-a/ and /en/bet-a/, one per purchasing sector and
  fiscal year. JSON twin of every page under /data/bet-a/.
  Known limits: the source publishes no predicted price, so there is NO award ratio
  (落札率); the prefecture is the winner's registered head office, not the place of
  performance; the sector says which ministry bought, not what was sold; sector-wide
  medians and quartiles are estimated from pre-aggregated buckets (method stated on
  every page and in every JSON file).
  Attribution: {PPORTAL_ATTRIB}
  Licence: {PPORTAL_LICENSE_NAME}. {PPORTAL_MODIFIED}
- Corporate-number registry diff archive: {bet_c['total']:,} records over
  {bet_c['n_files']} publication days ({bet_c['first_day']} to {bet_c['last_day']}),
  at /ja/bet-c/ and /en/bet-c/. JSON at /data/bet-c/daily.json, weekly RSS at
  /feeds/nta-diff.xml. The publisher deletes each daily file after about 40 days.
  Corporations and public bodies only; corporate numbers are not issued to sole
  proprietors and no individual's name is held.
  Attribution: {NTA_ATTRIB}
  Licence: {NTA_LICENSE_NAME}. {NTA_MODIFIED}
- Japan hiring first-seen index: announced, NOT published. The sources' reuse terms
  have been reviewed and permit reuse under conditions; publication is a separate
  approval that has not been given, so no record exists publicly and the collector
  runs with publication disabled.

## Rules this project holds itself to

- No email address is collected anywhere; there is no form on the site.
- No cookies, and no analytics beacon: page visits are not counted at all today.
  If cookie-less analytics is added later, /ja/privacy.html and /en/privacy.html
  and the Content-Security-Policy are updated in the same change.
- Individuals, including sole proprietors, are masked at ingestion. No winner
  directory is built for anyone.
- Contact and removal requests: GitHub Issues only, {GITHUB_ISSUES}

## Machine endpoints

- /data/bet-a/index.json — list of every statistics page and its JSON endpoint
- /data/bet-a/<sector-slug>-fy<year>.json — one statistics page
- /data/bet-c/daily.json — daily registry-diff counts
- /data/site.json — build status and data freshness
- /feeds/nta-diff.xml — weekly RSS
- /sitemap.xml
"""


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


#: Every data surface must name the upstream licence and declare that the data
#: is used in modified form. 政府標準利用規約 2.0 and 公共データ利用規約 1.0 both
#: require it, and this project makes it mechanical rather than a matter of
#: judgement: one template once omitted both, and the check below is what stops
#: that happening again.
LICENCE_NAMES = ("政府標準利用規約", "公共データ利用規約")
MODIFICATION_MARKERS = ("加工", "modified")


#: A generated file is a data surface if it carries an attribution string or
#: shows one of the build's headline figures, whatever template produced it.
#: The audit that prompted this found the home pages, the JSON directory and
#: llms.txt rendering Bet-A and Bet-C figures with 出典 and nothing else,
#: because the check only ran over pages emitted with is_data=True. It now
#: runs over every generated file.
ATTRIB_MARKER = "出典："
TEXT_SUFFIXES = frozenset({".html", ".json", ".txt", ".xml", ".js", ".css", ".md"})


def figure_patterns(figures):
    """Match a headline figure as rendered, with and without separators.

    Anything under 1,000 is too common a digit string to match safely (404
    contains 40), so the guard is built from the large counts only: they are
    the ones a page cannot show without taking on the attribution duty.
    """
    pats = []
    for n in sorted({int(f) for f in figures if int(f) >= 1000}):
        pats.append(re.compile(r"(?<![\d,])" + f"{n:,}" + r"(?![\d,])"))
        pats.append(re.compile(r"(?<!\d)" + str(n) + r"(?!\d)"))
    return pats


def json_values_text(text: str) -> str:
    """The string VALUES of a JSON document, joined.

    A key named "modified" is not a modification notice, and a provenance block
    left empty must not pass for a full one. So the JSON surfaces are tested on
    what they say, not on what their field names are called.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return text
    found, stack = [], [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            found.append(node)
    return "\n".join(found)


def attribution_problems(rel: str, text: str):
    """Source indication + licence name + modification notice, or a problem."""
    problems = []
    if "出典" not in text:
        problems.append(f"{rel}: data surface without an attribution string")
    if not any(name in text for name in LICENCE_NAMES):
        problems.append(f"{rel}: data surface that does not name the upstream licence")
    lowered = text.lower()
    if not any(marker in lowered for marker in MODIFICATION_MARKERS):
        problems.append(f"{rel}: data surface without a modification notice")
    return problems


def check(out: Path, data_pages, data_json=frozenset(), figures=()):
    problems = []
    total = 0
    biggest = ("", 0)
    figure_res = figure_patterns(figures)
    for p in sorted(out.rglob("*")):
        if p.is_dir():
            continue
        size = p.stat().st_size
        total += size
        rel = p.relative_to(out).as_posix()
        if size > biggest[1]:
            biggest = (rel, size)
        text = p.read_text(encoding="utf-8") if p.suffix in TEXT_SUFFIXES else ""
        # Every generated file that shows a figure derived from the sources, or
        # that quotes an attribution string, owes the full three-part notice:
        # the HTML pages, their JSON twins, llms.txt and the feed alike. This
        # is deliberately not limited to the pages the build labels as data.
        if (
            rel in data_pages
            or rel in data_json
            or ATTRIB_MARKER in text
            or any(pat.search(text) for pat in figure_res)
        ):
            problems += attribution_problems(
                rel, json_values_text(text) if p.suffix == ".json" else text
            )
        if p.suffix != ".html":
            continue
        if size > 100 * 1024:
            problems.append(f"{rel}: {size / 1024:.0f} KB exceeds the 100 KB page cap")
        if "<title>" not in text:
            problems.append(f"{rel}: no <title>")
        if 'name="description:ja"' not in text or 'name="description:en"' not in text:
            problems.append(f"{rel}: missing a meta description in both languages")
        if "mailto:" in text:
            problems.append(f"{rel}: contains a mailto: link")
        if 'type="email"' in text or "<form" in text:
            problems.append(f"{rel}: contains a form or an email input")
        for m in EMAIL_RE.finditer(text):
            # the bot's noreply address never appears on the site; nothing should match.
            problems.append(f"{rel}: looks like an email address: {m.group(0)}")
        if rel in data_pages:
            if '"@type":"Dataset"' not in text and '"@type":"DataCatalog"' not in text:
                problems.append(f"{rel}: data page without Dataset JSON-LD")
    if total > 50 * 1024 * 1024:
        problems.append(f"total build {total / 1024 / 1024:.1f} MB exceeds 50 MB")
    return problems, total, biggest


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Deltakura static site.")
    ap.add_argument("--clean", action="store_true", help="remove site/public/ first")
    ap.add_argument("--check", action="store_true", help="run the invariant checks (default on)")
    ap.add_argument("--no-check", dest="check", action="store_false")
    ap.set_defaults(check=True)
    args = ap.parse_args(argv)

    built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.clean and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    print("reading data/ ...")
    bet_a_pages = load_bet_a()
    bet_a_prov = load_bet_a_provenance(bet_a_pages)
    bet_c = load_bet_c()
    print(
        f"  bet A: {len(bet_a_pages)} sector-year pages, {bet_a_prov['records']:,} awards"
        f" aggregated ({bet_a_prov['records_parsed']:,} parsed,"
        f" {bet_a_prov['suppressed']:,} suppressed)\n"
        f"  bet C: {bet_c['n_files']} days, {bet_c['total']:,} records"
    )

    years_by_sector = collections.defaultdict(list)
    for p in bet_a_pages:
        years_by_sector[p["sector_slug"]].append(p["fiscal_year"])
    for k in years_by_sector:
        years_by_sector[k].sort()

    written = []
    data_pages = set()

    def emit(path, doc, is_data=False):
        rel = path.lstrip("/")
        if rel.endswith("/") or rel == "":
            rel += "index.html"
        write(OUT / rel, doc)
        written.append(path)
        if is_data:
            data_pages.add(rel)

    # assets
    write(OUT / "assets" / "style.css", CSS)
    write(OUT / "assets" / "intent.js", JS)
    write(
        OUT / "assets" / "config.js",
        "/* Runtime configuration. The intent endpoint is filled in once the\n"
        "   Cloudflare Worker exists. Empty means: count locally, post nothing. */\n"
        'window.DELTAKURA = { intentEndpoint: "%s" };\n' % INTENT_ENDPOINT,
    )

    # root
    emit("/", build_root(bet_a_pages, bet_c, built_at))

    index_entries = []
    for lang in ("ja", "en"):
        path, _alt, doc = build_home(lang, bet_a_pages, bet_a_prov, bet_c, built_at)
        emit(path, doc)

        path, _alt, doc = build_bet_a_index(lang, bet_a_pages, bet_a_prov, built_at)
        emit(path, doc, is_data=True)

        for page in bet_a_pages:
            p, _a, doc, payload = build_bet_a_page(
                lang, page, years_by_sector[page["sector_slug"]], bet_a_prov, built_at
            )
            emit(p, doc, is_data=True)
            if lang == "ja":  # the JSON is language-neutral; write it once
                write_json(
                    OUT / bet_a_json_path(page["sector_slug"], page["fiscal_year"]).lstrip("/"),
                    payload,
                )
                index_entries.append(
                    {
                        "fiscal_year": page["fiscal_year"],
                        "sector_ja": page["sector"],
                        "sector_en": page["sector_en"],
                        "sector_slug": page["sector_slug"],
                        "n_awards": page["n_awards"],
                        "amount_sum_jpy": page["amount_sum_jpy"],
                        "amount_median_jpy": page["amount_median_jpy"],
                        "amount_median_estimated": not page["quantiles_exact"],
                        "json": bet_a_json_path(page["sector_slug"], page["fiscal_year"]),
                        "page_ja": bet_a_page_path("ja", page["sector_slug"], page["fiscal_year"]),
                        "page_en": bet_a_page_path("en", page["sector_slug"], page["fiscal_year"]),
                    }
                )

        path, _alt, doc = build_bet_c(lang, bet_c, built_at)
        emit(path, doc, is_data=True)

        path, _alt, doc = build_pricing(lang, built_at)
        emit(path, doc)

        path, _alt, doc = build_privacy(lang, bet_a_prov, bet_c, built_at)
        emit(path, doc)

    # ---- JSON endpoints
    write_json(
        OUT / "data" / "bet-a" / "index.json",
        {
            "schema_version": "0.1",
            "dataset": "bet-a-national-tender-awards",
            "description": (
                "Statistics pages for Japanese national procurement awards, one per purchasing "
                "sector and fiscal year. No award ratio exists in this dataset: the publisher "
                "does not publish a predicted price."
            ),
            "n_pages": len(index_entries),
            "awards_aggregated": bet_a_prov["records"],
            "awards_normalized": bet_a_prov["records_normalized"],
            "awards_parsed_from_source": bet_a_prov["records_parsed"],
            "awards_deduplicated": bet_a_prov["deduplicated"],
            "awards_suppressed": bet_a_prov["suppressed"],
            "suppression_rule": (
                "A fiscal year x prefecture x sector bucket holding one or two masked "
                "individuals is dropped entirely, so the counts on these pages are smaller "
                "than the normalized store, which is in turn smaller than the raw rows "
                "parsed from the publisher's files after deduplication."
            ),
            "provenance": {
                "source_name": "調達ポータル 落札実績",
                "source_url": "https://www.p-portal.go.jp/",
                "license": "政府標準利用規約(第2.0版)",
                "attribution": PPORTAL_ATTRIB,
                "prefecture_join_attribution": NTA_ATTRIB,
                "prefecture_join_license": "公共データ利用規約(第1.0版)",
                # Both upstream licences require a modification notice, so it
                # travels on the index the same way it travels on every page.
                "modified": (
                    "出典データを加工して利用しています / Modified from the sources: "
                    "parsed, deduplicated, anonymised and aggregated to counts by Deltakura"
                ),
                "retrieved_at": bet_a_prov["retrieved_at"],
                "derived_aggregates_license": "CC BY 4.0",
            },
            "pages": sorted(index_entries, key=lambda x: (x["sector_slug"], x["fiscal_year"])),
            "generated_at": built_at,
        },
    )

    write_json(
        OUT / "data" / "bet-c" / "daily.json",
        {
            "schema_version": "0.1",
            "dataset": "bet-c-nta-corporate-registry-diff",
            "description": (
                "Daily record counts in the Japanese National Tax Agency corporate-number registry "
                "diff, retained past the publisher's ~40-day window. Counts only; the page and this "
                "endpoint carry no individual records and no names."
            ),
            "coverage": {"first_day": bet_c["first_day"], "last_day": bet_c["last_day"],
                         "publication_days": bet_c["n_files"], "records": bet_c["total"]},
            "publication_calendar": (
                "No file is produced on weekends, Japanese public holidays or 12/29-01/03; "
                "a missing date is the publisher's calendar, not a collection failure."
            ),
            "upstream_retention_days": 40,
            "daily": [
                {
                    "date": d,
                    "records": bet_c["daily"][d],
                    "by_process_code": bet_c["daily_process"][d],
                }
                for d in bet_c["days"]
            ],
            "process_codes": {
                k: {"ja": PROCESS_CODES.get(k, (k, k))[0], "en": PROCESS_CODES.get(k, (k, k))[1],
                    "records": v}
                for k, v in bet_c["process"].items()
            },
            "entity_kind_codes": {
                k: {"ja": KIND_CODES.get(k, (k, k))[0], "en": KIND_CODES.get(k, (k, k))[1],
                    "records": v}
                for k, v in bet_c["kinds"].items()
            },
            "personal_data": (
                "None. Corporate numbers are issued to corporations and public bodies, not to sole "
                "proprietors; the diff layout carries no representative-person field, and the parser "
                "refuses any column matching a personal-field pattern."
            ),
            "provenance": {
                "source_name": "国税庁 法人番号公表サイト 差分データ",
                "source_url": "https://www.houjin-bangou.nta.go.jp/download/sabun/",
                "license": "公共データ利用規約(第1.0版)",
                "attribution": NTA_ATTRIB,
                "modified": (
                    "出典データを加工して利用しています / Modified from the source:"
                    " normalised, deduplicated and re-encoded by Deltakura"
                ),
                "retrieved_at": bet_c["retrieved_at"],
                "derived_aggregates_license": "CC BY 4.0",
            },
            "generated_at": built_at,
        },
    )

    write_json(
        OUT / "data" / "site.json",
        {
            "schema_version": "0.1",
            "brand": BRAND,
            "built_at": built_at,
            "official": False,
            "data_last_updated": {
                "bet_a_tender_awards": bet_a_prov["retrieved_at"],
                "bet_c_registry_diff": bet_c["last_day"],
            },
            "datasets": {
                "bet_a_tender_awards": {
                    "status": "published",
                    "records": bet_a_prov["records"],
                    "pages": len(index_entries),
                    "endpoint": "/data/bet-a/index.json",
                },
                "bet_b_hiring_first_seen": {
                    "status": "not_published",
                    "reason": (
                        "source reuse terms reviewed and permit reuse under conditions; "
                        "publication is a separate approval that has not been given, so "
                        "no records are published"
                    ),
                    "records": 0,
                },
                "bet_c_registry_diff": {
                    "status": "published",
                    "records": bet_c["total"],
                    "publication_days": bet_c["n_files"],
                    "endpoint": "/data/bet-c/daily.json",
                    "feed": "/feeds/nta-diff.xml",
                },
            },
            # site.json carries the same headline counts as the home pages, so
            # it carries the same three-part attribution they do.
            "provenance": {
                "bet_a_tender_awards": {
                    "source_name": "調達ポータル 落札実績",
                    "source_url": "https://www.p-portal.go.jp/",
                    "license": PPORTAL_LICENSE_NAME,
                    "attribution": PPORTAL_ATTRIB,
                    "modified": PPORTAL_MODIFIED,
                },
                "bet_c_registry_diff": {
                    "source_name": "国税庁 法人番号公表サイト 差分データ",
                    "source_url": "https://www.houjin-bangou.nta.go.jp/download/sabun/",
                    "license": NTA_LICENSE_NAME,
                    "attribution": NTA_ATTRIB,
                    "modified": NTA_MODIFIED,
                },
                "derived_aggregates_license": "CC BY 4.0",
            },
            "contact": {"github_issues": GITHUB_ISSUES, "email": None},
            "privacy": {"cookies": False, "email_collection": False, "third_party_trackers": False},
        },
    )

    # a tiny human-readable index of the endpoints
    endpoints = [
        ("/data/site.json", "build status and data freshness"),
        ("/data/bet-a/index.json", "every tender-award statistics page"),
        ("/data/bet-a/&lt;sector-slug&gt;-fy&lt;year&gt;.json", "one statistics page"),
        ("/data/bet-c/daily.json", "daily registry-diff counts"),
        ("/feeds/nta-diff.xml", "weekly RSS of the registry diff"),
    ]
    rows = [[f'<code>{u}</code>' if "&lt;" in u else f'<a href="{u}"><code>{u}</code></a>', e(d)] for u, d in endpoints]
    emit(
        "/data/",
        render_page(
            lang="en",
            path="/data/",
            title="JSON endpoints | Deltakura",
            desc_ja="Deltakura の JSON エンドポイント一覧。各統計ページに対応する機械可読ファイルと、日次件数、週次 RSS。",
            desc_en="The Deltakura JSON endpoints: a machine-readable twin of every statistics page, the daily registry-diff counts, and the weekly RSS feed.",
            body=f"""
<h1>JSON endpoints</h1>
<p class="lede">Every page on this site has a machine-readable twin. No key, no rate limit today, CORS open. Each file carries its own provenance block: source, licence, attribution string, retrieval date, and the method used to derive anything that is not a raw count.</p>
{table("", ["Endpoint", "What it returns"], rows)}
<p class="small">Derived aggregates are CC BY 4.0 — keep the <code>provenance.attribution</code> string when you redistribute. Counts are exact; anything estimated says so in the file.</p>
{attribution_block("en", source_notices("en"))}
""",
            alt_path="/data/",
        ),
    )

    # ---- feed, sitemap, robots, llms.txt
    write(OUT / "feeds" / "nta-diff.xml", build_feed(bet_c, built_at))
    write(OUT / "sitemap.xml", build_sitemap(written, built_at))
    write(OUT / "robots.txt", build_robots())
    write(OUT / "llms.txt", build_llms_txt(bet_a_pages, bet_a_prov, bet_c, built_at))
    write(
        OUT / "404.html",
        render_page(
            lang="en",
            path="/404.html",
            title="Not found | Deltakura",
            desc_ja="お探しのページは見つかりませんでした。",
            desc_en="That page does not exist on this site.",
            body='<h1>404</h1><p class="lede">That page does not exist. / お探しのページは見つかりませんでした。</p>'
            '<p><a href="/ja/">日本語トップ</a> · <a href="/en/">English home</a> · '
            '<a href="/data/">JSON endpoints</a></p>',
            alt_path="/404.html",
        ),
    )

    print(f"wrote {len(written)} pages into {OUT}")

    # The machine-readable twins of the data pages. Same publication duty, so
    # the same check runs over them.
    data_json = {"data/bet-a/index.json", "data/bet-c/daily.json"} | {
        entry["json"].lstrip("/") for entry in index_entries
    }

    if args.check:
        # The figures that make a page a data surface. Only the large counts:
        # see figure_patterns().
        figures = (
            bet_a_prov["records"],
            bet_a_prov["records_parsed"],
            bet_a_prov["records_normalized"],
            bet_c["total"],
            bet_c["total_raw"],
        )
        problems, total, biggest = check(OUT, data_pages, data_json, figures)
        n_files = sum(1 for p in OUT.rglob("*") if p.is_file())
        print(
            f"check: {n_files} files, {total / 1024 / 1024:.2f} MB total, "
            f"largest {biggest[0]} at {biggest[1] / 1024:.0f} KB"
        )
        if problems:
            print(f"FAILED {len(problems)} check(s):", file=sys.stderr)
            for p in problems[:40]:
                print("  - " + p, file=sys.stderr)
            return 1
        print("check: all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
