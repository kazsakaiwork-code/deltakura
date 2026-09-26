# Deltakura v0 read API

A Cloudflare Worker over Japanese public open data. Read-only, except for two
anonymous counters: pay intent (`/v0/intent`) and feed fetches (`/v0/feeds/*`).

> **Status: live** at `https://deltakura-api.deltakura.workers.dev` (environment
> `production`, dedicated Cloudflare account on Workers Free, deployed 2026-09-23).
> Only the production environment carries real resource ids; the top level and
> `staging` keep `REPLACE_WITH_*` placeholders. `npm run deploy` still fails on
> purpose. `wrangler dev --local` runs the whole thing offline against data bundled
> at build time.

## Endpoints

| Method | Path | What it returns |
|---|---|---|
| `GET` | `/v0/health` | liveness, which data sources and bindings are live, build metadata |
| `GET` | `/v0/procurement/stats` | national procurement award statistics |
| `GET` | `/v0/corporate/{corporate_number}` | one 法人番号: name, address 都道府県, 法人種別, latest change |
| `GET` | `/v0/corporate/diff-summary` | daily register-change counts over a range |
| `GET` | `/v0/feeds/nta-diff.json` | JSON Feed 1.1 of daily change volume (counted) |
| `GET` | `/v0/feeds/nta-diff.xml` | the site's weekly RSS, relayed (counted) |
| `GET` | `/v0/feeds/articles.xml` | the site's article RSS, relayed (counted) |
| `GET` | `/v0/feeds/articles.json` | the site's article JSON Feed, relayed (counted) |
| `GET` | `/v0/feeds/stats` | daily fetch counts per feed |
| `POST` | `/v0/intent` | record one pay-intent click |
| `GET` | `/v0/intent` | the aggregate intent counters |

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

#### Counting rule: an upper bound, not an exact figure

The intent count is **de-duplicated per client, per product, per kind, per UTC day,
and reported as an upper bound** on the number of interested people. It is never
presented as exact, in the Gate-1 report or anywhere else. Why it can be high:

- `client_id` is minted by the client. Another browser, cleared storage or a script
  counts again; the per-isolate rate limit (20/min) slows a script, it does not stop one.
- De-duplication is an in-isolate memory set plus a KV key. KV is eventually
  consistent, so the same client posting twice within seconds to two isolates
  can be counted twice. The site posts each click at most once per browser, which
  keeps this rare.

Why it can be slightly low: counters are flushed with a KV read-then-write, so two
isolates flushing the same key in the same instant can lose one increment. At current
traffic this is negligible, and it does not change the rule: read the number as an
upper bound.

## Feed fetch counting

Every feed the site links to is served by this Worker, so a subscriber count
exists without a beacon (`src/feedcount.ts`, `src/routes/feeds.ts`).

- **Relay.** `nta-diff.xml`, `articles.xml` and `articles.json` are fetched from
  the static site (`SITE_ORIGIN`, default `https://deltakura-signals.web.app`,
  fixed paths only), memoised for 10 minutes per isolate, served with an `ETag`
  (a matching `If-None-Match` gets `304`). If the site cannot be reached and
  nothing is memoised, the answer is a `302` to the static file, so a reader
  never gets an error and keeps the Worker URL. `nta-diff.json` is generated
  here as before.
- **What is counted.** Per feed and UTC day: `requests` (every GET),
  `distinct_fetchers` (a salted SHA-256 of the normalised User-Agent plus the
  address coarsened to /16 for IPv4 or /48 for IPv6; the salt is
  `IP_HASH_SALT` plus the day), `crawler_fetchers` (known search and AI
  crawlers, kept apart), and `reported_subscribers` (the "N subscribers" an
  aggregator such as Feedly states in its User-Agent, as the day's maximum per
  UA family token). HEAD, `/v0/feeds/stats` and unknown paths are not counted.
- **What is stored.** In `KV_METRICS`: the de-duplication key
  `feed:dedup:<day>:<feed>:<hash>` for 25 hours, and the daily counters
  (`feed:count|crawl|req|rep:<feed>:<day>`) for 400 days. No raw IP address and
  no User-Agent string is stored; `rep` holds only the family token and a number.
  In production without `IP_HASH_SALT` the feed is still served and nothing is
  counted.
- **Write budget.** Counters are buffered in memory and flushed at most once a
  minute per key; a new fetcher costs one write per feed and day, capped at 400
  per isolate and day (beyond that, de-duplication is in memory only).
- **Reading it.** `GET /v0/feeds/stats?days=14` (1-31) returns daily rows per
  feed plus `readers_estimate` = distinct fetchers + the extra readers each
  reporting aggregator states. Like the intent count, read it as an estimate:
  one reader on two networks counts twice, many readers behind one aggregator
  that reports nothing count once.

## Two data sources

| | Production | Local dev and fallback |
|---|---|---|
| Procurement statistics | `src/data/procurement-stats.json`, bundled | the same bundled file |
| Corporate register | D1 `signals` (`migrations/0001_init.sql`), loaded by `scripts/d1-seed.mjs` | `src/data/nta-dev.json` (daily counts) + `src/data/nta-sample.json` (a fixed record sample) |

