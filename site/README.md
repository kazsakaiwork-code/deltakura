# Deltakura — static site

The public site (Japanese primary, English parity), generated from
`data/published/` by one standard-library Python script. No
framework, no build server, no package manager, no external assets, no
trackers, no paid service. The output is plain HTML/CSS/JS that Firebase
Hosting serves as static files.

The rules this generator is written against — the publication boundary, the
attribution requirements, the no-email rule and the data caveats — are stated
in the root `README.md` and enforced by the invariant checks below.

## Build and preview

```bash
python site/build.py            # build into site/public/ (about 6 s)
python site/build.py --clean    # wipe site/public/ first
python site/build.py --no-check # skip the invariant checks
```

Then either:

```bash
# plain Python, no Firebase CLI needed
python -m http.server 8765 --directory site/public
# → http://localhost:8765/

# or the real Hosting emulator, which also applies firebase.json headers
cd site && firebase emulators:start --only hosting
```

## What gets generated

| Path | Count | What |
|---|---|---|
| `public/index.html` | 1 | bilingual root, links to both language trees |
| `public/{ja,en}/index.html` | 2 | home: hero line, the 40-day strip, three facts, three product cards, the flow diagram, "what we do not do" tiles, developer doors |
| `public/{ja,en}/bet-a/index.html` | 2 | 年度 × セクター heatmap (classes h0..h6), every cell a link, row-total bars |
| `public/{ja,en}/bet-a/<sector>-fy<year>.html` | 338 | one page per 府省セクター × 年度 |
| `public/{ja,en}/bet-c/index.html` | 2 | 法人番号 差分 archive: the 40-day strip, daily chart, breakdown bars, schema in `<details>` |
| `public/{ja,en}/pricing.html` | 2 | one line: 有料プランはまだ開設していません。/ Paid plans are not open yet. |
| `public/{ja,en}/privacy.html` | 2 | six promise tiles, then the facts (button, scripts, feed counting, anonymisation, numbers, sources, collection, removals) in `<details>` |
| `public/{ja,en}/articles/index.html` | 2 | the article list; with none, one plain line (EN: "Articles are in Japanese only.") |
| `public/{ja,en}/articles/<slug>.html` | 1 per article | an article from `site/content/articles/` (see **Articles**) |
| `public/{ja,en}/subscribe.html` | 2 | RSS, JSON Feed, MCP and GitHub tiles, one line each, URLs as select-all slips |
| `public/data/**.json` | 172 | a JSON endpoint per data page + two indexes + `site.json` |
| `public/feeds/nta-diff.xml` | 1 | weekly RSS of daily registry-diff counts |
| `public/feeds/articles.{xml,json}` | 2 | the articles as RSS 2.0 and JSON Feed 1.1 |
| `public/{robots.txt,sitemap.xml,llms.txt,404.html}` | 4 | crawl, index and AI-search surface |
| `public/assets/{style.css,intent.js,config.js}` | 3 | the only assets; nothing is loaded from a third party |

354 pages plus the articles, ~10.5 MB. The build fails if any single page exceeds
100 KB (a Firebase Spark-plan constraint) or the whole tree exceeds 50 MB.

## Inputs

| Input | Used for | In the repository? |
|---|---|---|
| `data/published/pportal/stats_v0.csv` | every Bet-A number: the prefecture breakdown (already carrying the R6 suppression), the per-sector-year suppressed count, and the parsed/normalized/retrieved-at provenance | **yes** |
| `data/published/nta/summary.json` | every Bet-C number: per-day deduplicated counts, process-code breakdowns, coverage and provenance | **yes** |
| `$DELTAKURA_DATA_DIR/pportal/normalized/*.csv.gz` | **quantile precision only** — exact pooled q1/median/q3 per sector-year instead of the estimator | no, and never |
| `$DELTAKURA_DATA_DIR/pportal/manifest.csv` | the file count shown in the method note | no, and never |

**Every published count comes from `data/published/`.** The private record store
may refine a quantile; it may never change a count or a total. That is
deliberate: a build in CI and a build on the collecting machine must produce the
same numbers. The one visible difference is that without the private store the
pooled quartiles are the documented mixture estimate, and the pages say so with
a dagger and set `estimated: true` in the JSON.

