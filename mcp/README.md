# @deltakura/mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server over Japanese public
open data, on stdio.

Two datasets, neither of which an assistant can produce by reasoning:

- **National procurement award statistics** — 調達ポータル 落札実績, FY2013 onward, aggregated to
  年度 × 落札者所在都道府県 × 府省由来セクター. 313,568 awards behind 5,596 published buckets.
- **The corporate-number change register** — the 国税庁 法人番号 daily 差分 files. The publisher keeps
  them for about 40 days; Deltakura keeps collecting them, so the history outlives the window.

Every response carries its licence, its attribution string and the caveats that must be shown with
any number taken from it. No tool here returns data about an individual.

> **Deltakura is an unofficial archive of public data. It is not an official source, and it is not
> affiliated with, endorsed by or connected to 国税庁, デジタル庁 or any other government body.**
> Where a number here disagrees with the publisher, the publisher is right.

> **Status: 0.1.0, not published.** This package is not on npm yet; publishing is pending operator
> approval. `npm pack` works; `npm publish` is not run. The hosted API it can talk to is live at
> `https://deltakura-api.deltakura.workers.dev/v0`.

## Install

```bash
npm install @deltakura/mcp     # once published
```

Add it to an MCP client:

```json
{
  "mcpServers": {
    "deltakura": {
      "command": "npx",
      "args": ["-y", "@deltakura/mcp"],
      "env": {
        "DELTAKURA_DATA_DIR": "/path/to/deltakura/data"
      }
    }
  }
}
```

