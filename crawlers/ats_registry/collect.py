#!/usr/bin/env python3
"""Bet B - first_seen collector over the verified Japan-hiring registry.

The product is the *date our crawler first observed a posting*, which nobody
else keeps and which cannot be backfilled. Every surface must therefore say
"first seen by Deltakura on ...", never "posted on ...".

For each board in `registry.csv`:
  * one conditional GET of the public postings endpoint, >= 2 s apart, ETag
    cached, so an unchanged board costs a 304;
  * every posting seen gets `first_seen_at` on its first appearance and
    `last_seen_at` refreshed thereafter;
  * a posting absent from **two consecutive successful** crawls of its board is
    closed, with `closed_at` set to the first crawl that missed it. A failed
    crawl never closes anything.

Stored per posting: structured metadata and the public URL, nothing else. The
description body, recruiter names and contact addresses are never read into a
record.

    python crawlers/ats_registry/collect.py
    python crawlers/ats_registry/collect.py --japan-only
    python crawlers/ats_registry/collect.py --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from _lib import http, paths, tos  # noqa: E402

import ats as ats_mod  # noqa: E402
import japan  # noqa: E402

REGISTRY = HERE / "registry.csv"

JOBS_COLUMNS = [
    "posting_id",
    "ats",
    "company_slug",
    "company_name",
    "company_domain",
    "hq_country",
    "job_title",
    "job_title_normalized",
    "function_bucket",
    "location_raw",
    "is_japan",
    "japan_prefecture",
    "remote_flag",
    # `employment_type` was dropped on 2026-09-27 (task PC-5): it is derived from
    # Lever `categories.commitment`, which is not on the source allow-list by
    # name, so it is not stored until the Source Scout confirms that reading.
    # write_jobs() writes exactly these columns, so the next run removes it from
    # an existing jobs.csv as well.
    "job_url",
    "first_seen_at",
    "last_seen_at",
    "closed_at",
    "status",
    "consecutive_misses",
    "source_id",
    "tos_status",
    "attribution",
    # Added 2026-09-24. The posting's own dates, when the ATS supplies them, and
    # our provenance: which endpoint the record came from, when a payload
    # carrying it was last retrieved (200), and when its board was last checked
    # successfully (200 or 304). Older rows leave them blank until re-observed.
    "ats_created_at",
    "ats_updated_at",
    "source_url",
    "retrieved_at",
    "checked_at",
]

RUN_LOG_COLUMNS = [
    "run_at", "boards_attempted", "boards_ok", "boards_failed",
    "postings_seen", "japan_postings", "new_postings", "closed_postings", "note",
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_jobs(path: Path) -> Dict[str, Dict[str, str]]:
    return {r["posting_id"]: r for r in read_csv(path)}


def write_jobs(path: Path, jobs: Dict[str, Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=JOBS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(jobs):
            writer.writerow({c: jobs[key].get(c, "") for c in JOBS_COLUMNS})


def board_rows(jobs: Dict[str, Dict[str, str]], ats: str, token: str) -> List[str]:
    """Every posting id we hold for one board, open or closed."""
    return [
        pid for pid, row in jobs.items()
        if row["ats"] == ats and row["company_slug"] == token
    ]


def present_at_last_crawl(jobs: Dict[str, Dict[str, str]], held: List[str]) -> List[str]:
    """The postings the board's last successful payload contained.

    A 304 says "the payload is byte-for-byte what you saw last time", so it
    confirms exactly these - open and not already missed - and nothing else. A
    posting that had already dropped out stays dropped out, so a board that
    answers 304 for weeks still closes the postings it lost.
    """
    return [
        pid for pid in held
        if jobs[pid].get("status") != "closed"
        and (jobs[pid].get("consecutive_misses") or "0") == "0"
    ]


def confirm_unchanged(jobs: Dict[str, Dict[str, str]], present: List[str],
                      now: str, tos_status: str) -> None:
    """Record a 304: every posting in the last payload is still live."""
    for pid in present:
        row = jobs[pid]
        row["last_seen_at"] = now
        row["checked_at"] = now
        row["consecutive_misses"] = "0"
        row["tos_status"] = tos_status


def observe_board(
    jobs: Dict[str, Dict[str, str]],
    board: Dict[str, str],
    postings: List[Dict[str, str]],
    *,
    now: str,
    clearance: "tos.Clearance",
    source_url: str,
    japan_only: bool = False,
) -> Tuple[List[str], int, int]:
    """Apply one successful (200) payload. Returns (seen ids, new, Japan)."""
    name, token = board["ats"], board["board_token"].strip()
    seen: List[str] = []
    new = jp = 0
    for post in postings:
        pid = ats_mod.posting_id(name, token, post["job_id"])
        # The derived employment type (4th value) is not stored; see JOBS_COLUMNS.
        is_jp, pref, remote, _employment, func, norm = japan.classify(
            post["location"], post["title"], post["commitment"]
        )
        if is_jp:
            jp += 1
        if japan_only and not is_jp:
            continue
        seen.append(pid)
        existing = jobs.get(pid)
        if existing is None:
            new += 1
            jobs[pid] = {
                "posting_id": pid,
                "ats": name,
                "company_slug": token,
                "company_name": board.get("company_name", ""),
                "company_domain": board.get("company_domain", ""),
                "hq_country": board.get("hq_country", ""),
                "job_title": post["title"],
                "job_title_normalized": norm,
                "function_bucket": func,
                "location_raw": post["location"],
                "is_japan": "Y" if is_jp else "N",
                "japan_prefecture": pref,
                "remote_flag": "Y" if remote else "N",
                "job_url": post["url"],
                "first_seen_at": now,
                "last_seen_at": now,
                "closed_at": "",
                "status": "open",
                "consecutive_misses": "0",
                "source_id": f"ats_{name}",
                "tos_status": clearance.tos_status,
                "attribution": clearance.attribution,
                "ats_created_at": post.get("created_at", ""),
                "ats_updated_at": post.get("updated_at", ""),
                "source_url": source_url,
                "retrieved_at": now,
                "checked_at": now,
            }
        else:
            # first_seen_at is written once and never revised. A retitled
            # posting keeps its original first_seen date.
            existing["last_seen_at"] = now
            existing["consecutive_misses"] = "0"
            existing["status"] = "open"
            existing["closed_at"] = ""
            existing["job_title"] = post["title"]
            existing["job_title_normalized"] = norm
            existing["location_raw"] = post["location"]
            existing["is_japan"] = "Y" if is_jp else "N"
            existing["japan_prefecture"] = pref
            existing["tos_status"] = clearance.tos_status
            existing["ats_created_at"] = (
                post.get("created_at", "") or existing.get("ats_created_at", "")
            )
            existing["ats_updated_at"] = (
                post.get("updated_at", "") or existing.get("ats_updated_at", "")
            )
            existing["source_url"] = source_url
            existing["retrieved_at"] = now
            existing["checked_at"] = now
    return seen, new, jp


def close_missing(jobs: Dict[str, Dict[str, str]], crawled_boards: set,
                  seen_ids: set, today: str, now: str = "") -> int:
    """The closed rule, applied to boards this run crawled successfully only.

    A posting absent from two consecutive successful crawls is closed, with
    `closed_at` the first crawl that missed it. Returns the number closed now.
    """
    closed = 0
    for pid, row in jobs.items():
        if (row["ats"], row["company_slug"]) not in crawled_boards:
            continue
        if now:
            row["checked_at"] = now
        if pid in seen_ids or row.get("status") == "closed":
            continue
        misses = int(row.get("consecutive_misses") or 0) + 1
        row["consecutive_misses"] = str(misses)
        if misses == 1:
            row["closed_at"] = today       # provisional: first crawl that missed it
        if misses >= 2:
            row["status"] = "closed"
            closed += 1
    return closed


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bet-B first_seen collector")
    ap.add_argument("--japan-only", action="store_true",
                    help="store only postings whose location is in Japan")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--daily-cap", type=int, default=400,
                    help="per-host budget, set explicitly; see verify.py --help")
    args = ap.parse_args(argv)

    registry = read_csv(REGISTRY)
    if not registry:
        print(f"[ats] {REGISTRY.name} is empty; run verify.py first")
        return 1

    # Fail-closed clearance, plus the Bet-B operational hold. Greenhouse and
    # Lever are reuse_allowed=Y since 2026-09-22, so this is a production-mode
    # clearance; BET_B_LIVE=false then holds it back from publication until
    # go-live is approved (see ats.py).
    hold = ats_mod.hold_reason()
    clearances = {
        name: tos.clear(row, hold=hold)
        for name, row in ats_mod.TOS_ROWS.items()
    }

    jobs_path = paths.data_dir("ats") / "jobs.csv"
    jobs = load_jobs(jobs_path)
    print(f"[ats] registry : {len(registry)} board(s)")
    print(f"[ats] jobs.csv : {len(jobs)} posting(s) already tracked")
    for name, clearance in clearances.items():
        print(f"[ats] {name:<10}: {clearance.tos_status} "
              f"(reuse_allowed={clearance.reuse_allowed}, "
              f"publishable={'yes' if clearance.publishable else 'no'})")
    if hold:
        print(f"[ats] HELD      : {hold}")

    boards = registry[: args.limit] if args.limit else registry
    if args.dry_run:
        for b in boards:
            print(f"    would fetch {ats_mod.list_url(b['ats'], b['board_token'])}")
        return 0

    session = http.PoliteSession(
        state_path=paths.state_dir("ats") / "http_state.json",
        daily_cap=args.daily_cap,
    )

    now = http.utc_now_iso()
    today = now[:10]
    seen_ids: set = set()
    ok = failed = new_count = jp_count = 0
    crawled_boards: set = set()

    for i, board in enumerate(boards, start=1):
        name, token = board["ats"], board["board_token"].strip()
        url = ats_mod.list_url(name, token)
        held = board_rows(jobs, name, token)
        try:
            resp = session.get(url, accept="application/json")
            postings = ats_mod.parse(name, json.loads(resp.text()))
        except http.NotModified:
            if not held:
                # A 304 says "your cached copy is current", but the HTTP cache
                # and the record store are separate: verify.py may have primed
                # the ETag before jobs.csv existed. With nothing held for this
                # board there is nothing to confirm, so ask again without a
                # validator rather than record an empty board.
                try:
                    resp = session.get(url, accept="application/json", conditional=False)
                    postings = ats_mod.parse(name, json.loads(resp.text()))
                except (http.NotModified, http.DailyCapReached, http.HostPaused,
                        RuntimeError, ValueError) as exc:
                    failed += 1
                    print(f"[ats] {i:>3}/{len(boards)} {name}:{token:<28} "
                          f"FAILED on unconditional retry {exc}")
                    continue
            else:
                # An unchanged board is a *successful* crawl: every posting the
                # last payload carried is still live.
                ok += 1
                crawled_boards.add((name, token))
                present = present_at_last_crawl(jobs, held)
                confirm_unchanged(jobs, present, now, clearances[name].tos_status)
                seen_ids.update(present)
                print(f"[ats] {i:>3}/{len(boards)} {name}:{token:<28} unchanged (304)")
                continue
        except (http.DailyCapReached, http.HostPaused) as exc:
            print(f"[ats] stopping: {exc}")
            break
        except (RuntimeError, ValueError) as exc:
            failed += 1
            print(f"[ats] {i:>3}/{len(boards)} {name}:{token:<28} FAILED {exc}")
            continue

        ok += 1
        crawled_boards.add((name, token))
        seen, board_new, board_jp = observe_board(
            jobs, board, postings, now=now, clearance=clearances[name],
            source_url=url, japan_only=args.japan_only,
        )
        seen_ids.update(seen)
        new_count += board_new
        jp_count += board_jp
        print(f"[ats] {i:>3}/{len(boards)} {name}:{token:<28} "
              f"{len(postings):>4} postings, {board_jp:>3} JP, {board_new:>4} new")

    # Closed rule: only for boards this run actually crawled successfully.
    closed = close_missing(jobs, crawled_boards, seen_ids, today, now)

    write_jobs(jobs_path, jobs)
    session.save_state()

    log_path = paths.data_dir("ats") / "run_log.csv"
    is_new = not log_path.exists()
    with log_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUN_LOG_COLUMNS, lineterminator="\n")
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "run_at": now,
                "boards_attempted": len(boards),
                "boards_ok": ok,
                "boards_failed": failed,
                "postings_seen": len(seen_ids),
                "japan_postings": jp_count,
                "new_postings": new_count,
                "closed_postings": closed,
                "note": "japan-only" if args.japan_only else "",
            }
        )

    total_jp = sum(1 for r in jobs.values() if r.get("is_japan") == "Y")
    print(f"[ats] done     : {ok} board(s) ok, {failed} failed, "
          f"{new_count} new posting(s), {closed} closed")
    print(f"[ats] jobs.csv : {len(jobs)} posting(s), {total_jp} Japan-located "
          f"-> {paths.rel(jobs_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
