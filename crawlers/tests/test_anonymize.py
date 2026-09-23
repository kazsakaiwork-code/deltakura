"""Anonymisation test cases T1-T8, plus the guards around them.

Run via `python crawlers/tests/run_tests.py`.

One deliberate deviation from the case table, flagged here rather than hidden:
T1 specifies the corporate number `1234567890123` and labels it "(valid)".
Those digits do not satisfy the NTA check-digit algorithm — the label states
the intent, the digits are a placeholder. Using them literally would make T1
and T6 the same test and would assert the opposite of the rule. T1 therefore
uses a constructed, genuinely checksum-valid number, and
`test_t1_placeholder_is_actually_invalid` asserts why.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import anonymize, jputil, paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pportal"))

import stats as pportal_stats  # noqa: E402


# ------------------------------------------------------------ helpers

def make_valid_corporate_number(body12: str) -> str:
    """Prefix a 12-digit body with its NTA check digit."""
    assert len(body12) == 12 and body12.isdigit()
    total = sum(
        int(ch) * (1 if n % 2 else 2)
        for n, ch in enumerate(reversed(body12), start=1)
    )
    return f"{9 - (total % 9)}{body12}"


VALID_A = make_valid_corporate_number("234567890123")   # for T1
VALID_B = make_valid_corporate_number("010001138614")   # for T5
T1_PLACEHOLDER = "1234567890123"


# ------------------------------------------------------- the algorithm

def test_check_digit_algorithm_matches_real_published_numbers():
    # Numbers observed in the NTA 差分 files on 2026-09-18.
    for number in ("1010001138614", "7011003005763", "1010001171342"):
        assert anonymize.is_valid_corporate_number(number), number


def test_check_digit_rejects_malformed():
    for bad in ("", None, "123", "12345678901234", "abcdefghijklm", "０１０００１"):
        assert not anonymize.is_valid_corporate_number(bad), bad


def test_t1_placeholder_is_actually_invalid():
    """Documents why T1 below does not use the case table's literal digits."""
    assert not anonymize.is_valid_corporate_number(T1_PLACEHOLDER)


def test_make_valid_helper_round_trips():
    assert anonymize.is_valid_corporate_number(VALID_A)
    assert anonymize.is_valid_corporate_number(VALID_B)


# ------------------------------------------------------------- T1..T8

def test_T1_corporate_with_valid_number_is_kept_in_full():
    result = anonymize.anonymize_winner("株式会社山田製作所", VALID_A)
    assert result.winner_type == anonymize.CORPORATE
    assert result.winner_name == "株式会社山田製作所"
    assert result.winner_masked is None
    assert result.corporate_number == VALID_A
    assert result.flags == []


def test_T2_individual_without_number_is_masked():
    result = anonymize.anonymize_winner(
        "山田太郎", "", gyoshu="翻訳", prefecture="東京都"
    )
    assert result.winner_type == anonymize.MASKED_INDIVIDUAL
    assert result.winner_name is None
    assert result.winner_masked == "個人事業主・翻訳・東京都"
    assert result.corporate_number is None
    assert "山田" not in (result.winner_masked or "")


def test_T3_yago_containing_a_personal_name_is_masked():
    result = anonymize.anonymize_winner(
        "山田太郎商店", None, gyoshu="小売", prefecture="大阪府"
    )
    assert result.winner_type == anonymize.MASKED_INDIVIDUAL
    assert result.winner_name is None
    assert result.winner_masked == "個人事業主・小売・大阪府"


def test_T4_corporate_looking_name_without_a_number_is_still_masked():
    """Fail-closed: no number means mask, however corporate the name looks."""
    result = anonymize.anonymize_winner("ヤマダ印刷", "")
    assert result.winner_type == anonymize.MASKED_INDIVIDUAL
    assert result.winner_name is None
    assert result.winner_masked == "個人事業主・不明・不明"
    assert "no_corporate_number" in result.flags


def test_T4b_even_a_kabushiki_kaisha_without_a_number_is_masked():
    result = anonymize.anonymize_winner("株式会社ヤマダ印刷", "")
    assert result.winner_type == anonymize.MASKED_INDIVIDUAL
    assert result.winner_name is None


