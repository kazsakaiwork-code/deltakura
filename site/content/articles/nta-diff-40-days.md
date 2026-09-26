---
slug: nta-diff-40-days
title: 40日で消える法人番号の差分とは
date: 2026-09-27
description: 国税庁の法人番号差分ファイルは過去40日分だけ掲載されます。2026年7月24日〜9月18日の40公表日で89,885件。内容、消える範囲、使い方、取得方法。
lang: ja
sources:
  - 出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）｜公共データ利用規約（第1.0版）｜国税庁法人番号公表サイトの日次差分ファイルを加工して作成（読み込み・重複除去・件数への集計）
---

国税庁の法人番号公表サイトは、法人の新設・移転・閉鎖などの変更を、公表日ごとの「差分ファイル」で公開しています。掲載は過去40日分です。Deltakuraは2026年7月24日分から保存を続けており、9月18日分までの40公表日で89,885件になりました。9月27日時点では9月25日分までの42公表日、94,233件を保管しています。

## 差分ファイルとは

登録内容が変わった法人を、公表日ごとにまとめたCSVファイルです。1行が1件の変更で、法人番号、商号、本店所在地、法人種別、処理区分（新規・変更・閉鎖など）、変更年月日が入ります。

全件データは今の状態を示します。差分ファイルには、その公表日に何が変わったかが載ります。

## 40日を過ぎると何が消えるか

国税庁のダウンロードページには「過去40日分を提供」と書かれています。新しい公表日が加わるたびに、いちばん古い日のファイルが一覧から外れます。

2026年9月27日に確認したところ、一覧の最古は7月28日分でした。7月24日分（2,206件）と7月27日分（2,346件）は、国税庁のダウンロードページからはもう取得できません。Deltakuraには両日とも残っています。

{{chart:nta-strip:2026-07-28:2026-09-27}}

## 保存している40公表日の中身

期間は2026年7月24日〜9月18日、ファイルは40本です。件数は重複除去後で89,885件（原ファイルの行数は89,888件）。1公表日あたり平均2,247件で、最少は1,937件、最多は2,667件でした。

| 処理区分 | 件数 | 構成比 | 1公表日平均 |
|---|---:|---:|---:|
| 新規 | 44,149 | 49.1% | 1,104 |
| 国内所在地の変更 | 28,530 | 31.7% | 713 |
| 登記記録の閉鎖等 | 11,278 | 12.5% | 282 |
| 商号又は名称の変更 | 4,359 | 4.8% | 109 |
| 吸収合併 | 1,257 | 1.4% | 31 |
| その他（3区分） | 312 | 0.3% | 8 |
| 合計 | 89,885 | 100% | 2,247 |

変更の半分は新規です。法人種別で見ると、株式会社が56,518件（62.9%）、有限会社が12,889件（14.3%）、合同会社が12,281件（13.7%）です。

{{chart:nta-daily-by-process:2026-07-24:2026-09-18}}

## 地域と変化の種類で使う

都道府県別では東京都が27,121件で全体の30.2%を占めます。大阪府8,298件、神奈川県5,770件、愛知県4,652件が続きます。自分の県の件数を全国と並べると、地元で法人の動きがどのくらいあるかがわかります。

{{chart:nta-prefecture-top10:2026-07-24:2026-09-18}}

新規は1公表日あたり約1,100件です。新設法人を顧客にする事業（記帳、許認可、Web制作など）では、この数字が対象になる法人数の目安になります。

移転（国内所在地の変更）は1公表日あたり713件、閉鎖は282件です。顧問先や取引先が移転や閉鎖をしていないかは、国税庁の法人番号公表サイトで法人番号から検索できます。

- 都道府県＝各レコードに記載された本店所在地
- 業種：法人番号データに項目なし
- Deltakuraが公開しているのは件数のみ（法人ごとの記録は非公開）

## 取得方法

- Web：https://deltakura-signals.web.app/ja/bet-c/ （40日の帯、日別件数、内訳）
- RSS（週次）：https://deltakura-api.deltakura.workers.dev/v0/feeds/nta-diff.xml
- JSON：https://deltakura-signals.web.app/data/bet-c/daily.json
- API：https://deltakura-api.deltakura.workers.dev/v0/corporate/diff-summary?from=2026-07-24&to=2026-09-18
- MCP：`@deltakura/mcp` の `jp_corporate_diff_summary`（読み取り専用。npm公開前）

保存分は、国税庁が新しい公表日のファイルを出すたびに増えていきます。

