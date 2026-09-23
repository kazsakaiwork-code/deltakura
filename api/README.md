# Deltakura v0 read API

A Cloudflare Worker over Japanese public open data. Read-only, except for one
anonymous counter.

> **Status: not deployed.** No Cloudflare account exists yet, so every id in
> `wrangler.toml` is a placeholder and `npm run deploy` deliberately fails. `wrangler dev --local`
> runs the whole thing offline against data bundled at build time.

## Endpoints

| Method | Path | What it returns |
|---|---|---|
| `GET` | `/v0/health` | liveness, which data sources and bindings are live, build metadata |
| `GET` | `/v0/procurement/stats` | national procurement award statistics |
| `GET` | `/v0/corporate/{corporate_number}` | one 法人番号: name, address 都道府県, 法人種別, latest change |
| `GET` | `/v0/corporate/diff-summary` | daily register-change counts over a range |
| `GET` | `/v0/feeds/nta-diff.json` | JSON Feed 1.1 of daily change volume |
| `POST` | `/v0/intent` | record one pay-intent click (the Gate-1 metric) |
| `GET` | `/v0/intent` | the intent counters, for the KPI dashboard |

`GET /` and `GET /v0` return the endpoint index.

### `GET /v0/procurement/stats`

`fiscal_year`, `fiscal_year_from`, `fiscal_year_to`, `sector`, `winner_prefecture` (name, short name
or 2-digit code), `include_buckets`, `bucket_limit`, `lang=en|ja`.

```bash
curl 'http://localhost:8787/v0/procurement/stats?fiscal_year=2025&winner_prefecture=東京都&bucket_limit=3'
```

Same engine as the MCP tool `jp_procurement_stats` — literally the same module — so the API and the
MCP server cannot drift into answering one question two ways. 落札率 is not available and the
response says why; quartiles spanning more than one bucket are estimated and labelled as such.

### `GET /v0/corporate/{corporate_number}`

`history` (0–100, default 20), `lang`. `404` with `found: false` when no change was published for
that number inside the collected window — which is not the same as the corporation not existing, and
the response says so.

### `GET /v0/corporate/diff-summary`

`from`, `to` (ISO dates, ≤ 400 days apart), `group_by=change_kind`, `lang`. Dates with no published
file are reported as `days_without_file`, never as zero.

### `POST /v0/intent`

```bash
curl -X POST http://localhost:8787/v0/intent \
  -H 'content-type: application/json' \
  -d '{"product":"bet_a_report","kind":"click","client_id":"<random id from localStorage>"}'
```

`product` must be on the allowlist (`GET /v0/health` lists it); an unknown product is a `400`, so a
stray caller cannot invent KV keys. `kind` is `click` (default) or `view` — the two together give
`intent_rate = clicks / views`. `client_id` is a random id the page keeps in `localStorage`
(≥ 8 characters); without one the caller's IP hash is used instead. There is no cookie, and no raw
IP or raw client id is ever stored: both are hashed with a daily rotating salt.

One distinct client is counted once per product, per kind, per day.

## Two data sources

| | Production | Local dev and fallback |
|---|---|---|
| Procurement statistics | R2 `signals-archive/stats/procurement-stats.json` | `src/data/procurement-stats.json`, bundled |
| Corporate register | D1 `signals` (`migrations/0001_init.sql`) | `src/data/nta-dev.json` (daily counts) + `src/data/nta-sample.json` (a fixed record sample) |

Both bindings are optional. With none attached — every run until a Cloudflare account exists — the Worker answers from
the bundled JSON and says `data_source: "bundled"` in the response and in `/v0/health`. A corporate
lookup against the sample additionally sets `complete: false` and adds a note, because a miss against
a sample is not evidence of anything.

`DELTAKURA_MODE` selects: `dev` (bundled) or `prod` (bindings, falling back to bundled).

## Free-tier discipline

The Workers free plan allows about 100k requests a day and roughly 1,000 KV writes a day per
namespace, so:

- **Counters are never written per request.** They accumulate in memory and are flushed at most once
  a minute per key. Twenty clicks in a minute cost two KV writes, not forty.
