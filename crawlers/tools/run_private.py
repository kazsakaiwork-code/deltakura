#!/usr/bin/env python3
"""Private collection runner: the nightly and weekly jobs that keep the private
collection store's record-level history growing on a maintainer's machine.

    python crawlers/tools/run_private.py nightly    # Bet B first_seen + Bet C top-up
    python crawlers/tools/run_private.py weekly     # Bet A refresh + local rebuild
    python crawlers/tools/run_private.py gate       # Bet B clearance gate only
    python crawlers/tools/run_private.py baseline   # record the gate fingerprints

Everything here writes to `$DELTAKURA_DATA_DIR` (the private store), except the
weekly rebuild of the Bet-A statistics, which regenerates the committed
aggregates under `data/published/` in this checkout and leaves them for a
maintainer to review. Nothing here commits, pushes, publishes, deploys, logs in
or sends anything.

**nightly**

1. *Bet B clearance gate.* Before any board is fetched, the robots.txt of every
   ATS API host we read and the two operators' legal index pages are fetched
   fresh and fingerprinted, and the fingerprints are compared with the recorded
   baseline (`ats/state/clearance_baseline.json`). A difference, an
   unreachable page or a missing baseline halts that source for the night and
   the run exits non-zero. A halted source stays halted every night until a
   maintainer has reviewed the change and re-recorded the baseline.
2. *Bet B collection.* One request per registry board per night: a conditional
   GET when we already hold the board, an unconditional one when we do not
   (so a stale ETag cannot turn the only fetch into an empty 304). Crawl rules
   from `_lib/http.py` apply unchanged (robots, >= 2 s per host, identifying
   User-Agent, backoff), plus three nightly limits that persist across
   re-runs of the same night: a board already fetched tonight is not fetched
   again; a host that answered three consecutive failures stays halted for the
   rest of the night; and no host receives more than 400 requests in a night.
   Boards on the opt-out blocklist (`ats/blocklist.csv`) are never fetched and
   their postings are purged from the store. Postings are built from the
   field allow-list in `ats_registry/ats.py` only.
3. *Bet C top-up.* The NTA diff collector in `--backfill` mode, which fetches
   every file in the publisher's 40-day window that this store does not
   physically hold. The scheduled cloud run keeps counts only; this is what
   keeps the full records.

**weekly**

Bet A. One GET of the 調達ポータル listing page. The publisher serves no ETag
or Last-Modified on its bulk files, so change detection uses what the listing
does publish: the 全件 section's own 更新 date and each file's size label. Files
are downloaded only when that date moves (the publisher re-issued the set), when
a size label changes, or when a file is new. A downloaded file whose SHA-256 is
unchanged is left alone. When anything changed, the normalized store is rebuilt
from the raw files, the join table is rebuilt from the registry dump already on
disk (no download), and `pportal/stats.py` plus the two `build-data` scripts
regenerate the published aggregates locally.

Every run appends exactly one row to each run log it covers - `ats/run_log.csv`,
`nta/run_log.csv`, `pportal/run_log.csv` - including when it fails, and writes a
full transcript under `private_runs/`.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import html as html_lib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable, Dict, Iterable, List, Optional, Tuple

CRAWLERS = Path(__file__).resolve().parents[1]


def _early_data_dir(argv: List[str]) -> Optional[str]:
    """`--data-dir DIR` sets DELTAKURA_DATA_DIR for this process. It has to be
    read before `_lib.paths` is imported, because that module resolves the store
    once, at import. A scheduler can then pass the store on the command line."""
    for i, arg in enumerate(argv):
        if arg == "--data-dir" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--data-dir="):
            return arg.split("=", 1)[1]
    return None


_DATA_DIR_ARG = _early_data_dir(sys.argv[1:]) if __name__ == "__main__" else None
if _DATA_DIR_ARG:
    os.environ["DELTAKURA_DATA_DIR"] = _DATA_DIR_ARG

sys.path.insert(0, str(CRAWLERS))
sys.path.insert(0, str(CRAWLERS / "ats_registry"))   # ats, japan
sys.path.insert(0, str(CRAWLERS / "pportal"))        # codes

from _lib import anonymize, http, jputil, paths, tos  # noqa: E402

JST = timezone(timedelta(hours=9))

#: At most this many requests to one host in one night, whatever a caller asks.
MAX_REQUESTS_PER_HOST_PER_NIGHT = 400
#: A night runs from 12:00 JST to 12:00 JST, so a 04:10 run and a catch-up run
#: the same morning share one night, and one board is fetched once in it.
NIGHT_BOUNDARY_HOUR_JST = 12
TRANSCRIPTS_KEPT = 120


# ------------------------------------------------------------------ modules
#
# Three collectors are called `collect.py`, so each is loaded under its own
# alias from an explicit path. Two NTA scripts import their sibling as a bare
# `collect`; for those the NTA collector is put in place under that name while
# they load, and the previous binding is restored afterwards.

def _load(alias: str, path: Path, *, collect_as: Optional[ModuleType] = None) -> ModuleType:
    if alias in sys.modules:
        return sys.modules[alias]
    saved = sys.modules.get("collect")
    if collect_as is not None:
        sys.modules["collect"] = collect_as
    try:
        spec = importlib.util.spec_from_file_location(alias, path)
        module = importlib.util.module_from_spec(spec)      # type: ignore[arg-type]
        sys.modules[alias] = module
        try:
            spec.loader.exec_module(module)                  # type: ignore[union-attr]
        except BaseException:
            sys.modules.pop(alias, None)
            raise
    finally:
        if collect_as is not None:
            if saved is None:
                sys.modules.pop("collect", None)
            else:
                sys.modules["collect"] = saved
    return module


def ats_collect() -> ModuleType:
    return _load("deltakura_ats_collect", CRAWLERS / "ats_registry" / "collect.py")


def ats_module() -> ModuleType:
    return _load("ats", CRAWLERS / "ats_registry" / "ats.py")


def nta_collect() -> ModuleType:
    return _load("deltakura_nta_collect", CRAWLERS / "nta_diff" / "collect.py")


def nta_run_nightly() -> ModuleType:
    return _load("deltakura_nta_run_nightly", CRAWLERS / "nta_diff" / "run_nightly.py",
                 collect_as=nta_collect())


def nta_zenken() -> ModuleType:
    return _load("deltakura_nta_zenken", CRAWLERS / "nta_diff" / "zenken.py",
                 collect_as=nta_collect())


def pportal_collect() -> ModuleType:
    return _load("deltakura_pportal_collect", CRAWLERS / "pportal" / "collect.py")


def pportal_stats() -> ModuleType:
    return _load("deltakura_pportal_stats", CRAWLERS / "pportal" / "stats.py")


# -------------------------------------------------------------- utilities

def jst_now() -> datetime:
    return datetime.now(JST)


def jst_stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def night_key(now: Optional[datetime] = None) -> str:
    """The collection night `now` belongs to, as the JST date it started on."""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    return (now - timedelta(hours=NIGHT_BOUNDARY_HOUR_JST)).date().isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def append_csv_row(path: Path, columns: List[str], row: Dict[str, object]) -> None:
    """Append one row, writing the header first if the file is new.

    An existing file keeps its own header: a column this code does not know is
    left blank rather than silently re-shaping a log someone else also writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = columns
    if path.exists() and path.stat().st_size:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            first = next(csv.reader(fh), None)
        if first:
            header = first
    new = not path.exists() or not path.stat().st_size
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n",
                                extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in header})


