# Deltakura — public-data collectors

Collectors for the three Phase-1 bets. Each one fetches a public, openly
licensed dataset, normalises it, anonymises it at ingest, and writes to the
private collection store. Nothing here logs in, creates an account, accepts
terms, spends money, publishes anything or sends a message.

**Where the output goes.** Two locations, deliberately separate:

* `$DELTAKURA_DATA_DIR` — the private store: publisher originals, the per-record
  normalized layer, crawl state, lookup tables, run logs. **Never committed.**
  It defaults to `../data` relative to the repository root.
* `<repo>/data/published/` — the aggregates the site, the Worker and the npm
  package are built from. (One more data file is committed and is not an
  aggregate: `api/src/data/nta-sample.json`, a fixed corporate-record sample the
  Worker answers offline lookups from.)
  `pportal/stats.py` and `nta_diff/publish_summary.py` are the two scripts that
  write there.

Clearance comes from `tos_matrix.csv` in this directory, a generated projection
of the maintainers' fuller internal survey (`tools/sync_tos_matrix.py`). It is
committed so that the fail-closed gate works in a standalone checkout. The
projection is deliberately narrow: the verdict columns only, with `license` and
`robots_ok` reduced to a controlled vocabulary, and with `url` narrowed to the
site host for any source that is not cleared. The survey notes behind a verdict
are working material and are not published.

| Directory | Bet | Source | Clearance | Output |
|---|---|---|---|---|
| `nta_diff/` | C | 国税庁 法人番号 差分 + 全件 | **Y** | private `nta/`: 89,885 deduplicated records over 40 days. Published: `data/published/nta/{summary.json, manifest.csv}` |
| `pportal/` | A | 調達ポータル 落札実績 open data | **Y** | private `pportal/`: 313,568 awards FY2013–FY2026. Published: `data/published/pportal/stats_v0.csv` |
| `ats_registry/` | B | Greenhouse + Lever public boards | **Y, held by `BET_B_LIVE=false`** | private `ats/` only: 135 companies, 23,540 postings. Nothing published. |
| `_lib/` | — | shared: HTTP, ToS gate, anonymiser, JP helpers, paths | — | — |
| `tools/` | — | maintenance: clearance-matrix projection | — | `tos_matrix.csv` |
| `tests/` | — | runnable test suite, no network | — | — |

## Requirements

Python 3.10+ and `requests`. Everything else is the standard library —
no pandas, no pyarrow, no BeautifulSoup. Normalised output is gzipped CSV rather
than parquet so the store stays readable with nothing installed.

```bash
python -m pip install --require-hashes -r crawlers/requirements.txt   # pinned, hash-checked
```

## Run everything

```bash
# 0. tests first — they enforce in code the rules stated in prose below
python crawlers/tests/run_tests.py

# 1. Bet C — nightly, and the 40-day backfill
python crawlers/nta_diff/collect.py --backfill
python crawlers/nta_diff/run_nightly.py            # what the scheduler calls

# 1b. refresh the published Bet-C summary. Counts only, no records. The site
#     and the Worker both read it, and it is the authority for every Bet-C
#     number on every public surface.
python crawlers/nta_diff/publish_summary.py

# 2. Bet A — 14 fiscal years, then the join table, then the statistics
python crawlers/pportal/collect.py
python crawlers/nta_diff/zenken.py --needed-from "$DELTAKURA_DATA_DIR/pportal/normalized"
python crawlers/pportal/stats.py               # -> data/published/pportal/stats_v0.csv

# 3. Bet B — verify the candidate boards, then record first_seen.
#    Cleared (reuse_allowed=Y) but HELD: BET_B_LIVE is false until go-live is
#    approved, so every clearance carries a hold and nothing can reach a
#    publishable store.
python crawlers/ats_registry/verify.py  --daily-cap 400
python crawlers/ats_registry/collect.py --daily-cap 400
```

Every script takes `--dry-run` or `--help`. All of them are idempotent: a
re-run costs a conditional request per file and rewrites nothing unchanged.

## Data dictionary

| Dataset | Schema | Description of every column |
|---|---|---|
| Bet A awards | `crawlers/pportal/source.yaml` | `crawlers/pportal/source.yaml`, `$DELTAKURA_DATA_DIR/pportal/README.md` |
| Bet A statistics | `crawlers/pportal/stats.py` | `$DELTAKURA_DATA_DIR/pportal/README.md` (`stats_v0.csv` section) |
| Bet B postings | `crawlers/ats_registry/source.yaml` | `crawlers/ats_registry/source.yaml` + `README.md` |
| Bet C registry diffs | `crawlers/nta_diff/source.yaml` | `crawlers/nta_diff/source.yaml` + `README.md` |
| Code tables (府省, 入札方式) | publisher spec | `crawlers/pportal/codes.py` |

Each source directory holds a `source.yaml` recording the publisher, the terms
URL, the licence, the required attribution string, the access method and the
fields the publisher does **not** provide. Read it before using a column.

## The four rules that are enforced in code, not in prose

**1. Source clearance is fail-closed.** `_lib/tos.py` reads
`crawlers/tos_matrix.csv` and is the only way a collector gets permission to
fetch. `reuse_allowed=Y` passes; `N` raises and has no override; `unclear` raises
unless the caller asks for *prototype* mode, which additionally requires an
affirmative robots verdict, marks every record `tos_status=unclear-prototype`,
and is refused by `require_publishable()`. A source with no row at all is
blocked.