def test_T5_incorporated_association_with_valid_number_is_kept():
    result = anonymize.anonymize_winner("一般社団法人日本◯◯協会", VALID_B)
    assert result.winner_type == anonymize.CORPORATE
    assert result.winner_name == "一般社団法人日本◯◯協会"


def test_T6_invalid_checksum_is_masked_and_flagged():
    result = anonymize.anonymize_winner("株式会社サンプル", T1_PLACEHOLDER)
    assert result.winner_type == anonymize.MASKED_INDIVIDUAL
    assert result.winner_name is None
    assert result.corporate_number is None
    assert "invalid_corporate_number" in result.flags


def test_T7_personal_contact_fields_are_dropped_at_parse():
    record = {
        "調達案件名称": "翻訳業務",
        "担当者": "佐藤花子",
        "連絡先電話番号": "03-0000-0000",
        "メールアドレス": "someone@example.com",
        "落札価格": "1000000",
    }
    cleaned = anonymize.drop_personal_fields(record)
    assert "担当者" not in cleaned
    assert "連絡先電話番号" not in cleaned
    assert "メールアドレス" not in cleaned
    assert cleaned["落札価格"] == "1000000"
    assert "佐藤花子" not in "".join(str(v) for v in cleaned.values())


def test_T8_small_bucket_is_suppressed():
    buckets = {
        ("翻訳", "鳥取県", "2026-10"): 2,     # suppressed
        ("翻訳", "東京都", "2026-10"): 11,    # kept
        ("印刷", "鳥取県", "2026-10"): 0,     # kept: no individuals at all
        ("設計", "島根県", "2026-10"): 3,     # kept: exactly at the threshold
    }
    kept = anonymize.suppress_small_buckets(buckets)
    assert ("翻訳", "鳥取県", "2026-10") not in kept
    assert kept[("翻訳", "東京都", "2026-10")] == 11
    assert ("印刷", "鳥取県", "2026-10") in kept
    assert ("設計", "島根県", "2026-10") in kept
    assert not anonymize.bucket_is_publishable(2)
    assert anonymize.bucket_is_publishable(3)
    assert anonymize.bucket_is_publishable(0)


# ---------------------------------------------------------------- R4

def test_R4_detects_person_names_in_free_text():
    for text in (
        "佐藤花子氏に係る翻訳業務の委託",
        "山田 太郎 に対する講演謝金",
        "Lecture by Dr. Jane Doe",
        "田中様との個別相談",
    ):
        assert anonymize.contains_person_name(text), text


def test_R4_does_not_mask_ordinary_procurement_titles():
    for text in (
        "令和8年度デジタル人材採用に係る求人サービス等の利用支援",
        "神戸航空交通管制部で使用する電気の購入",
        "【関東地方整備局、本局】建設事業予算執行管理システムの運用管理及び保守等業務",
        "新潟航空基地庁舎ほか3箇所で使用する電気の調達(高圧)",
        "中村地区道路改良工事",
    ):
        assert not anonymize.contains_person_name(text), text


def test_R4_regression_real_titles_that_an_unanchored_detector_masked():
    """Every string here is a real 調達ポータル title.

    An earlier, unanchored version of the detector fired on 1,068 of the
    313,607 titles in the archive, and every one was a false positive: 仕様,
    多様, 模様 and 殿 are ordinary procurement vocabulary. Masking these would
    have destroyed legitimate titles while protecting nobody.
    """
    for text in (
        "家計調査オンライン調査システム設計・開発に係る調達仕様書作成等支援業務の請負",   # 仕様
        "令和７年度殿ダム管理支所昇降機保守点検",                                   # 殿
        "事務用封筒（間伐材仕様）の購入（単価契約）",                                # 仕様）
        "出動服（ドライ・ストレッチ仕様）　外１点",                                  # 仕様）
        "令和７年度生物多様性センター休日運営管理業務",                              # 多様性
        "令和６年度福岡矯正管区庁舎等模様替等工事",                                  # 模様替
        "七宗国有林　森林環境保全整備事業　岐阜６",                                  # 林＋空白＋森林
        "水無国有林　保安林総合改良整備工事　富山１",
        "財務省ホームページに係る調達仕様書作成等支援に関するコンサルティング業務",
        "地方自治体における情報システム（健康管理）の標準仕様書改定に向けた調査研究等一式",
    ):
        assert not anonymize.contains_person_name(text), text