class _Tee(io.TextIOBase):
    """Writes to every stream given; a missing console (pythonw) is skipped."""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, text):  # type: ignore[override]
        for stream in self._streams:
            try:
                stream.write(text)
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self):  # type: ignore[override]
        for stream in self._streams:
            try:
                stream.flush()
            except (OSError, ValueError):
                pass


@contextlib.contextmanager
def transcript(kind: str):
    """Mirror stdout/stderr into a transcript file in the private store."""
    log_dir = paths.data_dir("private_runs")
    path = log_dir / f"{kind}_{jst_now().strftime('%Y%m%dT%H%M%S')}.log"
    with path.open("w", encoding="utf-8") as fh:
        tee = _Tee(sys.__stdout__, fh)
        with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
            print(f"[run_private] {kind} started {jst_stamp(jst_now())}")
            print(f"[run_private] data dir : {paths.DATA.name}/ "
                  f"(from {'DELTAKURA_DATA_DIR' if os.environ.get('DELTAKURA_DATA_DIR') else 'default'})")
            yield path
    old = sorted(log_dir.glob("*.log"))
    for stale in old[:-TRANSCRIPTS_KEPT]:
        try:
            stale.unlink()
        except OSError:
            pass


# ======================================================== Bet B: the gate
#
# The clearance recorded for Greenhouse and Lever describes their robots.txt
# and legal pages as they read when it was granted, and nothing else. So every
# night, before any board is fetched, both are read again and compared.

#: The robots.txt directives the clearance was granted against, per API host we
#: read. `baseline` refuses to record a robots file that differs from these;
#: accepting a change is a reviewed edit to this table.
GATE_ROBOTS: Dict[str, Dict[str, object]] = {
    "boards-api.greenhouse.io": {
        "sources": ("greenhouse",),
        "expected": ("user-agent: *", "disallow: /embed/"),
    },
    "api.lever.co": {
        "sources": ("lever",),
        "expected": ("user-agent: *", "allow: /", "crawl-delay: 1"),
    },
    "api.eu.lever.co": {
        "sources": ("lever_eu",),
        "expected": ("user-agent: *", "allow: /", "crawl-delay: 1"),
    },
}

#: The operators' legal index pages. What is fingerprinted is the set of legal
#: documents each page links to (target and title), which is what would change
#: if a terms-of-use or an anti-scraping clause were added.
GATE_LEGAL: Dict[str, Tuple[str, ...]] = {
    "https://www.greenhouse.com/legal": ("greenhouse",),
    "https://www.lever.co/legal/": ("lever", "lever_eu"),
}

_LEGAL_WORDS = re.compile(
    r"legal|terms|privacy|policy|agreement|addendum|dpa|cookie|acceptable|"
    r"conditions|notice|dmca|scrap|robots|crawl|api", re.I)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_TAG = re.compile(r"<[^>]+>")


def robots_directives(body: str) -> List[str]:
    """Normalised directive lines: comments and blank lines dropped, the field
    name lower-cased, the value kept exactly (paths are case-sensitive)."""
    out = []
    for line in (body or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        out.append(f"{field.strip().lower()}: {value.strip()}")
    return out


def legal_links(page_html: str, page_url: str) -> List[str]:
    """Sorted, de-duplicated `target | title` pairs of the legal links on a page."""
    host = urllib.parse.urlsplit(page_url).netloc
    found = set()
    for m in _ANCHOR.finditer(page_html or ""):
        href_m = _HREF.search(m.group(1))
        if not href_m:
            continue
        href = html_lib.unescape(href_m.group(1)).strip()
        text = " ".join(html_lib.unescape(_TAG.sub(" ", m.group(2))).split())
        if not _LEGAL_WORDS.search(href + " " + text):
            continue
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, href))
        if parts.scheme in ("http", "https"):
            target = parts.path.rstrip("/") or "/"
            if parts.netloc != host:
                target = parts.netloc + target
        else:
            target = f"{parts.scheme}:{parts.path}"
        found.add(f"{target} | {text}")
    return sorted(found)


