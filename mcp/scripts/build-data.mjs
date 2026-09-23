#!/usr/bin/env node
/**
 * Build the compact procurement-statistics JSON that ships inside @deltakura/mcp
 * (and is reused by the Cloudflare Worker in ../api).
 *
 * Input : ../data/published/pportal/stats_v0.csv
 *         (Bet A statistics, one row per 年度 x 都道府県 x セクター)
 * Output: mcp/data/procurement-stats.json                    (the npm payload)
 *         ../data/published/pportal/procurement-stats.json   (the published copy)
 *
 * This builder is the single origin of the compact table. Both copies are written
 * from the same bytes, and ../api reads the published copy rather than rebuilding,
 * so the npm package, the Worker bundle and the published data directory cannot
 * drift apart.
 *
 * The output is dictionary-encoded: repeated strings (prefecture, sector, licence,
 * attribution) are stored once and referenced by index, so the whole table fits in
 * well under the 3 MB package budget.
 *
 * Usage:
 *   node scripts/build-data.mjs [--input <stats_v0.csv>] [--out <file.json>]
 *                               [--no-publish] [--check]
 *
 * Input resolution order:
 *   1. --input <path>
 *   2. $DELTAKURA_STATS_CSV
 *   3. <repo>/data/published/pportal/stats_v0.csv   (the normal case)
 *   4. $DELTAKURA_DATA_DIR/pportal/stats_v0.csv     (a private collection store)
 *
 * No network, no absolute path baked in, and nothing written outside this
 * repository.
 *
 * DETERMINISM. The payload carries no build timestamp. Its
 * provenance is `source_version` - the SHA-256 of the input CSV, truncated to
 * 16 hex characters - and `source_retrieved_at`, the publisher retrieval date
 * the CSV itself records. The same input therefore always produces the same
 * bytes, on any machine and at any hour, which is what lets CI assert that the
 * committed bundles match a fresh build (`npm run build-data` then
 * `git diff --exit-code`).
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = resolve(HERE, '..');
const REPO_ROOT = resolve(PKG_ROOT, '..');
const PUBLISHED_DIR = join(REPO_ROOT, 'data', 'published', 'pportal');
const MAX_BYTES = 3 * 1024 * 1024;

/** Caveats travel with the data: every tool and endpoint that returns a number returns these too. */
const CAVEATS_EN = [
  '落札率 (award ratio) does not exist in this dataset. 調達ポータル does not publish 予定価格, so every award_ratio field is empty and award_ratio_coverage is 0. Do not present this as an award-ratio product.',
  'prefecture is the WINNER\'S REGISTERED head-office prefecture, joined from the 国税庁 法人番号 register on corporate number only - never on name. It is not where the work is performed, so the axis over-weights 東京都 the way any head-office measure does.',
  'sector is derived from the procuring 府省 (ministry) code, not from an industry classification. It describes who bought, not what was sold: 防衛 means the Ministry of Defense was the buyer, not that the supplier is a defence contractor. The source has no 業種 field.',
  'Winners without a checksum-valid 13-digit 法人番号 are masked at ingestion and carry no prefecture, so every 個人事業主 lands in prefecture=不明.',
  'Buckets holding fewer than 3 masked individuals are suppressed entirely (k-anonymity rule R6), so these counts do not sum to the 313,568 awards in the underlying archive.',
  'Amounts are integer yen, rounded from the publisher\'s decimal field.',
  'National procurement only. Municipal and prefectural spending is not covered.',
  'FY2013-FY2015 are the publisher\'s system ramp-up (FY2013 contains a single row), not a measurement of procurement volume.'
];

const CAVEATS_JA = [
  '落札率は算出できません。調達ポータルは予定価格を公表していないため、award_ratio 系の列はすべて空です。',
  '都道府県は落札者の登記上の所在地（国税庁法人番号公表サイトと法人番号で突合）であり、履行地ではありません。東京都に偏ります。',
  'セクターは発注元の府省コード由来です。原データに業種はありません。「防衛」は買い手が防衛省という意味です。',
  'チェックディジットの通らない法人番号を持たない落札者は取込時に匿名化され、都道府県は「不明」になります。',
  '匿名化された個人が3件未満のバケットは丸ごと抑止しています（k-匿名性）。合計は原データの 313,568 件に一致しません。',
  '金額は円単位の整数です（公表データの小数を丸めています）。',
  '国の調達のみです。自治体の調達は含みません。',
  'FY2013〜FY2015 は公表システムの立ち上げ期です（FY2013 は1件のみ）。調達量の実態ではありません。'
];

