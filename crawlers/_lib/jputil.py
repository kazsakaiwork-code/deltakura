"""Japanese public-data helpers: 和暦 dates, 都道府県 codes, 年度, NFKC keys."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ------------------------------------------------------------------ 元号

_ERA_BASE = {
    "令和": 2018,
    "平成": 1988,
    "昭和": 1925,
    "大正": 1911,
    "明治": 1867,
}

_WAREKI_RE = re.compile(
    r"(令和|平成|昭和|大正|明治)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
_WAREKI_YM_RE = re.compile(r"(令和|平成|昭和|大正|明治)\s*(\d{1,2}|元)\s*年度")


def wareki_to_iso(text: str) -> Optional[str]:
    """'令和8年9月18日' -> '2026-09-18'. Returns None if no 和暦 date is found."""
    m = _WAREKI_RE.search(unicodedata.normalize("NFKC", text))
    if not m:
        return None
    era, year, month, day = m.groups()
    y = 1 if year == "元" else int(year)
    return f"{_ERA_BASE[era] + y:04d}-{int(month):02d}-{int(day):02d}"


def wareki_nendo_to_seireki(text: str) -> Optional[int]:
    """'令和07年度' -> 2025 (the calendar year the fiscal year starts in)."""
    m = _WAREKI_YM_RE.search(unicodedata.normalize("NFKC", text))
    if not m:
        return None
    era, year = m.groups()
    y = 1 if year == "元" else int(year)
    return _ERA_BASE[era] + y


def fiscal_year(iso_date: str) -> Optional[int]:
    """Japanese 年度: April 1 - March 31. '2026-03-31' -> 2025."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso_date or "")
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return year if month >= 4 else year - 1


# ------------------------------------------------------------ 都道府県

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

PREF_CODE_TO_NAME = {f"{i + 1:02d}": name for i, name in enumerate(PREFECTURES)}
PREF_NAME_TO_CODE = {name: code for code, name in PREF_CODE_TO_NAME.items()}

UNKNOWN = "不明"


def prefecture_from_code(code: str) -> str:
    if code is None:
        return UNKNOWN
    code = str(code).strip()
    if not code:
        return UNKNOWN
    return PREF_CODE_TO_NAME.get(code.zfill(2), UNKNOWN)


# ------------------------------------------------------------ normalise

_CORP_FORM_TOKENS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "特定非営利活動法人", "社会福祉法人", "医療法人社団", "医療法人財団",
    "医療法人", "学校法人", "宗教法人", "農業協同組合", "事業協同組合",
    "協同組合", "独立行政法人", "国立大学法人", "地方独立行政法人",
    "(株)", "（株）", "(有)", "（有）",
]


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def normalize_key(text: str) -> str:
    """NFKC, strip whitespace / 括弧 / 法人格 tokens, lowercase."""
    s = nfkc(text)
    for token in _CORP_FORM_TOKENS:
        s = s.replace(token, "")
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[()（）\[\]［］{}｛｝「」『』]", "", s)
    return s.lower()