The procurement table is small and changes only when the data is rebuilt, so it
always ships inside the Worker bundle. There is no R2 bucket: R2 needs a paid
subscription, and it is a post-gate option that requires explicit approval
before it can come back (see [Cost guard](#cost-guard-free-tiers-only)).

Every binding is optional. With none attached — every local and test run — the Worker answers from
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

`npm run dev` and `npm run dry-run` need no Cloudflare account: `--local` simulates KV and D1 on
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
wrangler d1 migrations apply signals --local                        # offline
wrangler d1 migrations apply signals --remote --env production      # the live database
```

**What production D1 holds (loaded 2026-09-23).** Only data that is already public in
this repository, generated by `node scripts/d1-seed.mjs` and loaded with
`npx wrangler d1 execute signals --remote --env production --file .wrangler/d1-seed.sql`:

| Table | Rows | From |
|---|---|---|
| `nta_daily` | 40 (sum 89,885 = the published deduplicated total) | `src/data/nta-dev.json`, i.e. `data/published/nta/summary.json` |
| `nta_daily_kind` | 262 | same |
| `nta_change` | 1,500 | `src/data/nta-sample.json`, the committed fixed sample |
| `load_log` | 41 | provenance of the two loads |

Database size after load: 454,656 bytes (free limit 5 GB); the load cost 6,686 row
writes (free limit 100,000/day). The private record store is **not** loaded, so
production sets `CORPORATE_RECORDS = "sample"` and every lookup answers
`complete: false` with a note that a miss is not evidence. Daily counts are complete
for the covered window. Re-run the seed after `npm run build-data` to refresh the
counts; nothing refreshes D1 automatically yet.

The schema is the response allowlist and nothing else: there is no 担当者, no 氏名, no 代表者, no
street-level address and no free-text 変更事由 column, so a personal string cannot be stored here,
let alone served.

## Deployment

Done on 2026-09-23 with operator approval, on free-tier products only: KV
namespaces `KV_INTENT` and `KV_METRICS`, D1 `signals`, the `IP_HASH_SALT` secret
(`wrangler secret put IP_HASH_SALT --env production`, never committed), and the
Worker `deltakura-api` on the account's `deltakura.workers.dev` subdomain.

Redeploy (an operator action, never a script):

```bash
npx wrangler whoami                      # must show the Deltakura account
npx wrangler deploy --env production     # never without --env: the top level is dev-only
```

The custom route `api.deltakura.dev/v0/*` waits for the domain, which is a later
phase and a purchase.

**Deploy prerequisites that this repository cannot decide:** a deploy requires the
`IP_HASH_SALT` secret (set with `wrangler secret put IP_HASH_SALT`, per environment;
never committed), and the exact Cloudflare account and `workers.dev` subdomain, which
the operator chooses. The subdomain becomes part of the public API hostname, so it is
an operator decision, not a default.

## Cost guard (free tiers only)

Until a paid feature is explicitly approved, this Worker runs on free tiers only, and
CI enforces it. `.github/scripts/cost_guard.py` runs in the `security-scan` job on
every push and pull request, and fails the build on:

| Where | What fails | Why |
|---|---|---|
| any `wrangler.toml` / `wrangler.json(c)`, every environment | `r2_buckets`, `queues`, `durable_objects`, `hyperdrive`, `ai`, `browser`, `vectorize`, `containers`, `dispatch_namespaces`, `pipelines`, `images`, `tail_consumers`, `logpush`, `limits`, `usage_model`, `triggers` | paid, subscription-only or unattended features. R2 in particular makes a deploy fail until the account is subscribed, and subscribing bills the payment method on file |
| same | `workers_dev = false`, `route`, `routes`, `custom_domain` | a custom domain or route needs a registered domain, which is a purchase |
| same | the words `plan`, `plans` or `billing` outside a comment | plan and billing selection is never a config edit |
| same | any table or key not on the free-tier allowlist (`vars`, `kv_namespaces`, `d1_databases`, `observability`, `build`) | a new Cloudflare feature is refused until it is reviewed, not accepted until it is noticed |
| `package.json` scripts | `wrangler r2` (and `queues`, `hyperdrive`, `vectorize`, `ai`, `pipelines`, `containers`, `dispatch-namespace`) | paid products |
| same | `wrangler deploy`, `publish`, `versions deploy/upload` or `pages deploy` without `--dry-run` | a real deploy is a per-item operator action, never a script |
| dependencies in `package.json`, `package-lock.json` (including transitive) and `requirements*.txt` | Stripe, Paddle, Polar, Lemon Squeezy, PayPal, OpenAI, Anthropic, Google Gemini, Cohere, Mistral, Replicate, Apify (`apify-client`) SDKs | paid-API clients have no place in a free, read-only public-data service |

Run it locally with `python .github/scripts/cost_guard.py` from the repository root.
Loosening a rule is a reviewed change to that script, made only after the paid feature
itself has been approved.

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
