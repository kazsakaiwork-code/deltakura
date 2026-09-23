/**
 * Offline fixtures. Every test in this package runs with no network and no
 * dependency on the private data store: the NTA fixtures are written into a
 * temporary directory by `makeNtaFixture()`.
 */
import { gzipSync } from 'node:zlib';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ProcurementDatasetLike } from '../src/query/procurement.js';

export const fixtureDataset: ProcurementDatasetLike = {
  dataset: 'pportal_awards_stats_v0',
  dataset_title: 'test dataset',
  grain: 'fiscal_year x winner_registered_prefecture x ministry_sector',
  source_version: 'sha256-0000000000000000',
  source_retrieved_at: '2026-09-21T00:00:00Z',
  source_id: 'pportal_awards',
  source_url: 'https://www.p-portal.go.jp/',
  license: '政府標準利用規約(第2.0版)',
  attribution: '出典：調達ポータル（https://www.p-portal.go.jp/）',
  attribution_secondary: '出典：国税庁法人番号公表サイト（国税庁）',
  prefecture_basis: 'winner_registered_nta',
  category_axis: '府省由来セクター',
  award_ratio_available: false,
  caveats_en: ['caveat one', 'caveat two'],
  caveats_ja: ['注意1', '注意2'],
  fiscal_years: [2024, 2025],
  prefectures: [
    ['13', '東京都'],
    ['27', '大阪府'],
    ['', '不明']
  ],
  sectors: ['防衛', '総務・情報通信'],
  row_count: 5,
  rows: [
    // fy, pref, sector, n, corp, masked, min, q1, med, q3, max, sum
    [2024, 0, 0, 10, 10, 0, 100, 200, 300, 400, 500, 3000],
    [2024, 1, 0, 4, 4, 0, 1000, 1000, 2000, 3000, 4000, 8000],
    [2025, 0, 0, 20, 19, 1, 50, 150, 250, 350, 450, 5000],
    [2025, 0, 1, 6, 6, 0, 10, 20, 30, 40, 50, 180],
    [2025, 2, 1, 3, 0, 3, 5, 5, 5, 5, 5, 15]
  ]
};

const NTA_HEADER = [
  'corporate_number', 'process_code', 'correct_flag', 'update_date', 'change_date',
  'sequence_number', 'name', 'name_image_id', 'kind_code', 'prefecture', 'city',
  'street_number', 'prefecture_code', 'city_code', 'post_code', 'close_date',
  'close_cause', 'successor_corporate_number', 'change_cause', 'assignment_date',
  'latest_flag', 'furigana', 'hidden_flag', 'corporate_number_valid', 'record_key',
  'file_date', 'source_id', 'source_url', 'license', 'attribution', 'retrieved_at', 'raw_hash'
];

interface FixtureRow {
  corporate_number: string;
  process_code: string;
  correct_flag?: string;
  update_date: string;
  change_date: string;
  sequence_number: string;
  name: string;
  kind_code: string;
  prefecture: string;
  city: string;
  prefecture_code: string;
  close_date?: string;
  close_cause?: string;
  successor_corporate_number?: string;
  assignment_date?: string;
  latest_flag?: string;
  corporate_number_valid?: string;
  file_date: string;
}

function csvRow(r: FixtureRow): string {
  const values: Record<string, string> = {
    ...Object.fromEntries(NTA_HEADER.map((h) => [h, ''])),
    ...r,
    street_number: '架空町１−１−１',
    furigana: 'テストホウジン',
    change_cause: '令和8年9月1日 架空町 合併',
    correct_flag: r.correct_flag ?? '0',
    corporate_number_valid: r.corporate_number_valid ?? 'Y',
    latest_flag: r.latest_flag ?? '1',
    source_id: 'nta_diff'
  };
  return NTA_HEADER.map((h) => {
    const v = values[h] ?? '';
    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  }).join(',');
}