def test_R4_masking_replaces_the_text():
    masked, hit = anonymize.mask_free_text("佐藤花子氏への謝金")
    assert hit and masked == anonymize.TITLE_MASK and "佐藤" not in masked
    kept, hit2 = anonymize.mask_free_text("電気の購入")
    assert not hit2 and kept == "電気の購入"


# ---------------------------------------------------------------- R5

def test_R5_refuses_an_aggregate_keyed_by_a_party_name():
    try:
        anonymize.assert_not_a_directory(
            [{"winner_name": "株式会社A", "n_awards": 3}], context="unit test"
        )
    except ValueError as exc:
        assert "R5" in str(exc)
    else:
        raise AssertionError("R5 guard did not fire")


def test_R5_allows_a_geography_by_category_aggregate():
    anonymize.assert_not_a_directory(
        [{"prefecture": "東京都", "sector": "防衛", "n_awards": 12}],
        context="unit test",
    )


# ------------------------------------- R6b: the suppression counter's guard

def test_r6b_zero_is_always_publishable():
    # Zero says nothing about any bucket, so it needs no ambiguity set.
    assert pportal_stats.suppression_disclosure(0, 0) == 0
    assert pportal_stats.suppression_disclosure(0, 47) == 0


def test_r6b_blanks_a_count_when_too_few_prefectures_are_absent():
    # One absent prefecture means the group itself names the suppressed bucket.
    assert pportal_stats.suppression_disclosure(2, 1) == ""
    assert pportal_stats.suppression_disclosure(1, 2) == ""


def test_r6b_publishes_a_count_with_a_wide_enough_ambiguity_set():
    assert pportal_stats.suppression_disclosure(1, 3) == 1
    assert pportal_stats.suppression_disclosure(2, 9) == 2


def test_r6b_holds_over_the_published_table():
    """Every non-zero count in the shipped stats_v0.csv passes the guard.

    Read from `data/published/pportal/stats_v0.csv`, so this is a check on what
    is actually committed, not on what the code would produce today.
    """
    import csv as _csv
    from collections import defaultdict as _dd

    path = paths.PUBLISHED / "pportal" / "stats_v0.csv"
    if not path.exists():                      # a checkout without the table
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))
    assert rows, "the published statistics table is empty"

    prefectures = {r["prefecture"] for r in rows}
    published = _dd(set)
    counts = {}
    for r in rows:
        group = (r["fiscal_year"], r["sector"])
        published[group].add(r["prefecture"])
        counts[group] = r["n_awards_suppressed_in_group"]

    for group, raw in counts.items():
        if raw in ("", "0"):
            continue
        absent = len(prefectures) - len(published[group])
        assert absent >= pportal_stats.MIN_ABSENT_PREFECTURES, (
            f"{group}: publishes {raw} suppressed award(s) with only {absent} "
            "prefecture(s) absent from the group"
        )


# ------------------------------------------------------- jputil basics

def test_wareki_conversion():
    assert jputil.wareki_to_iso("令和8年9月18日") == "2026-09-18"
    assert jputil.wareki_to_iso("令和元年5月1日") == "2019-05-01"
    assert jputil.wareki_to_iso("平成31年4月30日") == "2019-04-30"
    assert jputil.wareki_to_iso("no date here") is None


def test_fiscal_year_boundaries():
    assert jputil.fiscal_year("2026-04-01") == 2026
    assert jputil.fiscal_year("2026-03-31") == 2025
    assert jputil.fiscal_year("") is None


def test_prefecture_codes():
    assert jputil.prefecture_from_code("13") == "東京都"
    assert jputil.prefecture_from_code("1") == "北海道"
    assert jputil.prefecture_from_code("47") == "沖縄県"
    assert jputil.prefecture_from_code("") == "不明"


def test_normalize_key_strips_corporate_form():
    assert jputil.normalize_key("株式会社　山田製作所") == jputil.normalize_key("山田製作所")
    assert jputil.normalize_key("（株）ＡＢＣ") == "abc"
