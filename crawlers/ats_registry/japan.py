"""Deciding whether a public job posting is a Japan role.

The Bet-B prototype established the problem: the list endpoints return one
free-text location per posting, and on Japanese boards that string is often a
work style rather than a place (83 of 85 postings on one board said "Hybrid").
So country attribution comes from the curated registry, and this module answers
the narrower question "does this posting's location text name a place in
Japan?" — used to filter which of a Japan-hiring company's postings are the
Japan ones.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib.jputil import PREFECTURES  # noqa: E402,F401  (re-exported for callers)

# Romanised prefecture names, without the 県/府/都 suffix.
_PREF_ROMAJI = {
    "hokkaido": "北海道", "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県",
    "akita": "秋田県", "yamagata": "山形県", "fukushima": "福島県",
    "ibaraki": "茨城県", "tochigi": "栃木県", "gunma": "群馬県", "saitama": "埼玉県",
    "chiba": "千葉県", "tokyo": "東京都", "kanagawa": "神奈川県",
    "niigata": "新潟県", "toyama": "富山県", "ishikawa": "石川県", "fukui": "福井県",
    "yamanashi": "山梨県", "nagano": "長野県", "gifu": "岐阜県", "shizuoka": "静岡県",
    "aichi": "愛知県", "mie": "三重県", "shiga": "滋賀県", "kyoto": "京都府",
    "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県", "wakayama": "和歌山県",
    "tottori": "鳥取県", "shimane": "島根県", "okayama": "岡山県",
    "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県",
    "kagawa": "香川県", "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県",
    "saga": "佐賀県", "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県",
    "miyazaki": "宮崎県", "kagoshima": "鹿児島県", "okinawa": "沖縄県",
}

# Major cities that imply a prefecture without naming it.
_CITY_TO_PREF = {
    "yokohama": "神奈川県", "kawasaki": "神奈川県", "sagamihara": "神奈川県",
    "nagoya": "愛知県", "kobe": "兵庫県", "sapporo": "北海道",
    "sendai": "宮城県", "saitama": "埼玉県", "chiba": "千葉県",
    "kitakyushu": "福岡県", "hamamatsu": "静岡県", "kumamoto": "熊本県",
    "shibuya": "東京都", "shinjuku": "東京都", "minato": "東京都",
    "chiyoda": "東京都", "roppongi": "東京都", "otemachi": "東京都",
    "marunouchi": "東京都", "shinagawa": "東京都",
    "横浜": "神奈川県", "川崎": "神奈川県", "名古屋": "愛知県", "神戸": "兵庫県",
    "札幌": "北海道", "仙台": "宮城県", "福岡市": "福岡県", "渋谷": "東京都",
    "新宿": "東京都", "港区": "東京都", "千代田": "東京都", "品川": "東京都",
}

_JAPAN_WORDS = re.compile(r"\b(japan|nippon|nihon|jpn)\b", re.IGNORECASE)
# "Japantown" (San Francisco / San Jose) is the one common false friend.
_JAPAN_FALSE_FRIEND = re.compile(r"japan\s*town", re.IGNORECASE)
_JP_KANJI = re.compile(r"日本|東京|大阪|京都|北海道|沖縄")

_REMOTE = re.compile(r"\b(remote|リモート|在宅|work from home|wfh|anywhere)\b", re.IGNORECASE)

_EMPLOYMENT = {
    "full-time": "full_time", "fulltime": "full_time", "full time": "full_time",
    "part-time": "part_time", "parttime": "part_time", "part time": "part_time",
    "contract": "contract", "contractor": "contract",
    "intern": "intern", "internship": "intern",
    "temporary": "temporary", "temp": "temporary",
    "正社員": "full_time", "契約社員": "contract", "業務委託": "contract",
    "アルバイト": "part_time", "インターン": "intern",
}

_FUNCTION_RULES = [
    ("devrel", (
        "developer advocate", "developer relations", "devrel", "community manager",
        "technical evangelist", "developer experience",
    )),
    ("sales", (
        "sales", "account executive", "account manager", "business development",
        "bizdev", "partnerships", "customer success", "revenue", "sdr", "bdr",
        "営業", "セールス", "カスタマーサクセス",
    )),
    ("engineering", (
        "engineer", "engineering", "developer", "sre", "devops", "architect",
        "data scientist", "machine learning", "programmer", "qa ", "security",
        "platform", "backend", "frontend", "full stack", "fullstack",
        "エンジニア", "開発", "プログラマ",
    )),
]


def _fold(text: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def is_japan_location(location: Optional[str]) -> bool:
    """True when the location text names a place in Japan.

    Deliberately conservative: a bare "Remote" or "Hybrid" is not Japan, even on
    a Japanese company's board. The registry says the *company* hires in Japan;
    this says the *posting* is located there.
    """
    text = _fold(location)
    if not text:
        return False
    lowered = text.lower()
    if _JAPAN_FALSE_FRIEND.search(lowered):
        return False
    if _JAPAN_WORDS.search(lowered) or _JP_KANJI.search(text):
        return True
    for name in _PREF_ROMAJI:
        if re.search(rf"\b{name}\b", lowered):
            return True
    for city in _CITY_TO_PREF:
        if city.isascii():
            if re.search(rf"\b{city}\b", lowered):
                return True
        elif city in text:
            return True
    return any(pref in text for pref in PREFECTURES)


def japan_prefecture(location: Optional[str]) -> str:
    """Best-effort 都道府県 for a Japan location; '' when undetermined."""
    text = _fold(location)
    if not text:
        return ""
    lowered = text.lower()
    for pref in PREFECTURES:
        if pref in text:
            return pref
    for name, pref in _PREF_ROMAJI.items():
        if re.search(rf"\b{name}\b", lowered):
            return pref
    for city, pref in _CITY_TO_PREF.items():
        if city.isascii():
            if re.search(rf"\b{city}\b", lowered):
                return pref
        elif city in text:
            return pref
    return ""


def is_remote(location: Optional[str], extra: Optional[str] = None) -> bool:
    blob = f"{_fold(location)} {_fold(extra)}"
    return bool(_REMOTE.search(blob))


def employment_type(value: Optional[str]) -> str:
    text = _fold(value).lower()
    for needle, bucket in _EMPLOYMENT.items():
        if needle in text:
            return bucket
    return ""


def function_bucket(title: Optional[str]) -> str:
    """sales | engineering | devrel | other."""
    text = _fold(title).lower()
    for bucket, needles in _FUNCTION_RULES:
        if any(n in text for n in needles):
            return bucket
    return "other"


def normalize_title(title: Optional[str]) -> str:
    text = _fold(title).lower()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", text)        # (Tokyo), 【急募】
    text = re.sub(r"[-–—/|,、・]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify(location: Optional[str], title: Optional[str],
             commitment: Optional[str] = None) -> Tuple[bool, str, bool, str, str, str]:
    """(is_japan, japan_prefecture, remote_flag, employment_type,
        function_bucket, job_title_normalized)"""
    return (
        is_japan_location(location),
        japan_prefecture(location),
        is_remote(location, commitment),
        employment_type(commitment),
        function_bucket(title),
        normalize_title(title),
    )