def fingerprint(lines: Iterable[str]) -> str:
    return sha256_text("\n".join(lines))


def gate_items() -> List[Tuple[str, str, str, Tuple[str, ...]]]:
    """(item id, kind, url, sources) for everything the gate reads."""
    items = [
        (f"robots:{host}", "robots", f"https://{host}/robots.txt", tuple(cfg["sources"]))  # type: ignore[arg-type]
        for host, cfg in GATE_ROBOTS.items()
    ]
    items += [(f"legal:{url}", "legal", url, sources) for url, sources in GATE_LEGAL.items()]
    return items


def read_gate_item(session: http.PoliteSession, kind: str, url: str) -> Tuple[List[str], str]:
    """Fetch one gate item fresh. Returns (normalised lines, raw sha256)."""
    if kind == "robots":
        body = session.refresh_robots(url)
        return robots_directives(body), sha256_text(body)
    resp = session.get(url, accept="text/html", conditional=False)
    text = resp.text()
    return legal_links(text, url), resp.sha256


def compare_to_baseline(item_id: str, lines: List[str],
                        baseline: Dict[str, Dict[str, object]]) -> Tuple[str, str]:
    """('same' | 'changed' | 'no-baseline', human-readable detail)."""
    recorded = (baseline.get("items") or {}).get(item_id)  # type: ignore[union-attr]
    if not recorded:
        return "no-baseline", "no recorded fingerprint for this item"
    if recorded.get("fingerprint") == fingerprint(lines):
        return "same", ""
    before = set(recorded.get("lines") or [])
    after = set(lines)
    added = sorted(after - before)
    removed = sorted(before - after)
    detail = f"+{len(added)}/-{len(removed)}"
    if added:
        detail += " added: " + "; ".join(added[:5])
    if removed:
        detail += " removed: " + "; ".join(removed[:5])
    return "changed", detail


GATE_LOG_COLUMNS = ["checked_at", "item", "url", "result", "fingerprint", "raw_sha256", "detail"]


def run_gate(session: http.PoliteSession, baseline_path: Path,
             log_path: Path) -> Dict[str, str]:
    """Check every gate item. Returns {source: halt reason} for halted sources."""
    baseline = read_json(baseline_path, {})
    halted: Dict[str, str] = {}
    for item_id, kind, url, sources in gate_items():
        now = http.utc_now_iso()
        fp = raw = ""
        try:
            lines, raw = read_gate_item(session, kind, url)
            fp = fingerprint(lines)
            result, detail = compare_to_baseline(item_id, lines, baseline)
        except (http.DailyCapReached, http.HostPaused, RuntimeError, ValueError) as exc:
            result, detail = "error", f"{type(exc).__name__}: {exc}"
        append_csv_row(log_path, GATE_LOG_COLUMNS, {
            "checked_at": now, "item": item_id, "url": url, "result": result,
            "fingerprint": fp, "raw_sha256": raw, "detail": detail,
        })
        print(f"[gate] {item_id:<45} {result}{(' - ' + detail) if detail else ''}")
        if result != "same":
            for source in sources:
                halted.setdefault(source, f"{item_id} {result}")
    return halted


def cmd_baseline(args) -> int:
    """Record the gate fingerprints. Refuses a robots file that differs from
    the directives the clearance was granted against, and refuses to overwrite
    a different existing fingerprint unless --force is given."""
    baseline_path = paths.state_dir("ats") / "clearance_baseline.json"
    existing = read_json(baseline_path, {})
    session = http.PoliteSession(
        state_path=paths.state_dir("ats") / "http_state.json",
        daily_cap=MAX_REQUESTS_PER_HOST_PER_NIGHT,
    )
    items: Dict[str, Dict[str, object]] = {}
    problems = 0
    for item_id, kind, url, sources in gate_items():
        lines, raw = read_gate_item(session, kind, url)
        if kind == "robots":
            host = urllib.parse.urlsplit(url).netloc
            expected = list(GATE_ROBOTS[host]["expected"])  # type: ignore[arg-type]
            if lines != expected:
                print(f"[baseline] {item_id}: REFUSED - robots.txt reads {lines}, "
                      f"the clearance was granted against {expected}")
                problems += 1
                continue
        prior = (existing.get("items") or {}).get(item_id)
        if prior and prior.get("fingerprint") != fingerprint(lines) and not args.force:
            _, detail = compare_to_baseline(item_id, lines, existing)
            print(f"[baseline] {item_id}: differs from the recorded baseline ({detail}); "
                  "review it, then re-run with --force to accept")
            problems += 1
            continue
        items[item_id] = {
            "kind": kind, "url": url, "sources": list(sources),
            "fingerprint": fingerprint(lines), "raw_sha256": raw,
            "lines": lines, "recorded_at": http.utc_now_iso(),
        }
        print(f"[baseline] {item_id}: recorded ({len(lines)} line(s))")
    session.save_state()
    if problems:
        print(f"[baseline] {problems} item(s) not recorded; baseline left unchanged")
        return 1
    write_json(baseline_path, {"recorded_at": http.utc_now_iso(), "items": items})
    print(f"[baseline] written  : {paths.rel(baseline_path)}")
    return 0