On top of that legal gate there is an operational one. `clear(..., hold="...")`
returns a clearance that is legally cleared but **not publishable**. That is how
Bet B runs today: Greenhouse and Lever became `reuse_allowed=Y` on 2026-09-22,
but going live is a separate approval that has not been given, so
`ats_registry/ats.py` holds them behind
`BET_B_LIVE=false` and `require_publishable()` still refuses. A hold can only
make a clearance stricter; it can never rescue an `N` or an `unclear`.

**2. Politeness.** `_lib/http.py` implements the crawl rules in code: robots.txt fetched
per host and cached 24 h, a declared `Crawl-delay` raises ours, ≥ 2.0 s between
same-host requests with no parallelism, an identifying User-Agent with a contact
URL on every request including the robots fetch, conditional requests against an
on-disk cache, exponential backoff on 429/503, a host paused after three
consecutive failures, and a per-host daily request cap that persists across
restarts so a restart cannot reset the budget. There is no code path that logs
in, sends a credential or touches a CAPTCHA.

**3. Anonymisation is fail-closed.** `_lib/anonymize.py` implements R1–R6. The
decision is made by the *presence of a checksum-valid 法人番号*, never by how
corporate a name looks — so 「ヤマダ印刷」 with no number is masked and
「株式会社サンプル」 with a bad check digit is masked and flagged. Free text passes
a person-name detector. An aggregate keyed by a party name is refused outright.

**4. Paths are relative.** `_lib/paths.py` derives every location from its own
file position or from `DELTAKURA_DATA_DIR`. No absolute path, no machine name
and no personal identifier appears anywhere in this tree, and `paths.rel()`
keeps one out of the manifest columns too — this repository is public.

## Tests

```bash
python crawlers/tests/run_tests.py          # 77 tests, no network, ~1 s
python crawlers/tests/run_tests.py -v
python crawlers/tests/run_tests.py anonymize
```

* `test_anonymize.py` — the masking cases **T1–T8**, the check-digit algorithm
  against real published numbers, the R4 detector against real procurement
  titles (it must *not* fire on 「中村地区道路改良工事」), the R5 guard, and the
  R6 threshold.
* `test_politeness.py` — every politeness rule, driven against a stub HTTP session:
  a `Disallow: /` host is not fetched, robots is fetched once per host, the
  minimum delay is actually slept, a declared `Crawl-delay` wins, a 304 raises,
  an unchanged page is not re-fetched within 24 h, three failures pause the
  host, and the daily cap survives a process restart. Plus the ToS gate:
  `prototype=True` cannot force a `reuse_allowed=N` source, a source whose
  robots verdict is not affirmative cannot run even as a prototype, Bet B is
  cleared but held until go-live is approved, the User-Agent carries a contact
  URL, an uncleared row publishes no deep link, and the shipped clearance matrix
  still matches the internal copy.
* `test_parsers.py` — both collectors against fixtures taken from real payloads,
  the 府省/入札方式 code tables against the published spec, schema-drift
  detection for both ATS endpoints, and the Japan location classifier (a bare
  「Hybrid」 is not Japan; 「Japantown, San Francisco」 is not Japan).

One deliberate deviation is documented in the test file itself: the T1 case as
originally written supplies the corporate number `1234567890123` and calls it
valid, but those digits fail the NTA check digit. T1 uses a constructed valid
number and a separate test asserts why.

## Publication boundary

This repository (`kazsakaiwork-code/deltakura`) is the only thing that is ever published.
The maintainers' working material and the collection store both live outside it
and are never committed; the root `.gitignore` refuses `data/` except
`data/published/`, in case anyone ever points `DELTAKURA_DATA_DIR` inside the
checkout.

## Open items

1. **The User-Agent contact URL resolves only once the repository is public.** It is
   `DeltakuraBot/0.1 (+https://github.com/kazsakaiwork-code/deltakura)`: the repository page,
   chosen because `deltakura.dev` is not registered.
   No code change is needed when the repository becomes public. Our own rules require a resolving contact URL before any crawl above
   the prototype cap.
2. **The prototype cap of 20 requests/host/day was exceeded for Bet B**,
   deliberately and on the command line (`--daily-cap`), because a
   100-company registry cannot be verified inside 20 requests on two hosts.
   Bet A (14 requests) and Bet C (2/night) stay well inside it.
3. **Bet B is cleared but held.** The clearance matrix upgraded Greenhouse and
   Lever to `reuse_allowed=Y` on 2026-09-22, so the legal question is answered.
   Publication is a separate decision, pending operator approval, so `BET_B_LIVE` defaults to
   false, every clearance carries a hold, and the store stays outside the
   repository. Flipping that flag is the go-live switch.
4. **OpenPGP signatures on the NTA files are stored but not verified.**
5. **`--include-diff` for Bet A has not been run.** Those files expire upstream
   in about two months and have the same collect-it-or-lose-it property as
   Bet C.
6. **The NTA 全件 dump occupies ~255 MB** in the collection store under
   `nta/raw/zenken/`. It is the Bet-C baseline and the Bet-A join source,
   but the join table is already extracted and the dump is refreshed monthly.
   `python crawlers/nta_diff/zenken.py --needed-from … --discard-raw` frees it
   if disk matters.
7. **Bet B's registry is a snapshot.** "Japan-hiring" means *on `checked_on`*.
   `verify.py` needs re-running periodically, and that is the pass that hits the
   request cap in item 2.
