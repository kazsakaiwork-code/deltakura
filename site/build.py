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

# Helper modules next to this file (standard library only).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import articles as articles_mod  # noqa: E402  front matter + the Markdown subset
import charts  # noqa: E402  the 40-day strip and the small charts
from icons import icon  # noqa: E402  the in-house line icon set
from theme import CSS  # noqa: E402  the one stylesheet

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
OPERATOR_NAME = "Sirevo"
OPERATOR_URL = "https://sirevo.jp/"
GITHUB_ORG = GITHUB_REPO
GITHUB_ISSUES = GITHUB_REPO + "/issues"
GITHUB_CORE = GITHUB_REPO + "/tree/main/crawlers"
GITHUB_SITE = GITHUB_REPO + "/tree/main/site"
# The live Worker (api/, env "production"). firebase.json's CSP connect-src
# must name exactly this host; change both in the same commit.
INTENT_ENDPOINT = "https://deltakura-api.deltakura.workers.dev/v0/intent"

# Every feed the site links to is served, and counted, by the same Worker
# (api/src/routes/feeds.ts, api/src/feedcount.ts): distinct fetchers per feed
# and UTC day via a salted hash, no raw IP stored. The static copies this build
# writes under /feeds/ stay the source the Worker relays; links, <link
# rel="alternate"> and the self URLs inside the feeds point at the Worker.
API_BASE = "https://deltakura-api.deltakura.workers.dev"
FEED_BASE = API_BASE + "/v0/feeds"
FEED_URLS = {
    "articles.xml": FEED_BASE + "/articles.xml",
    "articles.json": FEED_BASE + "/articles.json",
    "nta-diff.xml": FEED_BASE + "/nta-diff.xml",
    "nta-diff.json": FEED_BASE + "/nta-diff.json",
}
FEED_STATS_URL = FEED_BASE + "/stats"

# The MCP server (mcp/). Its npm package is published only after operator
# approval; until then the site links to the source and says so in one line.
MCP_NPM_PUBLISHED = False
MCP_PACKAGE = "@deltakura/mcp"
GITHUB_MCP = GITHUB_REPO + "/tree/main/mcp"
GITHUB_COMMITS_ATOM = GITHUB_REPO + "/commits/main.atom"

# Articles: one Markdown file each (format: site/articles.py).
# DELTAKURA_ARTICLES_DIR points the build at another directory (a draft set, a
# test fixture); the default is the committed one.
CONTENT_DIR = Path(
    os.environ.get("DELTAKURA_ARTICLES_DIR", "").strip() or (SITE_DIR / "content" / "articles")
).expanduser().resolve()

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