def cmd_gate(args) -> int:
    session = http.PoliteSession(
        state_path=paths.state_dir("ats") / "http_state.json",
        daily_cap=MAX_REQUESTS_PER_HOST_PER_NIGHT,
    )
    halted = run_gate(session, paths.state_dir("ats") / "clearance_baseline.json",
                      paths.data_dir("ats") / "clearance_gate_log.csv")
    session.save_state()
    return 1 if halted else 0


# ================================================== Bet B: the collection

#: Where each stored `jobs.csv` column comes from. Fail-closed: a column that is
#: not classified here stops the run, so the stored shape cannot widen by
#: accident. `payload` columns come from the ATS allow-list only.
STORED_COLUMN_ORIGIN: Dict[str, str] = {
    "posting_id": "ours", "ats": "ours", "company_slug": "ours",
    "company_name": "registry", "company_domain": "registry", "hq_country": "registry",
    "job_title": "payload", "location_raw": "payload", "job_url": "payload",
    "ats_created_at": "payload", "ats_updated_at": "payload",
    "job_title_normalized": "derived", "function_bucket": "derived",
    "is_japan": "derived", "japan_prefecture": "derived", "remote_flag": "derived",
    # "employment_type" is deliberately absent (PC-5): it stays unclassified, so
    # re-adding the column fails the run until the Source Scout confirms it.
    "first_seen_at": "ours", "last_seen_at": "ours", "closed_at": "ours",
    "status": "ours", "consecutive_misses": "ours", "source_id": "ours",
    "tos_status": "ours", "attribution": "ours", "source_url": "ours",
    "retrieved_at": "ours", "checked_at": "ours",
}

BLOCKLIST_COLUMNS = ["ats", "board_token", "requested_on", "reason"]


def check_stored_columns(columns: Iterable[str]) -> None:
    columns = list(columns)
    unknown = [c for c in columns if c not in STORED_COLUMN_ORIGIN]
    if unknown:
        raise RuntimeError(f"jobs.csv columns not on the allow-list: {unknown}")
    personal = [c for c in columns if anonymize.is_personal_field(c)]
    if personal:
        raise RuntimeError(f"jobs.csv columns match the personal-field rule: {personal}")


def allow_listed(post: Dict[str, str], allowed: Iterable[str]) -> Dict[str, str]:
    """Keep exactly the allow-listed payload fields; drop anything else."""
    allowed = tuple(allowed)
    return {k: post.get(k, "") for k in allowed}


def load_blocklist(path: Path) -> set:
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, lineterminator="\n").writerow(BLOCKLIST_COLUMNS)
        return set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {
            (r.get("ats", "").strip(), r.get("board_token", "").strip())
            for r in csv.DictReader(fh) if r.get("board_token")
        }


def purge_blocked(jobs: Dict[str, Dict[str, str]], blocked: set) -> int:
    doomed = [pid for pid, r in jobs.items() if (r["ats"], r["company_slug"]) in blocked]
    for pid in doomed:
        del jobs[pid]
    return len(doomed)


class NightState:
    """What tonight has already done, persisted so a re-run cannot redo it."""

    def __init__(self, path: Path, night: str):
        self.path = path
        data = read_json(path, {})
        self.last_ok: Dict[str, str] = dict(data.get("last_ok") or {})
        if data.get("night") == night:
            self.fetched: Dict[str, str] = dict(data.get("fetched") or {})
            self.halted: Dict[str, str] = dict(data.get("halted") or {})
            self.requests: Dict[str, int] = {k: int(v) for k, v in (data.get("requests") or {}).items()}
        else:
            self.fetched, self.halted, self.requests = {}, {}, {}
        self.night = night

    def save(self) -> None:
        write_json(self.path, {
            "night": self.night, "fetched": self.fetched, "halted": self.halted,
            "requests": self.requests, "last_ok": self.last_ok,
        })


def plan_board(key: str, host: str, *, blocked: bool, gate_halt: Optional[str],
               night: NightState, cap: int) -> Optional[str]:
    """Why a board is skipped tonight, or None if it may be fetched."""
    if blocked:
        return "opt-out blocklist"
    if gate_halt:
        return f"clearance gate: {gate_halt}"
    if host in night.halted:
        return f"host halted tonight: {night.halted[host]}"
    if key in night.fetched:
        return "already fetched tonight"
    if night.requests.get(host, 0) >= cap:
        return f"{host} reached {cap} requests tonight"
    return None


ATS_RUN_LOG_COLUMNS = [
    "run_at", "boards_attempted", "boards_ok", "boards_failed",
    "postings_seen", "japan_postings", "new_postings", "closed_postings", "note",
]


def run_bet_b(cap: int) -> int:
    cap = min(int(cap), MAX_REQUESTS_PER_HOST_PER_NIGHT)
    run_at = http.utc_now_iso()
    counts = {"attempted": 0, "ok": 0, "failed": 0, "seen": 0, "jp": 0, "new": 0, "closed": 0}
    notes: List[str] = ["private-nightly"]
    rc = 0
    try:
        rc = _bet_b_body(cap, run_at, counts, notes)
    except Exception as exc:  # noqa: BLE001 - the row below must be written
        traceback.print_exc()
        notes.append(f"error: {type(exc).__name__}: {exc}")
        rc = 1
    finally:
        append_csv_row(paths.data_dir("ats") / "run_log.csv", ATS_RUN_LOG_COLUMNS, {
            "run_at": run_at,
            "boards_attempted": counts["attempted"],
            "boards_ok": counts["ok"],
            "boards_failed": counts["failed"],
            "postings_seen": counts["seen"],
            "japan_postings": counts["jp"],
            "new_postings": counts["new"],
            "closed_postings": counts["closed"],
            "note": "; ".join(notes),
        })
    return rc


