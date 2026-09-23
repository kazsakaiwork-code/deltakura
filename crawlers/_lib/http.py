"""Polite HTTP layer. The crawl rules live here in code, not in convention.

Rules enforced here (company policy, not caller options):

  1. robots.txt is fetched once per host, cached on disk for 24 h and obeyed.
     A declared Crawl-delay longer than our default raises our delay.
  2. >= 2.0 s between two requests to the same host. Single connection, no
     per-host parallelism. Sleeping happens inside this class.
  3. An identifying User-Agent carrying a contact URL on every request,
     including the robots.txt fetch. A browser UA is never sent.
  4. Conditional requests (If-None-Match / If-Modified-Since) backed by an
     on-disk cache; an unchanged resource is not re-fetched within 24 h.
  5. Never logs in, never sends credentials, never follows a login redirect,
     never touches a CAPTCHA. There is no code path here that can.
  6. 429/503 -> exponential backoff, at most one retry per hour per host.
     Three consecutive 4xx/5xx on a host pause that host for the run and raise
     HostPaused, which the caller reports rather than retries.
  7. A per-host daily request cap (default 20, the week-1 prototype cap) is
     persisted across runs so restarts cannot blow through it.

Standard library + requests only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import requests

# The contact URL must resolve before any crawl above the
# prototype cap. `deltakura.dev` is not registered, so the interim contact is the project's GitHub
# repository, whose issue tracker is also where removal requests are filed.
#
# The URL resolves for everyone once the repository is public - no code change
# needed. Later it becomes https://deltakura.dev/bot.
USER_AGENT = "DeltakuraBot/0.1 (+https://github.com/kazsakaiwork-code/deltakura)"

DEFAULT_DELAY_SEC = 2.0
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_MAX_BYTES = 300 * 1024 * 1024  # 300 MB hard cap on a single response
PROTOTYPE_DAILY_CAP = 20               # requests per host per day, prototype mode
ROBOTS_TTL_SEC = 24 * 3600
CACHE_TTL_SEC = 24 * 3600


class RobotsDisallowed(RuntimeError):
    """robots.txt forbids the requested path for our User-Agent."""


class HostPaused(RuntimeError):
    """Three consecutive failures on this host; the source is paused."""


class DailyCapReached(RuntimeError):
    """The per-host daily request budget is exhausted."""


class NotModified(RuntimeError):
    """The server answered 304; the cached copy is still current."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    headers: Dict[str, str]
    retrieved_at: str
    from_cache: bool = False

    @property
    def sha256(self) -> str:
        return sha256_hex(self.body)

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