`DELTAKURA_DATA_DIR` defaults to `../data` relative to the repository root. The
build never reads `**/raw/`, `**/state/` or `**/lookup/`, and never touches the
network.

## Invariants the build enforces

`build.py --check` (on by default) fails the build, not just warns, when:

* a page has no `<title>`, or lacks a meta description in **both** languages;
* a data page has no schema.org `Dataset`/`DataCatalog` JSON-LD block;
* **any generated file that shows a figure derived from the sources, or that
  quotes an attribution string, carries fewer than all three parts the upstream
  licences require**: the source indication, the licence name and a modification
  notice. The scope is not the pages the build labels as data — it is every file
  in `public/`, so the home pages, `/data/index.html`, `llms.txt`, `site.json`
  and the RSS feed are held to it as tightly as a statistics page. A file is in
  scope if it contains `出典：` or one of the build's headline counts (the
  aggregated, parsed and normalised award counts and the two Bet-C totals;
  counts under 1,000 are too common a digit string to match safely). On a JSON
  file the three parts are looked for in the **values**, never in the field
  names, so a `"modified"` key with an empty or evasive value fails;
* any page exceeds 100 KB, or the tree exceeds 50 MB;
* any page contains a `<form>`, an `input[type=email]`, a `mailto:` link or
  anything matching an email address — this project sends and collects no
  email at all, and this is the mechanical guard on that rule;
* an article page lacks `Article` JSON-LD, the attribution block (and its three
  parts) or a notify button; an article file has malformed front matter, an
  unknown chart id, an image, or a chart the published data cannot draw.

## Articles

One Markdown file per article in `site/content/articles/` (UTF-8). The format,
the front-matter keys and the supported Markdown subset are documented at the
top of `site/articles.py`; `python site/tests/test_articles.py` tests them.
In short:

```markdown
---
slug: ministry-award-counts          # a-z, 0-9, hyphens; the file name is free
title: 省庁別に見る国の落札件数
date: 2026-10-05                     # datePublished
updated: 2026-10-06                  # optional dateModified
description: 一文の要約（一覧・検索結果・フィード）
lang: ja                             # ja or en
product: bet_a                       # optional: bet_a (default) or bet_c
draft: false                         # optional
sources:
  - 出典：調達ポータル（https://www.p-portal.go.jp/）
---
本文。{{chart:<id>}} は単独の行に。<!-- 編集メモは出力されない -->
```

Raw HTML is escaped, never passed through; images are refused. Charts come only
from `data/published/`, built by `article_charts()` in `build.py`:

| Placeholder | Figure |
|---|---|
| `{{chart:nta-strip}}`, `{{chart:nta-strip:<oldest-listed>:<checked-on>}}` (alias `nta-40day-strip`) | the 40-day strip; the optional dates mark kept days the publisher no longer lists |
| `{{chart:nta-daily}}` | records per publication day |
| `{{chart:nta-daily-by-process}}` | the same, stacked by 処理区分, with the daily mean |
| `{{chart:nta-prefecture-top10}}` | top 10 prefectures, full table in `<details>` |
| `{{chart:bet-a-heatmap}}`, `{{chart:bet-a-heatmap:<fy>:<fy>}}`, `{{chart:awards-sector-fy-heatmap}}` (FY2014-FY2025) | year x ministry heatmap, rows by total |
| `{{chart:bet-a-years}}`, `{{chart:bet-a-years:<sector-slug>}}` | awards per fiscal year |
| `{{chart:bet-a-sectors}}`, `{{chart:bet-a-sectors:<fy>}}` | awards by sector |
| `{{chart:bet-a-median}}`, `{{chart:bet-a-median:<fy>}}` | median award price by sector |
| `{{chart:bet-a-prefs}}`, `{{chart:bet-a-prefs:<fy>}}` | awards by winner prefecture, top 10 |
| `{{chart:bet-a-box:<sector-slug>:<fy>}}` | one page's box plot |

A chart the published data cannot draw (today: `award-month-share`, which needs
award counts by month) fails the build with the reason instead of being drawn
from anywhere else. Every article page carries the attribution block, `Article`
JSON-LD, the notify button of its `product` (an allowlisted intent id, so the
intent contract is unchanged) and the feed links. The home page shows the three
newest articles of its language; with none the row is not rendered, and "記事"
joins the navigation only in a language that has an article.