def _bet_b_body(cap: int, now: str, counts: Dict[str, int], notes: List[str]) -> int:
    collect = ats_collect()
    ats = ats_module()
    check_stored_columns(collect.JOBS_COLUMNS)

    registry = collect.read_csv(collect.REGISTRY)
    if not registry:
        raise RuntimeError("registry.csv is empty")

    hold = ats.hold_reason()
    clearances = {name: tos.clear(row, hold=hold) for name, row in ats.TOS_ROWS.items()}
    for name, c in clearances.items():
        print(f"[ats] {name:<10}: {c.tos_status} (publishable={'yes' if c.publishable else 'no'})")

    state_dir = paths.state_dir("ats")
    session = http.PoliteSession(state_path=state_dir / "http_state.json", daily_cap=cap)
    if session.min_delay < 2.0:
        raise RuntimeError("per-host delay below 2 s")
    night = NightState(state_dir / "private_nightly.json", night_key())
    print(f"[ats] night    : {night.night} ({len(night.fetched)} board(s) already fetched)")

    def counted(host: str, fn: Callable[[], object]):
        before = session.requests_today(host)
        try:
            return fn()
        finally:
            night.requests[host] = night.requests.get(host, 0) + max(
                0, session.requests_today(host) - before)

    # 1. clearance gate --------------------------------------------------
    gate_log = paths.data_dir("ats") / "clearance_gate_log.csv"
    gate_halted = counted_gate(session, state_dir / "clearance_baseline.json", gate_log, night)
    if gate_halted:
        notes.append("GATE HALT " + ", ".join(f"{k} ({v})" for k, v in sorted(gate_halted.items())))
    else:
        notes.append("gate ok")

    # 2. store, blocklist --------------------------------------------------
    jobs_path = paths.data_dir("ats") / "jobs.csv"
    jobs = collect.load_jobs(jobs_path)
    blocked = load_blocklist(paths.data_dir("ats") / "blocklist.csv")
    purged = purge_blocked(jobs, blocked)
    if purged:
        notes.append(f"purged {purged} posting(s) of blocklisted boards")
    print(f"[ats] jobs.csv : {len(jobs)} posting(s) held; registry {len(registry)} board(s)")

    # 3. one request per board ---------------------------------------------
    today = now[:10]
    seen_ids: set = set()
    crawled: set = set()
    skipped: Dict[str, int] = {}
    for i, board in enumerate(registry, start=1):
        name, token = board["ats"].strip(), board["board_token"].strip()
        if name not in clearances:
            skipped["unsupported ats"] = skipped.get("unsupported ats", 0) + 1
            continue
        key = f"{name}:{token}"
        host = ats.HOSTS[name]
        why = plan_board(key, host, blocked=(name, token) in blocked,
                         gate_halt=gate_halted.get(name), night=night, cap=cap)
        if why:
            label = why.split(":")[0]
            skipped[label] = skipped.get(label, 0) + 1
            continue

        url = ats.list_url(name, token)
        held = collect.board_rows(jobs, name, token)
        known = bool(held) or key in night.last_ok
        counts["attempted"] += 1
        night.fetched[key] = http.utc_now_iso()
        try:
            resp = counted(host, lambda: session.get(
                url, accept="application/json", conditional=known))
            payload = json.loads(resp.text())
            postings = [allow_listed(p, ats.PAYLOAD_FIELDS) for p in ats.parse(name, payload)]
        except http.NotModified:
            counts["ok"] += 1
            crawled.add((name, token))
            present = collect.present_at_last_crawl(jobs, held)
            collect.confirm_unchanged(jobs, present, now, clearances[name].tos_status)
            seen_ids.update(present)
            night.last_ok[key] = now
            print(f"[ats] {i:>3}/{len(registry)} {key:<40} unchanged (304)")
            night.save()
            continue
        except http.DailyCapReached as exc:
            night.fetched.pop(key, None)       # no request was made
            counts["attempted"] -= 1
            night.halted[host] = "daily cap reached"
            print(f"[ats] {host}: {exc}")
            night.save()
            continue
        except http.HostPaused as exc:
            counts["failed"] += 1
            night.halted[host] = "3 consecutive failures"
            print(f"[ats] {i:>3}/{len(registry)} {key:<40} FAILED, host halted: {exc}")
            night.save()
            continue
        except http.RobotsDisallowed as exc:
            counts["failed"] += 1
            night.halted[host] = "robots.txt refused"
            print(f"[ats] {i:>3}/{len(registry)} {key:<40} FAILED, host halted: {exc}")
            night.save()
            continue
        except (RuntimeError, ValueError) as exc:
            counts["failed"] += 1
            print(f"[ats] {i:>3}/{len(registry)} {key:<40} FAILED {exc}")
            night.save()
            continue

        counts["ok"] += 1
        crawled.add((name, token))
        seen, new, jp = collect.observe_board(
            jobs, board, postings, now=now, clearance=clearances[name], source_url=url)
        seen_ids.update(seen)
        counts["new"] += new
        night.last_ok[key] = now
        night.save()
        print(f"[ats] {i:>3}/{len(registry)} {key:<40} "
              f"{len(postings):>4} postings, {jp:>3} JP, {new:>4} new")

    counts["closed"] = collect.close_missing(jobs, crawled, seen_ids, today, now)
    counts["seen"] = len(seen_ids)
    counts["jp"] = sum(1 for pid in seen_ids if jobs.get(pid, {}).get("is_japan") == "Y")

    check_stored_columns(collect.JOBS_COLUMNS)
    collect.write_jobs(jobs_path, jobs)
    session.save_state()
    night.save()

    if skipped:
        notes.append("skipped " + ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())))
    if night.halted:
        notes.append("hosts halted " + ", ".join(f"{h} ({r})" for h, r in sorted(night.halted.items())))
    notes.append("requests " + ", ".join(f"{h}={n}" for h, n in sorted(night.requests.items())))

    total_jp = sum(1 for r in jobs.values() if r.get("is_japan") == "Y")
    open_n = sum(1 for r in jobs.values() if r.get("status") == "open")
    print(f"[ats] done     : {counts['ok']} ok, {counts['failed']} failed, "
          f"{counts['seen']} seen ({counts['jp']} Japan), {counts['new']} new, "
          f"{counts['closed']} closed")
    print(f"[ats] jobs.csv : {len(jobs)} posting(s), {open_n} open, {total_jp} Japan-located")
    return 1 if (gate_halted or night.halted) else 0