<!--
CHART REQUESTS (for the site engineer; data from deltakura/data/published/nta/summary.json unless noted)

{{chart:nta-40day-strip}}
  What: the 40-day strip. One cell per NTA publication day, 2026-07-24 .. 2026-09-18 (40 cells, keys of summary.json "days").
  Encoding: cells for 2026-07-24 and 2026-07-27 marked "国税庁サイトから削除済み（2026-09-27確認）" (distinct fill), the other 38 marked "国税庁サイトにも掲載中（2026-09-27時点）".
  Label under the strip: 「蔵にある40公表日 / 国税庁サイトの一覧は7月28日分から（2026-09-27確認）」.
  Note: if the chart is built after more days are published, the deleted set grows; recompute by comparing the dates listed on https://www.houjin-bangou.nta.go.jp/download/sabun/ with summary.json days.

{{chart:nta-daily-by-process}}
  What: stacked bar chart, one bar per publication day (40 bars), x = file date (2026-07-24 .. 2026-09-18, publication days only, no gaps for weekends), y = records.
  Stacks: days[d].process_codes -> 01 新規, 12 国内所在地の変更, 21 登記記録の閉鎖等, 11 商号又は名称の変更, 71 吸収合併, rest (13, 22, 81) as その他.
  Bar total = days[d].records. Reference line: mean 2,247.

{{chart:nta-prefecture-top10}}
  What: horizontal bars, top 10 prefectures by records over the 40 days, from summary.json "prefectures".
  Values: 東京都 27,121 / 大阪府 8,298 / 神奈川県 5,770 / 愛知県 4,652 / 福岡県 3,684 / 埼玉県 3,576 / 千葉県 2,952 / 兵庫県 2,941 / 北海道 2,939 / 京都府 1,988.
  Label: 「都道府県＝各レコードの本店所在地」. Optional: full 47-prefecture list behind a <details>.

FIGURE PROVENANCE (every number in the article)
- 40日分: NTA download page text 「差分ダウンロードファイルは、過去40日分を提供しています。」 (fetched 2026-09-27); summary.json coverage.upstream_retention_days = 40.
- 2026年7月24日〜9月18日, 40公表日, 40本: summary.json coverage.from / coverage.to / coverage.days_with_data.
- 89,885件: summary.json coverage.records (deduplicated count, the authority per count_rule).
- 89,888件: summary.json coverage.records_raw.
- 7月28日分が最古 / 7月24日分・7月27日分は取得不可: https://www.houjin-bangou.nta.go.jp/download/sabun/ fetched 2026-09-27; 40 distinct dates listed, oldest 令和8年7月28日, newest 令和8年9月25日.
- 2,206件 / 2,346件: summary.json days["2026-07-24"].records and days["2026-07-27"].records.
- 平均2,247件: 89,885 / 40 = 2,247.1. 最少1,937件 (2026-08-31), 最多2,667件 (2026-08-07): min/max of days[*].records.
- Table: summary.json process_codes. 01=44,149; 12=28,530; 21=11,278; 11=4,359; 71=1,257; その他 = 22 (267) + 13 (43) + 81 (2) = 312. 構成比 = count / 89,885, 1 decimal. 1公表日平均 = count / 40, rounded to integer (1,103.7->1,104; 713.3->713; 282.0->282; 109.0->109; 31.4->31; 7.8->8). Labels follow build.py PROCESS_CODES.
- 変更の半分は新規: 49.1%.
- 株式会社56,518 (62.9%), 有限会社12,889 (14.3%), 合同会社12,281 (13.7%): summary.json kind_codes 301/302/305, share of 89,885.
- 東京都27,121 (30.2%), 大阪府8,298, 神奈川県5,770, 愛知県4,652: summary.json prefectures; share of 89,885. (Prefecture values sum to 89,724; 161 records carry no prefecture.)
- 新規 約1,100件/公表日: 44,149 / 40 = 1,103.7.
- 移転713件、閉鎖282件/公表日: 28,530 / 40 and 11,278 / 40.
- 業種の項目なし: NTA diff schema (columns listed in data/nta/normalized header; no industry column).
- Access URLs: deltakura/site/README.md (bet-c page, feeds/nta-diff.xml, data/bet-c/daily.json); deltakura/api/README.md (/v0/corporate/diff-summary, live per /v0/health on 2026-09-27); deltakura/mcp/README.md (tool jp_corporate_diff_summary; package 0.1.0 not yet on npm).
-->
