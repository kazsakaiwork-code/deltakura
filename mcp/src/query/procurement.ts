/**
 * Query engine for the Bet-A procurement statistics.
 *
 * Pure: no node builtins, no I/O. The MCP server and the Cloudflare Worker both
 * import this module so that one question asked two ways gets one answer.
 */

export interface ProcurementDatasetLike {
  dataset: string;
  dataset_title: string;
  grain: string;
  source_version: string;
  source_retrieved_at: string;
  source_id: string;
  source_url: string;
  license: string;
  attribution: string;
  attribution_secondary: string;
  prefecture_basis: string;
  category_axis: string;
  award_ratio_available: boolean;
  caveats_en: string[];
  caveats_ja: string[];
  fiscal_years: number[];
  prefectures: [string, string][];
  sectors: string[];
  row_count: number;
  rows: number[][];
}

const C = {
  FY: 0, PREF: 1, SECTOR: 2, N_AWARDS: 3, N_CORPORATE: 4, N_MASKED: 5,
  MIN: 6, Q1: 7, MEDIAN: 8, Q3: 9, MAX: 10, SUM: 11
} as const;

export const MAX_BUCKETS = 200;
export const DEFAULT_BUCKET_LIMIT = 25;

export interface ProcurementQuery {
  fiscal_year?: number;
  fiscal_year_from?: number;
  fiscal_year_to?: number;
  sector?: string;
  winner_prefecture?: string;
  include_buckets?: boolean;
  bucket_limit?: number;
  language?: 'en' | 'ja';
}

export interface ProcurementBucket {
  fiscal_year: number;
  prefecture: string;
  prefecture_code: string;
  sector: string;
  n_awards: number;
  n_corporate: number;
  n_masked_individual: number;
  amount_min_jpy: number;
  amount_q1_jpy: number;
  amount_median_jpy: number;
  amount_q3_jpy: number;
  amount_max_jpy: number;
  amount_sum_jpy: number;
}

export interface ProcurementResult {
  dataset: string;
  dataset_title: string;
  grain: string;
  source_version: string;
  source_retrieved_at: string;
  query: {
    fiscal_year?: number;
    fiscal_year_from?: number;
    fiscal_year_to?: number;
    sector?: string;
    winner_prefecture?: string;
  };
  matched_buckets: number;
  totals: {
    n_awards: number;
    n_corporate: number;
    n_masked_individual: number;
    amount_sum_jpy: number;
    amount_mean_jpy: number | null;
    amount_min_jpy: number | null;
    amount_max_jpy: number | null;
  };
  amount_quartiles_jpy: {
    q1: number | null;
    median: number | null;
    q3: number | null;
    exact: boolean;
    method: string;
  };
  award_ratio: { available: false; reason: string };
  buckets?: ProcurementBucket[];
  buckets_truncated?: boolean;
  dimensions: {
    fiscal_years: number[];
    sectors: string[];
    prefectures: string[];
  };
  caveats: string[];
  license: string;
  attribution: string[];
  source_url: string;
  prefecture_basis: string;
  notes: string[];
}

export class UnknownDimensionError extends Error {
  constructor(
    readonly dimension: 'fiscal_year' | 'sector' | 'winner_prefecture',
    readonly value: string | number,
    readonly available: (string | number)[]
  ) {
    super(
      `unknown ${dimension}: ${JSON.stringify(value)}. Available values: ` +
        available.map((v) => String(v)).join(', ')
    );
    this.name = 'UnknownDimensionError';
  }
}

/** NFKC + trim + lowercase; Japanese labels are compared after full-width folding. */
function norm(s: string): string {
  return s.normalize('NFKC').trim().toLowerCase();
}

/** Accepts "東京都", "東京", "13", "Tokyo"-style input for a prefecture. */
function matchPrefecture(dataset: ProcurementDatasetLike, input: string): number[] {
  const q = norm(input);
  const exact: number[] = [];
  const partial: number[] = [];
  dataset.prefectures.forEach(([code, name], i) => {
    const n = norm(name);
    if (n === q || norm(code) === q || norm(code).replace(/^0+/, '') === q.replace(/^0+/, '')) {
      exact.push(i);
      return;
    }
    if (n.startsWith(q) || q.startsWith(n) || n.includes(q)) partial.push(i);
  });
  const hits = exact.length ? exact : partial;
  if (!hits.length) {
    throw new UnknownDimensionError(
      'winner_prefecture',
      input,
      dataset.prefectures.map(([code, name]) => `${name} (${code})`)
    );
  }
  return hits;
}