def counted_gate(session: http.PoliteSession, baseline_path: Path, log_path: Path,
                 night: NightState) -> Dict[str, str]:
    hosts = {urllib.parse.urlsplit(url).netloc for _, _, url, _ in gate_items()}
    before = {h: session.requests_today(h) for h in hosts}
    try:
        return run_gate(session, baseline_path, log_path)
    finally:
        for h in hosts:
            night.requests[h] = night.requests.get(h, 0) + max(
                0, session.requests_today(h) - before[h])


# ===================================================== Bet C: the top-up

def parse_nta_summary(output: str) -> Tuple[str, str, str]:
    """(files_fetched, records, window_missing) from the collector's summary."""
    for line in output.splitlines():
        if "done     :" in line:
            parts = line.split("done     :", 1)[1].split(",")
            try:
                return (parts[0].strip().split()[0], parts[1].strip().split()[0],
                        parts[2].strip().split()[0])
            except IndexError:
                break
    return "", "", ""


def run_bet_c_topup() -> int:
    started = jst_now()
    status, note, rc = "ok", "private top-up (--backfill)", 0
    captured = io.StringIO()
    manifest_total = ""
    append = None
    try:
        nightly = nta_run_nightly()
        append = nightly._append_run_log
        collect = nta_collect()
        with contextlib.redirect_stdout(_Tee(sys.stdout, captured)):
            rc = collect.run(["--backfill"])
        if rc != 0:
            status, note = "error", note + f"; collector returned {rc}"
        manifest_total = str(len(collect.load_manifest(paths.DATA / "nta" / "manifest.csv")))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        status, note, rc = "error", note + f"; {type(exc).__name__}: {exc}", 1
    files_fetched, records, window_missing = parse_nta_summary(captured.getvalue())
    finished = jst_now()
    row = {
        "run_started_jst": jst_stamp(started),
        "run_finished_jst": jst_stamp(finished),
        "duration_sec": f"{(finished - started).total_seconds():.1f}",
        "status": status,
        "files_fetched": files_fetched,
        "records": records,
        "window_missing": window_missing,
        "manifest_total": manifest_total,
        "note": note,
    }
    if append is not None:
        append(row)
    else:
        append_csv_row(paths.DATA / "nta" / "run_log.csv", list(row), row)
    print(f"[nta_diff] run log  : {status}, {files_fetched or 0} file(s) fetched")
    return rc


def cmd_nightly(args) -> int:
    rc_b = 0 if args.skip_bet_b else run_bet_b(args.cap)
    rc_c = 0 if args.skip_bet_c else run_bet_c_topup()
    return 1 if (rc_b or rc_c) else 0


# ======================================================== Bet A: weekly

_STAMP_RE = re.compile(r"(令和|平成)\s*(元|\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日\s*更新")
_SIZE_RE = re.compile(
    r"doDownload\('(successful_bid_record_info_all_\d{4}\.zip)'\)[^>]*>\s*[^<]*?"
    r"\(\s*([\d.,]+\s*[KMG]?B)\s*\)", re.S)


def parse_all_stamp(page_html: str) -> str:
    """The 全件 section's own 更新 date as ISO, or "" if it cannot be read."""
    text = unicodedata.normalize("NFKC", page_html or "")
    idx = text.find("allDataFileTbl")
    region = text[max(0, idx - 4000): idx] if idx >= 0 else ""
    matches = list(_STAMP_RE.finditer(region))
    if not matches:
        return ""
    return jputil.wareki_to_iso(matches[-1].group(0)) or ""


def parse_size_labels(page_html: str) -> Dict[str, str]:
    return {name: label.replace(" ", "") for name, label in _SIZE_RE.findall(page_html or "")}


