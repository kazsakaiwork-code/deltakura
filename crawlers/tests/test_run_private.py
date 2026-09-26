"""The private nightly / weekly runner: its nightly limits, the Bet-B clearance
gate, the field allow-list and the Bet-A change detection.

No network access and nothing written outside a temporary directory.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Dict, List

HERE = Path(__file__).resolve().parent
CRAWLERS = HERE.parent
for _p in (CRAWLERS, CRAWLERS / "ats_registry", CRAWLERS / "pportal"):
    sys.path.insert(0, str(_p))

from _lib import http  # noqa: E402


def _load(alias: str, path: Path) -> ModuleType:
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    sys.modules[alias] = module
    spec.loader.exec_module(module)                          # type: ignore[union-attr]
    return module


rp = _load("deltakura_run_private", CRAWLERS / "tools" / "run_private.py")
ats_collect = rp.ats_collect()
ats = rp.ats_module()


# ------------------------------------------------------------- stub HTTP

class _Resp:
    def __init__(self, status=200, body=b"ok", headers=None, ctype="text/plain"):
        self.status_code = status
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.url = "stub"
        self.text = body.decode("utf-8", "replace")

        class _Raw:
            def read(self, n, decode_content=True):
                return body[:n]

        self.raw = _Raw()


class _Stub:
    def __init__(self, routes: Dict[str, object]):
        self.routes = routes
        self.calls: List[str] = []
        self.headers: Dict[str, str] = {}
        self.max_redirects = 5

    def get(self, url, **kw):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp() if callable(resp) else resp
        return _Resp(status=404, body=b"not found")


def _session(routes) -> http.PoliteSession:
    s = http.PoliteSession(state_path=None, daily_cap=400, min_delay=0.0)
    s._session = _Stub(routes)  # type: ignore[assignment]
    return s


ALLOW = _Resp(body=b"User-agent: *\nAllow: /\n")


# --------------------------------------------------------------- nights

def test_a_night_runs_noon_to_noon_jst():
    # 04:10 JST on the 25th and 11:59 JST the same morning share a night;
    # 12:00 JST starts the next one.
    at = lambda h, m, d=25: datetime(2026, 9, d, h, m, tzinfo=rp.JST)  # noqa: E731
    assert rp.night_key(at(4, 10)) == "2026-09-24"
    assert rp.night_key(at(11, 59)) == "2026-09-24"
    assert rp.night_key(at(12, 0)) == "2026-09-25"
    assert rp.night_key(at(4, 10, 26)) == "2026-09-25"


def test_a_board_is_fetched_once_per_night_and_a_halted_host_stays_halted():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "night.json"
        night = rp.NightState(path, "2026-09-24")
        assert rp.plan_board("greenhouse:a", "h", blocked=False, gate_halt=None,
                             night=night, cap=400) is None
        night.fetched["greenhouse:a"] = "t"
        night.halted["h2"] = "3 consecutive failures"
        night.last_ok["greenhouse:a"] = "t"
        night.save()

        again = rp.NightState(path, "2026-09-24")            # a re-run, same night
        assert "already fetched" in rp.plan_board(
            "greenhouse:a", "h", blocked=False, gate_halt=None, night=again, cap=400)
        assert "halted" in rp.plan_board(
            "lever:b", "h2", blocked=False, gate_halt=None, night=again, cap=400)

        tomorrow = rp.NightState(path, "2026-09-25")         # the next night
        assert tomorrow.fetched == {} and tomorrow.halted == {}
        assert tomorrow.last_ok == {"greenhouse:a": "t"}      # knowledge survives


def test_the_nightly_host_cap_is_400_and_cannot_be_raised():
    assert rp.MAX_REQUESTS_PER_HOST_PER_NIGHT == 400
    with tempfile.TemporaryDirectory() as tmp:
        night = rp.NightState(Path(tmp) / "n.json", "2026-09-24")
        night.requests["h"] = 400
        why = rp.plan_board("x:y", "h", blocked=False, gate_halt=None, night=night, cap=400)
        assert why and "400 requests" in why


def test_blocklist_and_gate_skip_a_board_before_anything_else():
    with tempfile.TemporaryDirectory() as tmp:
        night = rp.NightState(Path(tmp) / "n.json", "2026-09-24")
        assert rp.plan_board("x:y", "h", blocked=True, gate_halt=None,
                             night=night, cap=400) == "opt-out blocklist"
        assert rp.plan_board("x:y", "h", blocked=False, gate_halt="robots changed",
                             night=night, cap=400).startswith("clearance gate")


def test_a_blocklisted_board_is_purged_from_the_store():
    jobs = {
        "greenhouse:a:1": {"ats": "greenhouse", "company_slug": "a"},
        "greenhouse:b:1": {"ats": "greenhouse", "company_slug": "b"},
    }
    assert rp.purge_blocked(jobs, {("greenhouse", "a")}) == 1
    assert list(jobs) == ["greenhouse:b:1"]


# ------------------------------------------------------------ allow-list

def test_payload_allow_list_carries_no_prose_and_no_person_field():
    forbidden = ("description", "content", "body", "html", "email", "phone",
                 "recruiter", "manager", "contact", "salary", "compensation",
                 "lists", "additional", "candidate")
    for field in ats.PAYLOAD_FIELDS:
        assert not any(f in field.lower() for f in forbidden), field


def test_unknown_payload_fields_are_dropped():
    post = {"job_id": "1", "title": "t", "description": "PROSE", "email": "x@y"}
    kept = rp.allow_listed(post, ats.PAYLOAD_FIELDS)
    assert set(kept) == set(ats.PAYLOAD_FIELDS)
    assert "PROSE" not in json.dumps(kept) and "x@y" not in json.dumps(kept)


def test_parse_emits_exactly_the_allow_list_and_iso_dates():
    gh = ats.parse(ats.GREENHOUSE, {"jobs": [{
        "id": 7, "title": "SRE", "location": {"name": "Tokyo"},
        "absolute_url": "https://x/7", "updated_at": "2026-09-01T00:00:00-04:00",
        "content": "PROSE"}]})
    lv = ats.parse(ats.LEVER, [{
        "id": "a", "text": "PM", "hostedUrl": "https://x/a", "createdAt": 1750000000000,
        "categories": {"location": "Tokyo"}, "description": "PROSE"}])
    for rows in (gh, lv):
        assert set(rows[0]) == set(ats.PAYLOAD_FIELDS)
        assert "PROSE" not in json.dumps(rows)
    assert gh[0]["updated_at"] == "2026-09-01T00:00:00-04:00"
    assert lv[0]["created_at"] == "2025-06-15T15:06:40Z"


def test_stored_columns_are_classified_and_fail_closed():
    rp.check_stored_columns(ats_collect.JOBS_COLUMNS)          # today's shape passes
    for bad in ("job_description", "recruiter_email"):
        try:
            rp.check_stored_columns(list(ats_collect.JOBS_COLUMNS) + [bad])
        except RuntimeError:
            continue
        raise AssertionError(f"an unclassified column {bad!r} was accepted")
    # every record carries the attribution triple
    for col in ("source_url", "ats", "retrieved_at"):
        assert col in ats_collect.JOBS_COLUMNS


def test_employment_type_is_not_stored():
    """PC-5: the Lever-derived employment type stays out until the Scout confirms it."""
    assert "employment_type" not in ats_collect.JOBS_COLUMNS
    try:
        rp.check_stored_columns(list(ats_collect.JOBS_COLUMNS) + ["employment_type"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("employment_type must fail the stored-column allow-list")


# --------------------------------------------------------- closed rule

def _job(pid, misses="0", status="open"):
    ats_name, slug, _ = pid.split(":")
    return {"posting_id": pid, "ats": ats_name, "company_slug": slug,
            "consecutive_misses": misses, "status": status, "closed_at": ""}


def test_a_304_confirms_only_what_the_last_payload_carried():
    jobs = {
        "greenhouse:a:1": _job("greenhouse:a:1"),
        "greenhouse:a:2": _job("greenhouse:a:2", misses="1"),   # dropped out last night
        "greenhouse:a:3": _job("greenhouse:a:3", misses="2", status="closed"),
    }
    held = ats_collect.board_rows(jobs, "greenhouse", "a")
    present = ats_collect.present_at_last_crawl(jobs, held)
    assert present == ["greenhouse:a:1"]
    ats_collect.confirm_unchanged(jobs, present, "2026-09-25T19:10:00Z", "cleared-held")
    closed = ats_collect.close_missing(jobs, {("greenhouse", "a")}, set(present),
                                       "2026-09-25", "2026-09-25T19:10:00Z")
    assert closed == 1
    assert jobs["greenhouse:a:2"]["status"] == "closed"
    assert jobs["greenhouse:a:1"]["status"] == "open"


def test_a_failed_board_never_closes_anything():
    jobs = {"lever:b:1": _job("lever:b:1", misses="1")}
    assert ats_collect.close_missing(jobs, set(), set(), "2026-09-25") == 0
    assert jobs["lever:b:1"]["status"] == "open"


# --------------------------------------------------------- the gate

def test_robots_directives_ignore_comments_but_not_directives():
    a = rp.robots_directives("# docs link\n\nUser-agent: *\nDisallow: /embed/\n")
    b = rp.robots_directives("User-Agent: *\n# changed comment\nDisallow:  /embed/")
    assert a == b == ["user-agent: *", "disallow: /embed/"]
    c = rp.robots_directives("User-agent: *\nDisallow: /\n")
    assert rp.fingerprint(c) != rp.fingerprint(a)


def test_the_gate_expectations_match_the_recorded_clearance():
    gh = rp.GATE_ROBOTS["boards-api.greenhouse.io"]["expected"]
    assert "disallow: /embed/" in gh and "disallow: /" not in gh
    for host in ("api.lever.co", "api.eu.lever.co"):
        assert "allow: /" in rp.GATE_ROBOTS[host]["expected"]
    covered = {s for cfg in rp.GATE_ROBOTS.values() for s in cfg["sources"]}
    assert covered == set(ats.SUPPORTED)
    assert {s for v in rp.GATE_LEGAL.values() for s in v} == set(ats.SUPPORTED)


def test_legal_links_are_normalised_and_non_legal_links_ignored():
    page = ('<a href="/legal/terms-of-service/">Terms of <b>Service</b></a>'
            '<a href="https://www.example.com/pricing">Pricing</a>'
            '<a href="https://other.example/privacy?x=1">Privacy</a>')
    links = rp.legal_links(page, "https://www.example.com/legal")
    assert links == ["/legal/terms-of-service | Terms of Service",
                     "other.example/privacy | Privacy"]


def _gate_fixture(tmp: Path, robots_body: bytes, legal_body: bytes):
    routes = {}
    for host in rp.GATE_ROBOTS:
        routes[f"https://{host}/robots.txt"] = _Resp(body=robots_body)
    for url in rp.GATE_LEGAL:
        origin = "/".join(url.split("/")[:3])
        routes[origin + "/robots.txt"] = ALLOW
        routes[url] = _Resp(body=legal_body, ctype="text/html")
    return _session(routes)


def test_the_gate_halts_a_source_whose_robots_changed():
    legal = b'<a href="/legal/terms">Terms</a>'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        s = _gate_fixture(tmp, b"User-agent: *\nAllow: /\n", legal)
        baseline = {"items": {}}
        for item_id, kind, url, _ in rp.gate_items():
            lines, _ = rp.read_gate_item(s, kind, url)
            baseline["items"][item_id] = {"fingerprint": rp.fingerprint(lines), "lines": lines}
        (tmp / "b.json").write_text(json.dumps(baseline), encoding="utf-8")

        same = _gate_fixture(tmp, b"User-agent: *\nAllow: /\n", legal)
        assert rp.run_gate(same, tmp / "b.json", tmp / "log.csv") == {}

        changed = _gate_fixture(tmp, b"User-agent: *\nDisallow: /\n", legal)
        halted = rp.run_gate(changed, tmp / "b.json", tmp / "log.csv")
        assert set(halted) == set(ats.SUPPORTED)
        with (tmp / "log.csv").open(encoding="utf-8") as fh:
            results = [r["result"] for r in csv.DictReader(fh)]
        assert "changed" in results and results.count("same") >= 5


def test_the_gate_fails_closed_without_a_baseline_or_on_an_unreachable_page():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        s = _gate_fixture(tmp, b"User-agent: *\nAllow: /\n", b"")
        assert set(rp.run_gate(s, tmp / "missing.json", tmp / "log.csv")) == set(ats.SUPPORTED)

        down = _gate_fixture(tmp, b"", b"")
        for host in rp.GATE_ROBOTS:
            down._session.routes[f"https://{host}/robots.txt"] = _Resp(status=503, body=b"")
        halted = rp.run_gate(down, tmp / "missing.json", tmp / "log2.csv")
        assert set(halted) == set(ats.SUPPORTED)


def test_refresh_robots_ignores_the_24h_cache():
    s = _session({"https://r.example/robots.txt": ALLOW})
    s.robots_verdict("https://r.example/x")
    s.robots_verdict("https://r.example/y")
    assert s._session.calls.count("https://r.example/robots.txt") == 1
    s._session.routes["https://r.example/robots.txt"] = _Resp(body=b"User-agent: *\nDisallow: /\n")
    body = s.refresh_robots("https://r.example/x")
    assert "Disallow: /" in body
    assert s.robots_verdict("https://r.example/x") == "DISALLOWED"


# ------------------------------------------------------- backoff (rule 6)

def test_retry_after_up_to_60s_is_honoured_and_longer_is_not_waited_for():
    ok_after_one = iter([_Resp(status=429, body=b"", headers={"Retry-After": "0"}),
                         _Resp(body=b"fine")])
    s = _session({"https://ra.example/robots.txt": ALLOW,
                  "https://ra.example/d": lambda: next(ok_after_one)})
    assert s.get("https://ra.example/d", conditional=False).body == b"fine"

    s2 = _session({"https://rb.example/robots.txt": ALLOW,
                   "https://rb.example/d": _Resp(status=503, body=b"",
                                                 headers={"Retry-After": "3600"})})
    try:
        s2.get("https://rb.example/d", conditional=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("a 503 was treated as success")
    assert s2._session.calls.count("https://rb.example/d") == 1     # not retried


def test_a_5xx_is_retried_once_then_counts_as_a_failure():
    s = _session({"https://five.example/robots.txt": ALLOW,
                  "https://five.example/d": _Resp(status=502, body=b"")})
    try:
        s.get("https://five.example/d", conditional=False)
    except RuntimeError:
        pass
    assert s._session.calls.count("https://five.example/d") == 2
    assert s._fail_streak["five.example"] == 1


# ----------------------------------------------------------- Bet A weekly

LISTING = """
<h3 class="search-result table-title">全件データファイル・CSV形式（UTF-8（BOM付き））</h3>
<div class="result-detail"><p>
  令和08年09月21日更新
