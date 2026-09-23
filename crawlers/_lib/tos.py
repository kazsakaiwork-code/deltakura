"""Fail-closed source clearance gate.

`crawlers/tos_matrix.csv` is the single authority on whether a source may be
fetched. `reuse_allowed` is the only value the crawler runner reads, and
anything but `Y` blocks the source. The file is a generated projection of the
maintainers' fuller internal survey; it is committed so this gate works in a
standalone checkout of the public repository (see
`crawlers/tools/sync_tos_matrix.py`).

Three verdicts exist in the matrix:

  Y        cleared. Normal collection, publishable output.
  N        blocked. There is no override and no flag; the call raises.
  unclear  blocked for production. A collector may run in *prototype* mode
           only when it passes `prototype=True`, and only when the row also
           records `robots_ok=Y`. Prototype mode is capped at 20
           requests/host/day, its output is marked
           `tos_status=unclear-prototype`, and nothing it produces may be
           published or committed to a public repository until the row is
           upgraded. It exists because a probe is sometimes what settles the
           verdict, and a probe must never become a publication.

A cleared source can still be held back. `clear(..., hold="reason")` returns a
clearance that is **not** publishable even though the matrix says `Y`. That is
how Bet B stays held back after Greenhouse and Lever were upgraded to `Y` on
2026-09-22: the legal question is answered, but going live is a separate
approval that has not been given, so `crawlers/ats_registry/` passes
`BET_B_LIVE=false` (the default) and `require_publishable()` keeps refusing. A hold is an operational gate on top of
the legal gate; it can never turn an `N` or an `unclear` row into a `Y`.

Callers pass the exact `source` string from the matrix, so a renamed or
removed row fails loudly instead of silently crawling an uncleared host.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import paths


class SourceBlocked(RuntimeError):
    """The source is not cleared for the requested mode."""


@dataclass(frozen=True)
class Clearance:
    source: str
    url: str
    owner: str
    license: str
    robots_ok: str
    reuse_allowed: str
    attribution: str
    checked_on: str
    prototype_only: bool
    hold: str = ""

    @property
    def tos_status(self) -> str:
        if self.prototype_only:
            return "unclear-prototype"
        if self.hold:
            return "cleared-held"
        return "cleared"

    @property
    def publishable(self) -> bool:
        return not self.prototype_only and not self.hold


def _rows() -> List[Dict[str, str]]:
    with paths.TOS_MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def find_row(source_substring: str) -> Dict[str, str]:
    matches = [r for r in _rows() if source_substring in r["source"]]
    if not matches:
        raise SourceBlocked(
            f"no row in {paths.TOS_MATRIX.name} matches {source_substring!r}; "
            "a source with no clearance row is blocked"
        )
    if len(matches) > 1:
        raise SourceBlocked(
            f"{source_substring!r} matches {len(matches)} rows in {paths.TOS_MATRIX.name}; "
            "use a more specific substring"
        )
    return matches[0]


ATTRIBUTION: Dict[str, str] = {
    # Rendered exactly as the licence requires. Keyed by matrix substring.
    "調達ポータル 落札実績オープンデータ": "出典：調達ポータル（https://www.p-portal.go.jp/）",
    "法人番号公表サイト 差分データ": (
        "出典：国税庁法人番号公表サイト（国税庁）"
        "（https://www.houjin-bangou.nta.go.jp/download/sabun/）"
    ),
    "法人番号公表サイト 全件データ": (
        "出典：国税庁法人番号公表サイト（国税庁）"
        "（https://www.houjin-bangou.nta.go.jp/download/zenken/）"
    ),
    "Greenhouse Job Board API": (
        "Job metadata retrieved from the public Greenhouse Job Board API; "
        "postings are the hiring companies' own content."
    ),
    "Lever Postings API": (
        "Job metadata retrieved from the public Lever Postings API; "
        "postings are the hiring companies' own content."
    ),
}


def clear(source_substring: str, *, prototype: bool = False, hold: str = "") -> Clearance:
    """Return a Clearance or raise SourceBlocked. Call before the first fetch.

    `hold` records an operational reason (an approval not yet given, a pending review)
    why a legally cleared source must still not produce publishable output. It
    only ever makes a clearance stricter.
    """
    row = find_row(source_substring)
    verdict = (row.get("reuse_allowed") or "").strip().lower()
    robots_ok = (row.get("robots_ok") or "").strip()

    if verdict == "y":
        prototype_only = False
    elif verdict == "unclear":
        if not prototype:
            raise SourceBlocked(
                f"{row['source']}: reuse_allowed=unclear. Blocked for production "
                "collection. A collector may pass prototype=True "
                "to run a capped, non-publishable probe."
            )
        if not robots_ok.upper().startswith("Y"):
            raise SourceBlocked(
                f"{row['source']}: reuse_allowed=unclear AND robots_ok={robots_ok!r}. "
                "Prototype mode requires an affirmative robots verdict."
            )
        prototype_only = True
    else:
        raise SourceBlocked(
            f"{row['source']}: reuse_allowed={row.get('reuse_allowed')!r}. Blocked. "
            "There is no override for N."
        )

    attribution = ""
    for key, value in ATTRIBUTION.items():
        if key in row["source"]:
            attribution = value
            break

    return Clearance(
        source=row["source"],
        url=row["url"],
        owner=row["owner"],
        license=row["license"],
        robots_ok=robots_ok,
        reuse_allowed=row.get("reuse_allowed", ""),
        attribution=attribution,
        checked_on=row.get("checked_on", ""),
        prototype_only=prototype_only,
        hold=hold,
    )


def require_publishable(clearance: Clearance) -> None:
    """Guard placed immediately before any write into a publishable store."""
    if clearance.prototype_only:
        raise SourceBlocked(
            f"{clearance.source}: prototype-only clearance cannot write to a "
            "publishable store. Resolve reuse_allowed first."
        )
    if clearance.hold:
        raise SourceBlocked(
            f"{clearance.source}: reuse_allowed={clearance.reuse_allowed} but the "
            f"source is held: {clearance.hold}"
        )