`DELTAKURA_ARTICLES_DIR` points the build at another directory (a draft set or
a test fixture).

## Feeds

Every feed link, `<link rel="alternate">` and feed self URL points at the
Worker, which serves the feed and counts the fetch:

| URL | What |
|---|---|
| `https://deltakura-api.deltakura.workers.dev/v0/feeds/articles.xml` | articles, RSS (relayed from `public/feeds/articles.xml`) |
| `https://deltakura-api.deltakura.workers.dev/v0/feeds/articles.json` | articles, JSON Feed 1.1 (relayed) |
| `https://deltakura-api.deltakura.workers.dev/v0/feeds/nta-diff.xml` | registry diff, weekly RSS (relayed) |
| `https://deltakura-api.deltakura.workers.dev/v0/feeds/nta-diff.json` | registry diff, daily JSON Feed (generated by the Worker) |
| `https://deltakura-api.deltakura.workers.dev/v0/feeds/stats` | daily fetch counts per feed |

The static files stay on Hosting and are the copies the Worker relays, so an
old subscription to `/feeds/nta-diff.xml` keeps working (it is just not
counted). Counting rules: `api/README.md`, "Feed fetch counting".

## Design

Brief: `ops/design/site_redesign_v1.md` (token system, the 40-day strip,
icons, wireframes, the no-excuses rule). Code is split into three stdlib
modules next to `build.py`:

* `theme.py` - the one stylesheet (漆喰/墨/なまこ/薄墨/青磁/朱印 tokens, light
  default and dark via `prefers-color-scheme`, system 明朝/gothic/mono stacks,
  `.w0`..`.w100` width classes so bars need no inline style);
* `icons.py` - the in-house 24 px line icon set (`currentColor`, aria-hidden,
  always next to a text label);
* `charts.py` - the 40-day strip, card thumbnails, box plot, daily chart and
  HTML bars. Most SVGs use percentage x and pixel y with no `viewBox`, so they
  fit any width at 360 px while their text stays at real font size.

No webfont, no image file (the favicon is an inline data-URI SVG), no inline
style or script, no JavaScript except the intent counter. Every chart has
`role="img"` with `<title>`/`<desc>` and its numbers as text beside it.
`prefers-reduced-motion` turns off the single strip reveal.

## The intent button and its contract with the Worker

`assets/intent.js` records a click, and the fact that a page carrying a button
was seen. Nothing else. There is no email field, no name field and no form
anywhere on the site.

The POST body is exactly what `api/src/routes/intent.ts` reads:

```json
{"product": "bet_a_report", "kind": "click", "client_id": "<random>"}
```

* **`product`** must be one of the Worker's allowlisted ids, listed as
  `INTENT_PRODUCTS` at the top of `build.py`. `intent_button()` fails the build
  on anything else, and `api/test/intent-contract.test.ts` parses this file and
  drives the real Worker with the ids it finds. Anything unlisted is answered
  `400 unknown_product`.
* **`kind`** is `click` or `view`. One `view` per product per page load is what
  makes `intent_rate_14d` computable at all:
  without it there is no denominator.
* **`client_id`** is a random string in `localStorage`, so a second click from
  the same browser is not double-counted. The Worker stores only a salted hash
  of it; no cookie, no raw IP, no profile.

The endpoint is live: `INTENT_ENDPOINT` in `build.py` is
`https://deltakura-api.deltakura.workers.dev/v0/intent` (written into
`assets/config.js`). With the endpoint unset the button would confirm locally and
post nothing.

**Counting rule.** The Worker de-duplicates to one count per client, per product,
per kind (`view` / `click`), per UTC day. A client is only a random id the browser
mints itself, so the count is reported as an **upper bound** on interested people,
never as an exact figure; `/privacy.html` (methodology) says the same. See
`api/README.md` for the known sources of over- and under-counting.

## Deploying (once a Firebase project exists)

```bash
python site/build.py --clean
cd site
firebase use <project-id>       # or edit .firebaserc
firebase deploy --only hosting
```

`firebase.json` sets `public: "public"`, `cleanUrls: false` (links are written
with their real `.html` paths), CORS and JSON/RSS content types on `/data/**`
and `/feeds/**`, and a CSP with `default-src 'none'` that allows scripts and
styles from self plus `connect-src 'self' https://deltakura-api.deltakura.workers.dev`
for the intent POST: exactly the Worker host, no wildcard. `INTENT_ENDPOINT` and
this CSP change together or the button fails silently in the browser.

