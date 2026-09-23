"""Fail-closed anonymisation. Rules R1-R6.

Applied at ingestion, before anything is written to a normalized store. The
design rule is that there is no code path that writes an unmasked individual's
name: masking is decided by the *presence of a checksum-valid 法人番号*, never
by how corporate a name looks.

  R1  valid 13-digit 法人番号 -> keep `winner_name`, winner_type=corporate
  R2  no 法人番号            -> mask, whatever the name looks like
  R3  担当者/氏名/連絡先/電話/メール/個人住所 fields dropped at parse
  R4  free text (titles) passed through a person-name detector, masked on hit
  R5  no winner directory is ever built (enforced by the product shape, and by
      `assert_not_a_directory` below for any aggregate about to be published)
  R6  a 業種 x 都道府県 x 期間 bucket with fewer than 3 masked individuals is
      suppressed from public aggregates
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .jputil import UNKNOWN, nfkc

MIN_BUCKET_SIZE = 3  # R6

CORPORATE = "corporate"
MASKED_INDIVIDUAL = "masked_individual"
UNKNOWN_TYPE = "unknown"


# ------------------------------------------------------------- 法人番号

def is_valid_corporate_number(value: Optional[str]) -> bool:
    """13 digits whose leading check digit satisfies the NTA algorithm.

    check = 9 - (sum(P_n * Q_n) mod 9), where P_n is the n-th digit from the
    right of the 12-digit body and Q_n is 1 for odd n, 2 for even n.
    """
    if value is None:
        return False
    s = re.sub(r"[\s-]", "", str(value))
    if not re.fullmatch(r"\d{13}", s):
        return False
    check = int(s[0])
    body = s[1:]
    total = 0
    for n, ch in enumerate(reversed(body), start=1):
        total += int(ch) * (1 if n % 2 else 2)
    return check == 9 - (total % 9)


# --------------------------------------------------------------- R3

_PERSONAL_FIELD_RE = re.compile(
    r"(担当者|担当部署者|氏名|代表者名|代表者氏名|連絡先|電話|TEL|FAX|"
    r"メール|mail|e-?mail|携帯|個人住所|自宅|生年月日|署名|印影)",
    re.IGNORECASE,
)


def is_personal_field(name: str) -> bool:
    return bool(_PERSONAL_FIELD_RE.search(nfkc(name)))


def drop_personal_fields(record: Mapping[str, Any]) -> Dict[str, Any]:
    """R3. Returns a copy with every personal-contact field removed."""
    return {k: v for k, v in record.items() if not is_personal_field(k)}


# --------------------------------------------------------------- R4

# R4 is a *secondary* guard. The primary protection for Bet A is R2, which is
# purely arithmetic (no valid 法人番号 -> mask) and cannot be fooled by text. So
# R4 is tuned to catch real personal names in titles without shredding ordinary
# procurement language, and it is anchored on a known surname rather than on a
# bare kanji run.
#
# Measured against 313,568 real 調達ポータル titles, an unanchored honorific
# pattern fired on 1,068 of them and every sample was a false positive:
# 調達仕様書 / 多様性 / 模様替 / 殿ダム / 間伐材仕様 all contain 様 or 殿
# without naming anybody. Masking those would destroy legitimate titles while
# protecting nobody, so the honorific must follow a surname.
_COMMON_SURNAMES = (
    "佐藤 鈴木 高橋 田中 伊藤 渡辺 山本 中村 小林 加藤 吉田 山田 佐々木 山口 松本 "
    "井上 木村 林 斎藤 清水 山崎 阿部 森 池田 橋本 石川 前田 藤田 小川 後藤 岡田 "
    "長谷川 村上 近藤 石井 斉藤 坂本 遠藤 青木 藤井 西村 福田 太田 三浦 藤原 岡本 "
    "松田 中島 中野 原田 小野 田村 竹内 金子 和田 中山 石田 上田 森田 原 柴田 酒井 "
    "工藤 横山 宮崎 宮本 内田 高木 安藤 島田 谷口 大野 高田 丸山 今井 河野 藤本 "
    "村田 武田 上野 杉山 増田 小島 平野 大塚 千葉 久保 松井 岩崎 桜井 木下 野口 "
    "松尾 菊地 野村 渡部 菅原 久保田 古川 大西 市川 熊谷 川口 渡邊 星野 小山 "
    "松浦 大久保 篠原 服部 吉川 岩田 本田 浅野 小松 早川 川崎 柳沢 岡 秋山 松村"
).split()

_SURNAME_ALT = "|".join(sorted(_COMMON_SURNAMES, key=len, reverse=True))
# Single-kanji surnames (林, 森, 原, ...) are too common inside ordinary words
# (森林, 原子力, 林野庁) to anchor the separator pattern; they stay usable for
# the honorific pattern, where the honorific itself does the disambiguating.
_MULTI_KANJI_SURNAMES = [s for s in _COMMON_SURNAMES if len(s) >= 2]
_SURNAME_ALT_MULTI = "|".join(sorted(_MULTI_KANJI_SURNAMES, key=len, reverse=True))

_BOUNDARY = r"(?:^|[\s　、,，。．・「」『』（）()【】\[\]:：/／])"
# The honorific must not run on into another kanji compound: 佐藤氏 yes (even
# when followed by に係る), 佐藤氏族 no, 小林多様性 no.
_NOT_CONTINUED = r"(?![一-龥々])"

_PERSON_NAME_PATTERNS = [
    # 佐藤花子氏 / 山田様 / 田中殿 - anchored on a known surname.
    re.compile(rf"(?:{_SURNAME_ALT})[一-龥ぁ-んァ-ヶ]{{0,3}}[ 　]*(?:氏|様|殿|さん|先生){_NOT_CONTINUED}"),
    # 佐藤 花子 - surname, separator, given name. Multi-kanji surnames only.
    re.compile(
        rf"{_BOUNDARY}(?:{_SURNAME_ALT_MULTI})[ 　]+[一-龥]{{1,3}}{_NOT_CONTINUED}"
    ),
    # 氏名: / 担当者: followed by anything - a labelled personal field that
    # survived into free text.
    re.compile(r"(?:氏名|担当者|代表者)\s*[:：]\s*\S"),
    # Mr. John Smith / Dr Jane Doe
    re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"),
]

TITLE_MASK = "（個人名を含むため非公開）"


def contains_person_name(text: Optional[str]) -> bool:
    """R4 detector. Over-inclusive by design."""
    if not text:
        return False
    s = nfkc(text)
    return any(p.search(s) for p in _PERSON_NAME_PATTERNS)


def mask_free_text(text: Optional[str]) -> Tuple[Optional[str], bool]:
    """Returns (text_or_mask, was_masked)."""
    if contains_person_name(text):
        return TITLE_MASK, True
    return text, False


# ------------------------------------------------------------- R1/R2

@dataclass
class WinnerResult:
    winner_name: Optional[str]
    winner_type: str
    winner_masked: Optional[str]
    corporate_number: Optional[str]
    flags: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "winner_name": self.winner_name,
            "winner_type": self.winner_type,
            "winner_masked": self.winner_masked,
            "corporate_number": self.corporate_number,
            "anonymise_flags": "|".join(self.flags),
        }


def anonymize_winner(
    raw_name: Optional[str],
    corporate_number: Optional[str],
    *,
    gyoshu: str = UNKNOWN,
    prefecture: str = UNKNOWN,
) -> WinnerResult:
    """R1 + R2. The only supported way to turn a raw winner into a record."""
    number = re.sub(r"[\s-]", "", str(corporate_number or ""))
    name = nfkc(raw_name) or None

    if number and is_valid_corporate_number(number):
        # R1: a juridical person. The name is public by statute (法人番号公表).
        return WinnerResult(
            winner_name=name,
            winner_type=CORPORATE,
            winner_masked=None,
            corporate_number=number,
            flags=[],
        )

    flags: List[str] = []
    if number:
        # R2 + T6: a number was supplied but does not validate. Mask and flag.
        flags.append("invalid_corporate_number")
    else:
        flags.append("no_corporate_number")

    return WinnerResult(
        winner_name=None,
        winner_type=MASKED_INDIVIDUAL,
        winner_masked=f"個人事業主・{gyoshu or UNKNOWN}・{prefecture or UNKNOWN}",
        corporate_number=None,
        flags=flags,
    )


# --------------------------------------------------------------- R5

_DIRECTORY_KEYS = {"winner_name", "商号又は名称", "company_name", "name"}


def assert_not_a_directory(rows: Iterable[Mapping[str, Any]], *, context: str) -> None:
    """R5 guard. A public aggregate must not be keyed by a party's name."""
    for row in rows:
        overlap = _DIRECTORY_KEYS.intersection(row.keys())
        if overlap:
            raise ValueError(
                f"{context}: aggregate rows carry {sorted(overlap)}, which would make "
                "the output searchable by party name (rule R5). Aggregate over "
                "地域 / 分類 / 期間 instead."
            )
        return  # shape is uniform; one row is enough


# --------------------------------------------------------------- R6

def suppress_small_buckets(
    buckets: Mapping[Tuple[Any, ...], int],
    *,
    minimum: int = MIN_BUCKET_SIZE,
) -> Dict[Tuple[Any, ...], int]:
    """R6. Drop any bucket holding between 1 and `minimum - 1` masked
    individuals. A bucket with none is not a re-identification risk and stays.
    """
    return {k: v for k, v in buckets.items() if v == 0 or v >= minimum}


def bucket_is_publishable(masked_individual_count: int, *, minimum: int = MIN_BUCKET_SIZE) -> bool:
    return masked_individual_count == 0 or masked_individual_count >= minimum
