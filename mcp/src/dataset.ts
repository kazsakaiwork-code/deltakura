/**
 * The compact procurement-statistics table that ships inside this package.
 * Produced by `npm run build-data` from data/pportal/stats_v0.csv.
 */
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export interface ProcurementDataset {
  schema: number;
  dataset: string;
  dataset_title: string;
  grain: string;
  /** SHA-256 (16 hex) of the source CSV this table was built from. Deterministic. */
  source_version: string;
  /** The publisher retrieval date recorded in the source CSV. */
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
  columns: string[];
  fiscal_years: number[];
  /** [prefecture_code, prefecture_name] */
  prefectures: [string, string][];
  sectors: string[];
  row_count: number;
  /**
   * [fiscal_year, prefecture_idx, sector_idx, n_awards, n_corporate,
   *  n_masked_individual, min, q1, median, q3, max, sum]
   */
  rows: number[][];
}

export const COL = {
  FY: 0,
  PREF: 1,
  SECTOR: 2,
  N_AWARDS: 3,
  N_CORPORATE: 4,
  N_MASKED: 5,
  MIN: 6,
  Q1: 7,
  MEDIAN: 8,
  Q3: 9,
  MAX: 10,
  SUM: 11
} as const;

let cached: ProcurementDataset | undefined;

/** Resolve the shipped data file relative to this module - never an absolute literal. */
export function datasetPath(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  // dist/dataset.js -> <package root>/data/procurement-stats.json
  return resolve(here, '..', 'data', 'procurement-stats.json');
}

export function loadProcurementDataset(path?: string): ProcurementDataset {
  if (!path && cached) return cached;
  const file = path ?? datasetPath();
  let text: string;
  try {
    text = readFileSync(file, 'utf8');
  } catch (err) {
    throw new Error(
      `procurement statistics data file not found at ${file}. ` +
        'Run `npm run build-data` in the @deltakura/mcp package before building or publishing. ' +
        `(${err instanceof Error ? err.message : String(err)})`
    );
  }
  const parsed = JSON.parse(text) as ProcurementDataset;
  if (parsed.schema !== 1) {
    throw new Error(`unsupported procurement dataset schema ${parsed.schema}; this build understands schema 1`);
  }
  if (!path) cached = parsed;
  return parsed;
}

/** Test seam. */
export function setProcurementDataset(dataset: ProcurementDataset | undefined): void {
  cached = dataset;
}

export function joinDataPath(...parts: string[]): string {
  return join(...parts);
}
