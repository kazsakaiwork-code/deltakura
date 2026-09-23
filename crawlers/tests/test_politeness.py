"""The politeness rules are company policy, so they are
tested the way a rule is tested: by asserting the code refuses, not by asserting
it can be asked nicely.

No network access. The HTTP layer is driven against a stub session.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

from _lib import http, paths, tos  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ats_registry"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import ats as ats_mod  # noqa: E402
import sync_tos_matrix  # noqa: E402


class _Resp:
    def __init__(self, status=200, body=b"ok", headers=None, ctype="text/plain"):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.url = "stub"
        self.text = body.decode("utf-8", "replace")

        class _Raw:
            def __init__(self, data):
                self._data = data

            def read(self, n, decode_content=True):
                return self._data[:n]

        self.raw = _Raw(body)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=1):
        yield self._body


class _StubSession:
    """Stands in for requests.Session. Records every call."""

    def __init__(self, routes: Dict[str, _Resp]):
        self.routes = routes
        self.calls: List[str] = []
        self.headers: Dict[str, str] = {}
        self.max_redirects = 5

    def _answer(self, url: str) -> _Resp:
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return _Resp(status=404, body=b"not found")

    def get(self, url, **kw):
        return self._answer(url)

    def post(self, url, **kw):
        return self._answer(url)


def _session(routes, **kw) -> http.PoliteSession:
    s = http.PoliteSession(state_path=None, **kw)
    s._session = _StubSession(routes)  # type: ignore[assignment]
    return s


ROBOTS_ALLOW_ALL = _Resp(body=b"User-agent: *\nAllow: /\n")
ROBOTS_DISALLOW_ALL = _Resp(body=b"User-agent: *\nDisallow: /\n")


# ------------------------------------------------------------ rule 1/3

def test_robots_disallow_blocks_the_fetch():
    s = _session({"https://blocked.example/robots.txt": ROBOTS_DISALLOW_ALL})
    try:
        s.get("https://blocked.example/data.csv")
    except http.RobotsDisallowed:
        pass
    else:
        raise AssertionError("a Disallow: / host was fetched anyway")


def test_unreachable_robots_txt_means_full_disallow():
    # RFC 9309 2.3.1.4: a 5xx (or 429) on robots.txt is "unreachable", and the
    # crawler must assume complete disallow rather than "no directives".
    for status in (500, 503, 429):
        s = _session({"https://down.example/robots.txt": _Resp(status=status, body=b"")})
        try:
            s.get("https://down.example/data.csv")
        except http.RobotsDisallowed:
            pass
        else:
            raise AssertionError(f"fetched despite robots.txt answering {status}")
        assert not any(c.endswith("/data.csv") for c in s._session.calls)  # type: ignore[attr-defined]


def test_robots_verdict_reports_no_robots_file():
    s = _session({"https://open.example/robots.txt": _Resp(status=404, body=b"")})
    assert "no robots.txt" in s.robots_verdict("https://open.example/x")


def test_user_agent_identifies_the_bot_and_is_not_a_browser():
    ua = http.USER_AGENT
    assert ua.startswith("DeltakuraBot/")
    assert "+http" in ua                      # carries a contact URL
    for browser in ("Mozilla", "Chrome", "Safari", "Gecko"):
        assert browser not in ua


def test_user_agent_contact_url_is_the_github_repo():
    # The contact URL must resolve before any crawl above the prototype cap.
    # deltakura.dev is not registered, so the
    # interim contact is the project's GitHub repository.
    assert "(+https://github.com/kazsakaiwork-code/deltakura)" in http.USER_AGENT
    assert "deltakura.dev" not in http.USER_AGENT
    assert "pending" not in http.USER_AGENT.lower()


def test_robots_is_fetched_once_per_host():
    s = _session({
        "https://once.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://once.example/": _Resp(),
    })
    s.min_delay = 0.0
    s.get("https://once.example/a")
    s.get("https://once.example/b")
    robots_calls = [c for c in s._session.calls if c.endswith("/robots.txt")]  # type: ignore[attr-defined]
    assert len(robots_calls) == 1, robots_calls


# -------------------------------------------------------------- rule 2

def test_minimum_delay_between_same_host_requests():
    s = _session({
        "https://slow.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://slow.example/": _Resp(),
    })
    s.min_delay = 0.4          # scaled down so the suite stays fast
    s._host_delay["slow.example"] = 0.4
    s.get("https://slow.example/a")
    start = time.monotonic()
    s.get("https://slow.example/b")
    assert time.monotonic() - start >= 0.35


def test_declared_crawl_delay_raises_ours():
    s = _session({
        "https://polite.example/robots.txt": _Resp(
            body=b"User-agent: *\nCrawl-delay: 9\nAllow: /\n"
        ),
        "https://polite.example/": _Resp(),
    })
    s.robots_verdict("https://polite.example/a")
    assert s._host_delay["polite.example"] >= 9.0


def test_default_delay_is_at_least_two_seconds():
    assert http.DEFAULT_DELAY_SEC >= 2.0


# -------------------------------------------------------------- rule 4

def test_conditional_request_sends_the_validator():
    s = _session({
        "https://cache.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://cache.example/": _Resp(headers={"ETag": '"abc"'}),
    })
    s.min_delay = 0.0
    s.get("https://cache.example/f.json")
    assert s._state["cache"]["https://cache.example/f.json"]["etag"] == '"abc"'


def test_304_raises_not_modified():
    s = _session({
        "https://nm.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://nm.example/": _Resp(status=304, body=b""),
    })
    s.min_delay = 0.0
    try:
        s.get("https://nm.example/f.json")
    except http.NotModified:
        pass
    else:
        raise AssertionError("304 did not raise NotModified")


def test_no_validator_and_fresh_cache_refuses_a_refetch_within_24h():
    s = _session({
        "https://fresh.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://fresh.example/": _Resp(),
    })
    s.min_delay = 0.0
    s.get("https://fresh.example/page")
    try:
        s.get("https://fresh.example/page")
    except http.NotModified:
        pass
    else:
        raise AssertionError("an unchanged page was re-fetched within 24 h")


# -------------------------------------------------------------- rule 6

def test_three_consecutive_failures_pause_the_host():
    s = _session({
        "https://flaky.example/robots.txt": ROBOTS_ALLOW_ALL,
        "https://flaky.example/": _Resp(status=500, body=b"boom"),
    })
    s.min_delay = 0.0
    failures = 0
    for i in range(4):
        try:
            s.get(f"https://flaky.example/{i}")
        except http.HostPaused:
            assert failures == 2, failures
            return
        except RuntimeError:
            failures += 1
    raise AssertionError("host was never paused after repeated failures")


# -------------------------------------------------------------- rule 7

def test_daily_cap_is_enforced_and_survives_a_restart(tmp_state=None):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        routes = {
            "https://cap.example/robots.txt": ROBOTS_ALLOW_ALL,
            "https://cap.example/": _Resp(),
        }
        s = http.PoliteSession(state_path=state, daily_cap=3, min_delay=0.0)
        s._session = _StubSession(routes)  # type: ignore[assignment]
        # request 1 is the robots fetch, then two real ones
        s.get("https://cap.example/a")
        s.get("https://cap.example/b")
        try:
            s.get("https://cap.example/c")
        except http.DailyCapReached:
            pass
        else:
            raise AssertionError("daily cap was not enforced")
        s.save_state()

        # a fresh process must not get a fresh budget
        s2 = http.PoliteSession(state_path=state, daily_cap=3, min_delay=0.0)
        s2._session = _StubSession(routes)  # type: ignore[assignment]
        assert s2.requests_today("cap.example") >= 3
        try:
            s2.get("https://cap.example/d")
        except http.DailyCapReached:
            return
        raise AssertionError("daily cap reset across a restart")


def test_prototype_cap_is_twenty():
    assert http.PROTOTYPE_DAILY_CAP == 20


# ------------------------------------------------- ToS gate (fail-closed)

def test_cleared_source_passes():
    c = tos.clear("法人番号公表サイト 差分データ")
    assert c.reuse_allowed == "Y"
    assert c.publishable
    assert "国税庁" in c.attribution


def test_blocked_source_raises():
    try:
        tos.clear("SmartRecruiters Posting API")
    except tos.SourceBlocked as exc:
        assert "no override" in str(exc)
    else:
        raise AssertionError("a reuse_allowed=N source was cleared")


def test_blocked_source_cannot_be_forced_with_prototype():
    try:
        tos.clear("SmartRecruiters Posting API", prototype=True)
    except tos.SourceBlocked:
        return
    raise AssertionError("prototype=True overrode a reuse_allowed=N source")


def test_unclear_source_is_blocked_for_production():
    # Ashby is the live example of reuse_allowed=unclear in the matrix.
    try:
        tos.clear("Ashby Public Job Posting API")
    except tos.SourceBlocked as exc:
        assert "unclear" in str(exc)
    else:
        raise AssertionError("an unclear source was cleared for production")


def test_unclear_source_in_prototype_mode_is_not_publishable():
    c = tos.clear("Workable public job board widget API", prototype=True)
    assert c.prototype_only
    assert not c.publishable
    assert c.tos_status == "unclear-prototype"
    try:
        tos.require_publishable(c)
    except tos.SourceBlocked:
        return
    raise AssertionError("a prototype-only clearance was allowed to publish")


def test_unclear_source_with_a_conflicting_robots_verdict_is_blocked_even_as_prototype():
    # Ashby: jobs.ashbyhq.com disallows /api/. Prototype mode requires an
    # affirmative robots verdict, so there is no way in at all.
    try:
        tos.clear("Ashby Public Job Posting API", prototype=True)
    except tos.SourceBlocked as exc:
        assert "robots_ok" in str(exc)
        return
    raise AssertionError("a source with a conflicting robots verdict ran as a prototype")


# ------------------------------------------------- Bet B: cleared, but held

def test_bet_b_sources_are_cleared_for_production():
    # Rows updated 2026-09-22: Greenhouse and Lever are Y.
    for row in ("Greenhouse Job Board API", "Lever Postings API"):
        c = tos.clear(row)
        assert c.reuse_allowed == "Y", row
        assert not c.prototype_only, row
        assert c.publishable, row
        assert c.tos_status == "cleared", row


def test_bet_b_is_held_back_from_publication_until_req_005():
    assert ats_mod.bet_b_live() is False, "BET_B_LIVE must default to false"
    hold = ats_mod.hold_reason()
    assert "pending operator approval" in hold
    c = tos.clear("Greenhouse Job Board API", hold=hold)
    assert c.reuse_allowed == "Y"
    assert not c.publishable
    assert c.tos_status == "cleared-held"
    try:
        tos.require_publishable(c)
    except tos.SourceBlocked as exc:
        assert "held" in str(exc)
        return
    raise AssertionError("a held clearance was allowed to publish")


def test_bet_b_live_flag_is_the_only_way_to_lift_the_hold():
    previous = os.environ.get(ats_mod.BET_B_LIVE_ENV)
    try:
        os.environ[ats_mod.BET_B_LIVE_ENV] = "true"
        assert ats_mod.bet_b_live() is True
        assert ats_mod.hold_reason() == ""
        assert tos.clear("Lever Postings API", hold=ats_mod.hold_reason()).publishable
    finally:
        if previous is None:
            os.environ.pop(ats_mod.BET_B_LIVE_ENV, None)
        else:
            os.environ[ats_mod.BET_B_LIVE_ENV] = previous
    assert ats_mod.bet_b_live() is False


def test_a_hold_cannot_rescue_a_blocked_source():
    try:
        tos.clear("SmartRecruiters Posting API", hold="")
    except tos.SourceBlocked:
        return
    raise AssertionError("a hold argument changed an N verdict")


def test_shipped_matrix_publishes_clearance_facts_and_nothing_else():
    """The public projection is a verdict, not a survey.

    Four internal columns were dropped because they carried collection recipes
    for sources recorded as `reuse_allowed=N` (URL patterns, encodings,
    pagination parameters, column lists). `license` and `robots_ok` are reduced
    to a controlled vocabulary because the internal text characterises named
    public bodies' terms and records our own reasoning. This test is what stops
    either creeping back in.
    """
    import csv as _csv

    with paths.TOS_MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))
    assert rows, "the shipped clearance matrix is empty"
    assert list(rows[0].keys()) == sync_tos_matrix.SHIPPED_COLUMNS

    for dropped in ("api_available", "data_format", "rate_limit_notes", "notes"):
        assert dropped not in rows[0], f"{dropped} must not be published"

    licenses = {v for _, v in sync_tos_matrix.LICENSE_VOCABULARY} | {""}
    robots = {v for _, v in sync_tos_matrix.ROBOTS_VOCABULARY} | {""}
    for row in rows:
        assert row["license"] in licenses, (row["source"], row["license"])
        assert row["robots_ok"] in robots, (row["source"], row["robots_ok"])
        # A verdict, not a sentence: nothing here explains, qualifies or judges.
        for column in ("license", "robots_ok"):
            assert len(row[column]) <= 24, (row["source"], column, row[column])
        assert row["terms_url"] == "" or row["terms_url"].startswith("http"), row["source"]


def test_projection_refuses_an_unclassified_verdict():
    """Fail closed: unknown prose stops the projection instead of shipping."""
    for bad in ({"source": "x", "license": "probably fine, see the notes"},
                {"source": "x", "license": "unclear", "robots_ok": "depends"}):
        try:
            sync_tos_matrix.shipped_row(bad)
        except sync_tos_matrix.NotClassified:
            continue
        raise AssertionError(f"unclassified value was projected: {bad}")


def test_projection_never_rewrites_the_verdict_the_gate_reads():
    row = {"source": "x", "license": "custom terms - whatever", "robots_ok": "Y - fine",
           "reuse_allowed": "Y - discovery only"}
    assert sync_tos_matrix.shipped_row(row)["reuse_allowed"] == "Y - discovery only"


def test_an_uncleared_row_publishes_no_deep_link():
    """A row that is not cleared names the site, never the results page.

    The `url` column is the last field in the file that could be read as a
    collection recipe. For `reuse_allowed=N` and `unclear` rows it is narrowed
    to scheme + host, so it still says which site the verdict is about and
    nothing about where the data sits on it.
    """
    import csv as _csv

    with paths.TOS_MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))

    uncleared = [r for r in rows if not sync_tos_matrix.is_cleared(r["reuse_allowed"])]
    assert uncleared, "the matrix should still record sources that are not cleared"
    for row in uncleared:
        for url in row["url"].split(" ; "):
            if not url:
                continue
            assert re.fullmatch(r"https?://[^/?#]+/", url), (row["source"], url)

    cleared = [r for r in rows if sync_tos_matrix.is_cleared(r["reuse_allowed"])]
    assert any("/" in r["url"].split("://", 1)[1].rstrip("/") for r in cleared), (
        "cleared rows keep the full endpoint this project collects from"
    )


def test_projection_narrows_every_verdict_that_is_not_an_affirmative_y():
    deep = "https://www.example.lg.jp/keiyaku/kekka_buppin2026a.htm?pageno=1"
    base = {"source": "x", "license": "custom terms", "robots_ok": "Y", "url": deep}
    for verdict in ("N", "n", "unclear", "UNCLEAR", "under review", ""):
        out = sync_tos_matrix.shipped_row({**base, "reuse_allowed": verdict})
        assert out["url"] == "https://www.example.lg.jp/", (verdict, out["url"])
    for verdict in ("Y", "y", "Y - discovery only"):
        out = sync_tos_matrix.shipped_row({**base, "reuse_allowed": verdict})
        assert out["url"] == deep, (verdict, out["url"])


def test_projection_refuses_an_uncleared_row_whose_url_it_cannot_narrow():
    """Fail closed: an unparseable location is not published verbatim."""
    row = {"source": "x", "license": "custom terms", "robots_ok": "Y",
           "reuse_allowed": "N", "url": "ask the contracts desk"}
    try:
        sync_tos_matrix.shipped_row(row)
    except sync_tos_matrix.NotClassified:
        return
    raise AssertionError("an uncleared row published an unparsed url")


def test_shipped_clearance_matrix_matches_the_internal_copy():
    # In a standalone clone there is no internal matrix and this is a no-op;
    # where one sits beside the checkout it fails the build when the two drift.
    assert paths.TOS_MATRIX.is_file(), "the shipped clearance matrix must be committed"
    assert sync_tos_matrix.run(["--check"]) == 0


def test_unknown_source_is_blocked():
    try:
        tos.clear("a source nobody surveyed")
    except tos.SourceBlocked:
        return
    raise AssertionError("a source with no matrix row was cleared")
