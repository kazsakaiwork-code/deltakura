# Bet C — NTA 法人番号 差分 nightly collector

The 法人番号公表サイト keeps its daily diff files for **40 days**. After that
they are gone, and the only way to get the change history is to have been
collecting it. Two requests a night, about 145 KB, build something that cannot
be bought back later. This is also the anonymisation backstop for Bet A: a
tender winner that does not resolve to a registered 法人番号 is, by
construction, the case Bet A must mask.

Source clearance: `reuse_allowed=Y`, 公共データ利用規約(第1.0版), commercial
reuse and redistribution permitted with attribution. No account, no application
ID, no API key. Details in `source.yaml`.

## Attribution (required wherever this data is shown)

```
出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）
```

The data is used in modified form (normalised, deduplicated, re-encoded); both
the licence and 著作権法 32条2項 require that to be declared.

## Running it

```bash
python crawlers/nta_diff/collect.py              # nightly: fetch what is new
python crawlers/nta_diff/collect.py --backfill   # every file in the 40-day window
python crawlers/nta_diff/collect.py --dry-run    # list what would be fetched
python crawlers/nta_diff/run_nightly.py          # what the scheduler calls
```

`run_nightly.py` wraps `collect.py` and appends exactly one row to
`data/nta/run_log.csv` whatever happens — success, quiet no-op or crash —
because a silently missing night is the one failure this product cannot
recover from.

Idempotent: a date already in `data/nta/manifest.csv` is skipped without a
request, so a same-night re-run costs one HTTP request (the listing page).

## How the download works

The listing page carries a CSRF token; each file is a POST of
`{token, event=download, selDlFileNo=<fileNo>}` back to the same URL. No login,
no account, no personal data submitted, one request per file, ≥ 2 s apart. The
file numbers are read from the page at run time, never guessed. We take the
**CSV/Unicode** variant to avoid Shift_JIS round-trips.

If the page layout changes, `parse_listing()` raises rather than guessing — a
loud failure is correct here, because a wrong `selDlFileNo` would silently fetch
the wrong day.

## What lands where

```
data/nta/raw/<YYYY-MM-DD>/diff_YYYYMMDD.zip   publisher ZIP, verbatim (+ its
                                              OpenPGP .asc signature inside)
data/nta/normalized/nta_diff_<YYYY-MM>.csv.gz one gzipped CSV per month
data/nta/manifest.csv                         one row per file: bytes, sha256,
                                              record count, retrieved_at
data/nta/run_log.csv                          one row per scheduled run
data/nta/lookup/corp_location.csv.gz          the Bet-A join table (zenken.py)
data/nta/state/                               HTTP cache and request counters
```

`raw/`, `state/` and `lookup/` are git-ignored. Everything above lives in the
private collection store (`$DELTAKURA_DATA_DIR`), never in this repository.

Two files *are* committed, both written by `publish_summary.py`:

```
data/published/nta/summary.json   daily counts, code breakdowns, provenance
data/published/nta/manifest.csv   file id, date, sha256, bytes, row counts
```

Neither carries a record, and neither carries a build timestamp: provenance is
`source_version` (a content hash) and the publisher retrieval times. The
manifest is also operational — the next run reads it to know what is already
held (see Scheduling).

## Schema

The Bet-C schema, plus the shared fields. The publisher's CSV is 30 columns with no
header row; `NTA_LAYOUT` in `collect.py` maps them. Six columns are dropped on
purpose: the two 国外所在地 image-ID fields and the four English-transliteration
fields, which add nothing to a diff product and are the ones most likely to
carry a transliterated personal name.

Key: `corporate_number|change_date|sequence_number`. On a re-run, a correction
(`correct_flag=1`) supersedes, otherwise the highest `sequence_number` wins.

**Personal data: none.** 法人番号 are issued to 法人 and public bodies, not to
個人事業主, and the diff layout carries no representative-person field. The
parser additionally refuses any column whose name matches the R3 personal-field
pattern, so a future layout change fails closed rather than quietly publishing.

## Publication gaps are normal, not failures

Files are produced around 16:00 JST and **not** produced on weekends, public
holidays or 12/29–01/03. A missing date is the publisher's calendar, not a
collection error, so the collector reports rather than alerts. The nightly job
runs at 03:30 JST, which picks up the previous working day's file.

## The 全件 baseline

```bash
python crawlers/nta_diff/zenken.py --needed-from data/pportal/normalized
python crawlers/nta_diff/zenken.py --needed-from data/pportal/normalized --discard-raw
python crawlers/nta_diff/zenken.py --all          # the whole registry (large)
```

Downloads the nationwide Unicode dump (~255 MB zipped, 5.8M corporations),
streams it to disk, and reduces it to `corporate_number → 都道府県 / 市区町村 /
法人種別`. Filtered to the numbers a normalized store actually uses, that is a
file of tens of kilobytes. Fetch it about monthly; the diffs carry the changes
in between.

## Scheduling

Collection runs in **GitHub Actions**: `.github/workflows/nta-diff.yml`, daily at
**03:30 JST** (`cron: '30 18 * * *'`), with `workflow_dispatch` for a manual run.
The job collects the day's file into the runner's scratch space, rebuilds
`data/published/nta/summary.json` and `data/published/nta/manifest.csv`,
refreshes the Worker's bundled counts and commits those files. It needs no
secret: the source is public, keyless open data.

GitHub's scheduler can run late or skip a run under load. That is survivable,
because a run repairs the whole window rather than only the newest night.

### Request budget

| Case | Requests | Bytes |
|---|---|---|
| Normal night | **2** — the listing page, then one download | ~145 KB |
| After a gap of *n* days | 1 + *n*, one per missing day, ≥ 2 s apart | ~130 KB per day |
| Worst case (no manifest at all) | 41, the whole 40-day window | ~5.5 MB |
| A day already held | **0** | — |

"Already held" is read from the committed `data/published/nta/manifest.csv` as
well as from the local collection store, which is why a runner that starts with
an empty store still fetches only what is new. `--daily-cap` (default 60) is the
hard brake, and `--backfill` deliberately ignores the committed manifest to
rebuild a local archive.

### Running it locally

```bash
export DELTAKURA_DATA_DIR=/var/lib/deltakura/data   # or leave it beside the checkout
python crawlers/nta_diff/run_nightly.py
```

`run_nightly.py` takes no arguments, derives every path from its own location
and always appends a row to `$DELTAKURA_DATA_DIR/nta/run_log.csv`, including on
failure. Any scheduler that can run a command at a fixed time will drive it —
cron, a systemd timer, or a desktop task scheduler. Two schedulers may safely
run at once: the collector is idempotent and the second one of an evening finds
nothing to fetch.

The local path exists because it keeps the per-record archive. The Actions run
throws its normalized layer away with the runner and keeps only the published
counts, so a machine that holds the archive is still the one that can revise
history (`publish_summary.py` recomputes every day it can see).


## Known limits

* The User-Agent's contact URL (`https://github.com/kazsakaiwork-code/deltakura`) does not resolve
  until the repository is public. A resolving contact URL is required before any
  crawl above the prototype cap; at two requests a night this source is nowhere
  near that cap, but the URL still needs to start resolving. No code change is
  needed when it does.
* The OpenPGP signature shipped beside each CSV is stored but **not verified**.
  Verifying it needs the NTA public key and a GPG dependency; worth doing before
  anything downstream treats the archive as authoritative.