PREF_JA = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県",
    "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
    "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府",
    "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
    "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
    "鹿児島県", "沖縄県",
)

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
    # Per-day prefecture counts (summary.json carries them since 2026-09-27);
    # None for a day that has none, so a period chart can refuse to guess.
    daily_prefs = {
        d: (dict(days_map[d]["prefectures"]) if isinstance(days_map[d].get("prefectures"), dict) else None)
        for d in days
    }
    coverage = summary.get("coverage") or {}

    return {
        "days": days,
        "daily": daily,
        "daily_raw": daily_raw,
        "daily_process": daily_process,
        "daily_prefs": daily_prefs,
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

# The stylesheet lives in theme.py (token system: ops/design/site_redesign_v1.md).

JS = """/* Deltakura intent button. No cookies, no email, no third party.

   It records that someone asked to be notified about a product, and that a page
   carrying such a button was seen. Nothing else.

   CONTRACT with the Worker (api/src/routes/intent.ts). The POST body is exactly

       {"product": <allowlisted id>, "kind": "click" | "view", "client_id": <str>}

   `product` must be one of the Worker's allowlisted ids or the request is
   refused with 400 unknown_product, so every id in the markup is validated at
   build time against INTENT_PRODUCTS in site/build.py, which is asserted
   against the Worker's own list by api/test/intent-contract.test.ts.

   `kind` is why the pay-intent metric exists at all: intent_rate_14d is
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


# The languages that have at least one article; set in main() before any page
# is rendered. "記事" joins the navigation only where there is something to read.
ARTICLE_LANGS: set = set()


def nav_items(lang: str):
    p = f"/{lang}/"
    items = [
        (p + "bet-a/", t(lang, "落札統計", "Tender awards")),
        (p + "bet-c/", t(lang, "法人番号の差分", "Registry diff")),
    ]
    if lang in ARTICLE_LANGS:
        items.append((p + "articles/", t(lang, "記事", "Articles")))
    return items + [
        (p + "pricing.html", t(lang, "料金", "Pricing")),
        (p + "privacy.html", t(lang, "プライバシー", "Privacy")),
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
    attribution=None,
    switch_path=None,
    og_type: str = "website",
):
    """Return the html string.

    `switch_path` is where the language link goes when the page has no
    counterpart in the other language (alt_path == path): an article index,
    for example, rather than a page that does not exist.

    `attribution` is a list of source lines. It is rendered once, in the
    footer zone, on every page that shows a figure derived from the sources
    (check() enforces the three parts: source, licence, modification).
    """
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
            # "<" is escaped so that no string in the data can close the element.
            + json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
            + "</script>\n"
        )

    nav = "".join(
        '<a href="{}"{}>{}</a>'.format(
            e(href), ' aria-current="page"' if path.startswith(href) else "", e(label)
        )
        for href, label in nav_items(lang)
    )
    if switch_path and alt_path == path:
        nav += '<a class="lang" href="{}" hreflang="{}">{}</a>'.format(
            e(switch_path), other, "English" if lang == "ja" else "日本語"
        )
    else:
        nav += '<a class="lang" href="{}" hreflang="{}" rel="alternate">{}</a>'.format(
            e(alt_path), other, "English" if lang == "ja" else "日本語"
        )

    attrib = attribution_block(lang, attribution) if attribution else ""
    disclaimer = t(
        lang,
        "Deltakura は非公式アーカイブです。数値は出典の原本でご確認ください。",
        "Deltakura is an unofficial archive. Check the original source before relying on a number.",
    )
    foot_links = [
        (OPERATOR_URL, t(lang, "運営: Sirevo", "Operated by Sirevo"), True),
        (GITHUB_ORG, "GitHub", True),
        (GITHUB_ISSUES, t(lang, "削除・訂正の依頼（GitHub Issues）", "Removal and corrections (GitHub Issues)"), True),
        (f"/{lang}/subscribe.html", t(lang, "購読（RSS・JSON Feed）", "Subscribe (RSS, JSON Feed)"), False),
        ("/data/", t(lang, "データファイル（JSON）", "Data files (JSON)"), False),
        (f"/{lang}/privacy.html", t(lang, "プライバシー", "Privacy"), False),
    ]
    links = "".join(
        '<a href="{}"{}>{}</a>'.format(e(h), ' rel="noopener"' if ext else "", e(label))
        for h, label, ext in foot_links
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
<meta property="og:type" content="{e(og_type)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:locale" content="{'ja_JP' if lang == 'ja' else 'en_US'}">
<meta name="robots" content="index,follow,max-snippet:-1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="theme-color" content="#F2F3F0" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#15181B" media="(prefers-color-scheme: dark)">
<link rel="stylesheet" href="/assets/style.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M8 2l6 11H2z' fill='%233E7F74'/%3E%3C/svg%3E">
<link rel="alternate" type="application/rss+xml" title="Deltakura 記事 / Articles" href="{e(FEED_URLS['articles.xml'])}">
<link rel="alternate" type="application/feed+json" title="Deltakura 記事 / Articles (JSON Feed)" href="{e(FEED_URLS['articles.json'])}">
<link rel="alternate" type="application/rss+xml" title="Deltakura Registry Diff (weekly)" href="{e(FEED_URLS['nta-diff.xml'])}">
{blocks}</head>
<body>
<a class="skip" href="#main">{e(t(lang, "本文へスキップ", "Skip to content"))}</a>
<header class="site"><div class="wrap">
<a class="brand" href="/{lang}/">{icon("kura")}{BRAND}<small>{BRAND_JA}</small></a>
<nav class="site" aria-label="{e(t(lang, 'メインナビゲーション', 'Main navigation'))}">{nav}</nav>
</div></header>
<main id="main"><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
{attrib}
<p>{e(disclaimer)}</p>
<p class="foot-links">{links}</p>
<p>{e(t(lang, "コード: MIT ／ 集計データ: CC BY 4.0", "Code: MIT / Derived aggregates: CC BY 4.0"))}</p>
</div></footer>
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
    return (
        '<button class="intent" type="button" data-intent="{p}" data-done="{d}"{q}>{l}</button>'
    ).format(
        p=e(product),
        d=e(done),
        q=' data-variant="quiet"' if quiet else "",
        l=e(t(lang, label_ja, label_en)),
    )


def intent_note(lang: str) -> str:
    """The one factual line that goes with the notify buttons on a page."""
    return '<p class="fine">{}{}</p>'.format(
        icon("shield"),
        e(t(
            lang,
            "ボタンは押された回数だけを数えます。連絡先は受け取りません。",
            "The button counts clicks only. No contact details are taken.",
        )),
    )


def source_notices(lang: str):
    """The two upstream sources, one line each, carrying all three parts.

    Source indication + licence name + modification notice. Both upstream
    licences require all three, so no surface that shows a figure derived
    from them may carry fewer; check() fails the build on any generated file
    that shows a headline figure or an attribution string without them.
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
    head = t(lang, "出典", "Sources")
    items = "".join(f"<div>{e(x)}</div>" for x in lines)
    return f'<div class="attrib"><b>{e(head)}</b>{items}</div>'


def table(caption: str, headers, rows, aligns=None, cls=""):
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
    klass = f' class="{cls}"' if cls else ""
    return (
        f'<div class="tablewrap"><table{klass}>{cap}<thead><tr>{th}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def kpis(items):
    """A row of figures: [(value, label)]. Values are set in the data face."""
    cells = "".join(
        f'<li><span class="v">{e(v)}</span><span class="k">{e(k)}</span></li>' for v, k in items
    )
    return f'<ul class="kpis">{cells}</ul>'


def labels(items):
    """Data caveats as short labels next to the figure, not paragraphs."""
    return '<ul class="labels">' + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"


def heat_scale(values):
    """Sequential 青磁 classes h1..h6 by count sextile; h0 = no page.

    Returns (class_of, legend) where legend is [(class, range text)].
    """
    vs = sorted(v for v in values if v and v > 0)
    n = len(vs)
    cuts = [vs[min(n - 1, (n * k) // 6)] for k in range(1, 6)] if n else [1] * 5

    def cls(v):
        if not v or v <= 0:
            return "h0"
        return "h" + str(1 + sum(1 for c in cuts if v >= c))

    bounds = [1] + cuts
    legend = []
    for i in range(6):
        lo = bounds[i]
        hi = bounds[i + 1] - 1 if i < 5 else None
        if hi is not None and hi < lo:
            continue
        legend.append((f"h{i + 1}", f"{lo:,}+" if hi is None else f"{lo:,}–{hi:,}"))
    return cls, legend


# --------------------------------------------------------------------------
# Page builders
# --------------------------------------------------------------------------


def bet_a_grid(bet_a_pages):
    """(sectors sorted by slug, years, {(sector, fy): page}) for the heatmaps."""
    years = sorted({p["fiscal_year"] for p in bet_a_pages})
    sectors = sorted({p["sector"] for p in bet_a_pages}, key=lambda s: SECTORS[s][0])
    by_key = {(p["sector"], p["fiscal_year"]): p for p in bet_a_pages}
    return sectors, years, by_key


def build_home(lang, bet_a_pages, bet_a_prov, bet_c, built_at, arts=()):
    path = f"/{lang}/"
    alt = "/en/" if lang == "ja" else "/ja/"
    fy_max = max(p["fiscal_year"] for p in bet_a_pages)
    fy_min = min(p["fiscal_year"] for p in bet_a_pages)

    title = t(
        lang,
        "Deltakura（デルタ蔵）— 消える前に、蔵へ。",
        "Deltakura — into the storehouse, before it disappears",
    )
    desc_ja = (
        "国が公開し、やがて消すデータを毎晩保存するアーカイブ。"
        f"国の落札実績統計（FY{fy_min}–FY{fy_max}）と、国税庁 法人番号の日次差分。RSS と JSON でも配信。"
    )
    desc_en = (
        "An archive of Japanese public data that the state publishes and later deletes, saved every night: "
        f"national tender-award statistics (FY{fy_min}-FY{fy_max}) and the daily corporate-registry diff. "
        "Also as RSS and JSON."
    )

    # mini heatmap for the tender-award card, from the same counts as /bet-a/
    sectors, years, by_key = bet_a_grid(bet_a_pages)
    cls_of, _legend = heat_scale(p["n_awards"] for p in bet_a_pages)
    grid = [
        [(by_key[(s, y)]["n_awards"] if (s, y) in by_key else 0) for y in years]
        for s in sectors
    ]

    strip = charts.strip_figure(lang, bet_c["days"], bet_c["daily"], bet_c["total"])

    facts = [
        ("kura", f"{bet_c['total']:,}" + t(lang, "件", ""), t(lang, "法人番号の差分を保管", "registry-diff records kept")),
        ("tag", f"{bet_a_prov['records']:,}" + t(lang, "件", ""), t(lang, "国の落札を集計", "national tender awards aggregated")),
        ("moon", t(lang, "毎晩 03:30", "03:30 JST"), t(lang, "法人番号の差分を毎晩収集", "registry diff collected nightly")),
    ]
    facts_html = "".join(
        f'<li>{icon(i)}<span><span class="v">{e(v)}</span><span class="k">{e(k)}</span></span></li>'
        for i, v, k in facts
    )

    cards = f"""<div class="cards">
<article class="card">
<div class="card-h">{icon("tag")}<h3>{e(t(lang, "国の落札実績", "National tender awards"))}</h3></div>
{charts.heat_thumb(lang, grid, cls_of)}
<p class="grow">{e(t(lang, f"どの府省が、何件、いくらで発注したか。FY{fy_min}–FY{fy_max}。", f"Which ministries buy, how often, at what prices. FY{fy_min}-FY{fy_max}."))}</p>
<div class="act"><a class="btn primary" href="/{lang}/bet-a/">{e(t(lang, "統計を見る", "See the statistics"))}</a></div>
</article>
<article class="card">
<div class="card-h">{icon("kura")}<h3>{e(t(lang, "法人番号の差分", "Corporate registry diff"))}</h3></div>
{charts.spark_bars(lang, bet_c["days"], bet_c["daily"])}
<p class="grow">{e(t(lang, "新設・移転・閉鎖。法人登記の毎日の変化。", "New companies, moves and closures, day by day."))}</p>
<div class="act"><a class="btn" href="/{lang}/bet-c/">{e(t(lang, "アーカイブを見る", "Open the archive"))}</a></div>
</article>
<article class="card soon">
<div class="card-h">{icon("hiring")}<h3>{e(t(lang, "採用開始インデックス", "Hiring first-seen index"))}</h3></div>
<span class="soon-stamp">{e(t(lang, "準備中", "SOON"))}</span>
<div class="grow"></div>
<div class="act">{intent_button(lang, "bet_b_watchlist", "公開されたら知りたい", "Tell me when it opens")}</div>
</article>
</div>"""

    flow = f"""<ol class="flow">
<li>{icon("gov")}<span><b>{e(t(lang, "国の公開データ", "Public data"))}</b><small>{e(t(lang, "調達ポータル・国税庁", "Procurement portal, National Tax Agency"))}</small></span></li>
<li>{icon("moon")}<span><b>{e(t(lang, "自動で収集", "Collected automatically"))}</b></span></li>
<li>{icon("eye-off")}<span><b>{e(t(lang, "個人名を除いて集計", "Personal names removed, then counted"))}</b></span></li>
<li>{icon("kura")}<span><b>{e(t(lang, "蔵に保管", "Kept in the storehouse"))}</b></span></li>
<li class="out"><span><b>{e(t(lang, "届け先", "Delivered as"))}</b></span><span class="chips"><span class="chip">{icon("page")}{e(t(lang, "サイト", "This site"))}</span><span class="chip">{icon("rss")}RSS</span><span class="chip">{icon("braces")}API・MCP</span></span></li>
</ol>"""

    nots = [
        ("eye-off", t(lang, "個人名を扱いません", "No personal names")),
        ("ledger", t(lang, "落札者の名簿を作りません", "No winner directory")),
        ("mail-off", t(lang, "メールアドレスを集めません", "No email addresses collected")),
        ("cookie-off", t(lang, "Cookie を使いません", "No cookies")),
        ("tag", t(lang, "入札額の助言はしません", "No bidding advice")),
    ]
    tiles = "".join(f"<li>{icon(i)}<b>{e(x)}</b></li>" for i, x in nots)

    doors = f"""<ul class="doors">
<li>{icon("rss")}<span><a href="/{lang}/subscribe.html">RSS・JSON Feed</a><small>{e(t(lang, "記事と法人番号の差分", "Articles and the registry diff"))}</small></span></li>
<li>{icon("braces")}<span><a href="/data/">{e(t(lang, "データファイル（JSON）", "Data files (JSON)"))}</a><small>{e(t(lang, "全ページと同じ数値", "The same numbers as every page"))}</small></span></li>
<li>{icon("plug")}<span><a href="{e(GITHUB_MCP)}" rel="noopener">{e(t(lang, "MCP サーバー", "MCP server"))}</a><small>{e(MCP_PACKAGE if MCP_NPM_PUBLISHED else t(lang, "準備中", "Coming soon"))}</small></span></li>
</ul>"""

    teaser = ""
    if arts:
        teaser = f"""
<section class="sec" aria-labelledby="h-posts">
<h2 id="h-posts">{icon("ledger")}{e(t(lang, "新しい記事", "Latest articles"))}</h2>
{article_list(lang, arts[:3], row=True)}
<p class="more"><a href="/{lang}/articles/">{e(t(lang, "すべての記事", "All articles"))}</a></p>
</section>
"""

    body = f"""
<div class="hero">
<h1>{e(t(lang, "消える前に、蔵へ。", "Into the storehouse, before it disappears."))}</h1>
<p class="sub">{e(t(lang, "国が公開し、やがて消すデータを、毎晩保存しています。", "Japan publishes this data, then deletes it. We save it every night."))}</p>
{strip}
<ul class="facts">{facts_html}</ul>
</div>

<section class="sec" aria-labelledby="h-what">
<h2 id="h-what">{e(t(lang, "何が見られるか", "What you can see"))}</h2>
{cards}
{intent_note(lang)}
</section>
{teaser}
<section class="sec" aria-labelledby="h-how">
<h2 id="h-how">{e(t(lang, "仕組み", "How it works"))}</h2>
{flow}
</section>

<section class="sec" aria-labelledby="h-not">
<h2 id="h-not">{e(t(lang, "しないこと", "What we do not do"))}</h2>
<ul class="tiles">{tiles}</ul>
</section>

<section class="sec" aria-labelledby="h-dev">
<h2 id="h-dev">{e(t(lang, "開発者・AIエージェントの方へ", "For developers and AI agents"))}</h2>
{doors}
</section>

<section class="sec">
<p class="operator"><span>{e(t(lang, "運営", "Operated by"))} <a href="{e(OPERATOR_URL)}" rel="noopener">{e(OPERATOR_NAME)}</a></span><a href="{e(GITHUB_ORG)}" rel="noopener">{e(t(lang, "コードとデータ（GitHub）", "Code and data (GitHub)"))}</a><a href="{e(GITHUB_ISSUES)}" rel="noopener">{e(t(lang, "削除・訂正の依頼", "Removal and corrections"))}</a></p>
</section>
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
                "name": OPERATOR_NAME,
                "url": OPERATOR_URL,
                "description": t(
                    lang,
                    "日本の公開データの履歴を保存するアーカイブ Deltakura の運営者。",
                    "Operator of Deltakura, an archive of Japanese public-data histories.",
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
        attribution=source_notices(lang),
    )


def bet_a_page_path(lang, slug, fy):
    return f"/{lang}/bet-a/{slug}-fy{fy}.html"


def bet_a_json_path(slug, fy):
    return f"/data/bet-a/{slug}-fy{fy}.json"


def bet_a_labels(lang, page=None, *, any_estimated=False, suppressed=0):
    """The Bet-A data caveats, as short labels (brief section 6).

    These are correctness statements, not explanations: each says what an
    axis or a figure IS. The full method lives in the JSON twin and on the
    privacy page.
    """
    items = [
        t(lang, "セクター = 発注した府省", "Sector = the ministry that bought"),
        t(lang, "所在地 = 落札者の本店所在地", "Prefecture = winner's registered head office"),
        t(lang, "不明 = 匿名化した個人事業主", "Unknown = masked sole proprietors"),
        t(lang, "予定価格: 非公表", "Predicted price: not published"),
    ]
    estimated = (not page["quantiles_exact"]) if page is not None else any_estimated
    if estimated:
        items.append(t(lang, "† 中央値・四分位は推定値", "† median and quartiles are estimates"))
    n_sup = page["n_awards_suppressed"] if page is not None else suppressed
    if n_sup:
        items.append(
            t(lang, f"都道府県の内訳から除外: {n_sup:,}件（少数区分）", f"Held out of the prefecture breakdown: {n_sup:,} (small buckets)")
        )
    if page is None or page["fiscal_year"] <= 2015:
        items.append(t(lang, "FY2013–2015 = 公表の立ち上げ期", "FY2013-2015 = the publisher's ramp-up"))
    return items


BET_A_ATTRIB = {
    "ja": [
        PPORTAL_ATTRIB + "（政府標準利用規約 第2.0版。加工して利用しています。）",
        NTA_ATTRIB + "（公共データ利用規約 第1.0版。落札者の登記所在地の突合にのみ使用）",
    ],
    "en": [
        PPORTAL_ATTRIB + " (政府標準利用規約 v2.0; used in modified form.)",
        NTA_ATTRIB + " (公共データ利用規約 v1.0; used only to resolve the winner's registered prefecture)",
    ],
}


def build_bet_a_index(lang, pages, bet_a_prov, built_at):
    path = f"/{lang}/bet-a/"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/bet-a/"

    sectors, years, by_key = bet_a_grid(pages)
    heat, scale = bet_a_heat(lang, pages)

    title = t(
        lang,
        "国の落札実績 統計 — 年度 × 発注府省 | Deltakura",
        "Japanese national tender awards — by fiscal year and buying ministry | Deltakura",
    )
    return _bet_a_index_page(lang, path, alt, pages, bet_a_prov, sectors, years, heat, scale, title)


def bet_a_heat(lang, pages, *, by_total=False):
    """The year x ministry heatmap table and its colour legend (index and articles).

    `pages` may be a window of fiscal years; `by_total` orders the rows by their
    total over that window, largest first (the index keeps the slug order).
    """
    sectors, years, by_key = bet_a_grid(pages)
    cls_of, legend = heat_scale(p["n_awards"] for p in pages)

    row_totals = {s: sum(by_key[(s, y)]["n_awards"] for y in years if (s, y) in by_key) for s in sectors}
    max_total = max(row_totals.values()) or 1
    if by_total:
        sectors = sorted(sectors, key=lambda s: (-row_totals[s], SECTORS[s][0]))

    head = "".join(f'<th scope="col">{y}</th>' for y in years)
    head = (
        f'<thead><tr><th scope="col">{e(t(lang, "発注した府省", "Buying ministry"))}</th>{head}'
        f'<th scope="col">{e(t(lang, "合計", "Total"))}</th></tr></thead>'
    )
    body_rows = []
    for s in sectors:
        slug, en = SECTORS[s]
        name = e(s) if lang == "ja" else e(en)
        cells = []
        for y in years:
            p = by_key.get((s, y))
            if not p:
                cells.append('<td class="h0"><span class="x">—</span></td>')
                continue
            n = p["n_awards"]
            label = t(lang, f"{s} {y}年度: {n:,}件", f"{en}, FY{y}: {n:,} awards")
            cells.append(
                f'<td class="{cls_of(n)}"><a href="{e(bet_a_page_path(lang, slug, y))}" '
                f'aria-label="{e(label)}">{num(n)}</a></td>'
            )
        tot = row_totals[s]
        cells.append(
            f'<td class="tot">{num(tot)}<span class="tbar" aria-hidden="true">'
            f'<i class="{charts.wcls(tot, max_total)}"></i></span></td>'
        )
        body_rows.append(f'<tr><th scope="row">{name}</th>{"".join(cells)}</tr>')

    col_tot = [sum(by_key[(s, y)]["n_awards"] for s in sectors if (s, y) in by_key) for y in years]
    foot = "".join(f"<td>{num(v)}</td>" for v in col_tot)
    foot = (
        f'<tfoot><tr><th scope="row">{e(t(lang, "合計", "Total"))}</th>{foot}'
        f'<td class="tot">{num(sum(col_tot))}</td></tr></tfoot>'
    )
    heat = (
        f'<div class="tablewrap"><table class="heat">'
        f'<caption>{e(t(lang, "年度 × 発注した府省の落札件数。色が濃いほど件数が多い。数字を押すと詳細へ。", "Awards by fiscal year and buying ministry. Darker = more awards. Each number opens its page."))}</caption>'
        f'{head}<tbody>{"".join(body_rows)}</tbody>{foot}</table></div>'
    )
    scale = '<ul class="scale" aria-label="{}">{}</ul>'.format(
        e(t(lang, "色の凡例（件数）", "Colour legend (awards)")),
        "".join(f'<li class="{c}">{e(r)}</li>' for c, r in legend),
    )
    return heat, scale


def _bet_a_index_page(lang, path, alt, pages, bet_a_prov, sectors, years, heat, scale, title):
    desc_ja = (
        f"調達ポータルの落札実績 {bet_a_prov['records']:,} 件を、年度 × 発注府省で集計した "
        f"{len(pages)} ページの統計。件数、落札価格の中央値と四分位、落札者の所在地。"
    )
    desc_en = (
        f"{len(pages)} statistics pages built from {bet_a_prov['records']:,} Japanese national "
        "procurement awards: counts, median and quartile award prices, and where the winners are "
        "registered, by fiscal year and buying ministry."
    )

    body = f"""
<h1 class="title">{e(t(lang, "国の落札実績", "National tender awards"))}</h1>
<p class="sub">{e(t(lang, f"どの府省が、何件発注したか。FY{min(years)}–FY{max(years)}。", f"Which ministries buy, and how often. FY{min(years)}-FY{max(years)}."))}</p>
{kpis([
    (f"{bet_a_prov['records']:,}", t(lang, "集計した落札", "awards aggregated")),
    (f"{len(pages)}", t(lang, "統計ページ", "statistics pages")),
    (f"{len(sectors)}", t(lang, "府省セクター", "ministry sectors")),
    (f"{len(years)}", t(lang, "年度", "fiscal years")),
])}
<div class="act">{intent_button(lang, "bet_a_report", "更新を受け取る", "Notify me")}</div>
{intent_note(lang)}

<section class="sec" aria-labelledby="h-heat">
<h2 id="h-heat">{icon("tag")}{e(t(lang, "年度 × 府省", "Year x ministry"))}</h2>
{heat}
{scale}
{labels(bet_a_labels(lang, any_estimated=any(not p["quantiles_exact"] for p in pages), suppressed=bet_a_prov["suppressed"]))}
</section>

<p class="fine">{icon("braces")}<a href="/data/bet-a/index.json">{e(t(lang, "データファイル（JSON）", "Data file (JSON)"))}</a></p>
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
        attribution=BET_A_ATTRIB[lang],
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
        t(lang, "都道府県（本店所在地）", "Prefecture (registered)"),
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

    def pref_name(b):
        if b["prefecture"] == "不明":
            return t(lang, "不明（個人事業主）", "Unknown (sole proprietors)")
        if lang == "en":
            return PREF_EN.get(b["prefecture_code"], b["prefecture"])
        return b["prefecture"]

    rows = []
    for b in page["buckets"]:
        share = b["n_awards"] / page["n_awards_in_table"] if page["n_awards_in_table"] else 0
        rows.append(
            [
                e(pref_name(b)),
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
        f"落札者の本店所在地の上位は {top_txt}。"
    )
    desc_en = (
        f"Statistics for {page['n_awards']:,} FY{fy} Japanese national procurement awards bought by "
        f"{page['sector_en'].lower()} bodies: median award price {yen(page['amount_median_jpy'])}, "
        f"total {yen(page['amount_sum_jpy'])}, top registered winner prefectures {top_txt}."
    )

    top8 = page["buckets"][:8]
    bars = charts.hbars(
        [(e(pref_name(b)), b["n_awards"]) for b in top8],
        total=page["n_awards_in_table"],
    )
    box = charts.box_plot(
        lang,
        page["amount_min_jpy"],
        page["amount_q1_jpy"],
        page["amount_median_jpy"],
        page["amount_q3_jpy"],
        page["amount_max_jpy"],
        estimated=bool(dagger),
    )
    five = "".join(
        f'<li><span class="k">{e(k)}</span><span class="v">{e(v)}</span></li>'
        for k, v in (
            (t(lang, "最小", "Min"), yen(page["amount_min_jpy"])),
            (t(lang, "第1四分位", "Q1") + dagger, yen(page["amount_q1_jpy"])),
            (t(lang, "中央値", "Median") + dagger, yen(page["amount_median_jpy"])),
            (t(lang, "第3四分位", "Q3") + dagger, yen(page["amount_q3_jpy"])),
            (t(lang, "最大", "Max"), yen(page["amount_max_jpy"])),
        )
    )
    all_labels = bet_a_labels(lang, page)
    price_labels = [x for x in all_labels if x.startswith(("予定価格", "Predicted", "†"))]
    pref_labels = [x for x in all_labels if x.startswith(("所在地", "Prefecture =", "不明", "Unknown", "都道府県の内訳", "Held out"))]
    page_labels = [x for x in all_labels if x not in price_labels and x not in pref_labels]

    body = f"""
<p class="crumb"><a href="/{lang}/bet-a/">{e(t(lang, "国の落札実績", "National tender awards"))}</a> / {e(sector_label)}</p>
<h1 class="title">{e(sector_label)} · {e(fy_label)}</h1>
{labels(page_labels)}
<nav class="yearnav" aria-label="{e(t(lang, "年度", "Fiscal year"))}">{yearnav}</nav>
{kpis([
    (num(page["n_awards"]), t(lang, "落札件数", "awards")),
    (yen(page["amount_median_jpy"]) + dagger, t(lang, "落札価格の中央値", "median award price")),
    (yen(page["amount_sum_jpy"]), t(lang, "落札総額", "total awarded")),
    (yen(mean), t(lang, "平均", "mean")),
])}

<section class="sec" aria-labelledby="h-dist">
<h2 id="h-dist">{icon("tag")}{e(t(lang, "落札価格の分布", "Award prices"))}</h2>
<figure class="fig">
{box}
<figcaption>{e(t(lang, "箱 = 第1〜第3四分位、線 = 中央値、ひげ = 最小〜最大。対数目盛。", "Box = Q1 to Q3, line = median, whiskers = min to max. Log scale."))}</figcaption>
<ul class="five">{five}</ul>
</figure>
{labels(price_labels)}
</section>

<section class="sec" aria-labelledby="h-pref">
<h2 id="h-pref">{icon("gov")}{e(t(lang, "落札者の所在地（上位）", "Where the winners are registered (top)"))}</h2>
{bars}
{labels(pref_labels)}
<details>
<summary>{e(t(lang, f"都道府県別の表（{page['n_buckets']}区分）", f"Full prefecture table ({page['n_buckets']} buckets)"))}</summary>
{table("", headers, rows, aligns)}
</details>
</section>

<p class="fine">{icon("braces")}<a href="{e(json_url)}">{e(t(lang, "データファイル（JSON）", "Data file (JSON)"))}</a></p>
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
            attribution=BET_A_ATTRIB[lang],
        ),
        payload,
    )


def build_bet_c(lang, bet_c, built_at):
    path = f"/{lang}/bet-c/"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/bet-c/"
    days = bet_c["days"]
    values = [bet_c["daily"][d] for d in days]
    avg = sum(values) / len(values) if values else 0

    chart = charts.daily_chart(lang, days, values)
    strip = charts.strip_figure(lang, days, bet_c["daily"], bet_c["total"])

    def code_rows(counts, names):
        out = []
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            ja, en = names.get(code, (f"コード {code}", f"code {code}"))
            out.append((e(ja if lang == "ja" else en), n))
        return out

    proc_bars = charts.hbars(code_rows(bet_c["process"], PROCESS_CODES), total=bet_c["total"])
    kind_bars = charts.hbars(code_rows(bet_c["kinds"], KIND_CODES), total=bet_c["total"])

    schema_rows = [
        [
            f"<code>{e(col)}</code>",
            e(ja if lang == "ja" else en),
            f"{e(sample) if sample else '—'}",
        ]
        for col, ja, en, sample in NTA_SCHEMA
    ]

    title = t(
        lang,
        "法人番号の差分アーカイブ — 40日で消える日次差分を保管 | Deltakura",
        "Corporate registry diff archive — the daily file that vanishes in 40 days | Deltakura",
    )
    desc_ja = (
        f"国税庁 法人番号公表サイトが約40日で削除する日次差分を、毎晩保管しています。"
        f"{bet_c['n_files']} 日分・{bet_c['total']:,} レコード（{bet_c['first_day']}〜{bet_c['last_day']}）。"
        "法人のみ。週次 RSS と JSON。"
    )
    desc_en = (
        f"The National Tax Agency deletes its daily corporate-registry diff after about 40 days. We keep "
        f"every file: {bet_c['n_files']} days and {bet_c['total']:,} records "
        f"({bet_c['first_day']} to {bet_c['last_day']}). Corporations only. Weekly RSS and JSON."
    )

    points = [
        ("cal40", t(lang, "公式に残るのは直近40日分", "Upstream keeps only the last 40 days")),
        ("moon", t(lang, "毎晩1回取得し、全日を保管", "Fetched nightly; every day kept")),
        ("eye-off", t(lang, "法人のみ。個人の氏名は扱いません", "Corporations only; no personal names")),
    ]
    points_html = "".join(f"<li>{icon(i)}<b>{e(x)}</b></li>" for i, x in points)

    body = f"""
<h1 class="title">{e(t(lang, "法人番号の差分", "Corporate registry diff"))}</h1>
<p class="sub">{e(t(lang, "国税庁が40日で消す日次の差分を、消える前に保管しています。", "The National Tax Agency deletes each daily diff after 40 days. We keep it first."))}</p>
{strip}

<section class="sec" aria-label="{e(t(lang, "要点", "Key points"))}">
<ul class="tiles t3">{points_html}</ul>
<div class="act">{intent_button(lang, "bet_c_registry_diff", "更新を受け取る", "Notify me")}</div>
{intent_note(lang)}
</section>

<section class="sec" aria-labelledby="h-daily">
<h2 id="h-daily">{icon("cal40")}{e(t(lang, "1日あたりの件数", "Records per day"))}</h2>
{kpis([
    (f"{bet_c['total']:,}", t(lang, "保管したレコード", "records kept")),
    (f"{bet_c['n_files']}", t(lang, "公表日", "publication days")),
    (f"{avg:,.0f}", t(lang, "1日平均", "average per day")),
    (f"{bet_c['last_day']}", t(lang, "最新の公表日", "latest publication day")),
])}
<figure class="fig">
{chart}
<figcaption>{e(t(lang, f"{bet_c['first_day']}〜{bet_c['last_day']}、{len(days)}日分。", f"{bet_c['first_day']} to {bet_c['last_day']}, {len(days)} days."))}</figcaption>
</figure>
{labels([t(lang, "土日・祝日・12/29〜1/3 = 公表なし", "Weekends, holidays, 29 Dec-3 Jan = no file")])}
</section>

<section class="sec" aria-labelledby="h-kind">
<h2 id="h-kind">{icon("ledger")}{e(t(lang, "変更の内訳", "What changed"))}</h2>
<div class="two">
<div><h3>{e(t(lang, "処理区分", "Change type"))}</h3>{proc_bars}</div>
<div><h3>{e(t(lang, "法人種別", "Entity type"))}</h3>{kind_bars}</div>
</div>
</section>

<section class="sec" aria-labelledby="h-take">
<h2 id="h-take">{e(t(lang, "受け取り方", "Take the data"))}</h2>
<ul class="doors">
<li>{icon("rss")}<span><a href="{e(FEED_URLS['nta-diff.xml'])}">RSS</a><small>{e(t(lang, "週次", "weekly"))}</small></span></li>
<li>{icon("braces")}<span><a href="/data/bet-c/daily.json">{e(t(lang, "データファイル（JSON）", "Data file (JSON)"))}</a><small>{e(t(lang, "日次件数と内訳", "daily counts and breakdowns"))}</small></span></li>
<li>{icon("code")}<span><a href="{e(GITHUB_CORE)}" rel="noopener">{e(t(lang, "収集コード", "Collector code"))}</a><small>MIT</small></span></li>
</ul>
<details>
<summary>{e(t(lang, "列の一覧（正規化後）", "Columns (normalised schema)"))}</summary>
{table(t(lang, "値は形を示すサンプルです。", "Values are illustrative, not a real record."), [t(lang, "列", "Column"), t(lang, "意味", "Meaning"), t(lang, "サンプル値", "Illustrative value")], schema_rows, ["", "", ""])}
<p class="fine">{e(t(lang, "主キー: corporate_number|change_date|sequence_number", "Primary key: corporate_number|change_date|sequence_number"))}</p>
</details>
</section>
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
                "contentUrl": FEED_URLS["nta-diff.xml"],
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
        attribution=[
            NTA_ATTRIB
            + t(
                lang,
                "（公共データ利用規約 第1.0版。正規化・重複排除・再エンコードの加工をしています。）",
                " (公共データ利用規約 v1.0; used in modified form: normalised, deduplicated and re-encoded.)",
            )
        ],
    )


def build_pricing(lang, built_at):
    """One statement, nothing else (Owner, 2026-09-24; brief section 6)."""
    path = f"/{lang}/pricing.html"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/pricing.html"
    title = t(lang, "料金 | Deltakura", "Pricing | Deltakura")
    desc_ja = "有料プランはまだ開設していません。"
    desc_en = "Paid plans are not open yet."
    body = f'<h1 class="title lone">{e(t(lang, desc_ja, desc_en))}</h1>\n'
    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
    )


def build_privacy(lang, bet_a_prov, bet_c, built_at, any_estimated=False):
    """Promise tiles, then the facts in <details>. No justification prose."""
    path = f"/{lang}/privacy.html"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/privacy.html"

    title = t(
        lang,
        "プライバシー — Cookie なし・メール収集なし | Deltakura",
        "Privacy — no cookies, no email collection | Deltakura",
    )
    desc_ja = (
        "Deltakura のプライバシー。Cookie なし、メール収集なし、入力欄なし、個人名を扱わない。"
        "送信・保存するもの、匿名化、出典とライセンス、削除依頼の窓口（GitHub Issues）。"
    )
    desc_en = (
        "Deltakura privacy: no cookies, no email collection, no input fields, no personal names. "
        "What is sent and stored, the anonymisation rules, sources and licences, and the removal "
        "channel (GitHub Issues)."
    )

    promises = [
        ("cookie-off", t(lang, "Cookie なし", "No cookies")),
        ("mail-off", t(lang, "メール収集なし", "No email collected")),
        ("field-off", t(lang, "入力欄なし", "No input fields")),
        ("eye-off", t(lang, "個人名を扱わない", "No personal names")),
        ("clock", t(lang, "計測は25時間で消える", "Counter data gone in 25 hours")),
        ("code", t(lang, "コードは全部公開", "All code is public")),
    ]
    tiles = "".join(f"<li>{icon(i)}<b>{e(x)}</b></li>" for i, x in promises)

    def section(summary, items, extra=""):
        lis = "".join(f"<li>{x}</li>" for x in items)
        return (
            f"<details><summary>{e(summary)}</summary>"
            f'<ul class="facts-list">{lis}</ul>{extra}</details>'
        )

    api = "deltakura-api.deltakura.workers.dev"
    sec_button = section(
        t(lang, "「知りたい」「更新を受け取る」ボタン", "The notify buttons"),
        [
            e(t(lang, "送るのは3つだけ: 製品、表示かクリックか、ブラウザが作るランダムな文字列。",
                "Three things are sent: the product, view or click, and a random string your browser makes.")),
            e(t(lang, "文字列はブラウザの localStorage に保存します。Cookie ではありません。",
                "The string is kept in your browser's localStorage. It is not a cookie.")),
            e(t(lang, f"送信先: {api}（Cloudflare Workers で当プロジェクトが運用）。",
                f"Sent to: {api} (run by this project on Cloudflare Workers).")),
            e(t(lang, "保存: 文字列のソルト付きハッシュを最大25時間。その後は製品別・日別の件数だけ。",
                "Stored: a salted hash of the string for at most 25 hours; after that, per-product daily counts only.")),
            e(t(lang, "IP アドレスと生の文字列は保存しません。Cloudflare の運用ログは最長3日です。",
                "Neither your IP address nor the raw string is stored. Cloudflare keeps operational logs for up to 3 days.")),
            e(t(lang, "数え方: ブラウザ・製品・日（UTC）ごとに1回。件数は関心を持った人数の上限値です。",
                "Counting: once per browser, product and UTC day. The count is an upper bound on interested people.")),
            e(t(lang, "集計値の公開先: ", "Public totals: ")) + f"<code>https://{api}/v0/intent</code>",
        ],
    )
    sec_loads = section(
        t(lang, "読み込むもの・数えないもの", "What loads, and what is not counted"),
        [
            e(t(lang, "スクリプトは自前の2本だけ: ", "Two self-hosted scripts only: ")) + "<code>/assets/config.js</code>, <code>/assets/intent.js</code>",
            e(t(lang, "アクセス解析はありません。ページの閲覧数は数えていません。",
                "No analytics. Page visits are not counted.")),
            e(t(lang, "フィード（RSS・JSON Feed）の取得は、フィード別・日別の件数だけを数えます。重複は User-Agent と IP アドレス上位16ビットのソルト付きハッシュで除き、ハッシュは25時間で消えます。IP アドレスそのものは保存しません。集計値: ",
                "Feed fetches (RSS, JSON Feed) are counted as daily totals per feed, de-duplicated by a salted hash of the User-Agent and the first 16 bits of the IP address that is deleted after 25 hours. No IP address is stored. Totals: "))
            + f"<code>{e(FEED_STATS_URL)}</code>",
            e(t(lang, "計測を追加する場合は、先にこのページと CSP を更新します。",
                "If measurement is ever added, this page and the CSP change first.")),
        ],
    )
    sec_anon = section(
        t(lang, "匿名化のルール", "Anonymisation rules"),
        [
            e(t(lang, "名称を残すのは、検証済みの13桁の法人番号を持つ落札者だけです。",
                "Only winners with a verified 13-digit corporate number keep their name.")),
            e(t(lang, "それ以外の落札者は取り込み時に匿名化します。",
                "Every other winner is masked at ingestion.")),
            e(t(lang, "担当者・氏名・連絡先の列は、取り込む前に捨てます。",
                "Contact-person, name and contact columns are dropped before anything else.")),
            e(t(lang, "件名は人名検出にかけ、一致したら差し替えます。",
                "Contract titles go through a person-name detector; a match replaces the title.")),
            e(t(lang, "匿名化した個人が1〜2者の区分（都道府県 × セクター × 年度）は公開しません。",
                "A prefecture x sector x year bucket with one or two masked individuals is not published.")),
        ],
    )
    sec_numbers = section(
        t(lang, "数字の作り方", "How the numbers are made"),
        [
            e(t(lang, "件数・合計・最小・最大は、公開した集計表そのままの値です。",
                "Counts, totals, minimum and maximum are exactly the published table.")),
            e(
                t(lang, "中央値・四分位（†）は、集計済みの区分から推定した値です。",
                  "Medians and quartiles marked † are estimated from the aggregated buckets.")
                if any_estimated
                else t(lang, "中央値・四分位は、1件ごとの落札価格から計算した値です（inclusive 法）。",
                       "Medians and quartiles are computed from the individual award prices (inclusive method).")
            ),
            e(t(lang, "日付と時刻はすべて UTC です。", "Every date and time is UTC.")),
        ],
    )

    sources_rows = [
        [
            e("調達ポータル 落札実績"),
            '<a href="https://www.p-portal.go.jp/" rel="noopener nofollow">p-portal.go.jp</a>',
            e("政府標準利用規約(第2.0版)"),
            e(t(lang, f"{bet_a_prov['records']:,} 件", f"{bet_a_prov['records']:,} awards")),
        ],
        [
            e("国税庁 法人番号公表サイト 差分"),
            '<a href="https://www.houjin-bangou.nta.go.jp/download/sabun/" rel="noopener nofollow">houjin-bangou.nta.go.jp</a>',
            e("公共データ利用規約(第1.0版)"),
            e(t(lang, f"{bet_c['total']:,} レコード", f"{bet_c['total']:,} records")),
        ],
    ]
    sec_sources = (
        f"<details><summary>{e(t(lang, '出典とライセンス', 'Sources and licences'))}</summary>"
        + table("", [t(lang, "出典", "Source"), "URL", t(lang, "ライセンス", "Licence"), t(lang, "収録量", "Volume")], sources_rows, ["", "", "", "n"])
        + '<ul class="facts-list"><li>'
        + e(t(lang, "当プロジェクトの集計データ: CC BY 4.0。収集コード: MIT。", "Our derived aggregates: CC BY 4.0. Collector code: MIT."))
        + "</li></ul></details>"
    )
    sec_crawl = section(
        t(lang, "収集のしかた", "How collection behaves"),
        [
            e(t(lang, "robots.txt に従います（ホストごとに24時間ごとに再確認）。", "robots.txt is obeyed, re-checked every 24 hours per host.")),
            e(t(lang, "同じホストへのリクエストは2秒以上あけ、並列にしません。", "At least 2 seconds between requests to one host; never parallel.")),
            e(t(lang, "連絡先 URL つきの User-Agent を名乗ります。", "An identifying User-Agent with a contact URL is always sent.")),
            e(t(lang, "変わっていないファイルは取り直しません。", "An unchanged file is not fetched again.")),
            e(t(lang, "ログイン、アクセス制限の回避、CAPTCHA の突破はしません。", "No login, no bypassing access controls, no CAPTCHA solving.")),
            e(t(lang, "取得元から停止の連絡があれば、24時間以内に止めます。", "If a source asks us to stop, collection from it stops within 24 hours.")),
        ],
    )
    sec_removal = section(
        t(lang, "訂正・削除の依頼", "Corrections and removals"),
        [
            e(t(lang, "受付: ", "Where: ")) + f'<a href="{e(GITHUB_ISSUES)}" rel="noopener">GitHub Issues</a>'
            + e(t(lang, "（件名に「削除依頼」または REMOVAL）", " (put REMOVAL or 削除依頼 in the title)")),
            e(t(lang, "個人が特定できる情報の指摘: 24時間以内に非公開にします。",
                "Identifiable information: taken down within 24 hours.")),
            e(t(lang, "誤りの指摘・法人からの削除依頼: 通常2営業日以内に応答します。",
                "Errors and removal requests from companies: answered within two business days.")),
        ],
    )

    body = f"""
<h1 class="title">{e(t(lang, "プライバシー", "Privacy"))}</h1>
<ul class="tiles t3">{tiles}</ul>

<section class="sec" aria-label="{e(t(lang, "詳細", "Details"))}">
{sec_button}
{sec_loads}
{sec_anon}
{sec_numbers}
{sec_sources}
{sec_crawl}
{sec_removal}
</section>

<p class="fine">{e(t(lang, "運営: ", "Operated by "))}<a href="{e(OPERATOR_URL)}" rel="noopener">{e(OPERATOR_NAME)}</a> · {e(t(lang, "最終更新: ", "Last updated: "))}<span class="num">{e(built_at[:10])}</span> UTC</p>
"""

    return path, alt, render_page(
        lang=lang,
        path=path,
        title=title,
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
        attribution=source_notices(lang),
    )


def build_root(bet_a_pages, bet_c, built_at):
    body = """
<div class="hero">
<h1>消える前に、蔵へ。</h1>
<p class="sub" lang="en">Into the storehouse, before it disappears.</p>
<div class="act"><a class="btn" href="/ja/">日本語</a><a class="btn" href="/en/" lang="en">English</a></div>
</div>
"""
    return render_page(
        lang="ja",
        path="/",
        title="Deltakura / デルタ蔵 — 消える前に、蔵へ。",
        desc_ja="国が公開し、やがて消すデータを毎晩保存するアーカイブ。落札実績統計と法人番号の差分。",
        desc_en="An archive of Japanese public data that the state publishes and later deletes: tender award statistics and the corporate registry diff.",
        body=body,
        alt_path="/en/",
    )


# --------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------
#
# Written by people as Markdown under site/content/articles/ (format and the
# supported subset: site/articles.py). Every article page carries Article
# JSON-LD, the three-part attribution block, a notify button (the intent
# contract: the button counts views and clicks with the rest of the site) and
# the feed links. check() fails the build if any of that is missing.


def article_path(lang: str, slug: str) -> str:
    return f"/{lang}/articles/{slug}.html"


def article_list(lang, arts, row=False):
    items = "".join(
        f'<li><time class="num" datetime="{a["date"].isoformat()}">{a["date"].isoformat()}</time>'
        f'<a href="{e(article_path(a["lang"], a["slug"]))}">{e(a["title"])}</a>'
        f'<p>{e(a["description"])}</p></li>'
        for a in arts
    )
    return f'<ul class="posts{" row" if row else ""}">{items}</ul>'


def llms_articles(arts) -> str:
    if not arts:
        return ""
    lines = ["", "## Articles", ""]
    for a in arts:
        lines.append(f"- {a['date'].isoformat()} ({a['lang']}) {a['title']}: {BASE_URL}{article_path(a['lang'], a['slug'])}")
    return "\n".join(lines) + "\n"


#: The notify button on an article, by the article's `product`. The ids are
#: written out literally so api/test/intent-contract.test.ts sees them.
ARTICLE_INTENT = {
    "bet_a": lambda lang: intent_button(lang, "bet_a_report", "更新を受け取る", "Notify me"),
    "bet_c": lambda lang: intent_button(lang, "bet_c_registry_diff", "更新を受け取る", "Notify me"),
}

#: What an article may embed with {{chart:<id>}}, for the error message.
ARTICLE_CHART_IDS = (
    "nta-strip[:<oldest-listed-upstream>[:<checked-on>]]", "nta-40day-strip (= nta-strip)",
    "nta-daily[:<from>:<to>]", "nta-daily-by-process[:<from>:<to>]", "nta-prefecture-top10[:<from>:<to>]",
    "bet-a-heatmap[:<fy-from>:<fy-to>]", "awards-sector-fy-heatmap (FY2014-FY2025)",
    "bet-a-years[:<sector>]", "bet-a-sectors[:<fy>]", "bet-a-median[:<fy>]",
    "bet-a-prefs[:<fy>]", "bet-a-box:<sector>:<fy>",
)

#: Charts an article asked for that the published data cannot draw. The build
#: fails with this reason rather than drawing them from anywhere else: every
#: figure on the site comes from data/published/, so CI and a local build agree.
ARTICLE_CHARTS_UNAVAILABLE = {
    "award-month-share": (
        "needs award counts by month of 落札決定日, which data/published/ does not carry "
        "(stats_v0.csv is fiscal year x prefecture x sector). Publish a month aggregate first, "
        "or remove the placeholder"
    ),
}


def article_charts(bet_a_pages, bet_c):
    """Returns factory(lang) -> (chart(id, args) -> html, datasets used).

    Every chart is built here, at build time, from the same published numbers
    as the statistics pages; an article cannot bring its own figures.
    """
    ArticleError = articles_mod.ArticleError
    sectors, years, by_key = bet_a_grid(bet_a_pages)
    by_slug = {SECTORS[s][0]: s for s in sectors}
    fy_min, fy_max = min(years), max(years)

    def fy_of(arg):
        m = re.fullmatch(r"(?:fy)?(\d{4})", arg.lower())
        if not m or int(m.group(1)) not in years:
            raise ArticleError(f"unknown fiscal year {arg!r} (FY{fy_min}-FY{fy_max})")
        return int(m.group(1))

    def sector_of(arg):
        if arg not in by_slug:
            raise ArticleError(f"unknown sector {arg!r}; one of: {', '.join(sorted(by_slug))}")
        return by_slug[arg]

    def factory(lang):
        used = set()
        count = [0]

        def sname(s):
            return s if lang == "ja" else SECTORS[s][1]

        def src_line(kind):
            return {
                "bet-a": t(lang, "出典：調達ポータル 落札実績（Deltakura が集計）", "Source: 調達ポータル award data, aggregated by Deltakura"),
                "nta": t(lang, "出典：国税庁 法人番号公表サイト 差分（Deltakura が集計）", "Source: 国税庁 corporate-number diff, aggregated by Deltakura"),
            }[kind]

        def figure(inner, caption, kind, extra=""):
            used.add(kind)
            return (
                f'<figure class="fig chart">{inner}<figcaption>{e(caption)}'
                f'<span class="fig-src">{e(src_line(kind))}</span></figcaption>{extra}</figure>'
            )

        def period(args):
            """Publication days in [from, to] (ISO dates), or every day held."""
            if not args:
                return list(bet_c["days"])
            if len(args) != 2 or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", a) for a in args):
                raise ArticleError("a period is two ISO dates: <from>:<to>")
            lo, hi = args
            if lo > hi:
                raise ArticleError(f"period {lo} is after {hi}")
            days = [d for d in bet_c["days"] if lo <= d <= hi]
            if not days:
                raise ArticleError(f"no publication day held between {lo} and {hi}")
            return days

        def fy_label(fy):
            return t(lang, f"{fy}年度", f"FY{fy}") if fy else t(lang, f"FY{fy_min}–FY{fy_max}", f"FY{fy_min}-FY{fy_max}")

        def chart(cid, args):
            count[0] += 1
            uid = f"ch{count[0]}"

            def nargs(lo, hi):
                if not lo <= len(args) <= hi:
                    raise ArticleError(f"chart {cid!r} takes {lo}-{hi} argument(s), got {len(args)}")

            if cid in ARTICLE_CHARTS_UNAVAILABLE:
                raise ArticleError(f"chart {cid!r} {ARTICLE_CHARTS_UNAVAILABLE[cid]}")

            if cid in ("nta-strip", "nta-40day-strip"):
                nargs(0, 2)
                for a in args:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a):
                        raise ArticleError(f"chart {cid!r}: arguments are ISO dates, not {a!r}")
                used.add("nta")
                return charts.strip_figure(
                    lang, bet_c["days"], bet_c["daily"], bet_c["total"], uid=uid,
                    oldest_upstream=args[0] if args else None,
                    checked_on=args[1] if len(args) > 1 else None,
                )

            if cid == "nta-daily-by-process":
                nargs(0, 2)
                days = period(args)
                main_codes = ("01", "12", "21", "11", "71")
                classes = ("a", "b", "c", "d", "e")
                series = []
                for code, cls in zip(main_codes, classes):
                    ja, en = PROCESS_CODES.get(code, (code, code))
                    series.append((cls, ja if lang == "ja" else en,
                                   {d: bet_c["daily_process"][d].get(code, 0) for d in days}))
                series.append(("f", t(lang, "その他", "Other"), {
                    d: sum(v for c, v in bet_c["daily_process"][d].items() if c not in main_codes) for d in days
                }))
                mean = sum(bet_c["daily"][d] for d in days) / len(days)
                svg, legend = charts.stacked_daily(lang, days, series, uid=uid, mean=mean)
                return figure(
                    svg,
                    t(lang, f"公表日ごとの件数を処理区分で積み上げ。{days[0]}〜{days[-1]}、{len(days)}日分。点線は1日平均。",
                      f"Records per publication day, stacked by change type, {days[0]} to {days[-1]} ({len(days)} days). Dashed line = daily mean."),
                    "nta",
                    legend + labels([t(lang, "土日・祝日・12/29〜1/3 = 公表なし", "Weekends, holidays, 29 Dec-3 Jan = no file")]),
                )

            if cid == "nta-prefecture-top10":
                nargs(0, 2)
                days = period(args)
                missing = [d for d in days if bet_c["daily_prefs"][d] is None]
                if missing:
                    raise ArticleError(
                        f"summary.json has no per-day prefecture counts for {missing[0]} "
                        f"(and {len(missing) - 1} more); rebuild it with crawlers/nta_diff/publish_summary.py"
                    )
                prefs = collections.Counter()
                for d in days:
                    prefs.update(bet_c["daily_prefs"][d])
                records = sum(bet_c["daily"][d] for d in days)
                en_of = {ja: PREF_EN[f"{i + 1:02d}"] for i, ja in enumerate(PREF_JA)}

                def pname(name):
                    return name if lang == "ja" else en_of.get(name, name)

                ranked = prefs.most_common()
                full = table(
                    "", [t(lang, "都道府県", "Prefecture"), t(lang, "件数", "Records"), t(lang, "構成比", "Share")],
                    [[e(pname(k)), num(v), pct(v / records)] for k, v in ranked], ["", "n", "n"],
                )
                return figure(
                    charts.hbars([(e(pname(k)), v) for k, v in ranked[:10]], total=records),
                    t(lang, f"都道府県別の件数、上位10。{days[0]}〜{days[-1]}、{len(days)}公表日。構成比は全{records:,}件に対して。",
                      f"Records by prefecture, top 10, {days[0]} to {days[-1]} ({len(days)} publication days). Shares are of all {records:,} records."),
                    "nta",
                    labels([t(lang, "都道府県 = 各レコードの本店所在地", "Prefecture = the record's registered head office")])
                    + f'<details><summary>{e(t(lang, f"{len(ranked)}都道府県の表", f"All {len(ranked)} prefectures"))}</summary>{full}</details>',
                )

            if cid == "nta-daily":
                nargs(0, 2)
                days = period(args)
                vals = [bet_c["daily"][d] for d in days]
                return figure(
                    charts.daily_chart(lang, days, vals),
                    t(lang, f"公表日ごとの差分件数。{days[0]}〜{days[-1]}、{len(days)}日分。",
                      f"Registry diff records per publication day, {days[0]} to {days[-1]} ({len(days)} days)."),
                    "nta",
                    labels([t(lang, "土日・祝日・12/29〜1/3 = 公表なし", "Weekends, holidays, 29 Dec-3 Jan = no file")]),
                )

            if cid in ("bet-a-heatmap", "awards-sector-fy-heatmap"):
                if cid == "awards-sector-fy-heatmap":
                    nargs(0, 0)
                    lo, hi = 2014, 2025
                else:
                    if len(args) not in (0, 2):
                        raise ArticleError(f"chart {cid!r} takes no argument or <fy-from>:<fy-to>")
                    lo, hi = (fy_of(args[0]), fy_of(args[1])) if args else (fy_min, fy_max)
                if lo > hi:
                    raise ArticleError(f"chart {cid!r}: FY{lo} is after FY{hi}")
                used.add("bet-a")
                window = [p for p in bet_a_pages if lo <= p["fiscal_year"] <= hi]
                heat, scale = bet_a_heat(lang, window, by_total=True)
                notes = [t(lang, "セクター = 発注した府省", "Sector = the ministry that bought")]
                if lo <= 2015:
                    notes.append(t(lang, "FY2013–2015 = 公表の立ち上げ期", "FY2013-2015 = the publisher's ramp-up"))
                return (
                    f'<div class="chart">{heat}{scale}{labels(notes)}'
                    f'<p class="fig-src">{e(src_line("bet-a"))}</p></div>'
                )

            if cid == "bet-a-years":
                nargs(0, 1)
                sector = sector_of(args[0]) if args else None
                rows = [
                    (y, sum(by_key[(s, y)]["n_awards"] for s in sectors
                            if (s, y) in by_key and (sector is None or s == sector)))
                    for y in years
                ]
                who = f"（{sname(sector)}）" if sector and lang == "ja" else (f" ({sname(sector)})" if sector else "")
                table_html = table(
                    "", [t(lang, "年度", "Fiscal year"), t(lang, "件数", "Awards")],
                    [[e(fy_label(y)), num(v)] for y, v in rows], ["", "n"],
                )
                return figure(
                    charts.year_bars(lang, rows, uid=uid),
                    t(lang, f"年度別の落札件数{who}。{fy_label(None)}。", f"Awards per fiscal year{who}, {fy_label(None)}."),
                    "bet-a",
                    f'<details><summary>{e(t(lang, "数値", "Numbers"))}</summary>{table_html}</details>'
                    + labels([t(lang, "FY2013–2015 = 公表の立ち上げ期", "FY2013-2015 = the publisher's ramp-up")]),
                )

            if cid == "bet-a-sectors":
                nargs(0, 1)
                fy = fy_of(args[0]) if args else None
                rows = [
                    (s, sum(by_key[(s, y)]["n_awards"] for y in years if (s, y) in by_key and (fy is None or y == fy)))
                    for s in sectors
                ]
                rows = sorted((r for r in rows if r[1]), key=lambda r: -r[1])
                return figure(
                    charts.hbars([(e(sname(s)), n) for s, n in rows], total=sum(n for _, n in rows)),
                    t(lang, f"発注した府省（セクター）別の落札件数、{fy_label(fy)}。", f"Awards by buying ministry sector, {fy_label(fy)}."),
                    "bet-a",
                    labels([t(lang, "セクター = 発注した府省", "Sector = the ministry that bought")]),
                )

            if cid == "bet-a-median":
                nargs(0, 1)
                fy = fy_of(args[0]) if args else fy_max
                pages = [by_key[(s, fy)] for s in sectors if (s, fy) in by_key]
                pages.sort(key=lambda p: -p["amount_median_jpy"])
                estimated = any(not p["quantiles_exact"] for p in pages)
                rows = [(e(sname(p["sector"]) + ("" if p["quantiles_exact"] else "†")), p["amount_median_jpy"]) for p in pages]
                notes = [t(lang, "セクター = 発注した府省", "Sector = the ministry that bought"),
                         t(lang, "予定価格: 非公表", "Predicted price: not published")]
                if estimated:
                    notes.append(t(lang, "† 中央値は推定値", "† median is an estimate"))
                return figure(
                    charts.hbars(rows, fmt=yen),
                    t(lang, f"落札価格の中央値（府省セクター別、{fy_label(fy)}）。", f"Median award price by buying ministry sector, {fy_label(fy)}."),
                    "bet-a",
                    labels(notes),
                )

            if cid == "bet-a-prefs":
                nargs(0, 1)
                fy = fy_of(args[0]) if args else None
                counts = collections.Counter()
                names = {}
                for p in bet_a_pages:
                    if fy is not None and p["fiscal_year"] != fy:
                        continue
                    for b in p["buckets"]:
                        key = b["prefecture_code"] or ""
                        counts[key] += b["n_awards"]
                        if b["prefecture"] == "不明":
                            names[key] = t(lang, "不明（個人事業主）", "Unknown (sole proprietors)")
                        else:
                            names[key] = b["prefecture"] if lang == "ja" else PREF_EN.get(key, b["prefecture"])
                total = sum(counts.values())
                top = counts.most_common(10)
                return figure(
                    charts.hbars([(e(names[k]), n) for k, n in top], total=total),
                    t(lang, f"落札者の本店所在地（都道府県）別の件数、上位10、{fy_label(fy)}。",
                      f"Awards by the winner's registered prefecture, top 10, {fy_label(fy)}."),
                    "bet-a",
                    labels([t(lang, "所在地 = 落札者の本店所在地", "Prefecture = winner's registered head office")]),
                )

            if cid == "bet-a-box":
                nargs(2, 2)
                sector, fy = sector_of(args[0]), fy_of(args[1])
                page = by_key.get((sector, fy))
                if not page:
                    raise ArticleError(f"no statistics page for {args[0]} FY{fy}")
                dagger = "" if page["quantiles_exact"] else "†"
                box = charts.box_plot(
                    lang, page["amount_min_jpy"], page["amount_q1_jpy"], page["amount_median_jpy"],
                    page["amount_q3_jpy"], page["amount_max_jpy"], estimated=bool(dagger), uid=uid,
                )
                five = "".join(
                    f'<li><span class="k">{e(k)}</span><span class="v">{e(v)}</span></li>'
                    for k, v in (
                        (t(lang, "最小", "Min"), yen(page["amount_min_jpy"])),
                        (t(lang, "第1四分位", "Q1") + dagger, yen(page["amount_q1_jpy"])),
                        (t(lang, "中央値", "Median") + dagger, yen(page["amount_median_jpy"])),
                        (t(lang, "第3四分位", "Q3") + dagger, yen(page["amount_q3_jpy"])),
                        (t(lang, "最大", "Max"), yen(page["amount_max_jpy"])),
                    )
                )
                link = bet_a_page_path(lang, SECTORS[sector][0], fy)
                return figure(
                    box,
                    t(lang, f"{sname(sector)} {fy_label(fy)}: 落札価格の分布（箱 = 第1〜第3四分位、線 = 中央値、ひげ = 最小〜最大。対数目盛）。",
                      f"{sname(sector)}, {fy_label(fy)}: award prices (box = Q1 to Q3, line = median, whiskers = min to max; log scale)."),
                    "bet-a",
                    f'<ul class="five">{five}</ul><p class="fine"><a href="{e(link)}">'
                    f'{e(t(lang, "この統計のページ", "This statistics page"))}</a></p>',
                )

            raise ArticleError(f"unknown chart {cid!r}; available: {', '.join(ARTICLE_CHART_IDS)}")

        return chart, used

    return factory


def build_article(art, chart_factory, counterpart: bool):
    lang = art["lang"]
    other = "en" if lang == "ja" else "ja"
    path = article_path(lang, art["slug"])
    alt = article_path(other, art["slug"]) if counterpart else path

    chart, used = chart_factory(lang)
    body_html, _toc = articles_mod.render_markdown(art["body"], chart, art["file"], art["body_line"])

    published = art["date"].isoformat()
    updated = art["updated"].isoformat()
    meta = f'<span>{e(t(lang, "公開", "Published"))} <time class="num" datetime="{published}">{published}</time></span>'
    if updated != published:
        meta += f'<span>{e(t(lang, "更新", "Updated"))} <time class="num" datetime="{updated}">{updated}</time></span>'

    sources = "".join(f"<li>{articles_mod.inline(s, art['file'])}</li>" for s in art["sources"])
    data_links = []
    if "bet-a" in used:
        data_links.append(("/data/bet-a/index.json", t(lang, "落札統計のデータ（JSON）", "Tender-award data (JSON)")))
    if "nta" in used:
        data_links.append(("/data/bet-c/daily.json", t(lang, "法人番号の差分のデータ（JSON）", "Registry-diff data (JSON)")))
    data_html = "".join(
        f'<p class="fine">{icon("braces")}<a href="{e(h)}">{e(label)}</a></p>' for h, label in data_links
    )

    body = f"""
<article class="post">
<p class="crumb"><a href="/{lang}/articles/">{e(t(lang, "記事", "Articles"))}</a></p>
<h1 class="title">{e(art["title"])}</h1>
<p class="post-meta">{icon("kura")}{meta}</p>
<p class="lede">{e(art["description"])}</p>
<div class="prose">
{body_html}
</div>
<section class="post-end" aria-labelledby="h-src">
<h2 id="h-src">{e(t(lang, "出典", "Sources"))}</h2>
<ul class="src-list">{sources}</ul>
</section>
<div class="post-act">
<div class="act">{ARTICLE_INTENT[art["product"]](lang)}<a class="btn" href="/{lang}/subscribe.html">{icon("rss")}{e(t(lang, "RSS・JSON Feed で購読", "Subscribe by RSS or JSON Feed"))}</a></div>
{intent_note(lang)}
{data_html}
</div>
</article>
"""

    if lang == "ja":
        desc_ja = art["description"]
        desc_en = art["description_en"] or f"Japanese-language article from Deltakura: {art['title']}"
    else:
        desc_en = art["description"]
        desc_ja = f"Deltakura の英語の記事: {art['title']}"

    based_on = []
    if "bet-a" in used:
        based_on.append(BASE_URL + f"/{lang}/bet-a/")
    if "nta" in used:
        based_on.append(BASE_URL + f"/{lang}/bet-c/")
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": art["title"][:110],
        "description": art["description"],
        "datePublished": published,
        "dateModified": updated,
        "inLanguage": lang,
        "url": BASE_URL + path,
        "mainEntityOfPage": BASE_URL + path,
        "author": {"@type": "Organization", "name": BRAND, "url": BASE_URL + "/"},
        "publisher": {"@type": "Organization", "name": OPERATOR_NAME, "url": OPERATOR_URL},
        "isAccessibleForFree": True,
        "citation": art["sources"],
        **({"isBasedOn": based_on} if based_on else {}),
    }
    return path, render_page(
        lang=lang,
        path=path,
        title=f"{art['title']} | Deltakura",
        desc_ja=desc_ja,
        desc_en=desc_en,
        body=body,
        alt_path=alt,
        switch_path=f"/{other}/articles/",
        jsonld=[jsonld],
        attribution=source_notices(lang),
        og_type="article",
    )


def build_articles_index(lang, arts, other_lang_count=0):
    path = f"/{lang}/articles/"
    other = "en" if lang == "ja" else "ja"
    alt = f"/{other}/articles/"
    feeds = (
        f'<p class="fine">{icon("rss")}<a href="{e(FEED_URLS["articles.xml"])}">RSS</a>'
        f' · <a href="{e(FEED_URLS["articles.json"])}">JSON Feed</a>'
        f' · <a href="/{lang}/subscribe.html">{e(t(lang, "購読の方法", "How to subscribe"))}</a></p>'
    )
    if arts:
        listing = article_list(lang, arts)
    elif lang == "en":
        listing = '<p class="lone-line">Articles are in Japanese only.' + (
            ' <a href="/ja/articles/" hreflang="ja" lang="ja">記事一覧</a>' if other_lang_count else ""
        ) + "</p>"
    else:
        listing = '<p class="lone-line">記事はまだありません。</p>'

    body = f"""
<h1 class="title">{e(t(lang, "記事", "Articles"))}</h1>
<p class="sub">{e(t(lang, "蔵に保管したデータから書いた記事です。", "Articles written from the data in the storehouse."))}</p>
{listing}
{feeds}
"""
    jsonld = [{
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": t(lang, "Deltakura の記事", "Deltakura articles"),
        "url": BASE_URL + path,
        "inLanguage": lang,
        "hasPart": [
            {"@type": "Article", "headline": a["title"][:110], "url": BASE_URL + article_path(a["lang"], a["slug"]),
             "datePublished": a["date"].isoformat()}
            for a in arts
        ],
    }]
    return path, render_page(
        lang=lang,
        path=path,
        title=t(lang, "記事 | Deltakura", "Articles | Deltakura"),
        desc_ja="Deltakura が保管した公開データから書いた記事の一覧。国の落札実績と法人番号の差分。",
        desc_en="Articles Deltakura writes from the public data it keeps: national tender awards and the corporate registry diff.",
        body=body,
        alt_path=alt,
        jsonld=jsonld,
        attribution=source_notices(lang) if arts else None,
    )


def build_subscribe(lang):
    path = f"/{lang}/subscribe.html"
    alt = f"/{'en' if lang == 'ja' else 'ja'}/subscribe.html"

    def slips(rows):
        return '<dl class="urls">' + "".join(
            f"<div><dt>{e(label)}</dt><dd><code class=\"url\">{e(url)}</code></dd></div>" for label, url in rows
        ) + "</dl>"

    if MCP_NPM_PUBLISHED:
        config = json.dumps({"mcpServers": {"deltakura": {"command": "npx", "args": ["-y", MCP_PACKAGE]}}}, indent=2)
        mcp_extra = f'<pre class="url"><code>{e(config)}</code></pre>'
    else:
        mcp_extra = f'<span class="state">{e(t(lang, "npm 公開前", "Not on npm yet"))}</span>'

    tiles = [
        ("rss", "RSS",
         t(lang, "新しい記事と、法人番号の差分の週次まとめが届きます。", "New articles, and the weekly registry-diff summary."),
         slips([(t(lang, "記事", "Articles"), FEED_URLS["articles.xml"]),
                (t(lang, "法人番号の差分（週次）", "Registry diff (weekly)"), FEED_URLS["nta-diff.xml"])])),
        ("braces", "JSON Feed",
         t(lang, "同じ内容を JSON Feed 1.1 で。", "The same, as JSON Feed 1.1."),
         slips([(t(lang, "記事", "Articles"), FEED_URLS["articles.json"]),
                (t(lang, "法人番号の差分（日次）", "Registry diff (daily)"), FEED_URLS["nta-diff.json"])])),
        ("plug", t(lang, "MCP サーバー", "MCP server"),
         t(lang, "AI エージェントから落札統計と法人番号の差分を読めます。", "Lets an AI agent read the award statistics and the registry diff."),
         mcp_extra + f'<p class="fine"><a href="{e(GITHUB_MCP)}" rel="noopener">{e(t(lang, "ソースコード（GitHub）", "Source (GitHub)"))}</a></p>'),
        ("code", t(lang, "GitHub で Watch", "Watch on GitHub"),
         t(lang, "Watch で Issue の通知、Atom フィードでデータ更新のコミットが届きます。", "Watch for issues; the Atom feed carries every data-update commit."),
         slips([(t(lang, "リポジトリ", "Repository"), GITHUB_REPO),
                (t(lang, "コミットの Atom フィード", "Commits, Atom feed"), GITHUB_COMMITS_ATOM)])),
    ]
    tiles_html = "".join(
        f'<li><div class="sub-h">{icon(i)}<h2>{e(name)}</h2></div><p>{e(line)}</p>{extra}</li>'
        for i, name, line, extra in tiles
    )
    body = f"""
<h1 class="title">{e(t(lang, "購読する", "Subscribe"))}</h1>
<p class="sub">{e(t(lang, "登録もメールアドレスも要りません。URL をフィードリーダーに入れるだけです。", "No sign-up and no email address: paste a URL into your feed reader."))}</p>
<ul class="subs">{tiles_html}</ul>
<p class="fine">{icon("shield")}{e(t(lang, "フィードの取得は、日ごとの件数だけを数えます。IP アドレスは保存しません。", "Feed fetches are counted as daily totals only. No IP address is stored."))} <a href="/{lang}/privacy.html">{e(t(lang, "プライバシー", "Privacy"))}</a></p>
"""
    return path, render_page(
        lang=lang,
        path=path,
        title=t(lang, "購読する — RSS・JSON Feed・MCP | Deltakura", "Subscribe — RSS, JSON Feed, MCP | Deltakura"),
        desc_ja="Deltakura の更新を受け取る方法。記事と法人番号の差分の RSS・JSON Feed、MCP サーバー、GitHub。登録もメールも不要。",
        desc_en="Ways to follow Deltakura: RSS and JSON Feed for articles and the registry diff, the MCP server, GitHub. No sign-up, no email.",
        body=body,
        alt_path=alt,
    )


def build_articles_rss(arts, built_at):
    items = []
    for a in arts[:50]:
        url = BASE_URL + article_path(a["lang"], a["slug"])
        items.append(
            "<item>"
            f"<title>{e(a['title'])}</title>"
            f"<link>{e(url)}</link>"
            f'<guid isPermaLink="true">{e(url)}</guid>'
            f"<pubDate>{rfc822(a['date'])}</pubDate>"
            f"<description>{e(a['description'])}</description>"
            "</item>"
        )
    built = dt.datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    notice = f"{PPORTAL_ATTRIB}｜{PPORTAL_LICENSE_NAME}｜{PPORTAL_MODIFIED} / {NTA_FEED_NOTICE}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>Deltakura — 記事 / Articles</title>
<link>{e(BASE_URL)}/ja/articles/</link>
<atom:link href="{e(FEED_URLS['articles.xml'])}" rel="self" type="application/rss+xml"/>
<description>{e("Deltakura が保管した公開データから書いた記事。 / Articles written from the public data Deltakura keeps.")}</description>
<language>ja</language>
<generator>Deltakura site build</generator>
<lastBuildDate>{e(built.strftime("%a, %d %b %Y %H:%M:%S +0000"))}</lastBuildDate>
<copyright>{e("Derived aggregates CC BY 4.0. " + notice)}</copyright>
<ttl>1440</ttl>
{chr(10).join(items)}
</channel>
</rss>
"""


def build_articles_json(arts, built_at):
    def stamp(d):
        return f"{d.isoformat()}T12:00:00Z"  # the same instant as the RSS pubDate

    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Deltakura — 記事 / Articles",
        "home_page_url": BASE_URL + "/ja/articles/",
        "feed_url": FEED_URLS["articles.json"],
        "description": "Deltakura が保管した公開データから書いた記事。 / Articles written from the public data Deltakura keeps.",
        "language": "ja",
        "authors": [{"name": BRAND, "url": BASE_URL + "/"}],
        "items": [
            {
                "id": BASE_URL + article_path(a["lang"], a["slug"]),
                "url": BASE_URL + article_path(a["lang"], a["slug"]),
                "title": a["title"],
                "summary": a["description"],
                "content_text": a["description"],
                "date_published": stamp(a["date"]),
                "date_modified": stamp(a["updated"]),
                "language": a["lang"],
            }
            for a in arts[:50]
        ],
        "_deltakura": {
            "attribution": [PPORTAL_ATTRIB, NTA_ATTRIB],
            "license": [PPORTAL_LICENSE_NAME, NTA_LICENSE_NAME],
            "modified": [PPORTAL_MODIFIED, NTA_MODIFIED],
            "derived_aggregates_license": "CC BY 4.0",
            "generated_at": built_at,
        },
    }


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
<atom:link href="{e(FEED_URLS['nta-diff.xml'])}" rel="self" type="application/rss+xml"/>
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


def build_llms_txt(bet_a_pages, bet_a_prov, bet_c, built_at, arts=()):
    estimated = any(not p["quantiles_exact"] for p in bet_a_pages)
    quartiles = (
        "medians and quartiles marked with a dagger are estimated from aggregated buckets"
        if estimated
        else "medians and quartiles are computed from the individual award prices"
    )
    return f"""# Deltakura ({BRAND_JA})

> An archive of Japanese public data that the state publishes and later deletes,
> saved every night. Operated by {OPERATOR_NAME} ({OPERATOR_URL}). Unofficial: check
> the original source before relying on a number. Derived aggregates are CC BY 4.0.

When you quote a figure, keep its three parts together: source, licence,
modification notice.

- {PPORTAL_ATTRIB}
  Licence: {PPORTAL_LICENSE_NAME}. {PPORTAL_MODIFIED}
- {NTA_ATTRIB}
  Licence: {NTA_LICENSE_NAME}. {NTA_MODIFIED}

Data last updated: awards {bet_a_prov['retrieved_at'][:10]}, registry diff {bet_c['last_day']}.
Site built: {built_at[:10]}. All dates and times are UTC.

## Datasets

- National tender award statistics: {bet_a_prov['records']:,} awards, FY2013-FY2026,
  {len(bet_a_pages)} pages at /ja/bet-a/ and /en/bet-a/, one per buying ministry sector
  and fiscal year. JSON twin of every page under /data/bet-a/.
  Field notes: sector = the ministry that bought; prefecture = the winner's
  registered head office; the source publishes no predicted price (予定価格), so
  there is no award-ratio (落札率) field; {quartiles}.
  Attribution: {PPORTAL_ATTRIB}
  Licence: {PPORTAL_LICENSE_NAME}. {PPORTAL_MODIFIED}
- Corporate-number registry diff archive: {bet_c['total']:,} records over
  {bet_c['n_files']} publication days ({bet_c['first_day']} to {bet_c['last_day']}),
  at /ja/bet-c/ and /en/bet-c/. JSON at /data/bet-c/daily.json, weekly RSS at
  {FEED_URLS['nta-diff.xml']}. The publisher deletes each daily file after 40 days.
  Corporations and public bodies only; no individual's name is held.
  Attribution: {NTA_ATTRIB}
  Licence: {NTA_LICENSE_NAME}. {NTA_MODIFIED}
- Japan hiring first-seen index: not published yet.

## Privacy facts

- No email collection, no form, no cookies, no analytics.
- Two counters. The notify button: one view and one click per product,
  browser and UTC day, stored as a salted hash for at most 25 hours; the count
  is an upper bound. Feed fetches: daily totals per feed, de-duplicated by a
  salted hash of User-Agent and IP /16 kept at most 25 hours; no IP is stored.
- Individuals, including sole proprietors, are masked at ingestion. No winner
  directory exists.
- Contact and removal requests: GitHub Issues, {GITHUB_ISSUES}

## Machine endpoints

- /data/bet-a/index.json — every statistics page and its JSON endpoint
- /data/bet-a/<sector-slug>-fy<year>.json — one statistics page
- /data/bet-c/daily.json — daily registry-diff counts
- /data/site.json — build status and data freshness
- {FEED_URLS['articles.xml']} — articles, RSS
- {FEED_URLS['articles.json']} — articles, JSON Feed 1.1
- {FEED_URLS['nta-diff.xml']} — registry diff, weekly RSS
- {FEED_URLS['nta-diff.json']} — registry diff, daily JSON Feed 1.1
- /ja/subscribe.html — every way to follow updates
- /sitemap.xml
{llms_articles(arts)}"""


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


def check(out: Path, data_pages, data_json=frozenset(), figures=(), article_pages=frozenset()):
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
            or rel in article_pages
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
        if rel in article_pages:
            # An article states numbers from the sources: Article JSON-LD, the
            # attribution block and a notify button (so article views enter the
            # intent denominator with their clicks) are all required.
            if '"@type":"Article"' not in text:
                problems.append(f"{rel}: article without Article JSON-LD")
            if 'class="attrib"' not in text:
                problems.append(f"{rel}: article without the attribution block")
            if 'data-intent="' not in text:
                problems.append(f"{rel}: article without a notify button")
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
        # Empty the directory rather than remove it: a preview server or a shell
        # sitting in site/public/ holds the directory itself open on Windows.
        for child in OUT.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
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
    try:
        arts = articles_mod.load_articles(CONTENT_DIR)
    except articles_mod.ArticleError as err:
        print(f"article error: {err}", file=sys.stderr)
        return 1
    ARTICLE_LANGS.clear()
    ARTICLE_LANGS.update(a["lang"] for a in arts)
    n_ja = sum(1 for a in arts if a["lang"] == "ja")
    print(f"  articles: {len(arts)} (ja {n_ja}, en {len(arts) - n_ja})")
    chart_factory = article_charts(bet_a_pages, bet_c)
    article_pages = set()

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
        "/* Runtime configuration. intentEndpoint is the project's Cloudflare\n"
        "   Worker; the CSP connect-src allows exactly its host. Empty would mean:\n"
        "   confirm locally, post nothing. */\n"
        'window.DELTAKURA = { intentEndpoint: "%s" };\n' % INTENT_ENDPOINT,
    )

    # root
    emit("/", build_root(bet_a_pages, bet_c, built_at))

    index_entries = []
    for lang in ("ja", "en"):
        lang_arts = [a for a in arts if a["lang"] == lang]
        path, _alt, doc = build_home(lang, bet_a_pages, bet_a_prov, bet_c, built_at, arts=lang_arts)
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

        path, _alt, doc = build_privacy(
            lang, bet_a_prov, bet_c, built_at,
            any_estimated=any(not p["quantiles_exact"] for p in bet_a_pages),
        )
        emit(path, doc)

        path, doc = build_subscribe(lang)
        emit(path, doc)

        path, doc = build_articles_index(lang, lang_arts, other_lang_count=len(arts) - len(lang_arts))
        emit(path, doc)
        for art in lang_arts:
            counterpart = any(b["slug"] == art["slug"] and b["lang"] != lang for b in arts)
            try:
                path, doc = build_article(art, chart_factory, counterpart)
            except articles_mod.ArticleError as err:
                print(f"article error: {err}", file=sys.stderr)
                return 1
            emit(path, doc)
            article_pages.add(path.lstrip("/"))

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
                    "feed": FEED_URLS["nta-diff.xml"],
                },
            },
            "feeds": {**FEED_URLS, "stats": FEED_STATS_URL},
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
            "privacy": {
                "cookies": False,
                "email_collection": False,
                "third_party_trackers": False,
                "feed_fetch_counting": (
                    "daily totals per feed, de-duplicated by a salted hash of User-Agent and "
                    "IP /16 kept at most 25 hours; no IP address is stored"
                ),
            },
        },
    )

    # a tiny human-readable index of the endpoints
    endpoints = [
        ("/data/site.json", "build status and data freshness"),
        ("/data/bet-a/index.json", "every tender-award statistics page"),
        ("/data/bet-a/&lt;sector-slug&gt;-fy&lt;year&gt;.json", "one statistics page"),
        ("/data/bet-c/daily.json", "daily registry-diff counts"),
        (FEED_URLS["nta-diff.xml"], "weekly RSS of the registry diff"),
        (FEED_URLS["nta-diff.json"], "daily JSON Feed of the registry diff"),
        (FEED_URLS["articles.xml"], "articles, RSS"),
        (FEED_URLS["articles.json"], "articles, JSON Feed"),
        (FEED_STATS_URL, "feed fetch counts per day"),
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
<h1 class="title">Data files (JSON)</h1>
<p class="sub">A machine-readable twin of every statistics page. No key. CORS open.</p>
{table("", ["Endpoint", "What it returns"], rows)}
<p class="fine">Keep <code>provenance.attribution</code> when you redistribute.</p>
""",
            attribution=source_notices("en"),
            alt_path="/data/",
        ),
    )

    # ---- feed, sitemap, robots, llms.txt
    write(OUT / "feeds" / "nta-diff.xml", build_feed(bet_c, built_at))
    write(OUT / "feeds" / "articles.xml", build_articles_rss(arts, built_at))
    write_json(OUT / "feeds" / "articles.json", build_articles_json(arts, built_at))
    write(OUT / "sitemap.xml", build_sitemap(written, built_at))
    write(OUT / "robots.txt", build_robots())
    write(OUT / "llms.txt", build_llms_txt(bet_a_pages, bet_a_prov, bet_c, built_at, arts))
    write(
        OUT / "404.html",
        render_page(
            lang="en",
            path="/404.html",
            title="Not found | Deltakura",
            desc_ja="お探しのページは見つかりませんでした。",
            desc_en="That page does not exist on this site.",
            body='<h1 class="title">404</h1><p class="sub">That page does not exist. / お探しのページは見つかりませんでした。</p>'
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
        problems, total, biggest = check(OUT, data_pages, data_json, figures, article_pages)
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