/** Accepts an exact sector label or a substring of one. */
function matchSector(dataset: ProcurementDatasetLike, input: string): number[] {
  const q = norm(input);
  const exact: number[] = [];
  const partial: number[] = [];
  dataset.sectors.forEach((name, i) => {
    const n = norm(name);
    if (n === q) exact.push(i);
    else if (n.includes(q)) partial.push(i);
  });
  const hits = exact.length ? exact : partial;
  if (!hits.length) throw new UnknownDimensionError('sector', input, dataset.sectors);
  return hits;
}

/**
 * Cumulative share of a bucket's awards at or below `x`, modelling each bucket's
 * five-number summary as a piecewise-linear distribution.
 */
function bucketCdf(row: number[], x: number): number {
  const min = row[C.MIN];
  const q1 = row[C.Q1];
  const med = row[C.MEDIAN];
  const q3 = row[C.Q3];
  const max = row[C.MAX];
  if (x < min) return 0;
  if (x >= max) return 1;
  const seg = (lo: number, hi: number, c0: number, c1: number) =>
    hi <= lo ? c1 : c0 + ((c1 - c0) * (x - lo)) / (hi - lo);
  if (x < q1) return seg(min, q1, 0, 0.25);
  if (x < med) return seg(q1, med, 0.25, 0.5);
  if (x < q3) return seg(med, q3, 0.5, 0.75);
  return seg(q3, max, 0.75, 1);
}

