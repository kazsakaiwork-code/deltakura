# Deltakura — signals

**An unofficial, open archive of Japanese public data that the publishers themselves do not keep.**

Two datasets today:

- **Tender awards (Bet A).** National procurement award *statistics* built from 調達ポータル
  (p-portal.go.jp) open data, FY2013 onward. Statistics only: no winner directory is built, and
  no individual's name is held. 313,568 awards aggregated into 5,596 buckets.
- **Corporate registry diff (Bet C).** The 国税庁 法人番号公表サイト publishes a daily change file
  and deletes it after about 40 days. We fetch one file a night and keep it. Corporations and
  public bodies only; never an individual's name.

A third, **Japan hiring first-seen (Bet B)**, is collected but **not published**: go-live is a
separate approval, and the collector runs with `BET_B_LIVE=false` until then. Nothing derived from
job boards is in this repository.

> **Deltakura is not an official source.** It is not affiliated with, endorsed by, or connected to
> デジタル庁, 国税庁, or any other government body, nor to any applicant-tracking vendor. Where a
> number here disagrees with the publisher, the publisher is right.

---

## Layout

```
crawlers/          Python collectors. One directory per source.
                     _lib/       shared: politeness, anonymisation, the clearance gate, paths
                     pportal/    Bet A — 調達ポータル bulk award open data + stats_v0
                     nta_diff/   Bet C — nightly 法人番号 diff collector + published summary
                     ats_registry/ Bet B — public ATS boards (collected, not published)
                     tools/      maintenance scripts
                     tests/      `python crawlers/tests/run_tests.py`, no pytest needed
                     tos_matrix.csv   the source-clearance matrix the gate reads
data/published/    The published data layer. Aggregates, no records:
                     pportal/stats_v0.csv            Bet A statistics
                     pportal/procurement-stats.json  the compact table mcp/ and api/ ship
                     nta/summary.json                Bet C daily counts + provenance
                     nta/manifest.csv                Bet C collection manifest: file id,
                                                     date, sha256, bytes, row counts
site/              Static JA/EN site. `python site/build.py` → site/public/ (not committed)
mcp/               @deltakura/mcp — read-only MCP server (TypeScript, npm package)
api/               Cloudflare Worker: the v0 read API, the intent counter, the feeds
.github/workflows/ CI and the nightly Bet-C collection
```

`api/` imports `mcp/src/query/` directly (`api/src/shared.ts`), and `api/` and `site/` both read
`data/published/`. That is why this is one repository rather than four.

One more data file is committed and is **not** an aggregate:
`api/src/data/nta-sample.json`, a fixed 1,500-record sample of the corporate
registry diff that lets the Worker answer lookups offline. Registered
corporations and public bodies only — corporate numbers are not issued to sole
proprietors — published by the 国税庁 under 公共データ利用規約(第1.0版) with
redistribution permitted. It is regenerated only on request, never by a build
and never by the nightly job.

## Data directories

Two locations, deliberately separate:

| | Path | Committed | Contents |
|---|---|---|---|
| Published | `data/published/` | **yes** | aggregates only — counts, quantiles, per-day totals, provenance |
| Private | `$DELTAKURA_DATA_DIR`, default `../data` | **never** | publisher originals, the per-record normalized layer, crawl state, lookup tables, run logs |

`DELTAKURA_DATA_DIR` is resolved relative to the repository root, so a clone that sits next to its
collection directory needs no configuration. Set it explicitly anywhere else — CI does:

```bash
export DELTAKURA_DATA_DIR=/var/lib/deltakura/data
python crawlers/nta_diff/run_nightly.py
python crawlers/nta_diff/publish_summary.py     # -> data/published/nta/summary.json
```

Everything in this repository builds **without** the private directory, and builds
**reproducibly**: no generated file carries a build timestamp, so `npm run build-data`
on a clean checkout rewrites the same bytes and CI can prove it with
`git diff --exit-code`. Provenance is carried by `source_version` (a content hash of
the input) and by the publisher's own retrieval dates. Two things are degraded without
the private directory, and say so on the page: Bet-A pooled quartiles fall back to the documented mixture
estimator (`estimated: true`), and Bet-A provenance reports the aggregated count only, because the
publisher's own row count lives in the private manifest.