</p>
<table id="allDataFileTbl">
<tr><td>令和08年度</td><td><a class="text-link" onclick="doDownload('successful_bid_record_info_all_2026.zip')">
  successful_bid_record_info_all_2026.zip(0.7MB)
</a></td></tr>
<tr><td>令和07年度</td><td><a class="text-link" onclick="doDownload('successful_bid_record_info_all_2025.zip')">
  successful_bid_record_info_all_2025.zip(1.7MB)
</a></td></tr>
</table>
"""

FILES = [
    {"key": "FY2026", "file_name": "successful_bid_record_info_all_2026.zip", "kind": "all"},
    {"key": "FY2025", "file_name": "successful_bid_record_info_all_2025.zip", "kind": "all"},
]
MANIFEST = {
    "FY2026": {"status": "ok", "retrieved_at": "2026-09-21T14:28:16Z"},
    "FY2025": {"status": "ok", "retrieved_at": "2026-09-21T14:28:10Z"},
}


def test_listing_update_date_and_size_labels_are_read():
    assert rp.parse_all_stamp(LISTING) == "2026-09-21"
    assert rp.parse_size_labels(LISTING) == {
        "successful_bid_record_info_all_2026.zip": "0.7MB",
        "successful_bid_record_info_all_2025.zip": "1.7MB",
    }
    assert rp.parse_all_stamp("<table id='allDataFileTbl'></table>") == ""


def test_weekly_downloads_nothing_while_the_upstream_issue_is_unchanged():
    sizes = rp.parse_size_labels(LISTING)
    # first run: no recorded date, but the store retrieved this issue already
    assert rp.plan_refresh(FILES, MANIFEST, "2026-09-21", sizes, {}) == []
    state = {"stamp": "2026-09-21", "sizes": sizes}
    assert rp.plan_refresh(FILES, MANIFEST, "2026-09-21", sizes, state) == []


def test_weekly_refetches_when_the_publisher_reissues_or_a_size_moves():
    sizes = rp.parse_size_labels(LISTING)
    state = {"stamp": "2026-09-21", "sizes": sizes}
    reissued = rp.plan_refresh(FILES, MANIFEST, "2026-10-19", sizes, state)
    assert [f["key"] for f, _ in reissued] == ["FY2026", "FY2025"]

    grown = dict(sizes, **{"successful_bid_record_info_all_2026.zip": "0.8MB"})
    moved = rp.plan_refresh(FILES, MANIFEST, "2026-09-21", grown, state)
    assert [f["key"] for f, _ in moved] == ["FY2026"]

    new = rp.plan_refresh(FILES + [{"key": "FY2027", "file_name": "n.zip"}],
                          MANIFEST, "2026-09-21", sizes, state)
    assert [f["key"] for f, _ in new] == ["FY2027"]

    blind = rp.plan_refresh(FILES, MANIFEST, "", sizes, state)
    assert len(blind) == 2 and "unreadable" in blind[0][1]


# ------------------------------------------------------------- run logs

def test_run_log_rows_keep_an_existing_header():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run_log.csv"
        path.write_text("run_at,boards_ok,note\n", encoding="utf-8")
        rp.append_csv_row(path, ["run_at", "boards_ok", "extra", "note"],
                          {"run_at": "t", "boards_ok": 3, "extra": "x", "note": "n"})
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows == [["run_at", "boards_ok", "note"], ["t", "3", "n"]]


def test_the_runner_contains_no_push_or_publish_path():
    source = (CRAWLERS / "tools" / "run_private.py").read_text(encoding="utf-8")
    for word in ('"push"', "git ", "wrangler", "npm publish", "firebase deploy"):
        assert word not in source, word
    assert datetime.now(timezone.utc)  # keep the import honest


def test_data_dir_flag_is_read_before_the_store_is_resolved():
    assert rp._early_data_dir(["--data-dir", "X", "nightly"]) == "X"
    assert rp._early_data_dir(["--data-dir=Y", "weekly"]) == "Y"
    assert rp._early_data_dir(["nightly"]) is None
