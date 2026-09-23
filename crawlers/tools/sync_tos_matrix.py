#!/usr/bin/env python3
"""Project the maintainers' internal clearance matrix into the shipped copy.

`_lib/tos.py` is a fail-closed gate: a source may only be fetched when a row in
the clearance matrix records `reuse_allowed=Y`. For that gate to work in a
standalone checkout of this repository, the matrix has to travel with the code —
so `crawlers/tos_matrix.csv` is committed and is what the gate reads.

The authority is still the maintainers' internal survey, which stays private and
complete; it is found next to the checkout or through `$DELTAKURA_TOS_SOURCE`
(see `_lib/paths.py`). The shipped copy is the **minimum that makes the gate
verifiable**, and nothing more:

    shipped   bet, source, url, owner, terms_url, license, robots_ok,
              reuse_allowed, checked_on
    dropped   api_available, data_format, rate_limit_notes, notes

The four dropped columns are working notes. `data_format` in particular carried
URL patterns, character encodings, pagination parameters and column lists for
municipalities recorded as `reuse_allowed=N` - a usable collection recipe for
exactly the sources whose terms forbid reuse. The gate reads none of the four.

`url` is **narrowed for every row that is not cleared**. A row recorded as
`reuse_allowed=N` or `unclear` publishes only the scheme and host of the site it
refers to (`https://example.lg.jp/`), never the path, query or filename of a
results page. Identifying which site a clearance verdict is about needs the host;
pointing at the page the awards are actually listed on does not, and a deep link
to a results page is the last recipe-shaped field in the file. Cleared (`Y`) rows
keep their full URLs, because those are the endpoints this project itself
collects from and a reader checking our attribution needs them. In both cases
only the URLs survive: surrounding prose in the cell is dropped.

`license` and `robots_ok` are **reduced to a controlled vocabulary** rather than
copied. The internal values are survey prose: they characterise named public
bodies' terms, mark some of our own readings as unverified, and record our
reasoning about how to treat an access control. None of that is a clearance
fact, and published under the project's name it would be a legal opinion about
a third party. The shipped file carries the verdict only:

    license     政府標準利用規約(第2.0版) | 公共データ利用規約(第1.0版) | CC BY 4.0 |
                custom-terms | no-licence-granted | unclear | unverified
    robots_ok   Y | N | partial | conflicting | no-robots-file | not-retrievable |
                mixed

`reuse_allowed` is copied verbatim and never normalised: it is the value the
gate compares, so rewriting it here could silently change what may be crawled.
`terms_url` keeps only the URLs it contains, so a parenthetical note does not
ride along with it.

Every mapping is FAIL-CLOSED. An internal value this script cannot classify
stops the projection with an error naming the row, rather than falling back to
copying the prose; and a row whose `reuse_allowed` is not an affirmative `Y` is
narrowed, so a new verdict string never widens what is published by accident.

Nothing else is changed: the shipped file is a column projection, row for row,
in the same order. It is a plain CSV with a header row and no comment line,
because `_lib/tos.py` reads it with `csv.DictReader`.

The shipped file is generated. Do not edit it by hand.

    python crawlers/tools/sync_tos_matrix.py            # write
    python crawlers/tools/sync_tos_matrix.py --check    # exit 1 if stale

`--check` is what `crawlers/tests/test_politeness.py` runs. In a standalone
clone there is no internal matrix, so both the script and the test report
"nothing to compare" and succeed.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import paths  # noqa: E402

#: Columns the shipped copy keeps, in this order. `_lib/tos.py` reads
#: source/url/owner/license/robots_ok/reuse_allowed/checked_on; `bet` and
#: `terms_url` are kept because a reader checking a clearance claim needs to know
#: which dataset the row is for and where the terms are. Nothing else ships.
SHIPPED_COLUMNS = [
    "bet",
    "source",
    "url",
    "owner",
    "terms_url",
    "license",
    "robots_ok",
    "reuse_allowed",
    "checked_on",
]

#: Internal licence prose -> the published verdict. Matched on a normalised
#: prefix, longest first, and unmatched values are an error.
LICENSE_VOCABULARY = [
    ("政府標準利用規約(第2.0版)", "政府標準利用規約(第2.0版)"),
    ("政府標準利用規約（第2.0版）", "政府標準利用規約(第2.0版)"),
    ("公共データ利用規約(第1.0版)", "公共データ利用規約(第1.0版)"),
    ("公共データ利用規約（第1.0版）", "公共データ利用規約(第1.0版)"),
    ("cc by 4.0", "CC BY 4.0"),
    ("custom terms", "custom-terms"),
    ("no licence granted", "no-licence-granted"),
    ("no license granted", "no-licence-granted"),
    ("company-owned posting content", "no-licence-granted"),
    ("unverified", "unverified"),
    ("unclear", "unclear"),
]

#: Internal robots prose -> the published verdict. `_lib/tos.py` only asks
#: whether this value starts with "Y", and every mapping below preserves that
#: answer, so the projection cannot change what the gate decides.
ROBOTS_VOCABULARY = [
    (re.compile(r"^y\b", re.I), "Y"),
    (re.compile(r"^n\b", re.I), "N"),
    (re.compile(r"^no robots\.txt", re.I), "no-robots-file"),
    (re.compile(r"^partial\b", re.I), "partial"),
    (re.compile(r"^conflicting\b", re.I), "conflicting"),
    (re.compile(r"^not retrievable\b", re.I), "not-retrievable"),
    # A per-host list of verdicts ("www.example.jp: Y (...), other host: ...").
    (re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}\s*:", re.I), "mixed"),
]

URL_RE = re.compile(r"https?://[^\s,;]+")


class NotClassified(RuntimeError):
    """An internal value the controlled vocabulary does not cover."""


def neutral_license(value: str, source: str) -> str:
    """Reduce the Scout's licence prose to one published verdict."""
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for prefix, verdict in LICENSE_VOCABULARY:
        if lowered.startswith(prefix.lower()):
            return verdict
    raise NotClassified(
        f"license value for {source!r} does not start with a known verdict: {text[:60]!r}. "
        "Add it to LICENSE_VOCABULARY, or reword the row in the internal matrix."
    )