def plan_refresh(
    files: List[Dict[str, object]],
    manifest: Dict[str, Dict[str, str]],
    stamp: str,
    sizes: Dict[str, str],
    state: Dict[str, object],
    *,
    force: bool = False,
) -> List[Tuple[Dict[str, object], str]]:
    """Which 全件 files to download this week, each with its reason.

    The publisher serves no validators, so this is decided from the listing:
    a new file; a moved 更新 date (the set was re-issued: every file); a
    changed size label; or an unreadable date (fail-safe: every file). On the
    first run, with no recorded date, the date is compared with when the store
    last retrieved the files instead.
    """
    recorded_stamp = str(state.get("stamp") or "")
    recorded_sizes = state.get("sizes") or {}
    held_ok = {k for k, r in manifest.items() if r.get("status") == "ok"}
    if not recorded_stamp and held_ok:
        latest = max((manifest[k].get("retrieved_at") or "") for k in held_ok)
        latest_jst = ""
        if latest:
            try:
                latest_jst = datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc).astimezone(JST).date().isoformat()
            except ValueError:
                latest_jst = ""
        if stamp and latest_jst and stamp <= latest_jst:
            recorded_stamp = stamp          # we already hold this issue
    plan = []
    for f in files:
        key, name = str(f["key"]), str(f["file_name"])
        if force:
            reason = "forced"
        elif key not in held_ok:
            reason = "new file"
        elif not stamp:
            reason = "update date unreadable"
        elif stamp != recorded_stamp:
            reason = f"update date {recorded_stamp or 'unknown'} -> {stamp}"
        elif name in recorded_sizes and sizes.get(name) and sizes[name] != recorded_sizes[name]:  # type: ignore[index]
            reason = f"size {recorded_sizes[name]} -> {sizes[name]}"  # type: ignore[index]
        else:
            continue
        plan.append((f, reason))
    return plan


PPORTAL_RUN_LOG_COLUMNS = [
    "run_started_jst", "run_finished_jst", "duration_sec", "status",
    "upstream_updated", "files_listed", "files_fetched", "files_changed",
    "records_total", "stats_rebuilt", "note",
]


def cmd_weekly(args) -> int:
    started = jst_now()
    row: Dict[str, object] = {"status": "ok", "stats_rebuilt": "N"}
    notes: List[str] = ["private-weekly"]
    rc = 0
    try:
        rc = _weekly_body(args, row, notes)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        row["status"] = "error"
        notes.append(f"error: {type(exc).__name__}: {exc}")
        rc = 1
    finally:
        finished = jst_now()
        row.update({
            "run_started_jst": jst_stamp(started),
            "run_finished_jst": jst_stamp(finished),
            "duration_sec": f"{(finished - started).total_seconds():.1f}",
            "note": "; ".join(notes),
        })
        append_csv_row(paths.data_dir("pportal") / "run_log.csv", PPORTAL_RUN_LOG_COLUMNS, row)
        print(f"[pportal] run log  : {row['status']}, "
              f"{row.get('files_changed', 0)} file(s) changed")
    return rc


def _weekly_body(args, row: Dict[str, object], notes: List[str]) -> int:
    pc = pportal_collect()
    clearance = tos.clear(pc.TOS_ROW)
    tos.require_publishable(clearance)

    state_path = paths.state_dir("pportal") / "weekly_state.json"
    state = read_json(state_path, {})
    manifest_path = paths.DATA / "pportal" / "manifest.csv"
    manifest = pc.load_manifest(manifest_path)
    raw_root = paths.data_dir("pportal", "raw")

    session = http.PoliteSession(state_path=paths.state_dir("pportal") / "http_state.json",
                                 daily_cap=80)
    print(f"[pportal] robots   : {session.robots_verdict(pc.LIST_URL)}")
    page = session.get(pc.LIST_URL, accept="text/html", conditional=False)
    page_html = page.text()
    listing = pc.parse_listing(page_html)
    stamp = parse_all_stamp(page_html)
    sizes = parse_size_labels(page_html)
    files = list(listing["all"])
    row["upstream_updated"] = stamp
    row["files_listed"] = len(files)
    print(f"[pportal] listing  : {len(files)} 全件 file(s), updated {stamp or '(unreadable)'}")

    plan = plan_refresh(files, manifest, stamp, sizes, state, force=args.force)
    fetched = changed = 0
    changed_keys: List[str] = []
    for f, reason in plan:
        key, name = str(f["key"]), str(f["file_name"])
        url = str(listing["base"]) + name
        try:
            resp = session.get(url, accept="application/octet-stream")
        except http.NotModified:
            print(f"[pportal] {key}: unchanged (304 / fetched within 24 h)")
            continue
        except (http.DailyCapReached, http.HostPaused) as exc:
            notes.append(f"stopped: {exc}")
            row["status"] = "error"
            break
        except RuntimeError as exc:
            notes.append(f"{key} failed: {exc}")
            row["status"] = "error"
            continue
        fetched += 1
        if not resp.body.startswith(b"PK"):
            notes.append(f"{key}: not a ZIP")
            row["status"] = "error"
            continue
        prior = manifest.get(key) or {}
        if prior.get("raw_sha256") == resp.sha256 and prior.get("status") == "ok":
            print(f"[pportal] {key}: downloaded ({reason}), content unchanged")
            continue
        raw_path = raw_root / "all" / name
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(resp.body)
        manifest[key] = {
            **prior, "file_key": key, "kind": "all", "file_name": name,
            "raw_path": paths.rel(raw_path), "raw_bytes": str(len(resp.body)),
            "raw_sha256": resp.sha256, "etag": resp.headers.get("etag", "") or "",
            "retrieved_at": resp.retrieved_at, "status": "ok",
        }
        changed += 1
        changed_keys.append(key)
        print(f"[pportal] {key}: CHANGED ({reason}), {len(resp.body):,} bytes")
    session.save_state()
    row["files_fetched"] = fetched
    row["files_changed"] = changed
    if changed_keys:
        notes.append("changed " + " ".join(changed_keys))
    if not plan:
        notes.append("upstream unchanged; nothing downloaded")

    if changed or args.force_rebuild:
        pc.write_manifest(manifest_path, manifest)
        rebuild_normalized(pc, raw_root, manifest, manifest_path, clearance)
        rebuild_published(notes, row, use_node=not args.no_node)

    manifest = pc.load_manifest(manifest_path)
    row["records_total"] = sum(int(r.get("records") or 0) for r in manifest.values()
                               if r.get("status") == "ok")
    if row["status"] == "ok":
        write_json(state_path, {"stamp": stamp, "sizes": sizes,
                                "checked_at": http.utc_now_iso()})
    return 0 if row["status"] == "ok" else 1


