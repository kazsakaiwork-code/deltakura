# Bet B — Japan-hiring company registry and first_seen collector

The product is the **date our crawler first saw a job posting**. Nobody
publishes that, and it cannot be backfilled: a posting that appears and closes
before we look at the board leaves no trace anywhere. Everything else here
exists to make that date trustworthy.

## Read this first: cleared, and still held

The clearance matrix records **`reuse_allowed=Y`** for both the Greenhouse Job
Board API and the Lever Postings API, resolved on 2026-09-22. Greenhouse publishes no website terms of use at all, Lever's bind
"a customer" rather than a third-party reader, and both operators' own docs say
the GET endpoints are public and keyless. So the *legal* gate is open.

The *operational* gate is not. Publishing anything derived from these boards is
its own decision, and it has not been taken, so the collector holds itself back:

* `ats.py` exposes `BET_B_LIVE`, an environment flag that **defaults to false**;
* while it is false, every clearance is taken out with an explicit hold, so it
  reports `tos_status=cleared-held` and `publishable=False`;
* `require_publishable()` refuses a held clearance, so nothing here can reach a
  publishable store;
* the store lives outside the repository (`$DELTAKURA_DATA_DIR/ats/`), so
  nothing here reaches a public repository either.

`BET_B_LIVE=true` is the go-live switch. It is one grep-able line, deliberately,
so a reviewer can see when Bet B changed state and tie it to the approval.

Conditions the Scout attached to the Y verdict hold regardless of the flag:
store facts only and never a description body; drop every person field at
ingest; carry attribution and a non-affiliation line on every surface; honour a
company opt-out within 72 hours; never feed this corpus to model training; and
re-verify robots.txt and the operators' legal pages, halting on change. Lever's
terms additionally bar benchmarking use, so Deltakura publishes no
ATS-versus-ATS comparison derived from these calls.

Ashby is on hold (its job-board host disallows `/api/` while its API host serves
no robots.txt at all). SmartRecruiters is excluded outright — its robots.txt
allows LinkedInBot and nothing else, which is a deliberate allow-list.

## The three files

| File | What it is |
|---|---|
| `candidates.csv` | unverified leads from web search and public Japan job lists. Third-party claims; **not** a source of truth. |
| `verify.py` | asks each board's own endpoint whether the token resolves and whether it has Japan-located postings. |
| `registry.csv` | the verified output: companies that demonstrably had a Japan-located posting on the day we checked. |

A wrong candidate token costs exactly one request and then disappears — it 404s
or returns an empty board and never reaches `registry.csv`. That is why the
candidate list can be gathered cheaply and broadly: verification, not research,
is what makes the registry true.

`registry.csv` columns: `ats, board_token, company_name, company_domain,
hq_country, japan_hiring_evidence, source_url, checked_on`. The evidence field
records what was actually observed — the number of Japan-located postings and a
sample location string — not a claim from a web page.

## Why a registry is needed at all

The first prototype established that the ATS payloads cannot tell you the
country. The list endpoints return one free-text location per posting, and on
Japanese boards that string is frequently a work style rather than a place: 83
of 85 postings on one board said "Hybrid". Country attribution therefore comes
from the curated registry, and `japan.py` answers only the narrower question
"does this posting's location text name a place in Japan?".

`japan.py` is deliberately strict: a bare "Remote" or "Hybrid" is **not** Japan
even on a Japanese company's board, and "Japantown, San Francisco" is not Japan
either. Both are tested.

## Running it

```bash
# 1. verify the candidates -> registry.csv     (one request per candidate)
python crawlers/ats_registry/verify.py --daily-cap 400

# 2. record first_seen / last_seen per posting (one request per registry board)
python crawlers/ats_registry/collect.py --daily-cap 400
python crawlers/ats_registry/collect.py --japan-only   # store only Japan roles
```

`verify.py` keeps `verification_results.csv` beside the registry so a later run
can tell "not checked yet" from "checked and rejected" without re-requesting a
board. `--recheck` forces a re-probe.

## Output: `data/ats/jobs.csv`

Key: `<ats>:<company_slug>:<ats_job_id>`.

`first_seen_at` is written once and never revised — a retitled posting keeps its
original date. **Every surface must say "first seen by Deltakura on …", never
"posted on …".** We do not know when it was posted; we know when we first saw
it.

**Closed rule.** A posting absent from **two consecutive successful** crawls of
its board is closed, with `closed_at` set to the first crawl that missed it. A
failed crawl never closes anything, and the closing pass only considers boards
that this run actually crawled successfully — so a network outage cannot mark a
whole company's jobs as closed.

**Never stored:** the job description body, recruiter or hiring-manager names,
contact email addresses, salary free text. `ats.py` drops them at parse and
`crawlers/tests/test_parsers.py` asserts a Lever `description` field does not
survive into a record.

## Two things that bit us, recorded so they do not bite again

**1. A 304 is not the same as "we have the data".** `verify.py` primes the HTTP
ETag cache before `jobs.csv` exists, so the first `collect.py` run got 304s for
boards it held no records for and recorded nothing. The HTTP cache and the
record store are separate things. `collect.py` now re-asks without a validator
when it holds zero postings for a board — and a 304 for a board it *does* hold
is correctly treated as a successful crawl in which everything was still
present.

**2. Lever runs a separate EU instance.** `jobs.eu.lever.co` boards are not
reachable on `api.lever.co`; a board lives on one instance or the other. They
are a distinct `ats` value (`lever_eu`) rather than a fallback, so the request
count stays honest per host.

## Request cap for verification sweeps

Our own crawl rules cap prototype crawling at **20 requests per host per day** until
the bot contact URL resolves. Verifying 235 candidates and then tracking 135
boards cannot be done inside 20 requests on two hosts.

This run used roughly **400 requests to `boards-api.greenhouse.io`** and **220
to `api.lever.co`** in one day, set explicitly via `--daily-cap` on the command
line rather than silently in code, so the deviation is visible in the shell
history and in `source.yaml`. Everything else held: ≥ 2 s between same-host
requests, one connection, no parallelism, an identifying User-Agent, conditional
requests, and robots.txt obeyed.

The cap is tied to the contact URL. The User-Agent is now `DeltakuraBot/0.1
(+https://github.com/kazsakaiwork-code/deltakura)`, which resolves as soon as the repository is
public, with no code change. **Until it resolves, further large
verification passes should wait.** The nightly per-board refresh is one request
per board and is not the problem; the bulk verification sweep is.

## Known limits

* `hq_country` and `company_domain` come from the candidate research, not from
  the board, and are the least reliable columns here. `japan_hiring_evidence`
  and `checked_on` are the verified ones.
* "Japan-hiring" means *on the day we checked*. A company with a seasonal
  Tokyo role drops out of the registry when the role closes. Re-run `verify.py`
  periodically; `checked_on` is the freshness date.
* Greenhouse has low penetration among Japan-headquartered companies. Rakuten,
  Money Forward, freee, SmartHR, Cybozu, Sansan, DeNA, ZOZO, Cookpad, SmartNews,
  LayerX and others are not on it at all, and Mercari's Japan hiring is in-house.
  The bulk of Japan hiring visible on these two ATSs is foreign companies
  staffing Japan offices — which is a finding about the market, and a limit on
  what this index can claim to cover.
* Board tokens rarely match company names (Miro is `realtimeboardglobal`, Unity
  is `unity3d`, Sony Interactive is `siei`, Polyphony Digital is `pdi`). Guessing
  is not a discovery strategy; the token has to be seen in a URL.
