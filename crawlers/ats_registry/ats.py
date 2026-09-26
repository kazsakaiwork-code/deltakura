"""Public ATS endpoints: URL shapes and payload parsing.

Only the keyless, documented, public list endpoints are used. Nothing here can
log in, and nothing here reads or stores a job description body, a recruiter
name, or a contact address: all of those are "never stored".

Greenhouse: https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  -> {"jobs": [{"id", "title", "location": {"name"}, "absolute_url",
                "updated_at", "metadata": [...]}], "meta": {"total"}}
  `?content=true` would add the description body; it is deliberately not used.

Lever: https://api.lever.co/v0/postings/<site>?mode=json
  -> [{"id", "text", "hostedUrl", "createdAt",
       "categories": {"location", "team", "commitment"}}]
  The payload also carries `description`/`lists`; they are dropped here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- go-live ---
#
# The clearance matrix upgraded Greenhouse and Lever to reuse_allowed=Y on
# 2026-09-22, so the fail-closed legal gate in `_lib/tos.py` now admits them for
# production collection. Bet B going *live* — publishing anything derived from
# those boards — is a separate decision, pending operator approval.
#
# Until that approval is given this flag stays false, every clearance is taken out
# with an explicit hold, `tos.require_publishable()` refuses, and each record is
# marked `tos_status=cleared-held`. Flipping it is a one-line, auditable change
# that a reviewer can grep for, which is the point of having a flag instead of a
# comment.
BET_B_LIVE_ENV = "BET_B_LIVE"
BET_B_LIVE_DEFAULT = False
BET_B_HOLD_REASON = (
    "BET_B_LIVE=false - Bet B go-live is pending operator approval; collection "
    "runs, publication does not"
)


def bet_b_live() -> bool:
    """True only when BET_B_LIVE is explicitly set to a truthy value."""
    raw = (os.environ.get(BET_B_LIVE_ENV) or "").strip().lower()
    if not raw:
        return BET_B_LIVE_DEFAULT
    return raw in ("1", "true", "yes", "on")


def hold_reason() -> str:
    """The hold to pass to `tos.clear()`. Empty once Bet B is live."""
    return "" if bet_b_live() else BET_B_HOLD_REASON


GREENHOUSE = "greenhouse"
LEVER = "lever"
# Lever runs a separate EU instance; a board lives on one or the other, never
# both, so it is a distinct source id rather than a fallback.
LEVER_EU = "lever_eu"

SUPPORTED = (GREENHOUSE, LEVER, LEVER_EU)

HOSTS = {
    GREENHOUSE: "boards-api.greenhouse.io",
    LEVER: "api.lever.co",
    LEVER_EU: "api.eu.lever.co",
}

TOS_ROWS = {
    GREENHOUSE: "Greenhouse Job Board API",
    LEVER: "Lever Postings API",
    LEVER_EU: "Lever Postings API",
}

BOARD_PAGE = {
    GREENHOUSE: "https://job-boards.greenhouse.io/{token}",
    LEVER: "https://jobs.lever.co/{token}",
    LEVER_EU: "https://jobs.eu.lever.co/{token}",
}

# Both Lever instances serve the same payload shape.
_PARSE_AS = {GREENHOUSE: GREENHOUSE, LEVER: LEVER, LEVER_EU: LEVER}


def list_url(ats: str, token: str) -> str:
    if ats == GREENHOUSE:
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    if ats in (LEVER, LEVER_EU):
        return f"https://{HOSTS[ats]}/v0/postings/{token}?mode=json"
    raise ValueError(f"unsupported ATS {ats!r}")


#: The field allow-list, applied at parse time and fail-closed: `parse()` builds
#: each posting from exactly these keys, picked by name, so an unrecognised field
#: in the payload - including one a schema change adds tomorrow - is never
#: carried. `commitment` and `team` are short structured category labels; they
#: are not stored verbatim. `commitment` feeds only the derived remote flag (the
#: derived employment type is not stored, pending the Source Scout, PC-5). Never stored, in any form: the description body or any other prose,
#: recruiter / hiring-manager / contact names, emails or phone numbers,
#: compensation text, candidate data.
PAYLOAD_FIELDS = (
    "job_id", "title", "location", "url", "created_at", "updated_at",
    "commitment", "team",
)


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _epoch_ms_to_iso(value: Any) -> str:
    """Lever timestamps are epoch milliseconds; store them as ISO-8601 UTC."""
    if value in (None, ""):
        return ""
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ats: str, payload: Any) -> List[Dict[str, str]]:
    """Return a list of dicts keyed by exactly `PAYLOAD_FIELDS`.

    Raises ValueError on a payload shape we do not recognise, so a silent
    schema drift becomes a visible failure rather than an empty board.
    """
    ats = _PARSE_AS.get(ats, ats)
    if ats == GREENHOUSE:
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ValueError("greenhouse payload has no 'jobs' key")
        out = []
        for job in payload["jobs"]:
            location = (job.get("location") or {}).get("name")
            out.append(
                {
                    "job_id": _clean(job.get("id")),
                    "title": _clean(job.get("title")),
                    "location": _clean(location),
                    "url": _clean(job.get("absolute_url")),
                    # The job-board list endpoint carries no creation date.
                    "created_at": "",
                    "updated_at": _clean(job.get("updated_at")),
                    "commitment": "",
                    "team": "",
                }
            )
        return out

    if ats == LEVER:
        if not isinstance(payload, list):
            raise ValueError("lever payload is not a list")
        out = []
        for job in payload:
            cats = job.get("categories") or {}
            out.append(
                {
                    "job_id": _clean(job.get("id")),
                    "title": _clean(job.get("text")),
                    "location": _clean(cats.get("location")),
                    "url": _clean(job.get("hostedUrl")),
                    "created_at": _epoch_ms_to_iso(job.get("createdAt")),
                    "updated_at": _epoch_ms_to_iso(job.get("updatedAt")),
                    "commitment": _clean(cats.get("commitment")),
                    "team": _clean(cats.get("team")),
                }
            )
        return out

    raise ValueError(f"unsupported ATS {ats!r}")


def posting_id(ats: str, token: str, job_id: str) -> str:
    """The Bet-B posting key."""
    return f"{ats}:{token}:{job_id}"