Always deploy with the project named explicitly:
`firebase deploy --only hosting --project deltakura-signals`.

**Deploying is a per-item operator approval.** Nothing here may be published
without it. This directory is part of the `kazsakaiwork-code/deltakura` monorepo, and the
root `.gitignore` excludes the generated `public/` tree.

Because `public/` is generated and not committed, a deploy always builds first.
Build it where the private record store is, or accept the estimated quartiles
described under **Inputs**.

## Placeholders that need a real account

Every one is marked `TODO` at the top of `build.py`. All of them are pending
operator approval.

| Constant | Current value | Unblocked by |
|---|---|---|
| `BASE_URL` | `https://deltakura-signals.web.app` | the Firebase project — feeds canonical URLs, sitemap, JSON-LD |
| `GITHUB_ORG` / `GITHUB_ISSUES` / `GITHUB_CORE` / `GITHUB_SITE` | `github.com/kazsakaiwork-code/deltakura/…` | set. They resolve for everyone once the repository is made public. |
| `INTENT_ENDPOINT` (`assets/config.js`) | `https://deltakura-api.deltakura.workers.dev/v0/intent` | set (Worker live since 2026-09-23) |
| `.firebaserc` `projects.default` | `deltakura-signals` | the Firebase project (the only project id the security scan accepts) |

There is **no analytics beacon**, and both `/privacy.html` pages say exactly
that: page visits are not counted, the only scripts are the two self-hosted
files, and the CSP allows scripts from `self` only. If cookie-less analytics
(Cloudflare Web Analytics) is ever added, the privacy page and the `firebase.json`
CSP must be updated **in the same change that adds the script tag** — the page
promises that, so the order is not optional. The same rule covers feed
counts and the intent button. Feed fetches through the Worker are counted as
daily totals per feed (salted hash of User-Agent and IP /16, kept at most 25
hours, no IP stored), and the privacy page says so in one line. The intent
button is live and counts one view and one click per client, product and UTC
day at the Worker; the privacy page describes exactly that, including what is
sent and what is stored. Feed links are navigation, not `fetch()`, so the CSP
did not change for them.

`/privacy.html` carries **`Last updated: <build date> UTC`**, and that is a
deliberate choice, not a leftover. The date is the date of the build that
produced the page: `public/` is generated on every deploy and is never
committed, so a build date cannot drift away from the content it describes and
nothing has to be maintained by hand. It is not derived from `source_version` —
that field is a SHA-256 of the input, not a date — and the retrieval dates of
the source data are a different thing, shown in the sources table and on every
statistics page. The clock is spelled out because it is UTC, like every other
timestamp this build writes (`generated_at`, the sitemap's `lastmod`,
`llms.txt`): a build run on a Japanese morning still carries the previous UTC
day, and an unlabelled date invites the reader to call it stale.

## Data caveats this site is required to surface

These are not presentation choices; removing any of them makes the page wrong.

1. **There is no 落札率.** The national source publishes no 予定価格. Every
   Bet-A page carries the label 「予定価格: 非公表」 and every Bet-A JSON file
   sets `award_ratio.available: false`.
2. **Prefecture = the winner's registered head office**, joined via 法人番号 to
   the NTA registry, not the place of performance. It over-weights Tokyo.
3. **Sector = which ministry bought**, derived from the 府省 code. It is not an
   industry classification; 「防衛」 describes the buyer.
4. **Counts do not reconcile across layers, on purpose**: 313,607 rows parsed →
   313,568 after deduplication → 49 awards held out of the prefecture
   breakdowns by the R6 k-anonymity rule. Each page states the gap where it
   applies; `data/bet-a/index.json` carries all four numbers.
5. **FY2013–FY2015 is the publisher's ramp-up**, not a measurement of
   procurement volume (FY2013 contains one record).
6. **Bet B is collected but not published.** Its sources became
   `reuse_allowed=Y` on 2026-09-22, but going live is a separate approval that
   has not been given, so the collector
   runs with `BET_B_LIVE=false` and the site carries a status card and an intent
   button and zero records.