def neutral_robots(value: str, source: str) -> str:
    """Reduce the Scout's robots.txt prose to one published verdict."""
    text = (value or "").strip()
    if not text:
        return ""
    for pattern, verdict in ROBOTS_VOCABULARY:
        if pattern.match(text):
            return verdict
    raise NotClassified(
        f"robots_ok value for {source!r} does not start with a known verdict: {text[:60]!r}. "
        "Add it to ROBOTS_VOCABULARY, or reword the row in the internal matrix."
    )


def urls_only(value: str) -> str:
    """Keep the URLs in a cell and drop the commentary around them."""
    found = URL_RE.findall(value or "")
    return " ; ".join(found) if found else (value or "").strip()


def is_cleared(reuse_allowed: str) -> bool:
    """True only for an affirmative clearance verdict. Anything else narrows.

    The gate itself accepts the exact string `Y`; this is deliberately a little
    wider (`Y - discovery only`) so that a qualified clearance still publishes
    the endpoint it is qualified about, and deliberately fail-closed on
    everything else, including a blank cell.
    """
    return (reuse_allowed or "").strip().lower().startswith("y")


def site_root(value: str) -> str:
    """Scheme + host for every distinct host in the cell. No path, no query.

    `https://www.example.lg.jp/keiyaku/kekka_buppin.html` becomes
    `https://www.example.lg.jp/`. Order is preserved and duplicates collapse, so
    a cell naming two systems still shows both sites.
    """
    roots: List[str] = []
    for url in URL_RE.findall(value or ""):
        m = re.match(r"(https?://[^/?#\s]+)", url)
        if not m:
            continue
        root = m.group(1).rstrip("/") + "/"
        if root not in roots:
            roots.append(root)
    return " ; ".join(roots)


def public_url(value: str, reuse_allowed: str, source: str) -> str:
    """The `url` cell as it ships: full for a cleared row, host root otherwise."""
    if is_cleared(reuse_allowed):
        return urls_only(value)
    narrowed = site_root(value)
    if not narrowed and (value or "").strip():
        raise NotClassified(
            f"url value for {source!r} contains no http(s) URL to narrow: "
            f"{(value or '').strip()[:60]!r}. A row that is not cleared may not "
            "publish an unparsed location."
        )
    return narrowed


def shipped_row(row: dict) -> dict:
    source = row.get("source", "")
    out = {c: (row.get(c) or "").strip() for c in SHIPPED_COLUMNS}
    out["license"] = neutral_license(row.get("license", ""), source)
    out["robots_ok"] = neutral_robots(row.get("robots_ok", ""), source)
    out["terms_url"] = urls_only(row.get("terms_url", ""))
    # reuse_allowed is deliberately untouched: it is what the gate compares.
    out["reuse_allowed"] = (row.get("reuse_allowed") or "").strip()
    out["url"] = public_url(row.get("url", ""), out["reuse_allowed"], source)
    return out

def project(rows: List[dict]) -> str:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=SHIPPED_COLUMNS, lineterminator="\n",
                            quoting=csv.QUOTE_ALL, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(shipped_row(row))
    return out.getvalue()


def read_internal(path: Path) -> List[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="compare instead of writing")
    args = ap.parse_args(argv)

    internal = paths.internal_tos_matrix()
    shipped = paths.CRAWLERS / "tos_matrix.csv"

    if internal is None:
        print("[tos-sync] no internal clearance matrix next to this checkout; nothing to compare")
        return 0

    try:
        wanted = project(read_internal(internal))
    except NotClassified as exc:
        print(f"[tos-sync] REFUSING to project: {exc}")
        return 1
    current = shipped.read_text(encoding="utf-8") if shipped.exists() else ""

    if current == wanted:
        print(f"[tos-sync] {paths.rel(shipped)} is current ({wanted.count(chr(10)) - 1} row(s))")
        return 0

    if args.check:
        print(f"[tos-sync] STALE: {paths.rel(shipped)} differs from {internal.name}. "
              "Run `python crawlers/tools/sync_tos_matrix.py`.")
        return 1

    shipped.write_text(wanted, encoding="utf-8")
    print(f"[tos-sync] wrote {paths.rel(shipped)} ({wanted.count(chr(10)) - 1} row(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