`DELTAKURA_DATA_DIR` is optional — see [Two data modes](#two-data-modes).

## Tools

### `jp_procurement_stats`

Award statistics from 調達ポータル. Filters, all optional:

| Argument | Meaning |
|---|---|
| `fiscal_year` | 年度, e.g. `2025` = 2025-04-01 → 2026-03-31 |
| `fiscal_year_from` / `fiscal_year_to` | inclusive 年度 range |
| `sector` | ministry-derived sector, exact or substring: `防衛`, `国土交通・運輸`, `厚生・労働`, … |
| `winner_prefecture` | `東京都`, `東京`, the code `13`, or `不明` for masked individuals |
| `include_buckets`, `bucket_limit` | whether and how many per-bucket rows to return (default 25, max 200) |
| `language` | `en` (default) or `ja`, for the caveat strings |

Returns award counts, corporate vs masked-individual counts, amount sum / mean / min / max, and
amount quartiles in yen.

Three things this tool will not do:

- **It will not give you a 落札率.** 調達ポータル does not publish 予定価格, so the award ratio cannot
  be computed. `award_ratio.available` is always `false` and says why.
- **It will not pretend combined quartiles are measured.** The source stores one five-number summary
  per bucket, and quartiles do not add up. When more than one bucket matches, the response models
  each bucket as a piecewise-linear distribution weighted by its award count and inverts the mixture;
  `amount_quartiles_jpy.exact` is `false` and `.method` explains it. Counts, sums, min and max are
  exact either way.
- **It will not name a winner.** The statistics layer carries no party name at all.

### `jp_corporate_number_lookup`

Looks up a 13-digit 法人番号 and returns the registered name, the address 都道府県 (and 市区町村), the
法人種別, and the latest change kind and date, with a short history. Spaces, hyphens and full-width
digits are tolerated; the NTA check digit is verified.

`found: false` means *no change was published for that number inside the collected window* — never
that the corporation does not exist.

### `jp_corporate_diff_summary`

Daily counts of register changes over a date range (`from`, `to`, ISO dates), optionally broken down
by 処理区分 with `group_by_change_kind: true`. Dates with no published file are reported as
`days_without_file` rather than as zero, because the publisher issues no file on weekends, Japanese
public holidays or 29 Dec – 3 Jan.

## Two data modes

The procurement statistics ship inside the package and need no configuration.

The two corporate-number tools need the change archive, which is far too large to ship:

| `DELTAKURA_DATA_DIR` | Mode | Behaviour |
|---|---|---|
| set | **local** | reads `<dir>/nta/normalized/*.csv.gz` and `<dir>/nta/manifest.csv` directly |
| unset | **remote** | calls `https://deltakura-api.deltakura.workers.dev/v0/…` |

Remote calls are **opt-in**. While `DELTAKURA_API_ENABLED` is unset, remote mode makes no network
request and returns a clear `remote not available` error naming the endpoint it would have called
and the two ways to answer it (`DELTAKURA_API_ENABLED=1` or `DELTAKURA_DATA_DIR`).

Either the data root or the `nta` directory itself is accepted as `DELTAKURA_DATA_DIR`.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `DELTAKURA_DATA_DIR` | — | local Deltakura data directory; selects local mode |
| `DELTAKURA_API_BASE` | `https://deltakura-api.deltakura.workers.dev/v0` | hosted API base URL |
| `DELTAKURA_API_ENABLED` | unset | set to `1` to actually call the hosted API |
| `DELTAKURA_NTA_MAX_SCAN_BYTES` | `268435456` | cap on gzipped bytes scanned per call |

## What this server will never return

- **No individual's data.** 法人番号 are issued to 法人 and public bodies, not to 個人事業主. On top of
  that: a record whose corporate number failed the check digit at ingestion is skipped; a short,
  token-free name on a non-corporate 法人種別 is masked; a field allowlist (not a blocklist) decides
  what travels; and a source file that grows a 担当者 / 氏名 / 連絡先 column is refused outright rather
  than read around.
- **No individual winner of a public contract.** Procurement winners without a checksum-valid
  法人番号 are masked at ingestion, and any 年度 × 都道府県 × セクター bucket holding fewer than three of
  them is suppressed entirely, so no one can be identified by elimination.
- **No writes of any kind.** Three tools, all `readOnlyHint: true`. Nothing authenticates, posts,
  sends or deletes.

## Attribution

Reproduce these wherever you show a number from this server. They travel in every tool response.

```
出典：調達ポータル（https://www.p-portal.go.jp/）
出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）
```

Licences: 政府標準利用規約（第2.0版）for the procurement data, 公共データ利用規約（第1.0版）for the
corporate-number data. Both permit commercial reuse with source indication and require modification
to be declared — the data here is normalised, aggregated and anonymised. Code: MIT. Our derived
aggregates: CC BY 4.0.

## Development

```bash
npm install
npm run build-data   # rebuild the compact table from ../data/published/pportal/stats_v0.csv
npm run build        # tsc -> dist/
npm test             # vitest, fully offline
```

`build-data` looks for `stats_v0.csv` at `../data/published/pportal/stats_v0.csv`, then at
`$DELTAKURA_STATS_CSV`, then under `$DELTAKURA_DATA_DIR`; `--input` overrides all three.

It writes **two** copies of the same bytes: `mcp/data/procurement-stats.json` (the npm payload) and
`../data/published/pportal/procurement-stats.json` (the published artefact the Cloudflare Worker in
`../api` copies rather than rebuilds). That is why the MCP server and the Worker cannot answer one
question two ways. `--no-publish` writes only the package copy; `--out` writes only where you say.

It refuses to write more than 3 MB, and refuses outright if the statistics table ever grows a
party-name column. `--check` verifies that every committed copy still matches a fresh build.

Tests run with no network and no access to the private data store: the corporate-number fixtures are
gzipped CSVs written to a temporary directory, with invented names on real, checksum-valid numbers.

---

## 日本語

**@deltakura/mcp** は、日本の公開オープンデータを読み取り専用で提供する MCP サーバー（stdio）です。

対象は2つ。**調達ポータルの落札実績**（FY2013〜、年度 × 落札者所在都道府県 × 府省由来セクターに集計）と、
**国税庁 法人番号の差分データ**（公表側の保存期間は約40日。収集を続けることで、その窓より長い履歴になります）。

### ツール

- `jp_procurement_stats` — 落札件数、金額の合計・平均・最小・最大、四分位数。年度・セクター・落札者所在都道府県で絞り込み。
- `jp_corporate_number_lookup` — 13桁の法人番号から、商号、所在地の都道府県（市区町村）、法人種別、直近の変更区分と変更年月日。
- `jp_corporate_diff_summary` — 指定期間の日次変更件数。`group_by_change_kind` で処理区分別の内訳も。

### 必ず読んでほしい注意

- **落札率は出せません。** 調達ポータルは予定価格を公表していないためです。
- **都道府県は落札者の登記上の所在地**であり、履行地ではありません。東京都に偏ります。
- **セクターは発注元の府省由来**です。原データに業種はありません。「防衛」は買い手が防衛省という意味です。
- **複数バケットにまたがる四分位数は推定値**です（元データはバケットごとの五数要約のみを持つため）。件数・合計・最小・最大は正確です。
- 法人番号の照会で「該当なし」は、**収集期間内に変更の公表がなかった**という意味であり、法人が存在しないという意味ではありません。

### 個人情報は返しません

法人番号は個人事業主には指定されません。加えて、取込時にチェックディジットが通らなかったレコードは無視し、
法人格を示す語を含まない短い名称が非法人の法人種別に付いている場合は伏せ、出力はブロックリストではなく
**許可リスト**で決めています。担当者・氏名・連絡先といった列が入力ファイルに現れた場合は、読み飛ばすのではなく
**ファイルごと拒否**します。落札者側も、有効な法人番号を持たない落札者は取込時に匿名化され、
匿名化された個人が3件未満のバケットは丸ごと抑止しています。

### モード

法人番号系のツールは、`DELTAKURA_DATA_DIR` が設定されていればローカルのファイルを読み、
未設定ならホスト版 API（`https://deltakura-api.deltakura.workers.dev/v0/…`）を呼びます。
API の呼び出しは `DELTAKURA_API_ENABLED=1` を設定したときだけ行います。未設定の間は
「remote not available」というエラーを返し、ネットワークアクセスは発生しません。
調達統計はパッケージに同梱しているので設定不要です。

### 出典（表示必須）

```
出典：調達ポータル（https://www.p-portal.go.jp/）
出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）
```

ライセンスは順に 政府標準利用規約（第2.0版）、公共データ利用規約（第1.0版）。いずれも出典の明示を条件に
商用利用・再配布が可能で、加工した場合はその旨の明示が必要です（本データは正規化・集計・匿名化しています）。
コードは MIT、派生した集計値は CC BY 4.0 です。