function combinedQuantile(rows: number[][], totalAwards: number, p: number): number {
  let lo = Infinity;
  let hi = -Infinity;
  for (const r of rows) {
    if (r[C.MIN] < lo) lo = r[C.MIN];
    if (r[C.MAX] > hi) hi = r[C.MAX];
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return 0;
  if (lo === hi) return lo;
  const target = p * totalAwards;
  for (let i = 0; i < 80 && hi - lo > 0.5; i++) {
    const mid = (lo + hi) / 2;
    let acc = 0;
    for (const r of rows) acc += r[C.N_AWARDS] * bucketCdf(r, mid);
    if (acc < target) lo = mid;
    else hi = mid;
  }
  return Math.round(hi);
}

const QUARTILE_METHOD_SINGLE =
  'exact: a single bucket matched, so these are the quartiles computed at ingestion over that bucket\'s awards.';
const QUARTILE_METHOD_COMBINED =
  'estimated: the source table stores one five-number summary per 年度 x 都道府県 x セクター bucket, ' +
  'and quartiles are not additive. Each matched bucket is modelled as a piecewise-linear distribution ' +
  'between its min/Q1/median/Q3/max, weighted by its award count, and the combined quantile is inverted ' +
  'from that mixture. Counts, sums, min and max are exact; these three numbers are not.';

const AWARD_RATIO_REASON =
  '調達ポータル does not publish 予定価格, so 落札率 cannot be computed from this source. ' +
  'The field is reserved for the municipal sources that do publish it.';

export function queryProcurementStats(
  dataset: ProcurementDatasetLike,
  query: ProcurementQuery = {}
): ProcurementResult {
  const language = query.language === 'ja' ? 'ja' : 'en';

  if (query.fiscal_year !== undefined && !dataset.fiscal_years.includes(query.fiscal_year)) {
    throw new UnknownDimensionError('fiscal_year', query.fiscal_year, dataset.fiscal_years);
  }
  const prefIdx = query.winner_prefecture ? new Set(matchPrefecture(dataset, query.winner_prefecture)) : undefined;
  const sectorIdx = query.sector ? new Set(matchSector(dataset, query.sector)) : undefined;

  const from = query.fiscal_year ?? query.fiscal_year_from;
  const to = query.fiscal_year ?? query.fiscal_year_to;

  const matched: number[][] = [];
  for (const row of dataset.rows) {
    if (from !== undefined && row[C.FY] < from) continue;
    if (to !== undefined && row[C.FY] > to) continue;
    if (prefIdx && !prefIdx.has(row[C.PREF])) continue;
    if (sectorIdx && !sectorIdx.has(row[C.SECTOR])) continue;
    matched.push(row);
  }

  let nAwards = 0;
  let nCorporate = 0;
  let nMasked = 0;
  let sum = 0;
  let min: number | null = null;
  let max: number | null = null;
  for (const r of matched) {
    nAwards += r[C.N_AWARDS];
    nCorporate += r[C.N_CORPORATE];
    nMasked += r[C.N_MASKED];
    sum += r[C.SUM];
    min = min === null || r[C.MIN] < min ? r[C.MIN] : min;
    max = max === null || r[C.MAX] > max ? r[C.MAX] : max;
  }

  const single = matched.length === 1;
  let q1: number | null = null;
  let median: number | null = null;
  let q3: number | null = null;
  if (single) {
    q1 = matched[0][C.Q1];
    median = matched[0][C.MEDIAN];
    q3 = matched[0][C.Q3];
  } else if (matched.length > 1 && nAwards > 0) {
    q1 = combinedQuantile(matched, nAwards, 0.25);
    median = combinedQuantile(matched, nAwards, 0.5);
    q3 = combinedQuantile(matched, nAwards, 0.75);
  }

  const limit = Math.min(Math.max(query.bucket_limit ?? DEFAULT_BUCKET_LIMIT, 0), MAX_BUCKETS);
  const includeBuckets = query.include_buckets !== false && limit > 0;
  const slice = includeBuckets ? matched.slice(0, limit) : undefined;

  const notes: string[] = [];
  if (matched.length === 0) {
    notes.push(
      'No bucket matched. Either the combination does not occur in the archive, or it was suppressed ' +
        'by the k-anonymity rule (a bucket with fewer than 3 masked individuals is dropped entirely).'
    );
  }
  if (matched.length > 1) {
    notes.push(
      'Quartiles across more than one bucket are estimated, not measured - see amount_quartiles_jpy.method.'
    );
  }
  if (slice && matched.length > slice.length) {
    notes.push(`Showing the first ${slice.length} of ${matched.length} matched buckets; totals cover all of them.`);
  }

  return {
    dataset: dataset.dataset,
    dataset_title: dataset.dataset_title,
    grain: dataset.grain,
    source_version: dataset.source_version,
    source_retrieved_at: dataset.source_retrieved_at,
    query: {
      fiscal_year: query.fiscal_year,
      fiscal_year_from: query.fiscal_year === undefined ? query.fiscal_year_from : undefined,
      fiscal_year_to: query.fiscal_year === undefined ? query.fiscal_year_to : undefined,
      sector: query.sector,
      winner_prefecture: query.winner_prefecture
    },
    matched_buckets: matched.length,
    totals: {
      n_awards: nAwards,
      n_corporate: nCorporate,
      n_masked_individual: nMasked,
      amount_sum_jpy: sum,
      amount_mean_jpy: nAwards > 0 ? Math.round(sum / nAwards) : null,
      amount_min_jpy: min,
      amount_max_jpy: max
    },
    amount_quartiles_jpy: {
      q1,
      median,
      q3,
      exact: single,
      method: single ? QUARTILE_METHOD_SINGLE : QUARTILE_METHOD_COMBINED
    },
    award_ratio: { available: false, reason: AWARD_RATIO_REASON },
    ...(slice
      ? {
          buckets: slice.map((r) => toBucket(dataset, r)),
          buckets_truncated: matched.length > slice.length
        }
      : {}),
    dimensions: {
      fiscal_years: dataset.fiscal_years,
      sectors: dataset.sectors,
      prefectures: dataset.prefectures.map(([, name]) => name)
    },
    caveats: language === 'ja' ? dataset.caveats_ja : dataset.caveats_en,
    license: dataset.license,
    attribution: [dataset.attribution, dataset.attribution_secondary],
    source_url: dataset.source_url,
    prefecture_basis: dataset.prefecture_basis,
    notes
  };
}

function toBucket(dataset: ProcurementDatasetLike, r: number[]): ProcurementBucket {
  const [code, name] = dataset.prefectures[r[C.PREF]];
  return {
    fiscal_year: r[C.FY],
    prefecture: name,
    prefecture_code: code,
    sector: dataset.sectors[r[C.SECTOR]],
    n_awards: r[C.N_AWARDS],
    n_corporate: r[C.N_CORPORATE],
    n_masked_individual: r[C.N_MASKED],
    amount_min_jpy: r[C.MIN],
    amount_q1_jpy: r[C.Q1],
    amount_median_jpy: r[C.MEDIAN],
    amount_q3_jpy: r[C.Q3],
    amount_max_jpy: r[C.MAX],
    amount_sum_jpy: r[C.SUM]
  };
}