/** 1010001005145 and 1000020249203 are real, checksum-valid numbers; names below are invented. */
export const FIXTURE_ROWS: FixtureRow[] = [
  {
    corporate_number: '1010001005145', process_code: '01', update_date: '2026-09-01',
    change_date: '2026-08-20', sequence_number: '1', name: 'テスト架空株式会社', kind_code: '301',
    prefecture: '東京都', city: '文京区', prefecture_code: '13', assignment_date: '2026-08-20',
    file_date: '2026-09-01'
  },
  {
    corporate_number: '1010001005145', process_code: '11', update_date: '2026-09-03',
    change_date: '2026-09-02', sequence_number: '1', name: 'テスト架空第二株式会社', kind_code: '301',
    prefecture: '東京都', city: '文京区', prefecture_code: '13', assignment_date: '2026-08-20',
    file_date: '2026-09-03'
  },
  {
    corporate_number: '1000020249203', process_code: '12', update_date: '2026-09-02',
    change_date: '2026-09-01', sequence_number: '1', name: '架空村', kind_code: '201',
    prefecture: '三重県', city: '架空市', prefecture_code: '24', file_date: '2026-09-02'
  },
  {
    corporate_number: '1000020249203', process_code: '21', update_date: '2026-09-03',
    change_date: '2026-09-03', sequence_number: '2', name: '架空村', kind_code: '201',
    prefecture: '三重県', city: '架空市', prefecture_code: '24', close_date: '2026-09-03',
    close_cause: '01', file_date: '2026-09-03'
  },
  // 法人種別 "その他" with a short, token-free name: the fail-closed guard must mask it.
  {
    corporate_number: '7000012050002', process_code: '01', update_date: '2026-09-03',
    change_date: '2026-09-03', sequence_number: '1', name: '山田太郎', kind_code: '499',
    prefecture: '鳥取県', city: '架空町', prefecture_code: '31', file_date: '2026-09-03'
  },
  // A row whose corporate number failed the checksum at ingest: never described.
  {
    corporate_number: '9999999999998', process_code: '01', update_date: '2026-09-03',
    change_date: '2026-09-03', sequence_number: '1', name: '無効番号株式会社', kind_code: '301',
    prefecture: '東京都', city: '千代田区', prefecture_code: '13',
    corporate_number_valid: 'N', file_date: '2026-09-03'
  }
];

export interface NtaFixture {
  dir: string;
  cleanup(): void;
}

export function makeNtaFixture(opts: { withManifest?: boolean; extraHeader?: string } = {}): NtaFixture {
  const dir = mkdtempSync(join(tmpdir(), 'deltakura-test-'));
  const ntaDir = join(dir, 'nta');
  mkdirSync(join(ntaDir, 'normalized'), { recursive: true });

  const header = opts.extraHeader ? [...NTA_HEADER, opts.extraHeader] : NTA_HEADER;
  const body = FIXTURE_ROWS.map((r) => (opts.extraHeader ? `${csvRow(r)},x` : csvRow(r)));
  const csv = `${header.join(',')}\n${body.join('\n')}\n`;
  writeFileSync(join(ntaDir, 'normalized', 'nta_diff_2026-09.csv.gz'), gzipSync(Buffer.from(csv, 'utf8')));

  if (opts.withManifest !== false) {
    const manifest = [
      'file_date,file_no,file_name,raw_path,raw_bytes,raw_sha256,signature_stored,records,normalized_path,retrieved_at,status',
      '2026-09-01,1,diff_20260901.csv,raw,1,hash,Y,1,normalized,2026-09-21T00:00:00Z,ok',
      '2026-09-02,2,diff_20260902.csv,raw,1,hash,Y,1,normalized,2026-09-21T00:00:00Z,ok',
      '2026-09-03,3,diff_20260903.csv,raw,1,hash,Y,4,normalized,2026-09-21T00:00:00Z,ok'
    ].join('\n');
    writeFileSync(join(ntaDir, 'manifest.csv'), `${manifest}\n`, 'utf8');
  }

  return {
    dir,
    cleanup: () => rmSync(dir, { recursive: true, force: true })
  };
}
