# Bet A — 調達ポータル 落札実績オープンデータ

National procurement awards, FY2013 onward, published by デジタル庁 as bulk
CSV/JSON under 政府標準利用規約(第2.0版). This is the best Bet-A source in the
survey and the only national one that survived the ToS review: the plan's
original assumption, 官公需情報ポータル (kkj.go.jp), is blocked by a host-wide
`Disallow: /`.

Clearance: `reuse_allowed=Y`. No account, no key. Details in `source.yaml`.

## Attribution (required wherever this data is shown)

```
出典：調達ポータル（https://www.p-portal.go.jp/）
```

Where a prefecture is attached, the NTA registry needs its own line — see
`data/pportal/README.md`.

## Running it

```bash
python crawlers/pportal/collect.py                    # every fiscal year, newest first
python crawlers/pportal/collect.py --years 2026 2025  # just these
python crawlers/pportal/collect.py --include-diff     # plus the daily 差分 window
python crawlers/pportal/collect.py --refetch          # re-request the monthly refresh
python crawlers/pportal/stats.py                      # build data/pportal/stats_v0.csv
```

`collect.py` is conditional on ETag, so a re-run of an unchanged year costs a
304. The 全件 files are re-published monthly; `--refetch` is how you pick that
up. 差分 files never change, so they are fetched once.

## The download URL — resolved

This was an open item in the source survey: the page triggers downloads
through a JS `doDownload()` and the obvious static paths 404. The page's own
script builds a plain GET against a separate API host:

```
https://api.p-portal.go.jp/pps-web-biz/UAB03/OAB0301?fileversion=v001&filename=<name>
```

`collect.py` reads that template out of the page (`uab02FileDownloadUrl`) at run
time rather than hard-coding it, so a publisher-side change surfaces as a parse
error instead of a silent 404.

## Columns — what the source does and does not publish

Eight columns, verified against the publisher's own file spec (令和8年3月版):
調達案件番号 / 調達案件名称 / 落札決定日 / 落札価格 / 府省コード /
入札方式コード / 商号又は名称 / 法人番号 (optional).

**Not published, and therefore never inferred:**

| Field | Consequence |
|---|---|
| 予定価格 | 落札率 cannot be computed. The columns exist and are empty. |
| 業種 | No industry axis. `sector` is derived from the 府省 — who *bought*. |
| 都道府県 | No location field at all. See `stats.py` and the data README. |
| 入札参加者数 | `bidder_count` stays empty. |

The clearance survey recorded the column order as 本省コード then 落札方式コード;
the spec calls them 府省コード and 入札方式コード, in that order. Positions 5 and
6 are right, the names were not. `codes.py` carries both full code tables (53
府省, 16 入札方式).

## Anonymisation

Applied before anything reaches `data/pportal/normalized/`:

* **R1** valid 13-digit 法人番号 → name kept, `winner_type=corporate`.
* **R2** no number, or a number that fails the check digit → masked, whatever
  the name looks like. `winner_name=null`,
  `winner_masked=個人事業主・<業種>・<都道府県>`.
* **R4** the title passes a person-name detector; a hit replaces it. This
  matters for 随意契約 titles, which occasionally name a person.
* A hard guard in `normalize_rows()` refuses to emit a record that is masked and
  still carries a name, so the rule cannot be lost to a later edit.

This source publishes neither 業種 nor 都道府県, so a masked winner reads
`個人事業主・不明・不明`. That is less informative than the masking rule's own
example and also less re-identifying, which is the right direction to fail.

## Prefecture enrichment

`stats.py` joins `corporate_number` to the NTA 法人番号 registry — the only join
permitted between Bet A and Bet C, and never on a name. Build the join table
first:

```bash
python crawlers/nta_diff/zenken.py --needed-from "$DELTAKURA_DATA_DIR/pportal/normalized"
```

All 22,837 distinct corporate numbers in the FY2013–FY2026 archive resolved.
The result is the **winner's registered** prefecture, not where the work is; the
caveats are spelled out in `data/pportal/README.md` and must travel with any
chart made from it.

## Known limits

* FY2013 contains a single record in the publisher's own file. FY2013–FY2015 are
  the system's ramp-up, not a measurement of procurement volume.
* National procurement only. 自治体 spending is a separate layer, limited to
  the four entities that actually grant reuse.
* Award prices are rounded to integer yen from the publisher's decimal field.
* `--include-diff` has not been run yet. The 差分 files are retained upstream for
  about two months and carry the same "collect it or lose it" property as Bet C,
  so they are worth adding to the nightly job once Bet A moves to a schedule.