- **De-duplication costs one write per distinct client, per product, per kind, per day** — bounded by
  real visitors, not by traffic.
- **Rate limiting writes nothing at all.** It is an in-isolate sliding window (default 60 req/min for
  reads, 20 for `/v0/intent`), keyed by a salted hash of the client IP that dies with the isolate. It
  is therefore best-effort against one noisy client rather than a distributed quota — the right trade
  while the whole service is free and read-only. If abuse ever justifies a real quota, Cloudflare's
  Rate Limiting binding replaces `src/http.ts` without touching a route.

## Local development

```bash
npm install
npm run build-data     # regenerate src/data/*.json from ../data/published/
npm test               # vitest, fully offline, no wrangler and no account
npm run typecheck
npm run dev            # wrangler dev --local, http://localhost:8787
npm run dry-run        # wrangler deploy --dry-run: bundles, uploads nothing
```

`npm run dev` and `npm run dry-run` need no Cloudflare account: `--local` simulates KV, D1 and R2 on
disk under `.wrangler/`. The KV namespace ids in `wrangler.toml` are placeholders and are ignored in
local mode.

`build-data` copies `../data/published/pportal/procurement-stats.json`, which
`../mcp/scripts/build-data.mjs` produces, so there is exactly
one definition of that file's format. The Bet-C daily counts come from
`../data/published/nta/summary.json`, whose `records` field is the **deduplicated** count and is the
single authority for every public surface — the site, the RSS feed, this Worker and the MCP server
all read it, so they cannot report different totals.

**`build-data` is deterministic and needs nothing but this repository.** Neither
output carries a build timestamp; provenance is `source_version`, the content
hash of the input it was built from. Running it on a clean checkout rewrites the
same bytes, which is what CI asserts with `npm run build-data && git diff --exit-code`.

The bundled corporate *sample* is the one thing that cannot be derived from
`data/published/`, which holds counts and no records. It is therefore a **fixed
file**, `src/data/nta-sample.json`, committed once and left alone by a normal
build. Regenerate it deliberately, on a machine that holds the private record
store:

```bash
node scripts/build-data.mjs --refresh-sample          # needs $DELTAKURA_DATA_DIR
node scripts/build-data.mjs --refresh-sample --sample 500
```

Its rule is stated at the top of `scripts/build-data.mjs`: checksum-valid
corporate numbers, corporate or public-body 法人種別 only, sorted by
(file_date, corporate_number, sequence_number), first N rows, SAMPLE_FIELDS
columns only. Two machines holding the same archive produce the same file.

### D1

```bash
wrangler d1 migrations apply signals --local     # offline
wrangler d1 migrations apply signals --remote    # needs a Cloudflare account
```

The schema is the response allowlist and nothing else: there is no 担当者, no 氏名, no 代表者, no
street-level address and no free-text 変更事由 column, so a personal string cannot be stored here,
let alone served.

## Before this can be deployed

1. **The GitHub repository** (`https://github.com/kazsakaiwork-code/deltakura`), so CI runs. Done; public visibility is a separate approval.
2. **A Cloudflare account and API token.** Then: create `KV_INTENT`, `KV_METRICS`, D1
   `signals`, R2 `signals-archive`; replace every `REPLACE_WITH_*` in `wrangler.toml`;
   `wrangler secret put IP_HASH_SALT`.
3. **A per-item operator approval for the first deploy.** Nothing here deploys itself.

All three are pending operator approval. The custom route `api.deltakura.dev/v0/*`
additionally waits for the domain, which is a later phase.

> Deltakura is an unofficial archive. It is not affiliated with, endorsed by or
> connected to デジタル庁, 国税庁 or any other government body.

## Attribution

```
出典：調達ポータル（https://www.p-portal.go.jp/）
出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）
```

政府標準利用規約（第2.0版）and 公共データ利用規約（第1.0版）respectively; both permit commercial
reuse with source indication and require modification to be declared. Every response carrying a
number carries its licence, attribution and caveats.