### One count, one authority

`data/published/nta/summary.json` carries, per day, both `records` (deduplicated)
and `records_raw` (the publisher's row count). **`records` is the authority.** The site, the RSS
feed, the Worker and the MCP server all read it, so they cannot report different totals for the
same day. The two numbers differ on three days in the current window; `count_rule` in that file
explains why, and the difference is published rather than hidden.

## How to run each part

```bash
# Collectors (Python 3.10+, `requests`)
python crawlers/pportal/collect.py              # Bet A: bulk award open data
python crawlers/pportal/stats.py                # -> data/published/pportal/stats_v0.csv
python crawlers/nta_diff/collect.py             # Bet C: today's registry diff
python crawlers/nta_diff/publish_summary.py     # -> data/published/nta/summary.json
python crawlers/tests/run_tests.py              # 77 tests, no network

# MCP server / npm package (Node 22+)
cd mcp && npm ci && npm run build-data && npm run build && npm test
node dist/cli.js                                # stdio MCP server

# Worker (Node 22+)
cd api && npm ci && npm run build-data && npm test && npm run dev   # wrangler dev --local

# Site (Python 3.10+, standard library only)
python site/build.py                            # -> site/public/, invariants checked
```

Nothing in this repository is deployed or published by running it. `npm run deploy` in `api/`
deliberately exits 1.

## Licences

**Code: MIT** (`LICENSE`). `mcp/` and `api/` carry their own copy because each is packaged
separately; `crawlers/` and `site/` are covered by the root file.

**Data: not ours to relicense.** Our derived aggregates are offered under **CC BY 4.0**, on top of
the upstream terms:

| Dataset | Source indication | Upstream licence |
|---|---|---|
| 調達ポータル 落札実績 | 出典：調達ポータル（https://www.p-portal.go.jp/） | 政府標準利用規約（第2.0版） |
| 国税庁 法人番号公表サイト 差分データ | 出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/） | 公共データ利用規約（第1.0版） |

Both datasets are used **in modified form** — parsed, deduplicated, anonymised and aggregated —
which both licences require to be declared. Both licences also require a source indication, so the
attribution string travels with the data: it is in every published file, every page, every feed
item and every API and MCP response. Reproduce it wherever you show a number from here.

## What this project will not do

- No individual's name, ever. Winners without a checksum-valid 13-digit 法人番号 are masked at
  ingestion, fail-closed, and buckets with fewer than three masked individuals are suppressed.
- No 落札率. 調達ポータル publishes no 予定価格, so the figure cannot be computed from this data,
  and nothing here claims otherwise.
- No email. There is no input field on the site, no newsletter and no outreach. Updates go out
  through RSS/JSON feeds and GitHub releases.
- No crawling against a publisher's terms. `crawlers/_lib/tos.py` reads `crawlers/tos_matrix.csv`
  and refuses any source not recorded as `reuse_allowed=Y` — there is no override flag.

## Corrections and removal requests

Open a GitHub issue on this repository. We commit to:

- removing anything that turns out to identify an individual **within 24 hours**;
- answering other requests within two business days;
- recording each decision in a public issue.

What can be removed is limited: the upstream sources are state-published open data, and removing a
record here does not remove it there. The full policy, in both languages, is on the site's privacy
page (`/ja/privacy.html`, `/en/privacy.html`). There is no email address, by design.

## Status

Nothing is deployed, published or listed yet. The repository lives at
https://github.com/kazsakaiwork-code/deltakura; the Firebase
project id, the Worker hostnames and the Cloudflare/D1/KV ids in `api/wrangler.toml` are
placeholders waiting on their approvals. The crawler User-Agent is
`DeltakuraBot/0.1 (+https://github.com/kazsakaiwork-code/deltakura)`; that URL resolves for everyone once the
repository is public, with no code change.
