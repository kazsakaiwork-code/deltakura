#!/usr/bin/env python3
"""Bet B - turn a candidate list into a verified Japan-hiring registry.

Candidates come from web search and public lists (`candidates.csv`); nothing is
harvested from LinkedIn or any site behind a login. This script is the step that
makes the registry trustworthy: it asks each board's own public endpoint whether
the token resolves and whether the company currently has Japan-located
postings. A token that was wrong simply 404s or returns an empty board and is
dropped, so a mistaken candidate costs one request and never reaches
`registry.csv`.

One request per board, >= 2 s apart, ETag-cached.

    python crawlers/ats_registry/verify.py
    python crawlers/ats_registry/verify.py --ats greenhouse --limit 20
    python crawlers/ats_registry/verify.py --recheck        # ignore prior results
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from _lib import http, paths, tos  # noqa: E402

import ats as ats_mod  # noqa: E402
import japan  # noqa: E402

CANDIDATES = HERE / "candidates.csv"
REGISTRY = HERE / "registry.csv"

REGISTRY_COLUMNS = [
    "ats",
    "board_token",
    "company_name",
    "company_domain",
    "hq_country",
    "japan_hiring_evidence",
    "source_url",
    "checked_on",
]

# Kept beside the registry so a later run can tell "not checked" from
# "checked and rejected" without re-requesting the board.
RESULTS = "verification_results.csv"
RESULT_COLUMNS = [
    "ats", "board_token", "status", "postings_total", "postings_japan",
    "sample_japan_location", "board_url", "checked_on",
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="verify ATS board candidates")
    ap.add_argument("--ats", choices=ats_mod.SUPPORTED, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--recheck", action="store_true",
                    help="re-request boards that already have a result")
    ap.add_argument("--daily-cap", type=int, default=400,
                    help="per-host budget. The standing prototype cap is 20 and "
                         "applies until the bot contact URL resolves; building "
                         "a 100-company registry needs more, so this is set "
                         "explicitly and recorded in the run output.")
    args = ap.parse_args(argv)

    candidates = read_csv(CANDIDATES)
    if not candidates:
        print(f"[verify] no candidates in {CANDIDATES.name}")
        return 1

    # Fail-closed clearance. Greenhouse and Lever are reuse_allowed=Y in the
    # clearance matrix since 2026-09-22, so discovery runs in production mode
    # — but BET_B_LIVE=false holds the result back from publication until
    # go-live is approved, so every clearance carries the hold and nothing here
    # can write to a publishable store.
    hold = ats_mod.hold_reason()
    clearances = {
        name: tos.clear(row, hold=hold)
        for name, row in ats_mod.TOS_ROWS.items()
    }
    for name, clearance in clearances.items():
        print(f"[verify] {name:<10}: reuse_allowed={clearance.reuse_allowed} "
              f"-> {clearance.tos_status}")
    if hold:
        print(f"[verify] HELD     : {hold}")

    results = {
        (r["ats"], r["board_token"]): r
        for r in read_csv(HERE / RESULTS)
    }

    session = http.PoliteSession(
        state_path=paths.state_dir("ats") / "http_state.json",
        daily_cap=args.daily_cap,
    )
    for name in ats_mod.SUPPORTED:
        print(f"[verify] robots {name:<10}: "
              f"{session.robots_verdict(ats_mod.list_url(name, 'robots-probe'))}")

    todo = [
        c for c in candidates
        if (not args.ats or c["ats"] == args.ats)
        and (args.recheck or (c["ats"], c["board_token"]) not in results)
    ]
    if args.limit:
        todo = todo[: args.limit]

    print(f"[verify] candidates: {len(candidates)} total, {len(todo)} to check")

    checked_on = http.utc_now_iso()[:10]
    for i, cand in enumerate(todo, start=1):
        name, token = cand["ats"], cand["board_token"].strip()
        url = ats_mod.list_url(name, token)
        result = {
            "ats": name,
            "board_token": token,
            "board_url": ats_mod.BOARD_PAGE[name].format(token=token),
            "checked_on": checked_on,
            "postings_total": "0",
            "postings_japan": "0",
            "sample_japan_location": "",
        }
        try:
            resp = session.get(url, accept="application/json")
            payload = json.loads(resp.text())
            postings = ats_mod.parse(name, payload)
        except http.NotModified:
            result["status"] = "unchanged"
            results[(name, token)] = result
            continue
        except (http.DailyCapReached, http.HostPaused) as exc:
            print(f"[verify] stopping: {exc}")
            break
        except (RuntimeError, ValueError) as exc:
            msg = str(exc)
            result["status"] = "not_found" if "404" in msg else f"error: {msg[:80]}"
            results[(name, token)] = result
            print(f"[verify] {i:>3}/{len(todo)} {name}:{token:<28} {result['status']}")
            continue

        jp = [p for p in postings if japan.is_japan_location(p["location"])]
        result["postings_total"] = str(len(postings))
        result["postings_japan"] = str(len(jp))
        result["sample_japan_location"] = jp[0]["location"] if jp else ""
        result["status"] = "japan_hiring" if jp else (
            "no_japan_postings" if postings else "empty_board"
        )
        results[(name, token)] = result
        print(f"[verify] {i:>3}/{len(todo)} {name}:{token:<28} "
              f"{result['status']:<18} {len(jp):>3} JP / {len(postings):>4} total")

    session.save_state()
    write_csv(HERE / RESULTS, RESULT_COLUMNS, list(results.values()))

    # Build the registry: verified Japan-hiring boards only.
    registry: List[Dict[str, str]] = []
    by_key = {(c["ats"], c["board_token"].strip()): c for c in candidates}
    for key, result in sorted(results.items()):
        if result.get("status") != "japan_hiring":
            continue
        cand = by_key.get(key, {})
        sample = result.get("sample_japan_location", "")
        registry.append(
            {
                "ats": key[0],
                "board_token": key[1],
                "company_name": cand.get("company_name", ""),
                "company_domain": cand.get("company_domain", ""),
                "hq_country": cand.get("hq_country", ""),
                "japan_hiring_evidence": (
                    f"{result['postings_japan']} Japan-located posting(s) on the "
                    f"public {key[0]} board, e.g. \"{sample}\""
                ),
                "source_url": result.get("board_url", cand.get("source_url", "")),
                "checked_on": result.get("checked_on", checked_on),
            }
        )

    write_csv(REGISTRY, REGISTRY_COLUMNS, registry)

    counts: Dict[str, int] = {}
    for r in results.values():
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    print(f"[verify] outcomes  : {counts}")
    print(f"[verify] registry  : {len(registry)} verified Japan-hiring company(ies) "
          f"-> crawlers/ats_registry/registry.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