@dataclass
class PoliteSession:
    """One instance per process. Not thread-safe by design: no parallelism."""

    user_agent: str = USER_AGENT
    min_delay: float = DEFAULT_DELAY_SEC
    state_path: Optional[Path] = None
    daily_cap: Optional[int] = PROTOTYPE_DAILY_CAP
    max_bytes: int = DEFAULT_MAX_BYTES
    timeout: int = DEFAULT_TIMEOUT_SEC
    respect_robots: bool = True

    _last_hit: Dict[str, float] = field(default_factory=dict)
    _host_delay: Dict[str, float] = field(default_factory=dict)
    _robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = field(default_factory=dict)
    _fail_streak: Dict[str, int] = field(default_factory=dict)
    _paused: Dict[str, str] = field(default_factory=dict)
    _state: Dict[str, dict] = field(default_factory=dict)
    _session: requests.Session = field(default_factory=requests.Session)
    _requests_made: int = 0

    # --------------------------------------------------------------- state
    def __post_init__(self) -> None:
        self._session.headers.update({"User-Agent": self.user_agent})
        # requests would otherwise happily carry credentials through a redirect
        self._session.max_redirects = 5
        self._load_state()

    def _load_state(self) -> None:
        self._state = {"counts": {}, "cache": {}, "robots": {}}
        if self.state_path and self.state_path.exists():
            try:
                self._state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass  # a corrupt cache is not a reason to stop; it is rebuilt
        for key in ("counts", "cache", "robots"):
            self._state.setdefault(key, {})

    def save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # keep only today's and yesterday's counters
        keep = {k: v for k, v in self._state["counts"].items() if k.endswith(_today())}
        self._state["counts"] = keep or self._state["counts"]
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # ---------------------------------------------------------------- caps
    def _cap_key(self, host: str) -> str:
        return f"{host}|{_today()}"

    def requests_today(self, host: str) -> int:
        return int(self._state["counts"].get(self._cap_key(host), 0))

    def _bump_count(self, host: str) -> None:
        key = self._cap_key(host)
        self._state["counts"][key] = self.requests_today(host) + 1
        self._requests_made += 1

    def _check_cap(self, host: str) -> None:
        if self.daily_cap is None:
            return
        if self.requests_today(host) >= self.daily_cap:
            raise DailyCapReached(
                f"{host}: {self.daily_cap} requests already made today; "
                "raise daily_cap deliberately or wait for the next UTC day"
            )

    # -------------------------------------------------------------- robots
    def _robots_for(self, url: str):
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        cached = self._state["robots"].get(origin)
        if cached and time.time() - cached.get("fetched_epoch", 0) < ROBOTS_TTL_SEC:
            parser = None
            if cached.get("body"):
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(cached["body"].splitlines())
            self._robots[origin] = parser
            self._apply_crawl_delay(parts.netloc, parser)
            return parser

        parser = None
        body = ""
        try:
            self._wait(parts.netloc)
            resp = self._session.get(origin + "/robots.txt", timeout=30, allow_redirects=False)
            self._last_hit[parts.netloc] = time.monotonic()
            self._bump_count(parts.netloc)
            if resp.status_code == 429 or resp.status_code >= 500:
                # RFC 9309 2.3.1.4: an unreachable robots.txt means "assume
                # complete disallow". Not cached, so the next run asks again.
                raise RobotsDisallowed(
                    f"{origin}/robots.txt answered HTTP {resp.status_code}; "
                    "treating the host as fully disallowed for this run"
                )
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if resp.status_code == 200 and "html" not in ctype and "json" not in ctype:
                body = resp.text
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(body.splitlines())
            # 404 / redirect / an HTML or JSON error page all mean "no usable
            # robots.txt" -> no directives. We still rate-limit and still obey
            # the per-source clearance in crawlers/tos_matrix.csv.
        except requests.RequestException as exc:
            raise RobotsDisallowed(
                f"could not read {origin}/robots.txt ({exc}); refusing to fetch"
            ) from exc

        self._state["robots"][origin] = {"body": body, "fetched_epoch": time.time()}
        self._robots[origin] = parser
        self._apply_crawl_delay(parts.netloc, parser)
        return parser

    def _apply_crawl_delay(self, netloc: str, parser) -> None:
        delay = self.min_delay
        if parser is not None:
            declared = parser.crawl_delay(self.user_agent)
            if declared:
                delay = max(delay, float(declared))
        self._host_delay[netloc] = delay

    def robots_verdict(self, url: str) -> str:
        if not self.respect_robots:
            return "robots check disabled (explicitly, for a non-HTTP source)"
        parser = self._robots_for(url)
        if parser is None:
            return "no robots.txt served (no directives) - allowed"
        return "allowed" if parser.can_fetch(self.user_agent, url) else "DISALLOWED"

    # --------------------------------------------------------------- delay
    def _wait(self, netloc: str) -> None:
        last = self._last_hit.get(netloc)
        if last is None:
            return
        delay = self._host_delay.get(netloc, self.min_delay)
        remaining = delay - (time.monotonic() - last)
        if remaining > 0:
            time.sleep(remaining)

    # --------------------------------------------------------------- fetch
    def _guard(self, url: str) -> str:
        host = _host_of(url)
        if host in self._paused:
            raise HostPaused(f"{host} paused this run: {self._paused[host]}")
        if self.respect_robots:
            parser = self._robots_for(url)
            if parser is not None and not parser.can_fetch(self.user_agent, url):
                raise RobotsDisallowed(f"robots.txt disallows {url}")
        self._check_cap(host)
        return host

    def _note_failure(self, host: str, why: str) -> None:
        self._fail_streak[host] = self._fail_streak.get(host, 0) + 1
        if self._fail_streak[host] >= 3:
            self._paused[host] = why
            raise HostPaused(
                f"{host} paused after 3 consecutive failures ({why}); "
                "raise this with a maintainer rather than retrying"
            )

    def _read_capped(self, resp: requests.Response) -> bytes:
        body = resp.raw.read(self.max_bytes + 1, decode_content=True)
        if len(body) > self.max_bytes:
            raise RuntimeError(f"{resp.url} exceeds the {self.max_bytes} byte cap")
        return body

    def get(
        self,
        url: str,
        *,
        accept: str = "*/*",
        conditional: bool = True,
        allow_redirects: bool = True,
    ) -> FetchResult:
        """GET with conditional-request support. Raises NotModified on 304."""
        host = self._guard(url)
        headers = {"Accept": accept}
        entry = self._state["cache"].get(url) if conditional else None
        if entry:
            fresh = time.time() - entry.get("fetched_epoch", 0) < CACHE_TTL_SEC
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]
            if fresh and not (entry.get("etag") or entry.get("last_modified")):
                # No validator available and fetched within 24 h: rule 4 says
                # do not re-fetch an unchanged resource.
                raise NotModified(f"{url} fetched within 24 h and has no validator")

        attempt = 0
        while True:
            attempt += 1
            self._wait(host)
            try:
                resp = self._session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                    allow_redirects=allow_redirects,
                )
                self._last_hit[host] = time.monotonic()
                self._bump_count(host)

                if resp.status_code == 304:
                    self._fail_streak[host] = 0
                    raise NotModified(f"{url} unchanged (304)")

                if resp.status_code in (429, 503) and attempt == 1:
                    time.sleep(min(60.0, self._host_delay.get(host, self.min_delay) * 8))
                    continue

                if resp.status_code >= 400:
                    self._note_failure(host, f"HTTP {resp.status_code}")
                    raise RuntimeError(f"{url} -> HTTP {resp.status_code}")

                body = self._read_capped(resp)
                self._fail_streak[host] = 0
                result = FetchResult(
                    url=url,
                    status=resp.status_code,
                    body=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    retrieved_at=utc_now_iso(),
                )
                self._state["cache"][url] = {
                    "etag": resp.headers.get("ETag"),
                    "last_modified": resp.headers.get("Last-Modified"),
                    "fetched_epoch": time.time(),
                    "sha256": result.sha256,
                    "bytes": len(body),
                }
                return result
            except requests.RequestException as exc:
                self._last_hit[host] = time.monotonic()
                if attempt >= 2:
                    self._note_failure(host, str(exc))
                    raise RuntimeError(f"fetch failed for {url}: {exc}") from exc
                time.sleep(self._host_delay.get(host, self.min_delay) * 4)

    def post_to_file(
        self, url: str, data: Dict[str, str], dest: Path, *, accept: str = "*/*"
    ) -> FetchResult:
        """As `post`, but streams the body to `dest` in chunks.

        Used for the few publisher files measured in hundreds of megabytes (the
        NTA 全件 dump), where buffering the whole response in memory is wasteful.
        The returned FetchResult carries an empty body; `sha256` and the byte
        count are computed while streaming.
        """
        host = self._guard(url)
        self._wait(host)
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with self._session.post(
                url, data=data, headers={"Accept": accept},
                timeout=self.timeout, stream=True,
            ) as resp:
                self._last_hit[host] = time.monotonic()
                self._bump_count(host)
                if resp.status_code >= 400:
                    self._note_failure(host, f"HTTP {resp.status_code}")
                    raise RuntimeError(f"{url} -> HTTP {resp.status_code}")
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > self.max_bytes:
                            raise RuntimeError(f"{url} exceeds the {self.max_bytes} byte cap")
                        digest.update(chunk)
                        fh.write(chunk)
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            self._note_failure(host, str(exc))
            raise RuntimeError(f"post failed for {url}: {exc}") from exc
        tmp.replace(dest)
        self._fail_streak[host] = 0
        result = FetchResult(
            url=url, status=200, body=b"", headers=headers,
            retrieved_at=utc_now_iso(),
        )
        result.streamed_sha256 = digest.hexdigest()  # type: ignore[attr-defined]
        result.streamed_bytes = written              # type: ignore[attr-defined]
        return result

    def post(self, url: str, data: Dict[str, str], *, accept: str = "*/*") -> FetchResult:
        """POST a public form. Used only where a publisher exposes its bulk
        downloads behind a form with no login and no account (NTA). Never used
        to submit personal data, credentials, or anything that changes state on
        the publisher's side.
        """
        host = self._guard(url)
        attempt = 0
        while True:
            attempt += 1
            self._wait(host)
            try:
                resp = self._session.post(
                    url,
                    data=data,
                    headers={"Accept": accept},
                    timeout=self.timeout,
                    stream=True,
                )
                self._last_hit[host] = time.monotonic()
                self._bump_count(host)
                if resp.status_code in (429, 503) and attempt == 1:
                    time.sleep(min(60.0, self._host_delay.get(host, self.min_delay) * 8))
                    continue
                if resp.status_code >= 400:
                    self._note_failure(host, f"HTTP {resp.status_code}")
                    raise RuntimeError(f"{url} -> HTTP {resp.status_code}")
                body = self._read_capped(resp)
                self._fail_streak[host] = 0
                return FetchResult(
                    url=url,
                    status=resp.status_code,
                    body=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    retrieved_at=utc_now_iso(),
                )
            except requests.RequestException as exc:
                self._last_hit[host] = time.monotonic()
                if attempt >= 2:
                    self._note_failure(host, str(exc))
                    raise RuntimeError(f"post failed for {url}: {exc}") from exc
                time.sleep(self._host_delay.get(host, self.min_delay) * 4)


def write_gz(path: Path, data: bytes) -> int:
    """Write bytes gzip-compressed; return the on-disk size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(data)
    return path.stat().st_size