/** Minimal RFC 4180 CSV reader. The licence column contains commas and quotes. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  let i = 0;
  if (text.charCodeAt(0) === 0xfeff) i = 1; // strip BOM
  for (; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') { quoted = true; continue; }
    if (c === ',') { row.push(field); field = ''; continue; }
    if (c === '\r') continue;
    if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    field += c;
  }
  if (field !== '' || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

function arg(name) {
  const idx = process.argv.indexOf(name);
  return idx === -1 ? undefined : process.argv[idx + 1];
}

function resolveInput() {
  const candidates = [
    arg('--input'),
    process.env.DELTAKURA_STATS_CSV,
    join(PUBLISHED_DIR, 'stats_v0.csv'),
    process.env.DELTAKURA_DATA_DIR
      ? join(process.env.DELTAKURA_DATA_DIR, 'pportal', 'stats_v0.csv')
      : undefined
  ].filter(Boolean);
  for (const c of candidates) if (existsSync(c)) return c;
  throw new Error(
    'stats_v0.csv not found. Looked at:\n  ' +
      candidates.join('\n  ') +
      '\nPass --input <path> or set DELTAKURA_STATS_CSV.'
  );
}

/** Deterministic stand-in for a build timestamp: the input's own content hash. */
export function sourceVersion(csvText) {
  return 'sha256-' + createHash('sha256').update(csvText, 'utf8').digest('hex').slice(0, 16);
}

function num(v) {
  if (v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function buildDataset(csvText, opts = {}) {
  const rows = parseCsv(csvText);
  if (rows.length < 2) throw new Error('stats CSV is empty');
  const header = rows[0];
  const col = Object.fromEntries(header.map((h, i) => [h.trim(), i]));
  for (const required of [
    'fiscal_year', 'prefecture', 'prefecture_code', 'sector', 'n_awards',
    'n_corporate', 'n_masked_individual', 'amount_min_jpy', 'amount_q1_jpy',
    'amount_median_jpy', 'amount_q3_jpy', 'amount_max_jpy', 'amount_sum_jpy',
    'license', 'attribution'
  ]) {
    if (!(required in col)) throw new Error(`stats CSV is missing column: ${required}`);
  }

  // Refuse to build if a party-name column ever appears: the statistics layer is
  // never keyed by a winner. Fail closed rather than ship it.
  const forbidden = header.filter((h) =>
    /winner|name|担当|氏名|代表|連絡先|電話|メール/i.test(h) && h !== 'publisher_name'
  );
  if (forbidden.length) {
    throw new Error(`refusing to build: stats CSV carries party-identifying column(s): ${forbidden.join(', ')}`);
  }

  let retrievedAt = '';
  const prefIndex = new Map();
  const prefList = [];
  const sectorIndex = new Map();
  const sectorList = [];
  const years = new Set();
  const out = [];
  let license = '';
  let attribution = '';
  let prefectureBasis = '';
  let categoryAxis = '';
  let sourceId = '';

  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    if (row.length === 1 && row[0] === '') continue;
    const fy = num(row[col.fiscal_year]);
    if (fy === null) continue;
    const prefName = row[col.prefecture];
    const prefCode = row[col.prefecture_code] ?? '';
    const prefKey = `${prefCode}|${prefName}`;
    if (!prefIndex.has(prefKey)) {
      prefIndex.set(prefKey, prefList.length);
      prefList.push([prefCode, prefName]);
    }
    const sector = row[col.sector];
    if (!sectorIndex.has(sector)) {
      sectorIndex.set(sector, sectorList.length);
      sectorList.push(sector);
    }
    years.add(fy);
    license ||= row[col.license] ?? '';
    attribution ||= row[col.attribution] ?? '';
    prefectureBasis ||= row[col.prefecture_basis] ?? '';
    categoryAxis ||= row[col.category_axis] ?? '';
    sourceId ||= row[col.source_id] ?? '';
    // The publisher's own retrieval date, carried on every row of the CSV. The
    // latest one is the dataset's "data as of", and it is a property of the
    // input, not of this run.
    const rowRetrieved = row[col.retrieved_at] ?? '';
    if (rowRetrieved > retrievedAt) retrievedAt = rowRetrieved;

    out.push([
      fy,
      prefIndex.get(prefKey),
      sectorIndex.get(sector),
      num(row[col.n_awards]) ?? 0,
      num(row[col.n_corporate]) ?? 0,
      num(row[col.n_masked_individual]) ?? 0,
      num(row[col.amount_min_jpy]) ?? 0,
      num(row[col.amount_q1_jpy]) ?? 0,
      num(row[col.amount_median_jpy]) ?? 0,
      num(row[col.amount_q3_jpy]) ?? 0,
      num(row[col.amount_max_jpy]) ?? 0,
      num(row[col.amount_sum_jpy]) ?? 0
    ]);
  }

  out.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);

  return {
    schema: 1,
    dataset: 'pportal_awards_stats_v0',
    dataset_title: 'Deltakura Tender Archive (JP) - national procurement award statistics',
    grain: 'fiscal_year x winner_registered_prefecture x ministry_sector',
    source_version: opts.sourceVersion ?? sourceVersion(csvText),
    source_retrieved_at: retrievedAt,
    source_id: sourceId || 'pportal_awards',
    source_url: 'https://www.p-portal.go.jp/',
    license,
    attribution,
    attribution_secondary: '出典：国税庁法人番号公表サイト（国税庁）',
    prefecture_basis: prefectureBasis || 'winner_registered_nta',
    category_axis: categoryAxis,
    award_ratio_available: false,
    caveats_en: CAVEATS_EN,
    caveats_ja: CAVEATS_JA,
    columns: [
      'fiscal_year', 'prefecture_idx', 'sector_idx', 'n_awards', 'n_corporate',
      'n_masked_individual', 'amount_min_jpy', 'amount_q1_jpy', 'amount_median_jpy',
      'amount_q3_jpy', 'amount_max_jpy', 'amount_sum_jpy'
    ],
    fiscal_years: [...years].sort((a, b) => a - b),
    prefectures: prefList,
    sectors: sectorList,
    row_count: out.length,
    rows: out
  };
}

