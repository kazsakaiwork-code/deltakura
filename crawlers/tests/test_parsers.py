"""Parser and classifier tests. Fixtures only; no network access.

The fixtures are trimmed copies of real payloads observed on 2026-09-21, kept
small enough to read. They exist so a publisher-side schema change shows up as a
failing test rather than as a quietly empty dataset.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
CRAWLERS = HERE.parent
for _p in (CRAWLERS, CRAWLERS / "ats_registry", CRAWLERS / "pportal", CRAWLERS / "nta_diff"):
    sys.path.insert(0, str(_p))

from _lib import anonymize, tos  # noqa: E402

import ats as ats_mod  # noqa: E402
import codes  # noqa: E402
import japan  # noqa: E402


def _load(alias: str, path: Path) -> ModuleType:
    """Load a module from an explicit path.

    Both collectors are called `collect.py`, so a plain `import collect` would
    resolve to whichever directory happens to sit first on sys.path and the
    other one's tests would silently test the wrong module.
    """
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    sys.modules[alias] = module
    spec.loader.exec_module(module)                          # type: ignore[union-attr]
    return module


pportal_collect = _load("deltakura_pportal_collect", CRAWLERS / "pportal" / "collect.py")
nta_collect = _load("deltakura_nta_collect", CRAWLERS / "nta_diff" / "collect.py")


# ------------------------------------------------------- 調達ポータル

PPORTAL_CSV = (
    '"0000000000000496653","令和7年度デジタル人材採用に係る求人サービス等の利用支援",'
    '"2026-04-01","31389000.00","W1","8004030","Ｐｏｌｅ＆Ｌｉｎｅ合同会社","7011003005763"\r\n'
    '"0000000000000539273","建設事業予算執行管理システムの運用管理及び保守等業務",'
    '"2026-04-01","1073500000.00","S1","8002040","株式会社ＮＴＴデータ","6010601062093"\r\n'
    '"0000000000000999999","翻訳業務","2026-04-02","250000.00","J1","8014020","個人名が入る欄",""\r\n'
)


def _pportal_rows():
    clearance = tos.clear("調達ポータル 落札実績オープンデータ")
    return pportal_collect.normalize_rows(
        PPORTAL_CSV.encode("utf-8-sig"), clearance, "2026-09-21T00:00:00Z"
    )


def test_pportal_columns_and_codes():
    rows = _pportal_rows()
    assert len(rows) == 3
    first = rows[0]
    assert first["publisher_code"] == "W1"
    assert first["publisher_name"] == "デジタル庁"
    assert first["sector"] == "総務・情報通信"
    assert first["method"] == "随意契約"
    assert first["method_detail"] == "随意契約方式・公募型プロポーザル方式"
    assert first["amount_jpy"] == 31389000
    assert first["fiscal_year"] == 2026
    assert first["award_date"] == "2026-04-01"


def test_pportal_keeps_a_corporate_winner_and_masks_one_without_a_number():
    rows = _pportal_rows()
    assert rows[0]["winner_type"] == anonymize.CORPORATE
    assert rows[0]["winner_name"] == "Pole&Line合同会社"   # NFKC-normalised
    last = rows[2]
    assert last["winner_type"] == anonymize.MASKED_INDIVIDUAL
    assert last["winner_name"] == ""
    assert last["winner_masked"] == "個人事業主・不明・不明"
    assert "個人名が入る欄" not in json.dumps(rows, ensure_ascii=False, default=str)


def test_pportal_carries_attribution_and_declares_missing_columns():
    row = _pportal_rows()[0]
    assert "調達ポータル" in row["attribution"]
    assert row["source_url"].startswith("https://www.p-portal.go.jp/")
    # The source publishes neither of these, and the record must say so rather
    # than invent a value.
    assert row["predicted_price_jpy"] == ""
    assert row["award_ratio"] == ""
    assert row["bidder_count"] == ""
    assert row["category_code"] == ""


def test_pportal_award_id_is_stable_and_order_independent():
    a = _pportal_rows()
    b = _pportal_rows()
    assert [r["award_id"] for r in a] == [r["award_id"] for r in b]
    assert len({r["award_id"] for r in a}) == 3


def test_ministry_and_method_tables_cover_the_spec():
    assert len(codes.MINISTRY) == 53
    assert len(codes.BIDDING_METHOD) == 16
    assert codes.ministry_name("U1") == "防衛省"
    assert codes.method_bucket("8002010") == "一般競争"
    assert codes.method_bucket("8003040") == "指名競争"
    assert codes.method_bucket("8001010") == "随意契約"
    assert codes.method_bucket("9999999") == "その他"
    assert all(code in codes.SECTOR for code in codes.MINISTRY)


# ---------------------------------------------------------------- NTA

NTA_CSV = (
    '1,1010001138614,12,0,2026-09-18,2026-08-25,"株式会社百吉",,301,"東京都","港区",'
    '"北青山１丁目３番１号",,13,103,1070061,,,,,,,2015-10-05,1,,,,,"モモキチ",0\r\n'
    '2,1010001171342,12,0,2026-09-18,2026-09-08,"ヴェルテックス株式会社",,301,"広島県",'
    '"広島市中区","江波東２丁目３番２９号",,34,101,7300832,,,,,,,2015-10-28,1,,,,,'
    '"ヴエルテツクス",0\r\n'
)


def test_nta_layout_maps_thirty_columns():
    clearance = tos.clear("法人番号公表サイト 差分データ")
    rows = nta_collect.parse_diff_csv(
        NTA_CSV.encode("utf-8-sig"), "2026-09-18", clearance,
        "2026-09-21T00:00:00Z", "https://example.invalid/",
    )
    assert len(rows) == 2
    first = rows[0]
    assert first["corporate_number"] == "1010001138614"
    assert first["corporate_number_valid"] == "Y"
    assert first["prefecture"] == "東京都"
    assert first["prefecture_code"] == "13"
    assert first["city"] == "港区"
    assert first["change_date"] == "2026-08-25"
    assert first["update_date"] == "2026-09-18"
    assert first["furigana"] == "モモキチ"
    assert first["record_key"] == "1010001138614|2026-08-25|1"
    assert "国税庁" in first["attribution"]


def test_nta_layout_has_no_column_that_could_hold_a_person():
    for _, name in nta_collect.NTA_LAYOUT:
        if name:
            assert not anonymize.is_personal_field(name), name


# ---------------------------------------------------------------- ATS

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 4567890,
            "title": "Senior Backend Engineer",
            "location": {"name": "Tokyo, Japan"},
            "absolute_url": "https://job-boards.greenhouse.io/example/jobs/4567890",
            "updated_at": "2026-09-01T00:00:00-04:00",
        },
        {
            "id": 4567891,
            "title": "Account Executive",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://job-boards.greenhouse.io/example/jobs/4567891",
        },
        {
            "id": 4567892,
            "title": "カスタマーサクセス担当",
            "location": {"name": "Hybrid"},
            "absolute_url": "https://job-boards.greenhouse.io/example/jobs/4567892",
        },
    ],
    "meta": {"total": 3},
}

LEVER_PAYLOAD = [
    {
        "id": "abc-123",
        "text": "Developer Advocate",
        "hostedUrl": "https://jobs.lever.co/example/abc-123",
        "createdAt": 1750000000000,
        "categories": {"location": "Tokyo", "team": "DevRel", "commitment": "Full-time"},
        "description": "SHOULD NOT BE STORED",
    }
]


def test_greenhouse_parse_shape():
    rows = ats_mod.parse(ats_mod.GREENHOUSE, GREENHOUSE_PAYLOAD)
    assert len(rows) == 3
    assert rows[0]["job_id"] == "4567890"
    assert rows[0]["location"] == "Tokyo, Japan"
    assert rows[0]["url"].endswith("/4567890")


def test_lever_parse_shape_and_drops_the_description():
    rows = ats_mod.parse(ats_mod.LEVER, LEVER_PAYLOAD)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "abc-123"
    assert rows[0]["title"] == "Developer Advocate"
    assert rows[0]["commitment"] == "Full-time"
    assert "description" not in rows[0]
    assert "SHOULD NOT BE STORED" not in json.dumps(rows)


def test_parse_raises_on_schema_drift():
    for ats_name, bad in (
        (ats_mod.GREENHOUSE, {"results": []}),
        (ats_mod.LEVER, {"postings": []}),
    ):
        try:
            ats_mod.parse(ats_name, bad)
        except ValueError:
            continue
        raise AssertionError(f"{ats_name} accepted a payload of the wrong shape")


def test_posting_id_follows_the_documented_key():
    assert ats_mod.posting_id("greenhouse", "paypay", "123") == "greenhouse:paypay:123"


# -------------------------------------------------- Japan classifier

def test_japan_location_detection():
    for text in (
        "Tokyo, Japan", "Japan", "Osaka", "東京都", "日本・大阪",
        "Yokohama, Kanagawa", "Remote - Japan", "Fukuoka, Japan", "JPN",
    ):
        assert japan.is_japan_location(text), text


def test_non_japan_and_workstyle_strings_are_not_japan():
    for text in (
        "", "Hybrid", "Remote", "New York, NY", "London", "Singapore",
        "Japantown, San Francisco", "Anywhere",
    ):
        assert not japan.is_japan_location(text), text


def test_prefecture_resolution():
    assert japan.japan_prefecture("Tokyo, Japan") == "東京都"
    assert japan.japan_prefecture("Yokohama") == "神奈川県"
    assert japan.japan_prefecture("大阪府") == "大阪府"
    assert japan.japan_prefecture("Nagoya, Japan") == "愛知県"
    assert japan.japan_prefecture("Japan") == ""       # country only


def test_function_bucketing():
    assert japan.function_bucket("Senior Backend Engineer") == "engineering"
    assert japan.function_bucket("Account Executive, Japan") == "sales"
    assert japan.function_bucket("Developer Advocate") == "devrel"
    assert japan.function_bucket("Office Manager") == "other"
    assert japan.function_bucket("営業マネージャー") == "sales"


def test_remote_and_employment_type():
    assert japan.is_remote("Remote - Japan")
    assert not japan.is_remote("Tokyo, Japan")
    assert japan.employment_type("Full-time") == "full_time"
    assert japan.employment_type("業務委託") == "contract"
    assert japan.employment_type("") == ""


def test_title_normalisation():
    assert japan.normalize_title("Senior Engineer (Tokyo)") == "senior engineer"
    assert japan.normalize_title("【急募】データ分析エンジニア") == "データ分析エンジニア"