def rebuild_normalized(pc: ModuleType, raw_root: Path, manifest, manifest_path: Path,
                       clearance) -> None:
    """Rebuild the normalized layer from the raw files alone, atomically.

    A re-issued fiscal-year file can correct or withdraw awards, so it replaces
    what it supersedes instead of being merged on top of it.
    """
    norm = paths.DATA / "pportal" / "normalized"
    prev = paths.DATA / "pportal" / "normalized.prev"
    if prev.exists():
        shutil.rmtree(prev)
    if norm.exists():
        norm.rename(prev)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as quiet:
            rc = pc._renormalize(raw_root, manifest, manifest_path, clearance)
        summary = [ln for ln in quiet.getvalue().splitlines() if "done" in ln]
        print("\n".join(summary))
        if rc != 0:
            raise RuntimeError(f"re-normalize returned {rc}")
    except BaseException:
        if norm.exists():
            shutil.rmtree(norm)
        if prev.exists():
            prev.rename(norm)
        raise
    if prev.exists():
        shutil.rmtree(prev)


def rebuild_published(notes: List[str], row: Dict[str, object], *, use_node: bool) -> None:
    """Join table from the dump on disk, then the statistics, then the two
    derived JSON copies. Local only: the checkout is left for review."""
    zen_raw = paths.DATA / "nta" / "raw" / "zenken" / "houjin_zenken_unicode.zip"
    if zen_raw.exists():
        with contextlib.redirect_stdout(io.StringIO()):
            rc = nta_zenken().run(["--reuse-raw", "--needed-from",
                                   str(paths.DATA / "pportal" / "normalized")])
        notes.append("join table rebuilt" if rc == 0 else f"join table rc={rc}")
    else:
        notes.append("join table kept (no registry dump on disk; nothing downloaded)")

    published = paths.PUBLISHED / "pportal" / "stats_v0.csv"
    before = hashlib.sha256(published.read_bytes()).hexdigest() if published.exists() else ""
    rc = pportal_stats().run([])
    if rc != 0:
        raise RuntimeError(f"stats.py returned {rc}")
    after = hashlib.sha256(published.read_bytes()).hexdigest()
    row["stats_rebuilt"] = "Y" if after != before else "Y (identical)"
    if after == before:
        return
    notes.append("published stats_v0.csv changed (not committed)")
    node = shutil.which("node") if use_node else None
    if not node:
        notes.append("node not run: derived JSON not rebuilt")
        return
    for sub in ("mcp", "api"):
        script = paths.ROOT / sub / "scripts" / "build-data.mjs"
        proc = subprocess.run([node, str(script)], cwd=str(paths.ROOT / sub),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
        print(proc.stdout.strip())
        if proc.returncode != 0:
            print(proc.stderr.strip())
            raise RuntimeError(f"{sub} build-data failed ({proc.returncode})")
    notes.append("mcp+api build-data rebuilt (not committed)")


# --------------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="private nightly / weekly collection runner")
    ap.add_argument("--data-dir", default=None,
                    help="the private store; sets DELTAKURA_DATA_DIR for this run "
                         "(must precede the subcommand)")
    sub = ap.add_subparsers(dest="command", required=True)
    n = sub.add_parser("nightly", help="Bet B first_seen collection + Bet C top-up")
    n.add_argument("--cap", type=int, default=MAX_REQUESTS_PER_HOST_PER_NIGHT,
                   help=f"requests per host per night (at most {MAX_REQUESTS_PER_HOST_PER_NIGHT})")
    n.add_argument("--skip-bet-b", action="store_true")
    n.add_argument("--skip-bet-c", action="store_true")
    w = sub.add_parser("weekly", help="Bet A refresh + local rebuild of the aggregates")
    w.add_argument("--force", action="store_true", help="download every 全件 file")
    w.add_argument("--force-rebuild", action="store_true",
                   help="rebuild the normalized layer and aggregates even if nothing changed")
    w.add_argument("--no-node", action="store_true", help="do not run the build-data scripts")
    sub.add_parser("gate", help="run the Bet B clearance gate only")
    b = sub.add_parser("baseline", help="record the Bet B clearance-gate fingerprints")
    b.add_argument("--force", action="store_true",
                   help="accept a legal-page change after it has been reviewed")
    args = ap.parse_args(argv)

    handlers = {"nightly": cmd_nightly, "weekly": cmd_weekly,
                "gate": cmd_gate, "baseline": cmd_baseline}
    with transcript(args.command) as log_path:
        started = time.monotonic()
        try:
            rc = handlers[args.command](args)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            rc = 1
        print(f"[run_private] {args.command} finished rc={rc} in "
              f"{time.monotonic() - started:.0f} s; transcript {paths.rel(log_path)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