function main() {
  const input = resolveInput();
  const explicitOut = arg('--out');
  const outPath = resolve(explicitOut ?? join(PKG_ROOT, 'data', 'procurement-stats.json'));
  // The published copy travels with the package copy unless the caller asked for
  // a one-off output (--out) or opted out (--no-publish).
  const publishPath =
    explicitOut || process.argv.includes('--no-publish')
      ? undefined
      : join(PUBLISHED_DIR, 'procurement-stats.json');
  const targets = [outPath, publishPath].filter(Boolean);
  const csvText = readFileSync(input, 'utf8');
  const dataset = buildDataset(csvText);
  const json = JSON.stringify(dataset);
  const bytes = Buffer.byteLength(json, 'utf8');
  if (bytes > MAX_BYTES) {
    throw new Error(`output is ${bytes} bytes, over the ${MAX_BYTES}-byte package budget`);
  }
  if (process.argv.includes('--check')) {
    for (const target of targets) {
      if (!existsSync(target)) throw new Error(`--check: ${target} does not exist`);
      // Byte-for-byte. There is nothing left in the payload that a second
      // build could legitimately change, so any difference is real drift.
      if (readFileSync(target, 'utf8') !== json) {
        throw new Error(`--check: ${target} differs from a fresh build`);
      }
    }
    console.log(
      `ok: ${targets.length} copy/copies match a fresh build (${statSync(outPath).size} bytes each)`
    );
    return;
  }
  for (const target of targets) {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, json, 'utf8');
  }
  console.log(
    `wrote ${targets.join(', ')}\n  rows: ${dataset.row_count}` +
      `\n  fiscal years: ${dataset.fiscal_years[0]}-${dataset.fiscal_years.at(-1)}` +
      `\n  prefectures: ${dataset.prefectures.length}  sectors: ${dataset.sectors.length}` +
      `\n  bytes: ${bytes} (${(bytes / 1024 / 1024).toFixed(2)} MB of a ${MAX_BYTES / 1024 / 1024} MB budget)`
  );
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  try {
    main();
  } catch (err) {
    console.error(String(err instanceof Error ? err.message : err));
    process.exit(1);
  }
}
