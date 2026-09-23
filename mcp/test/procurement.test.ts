import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync, statSync } from 'node:fs';
import {
  DEFAULT_BUCKET_LIMIT,
  queryProcurementStats,
  UnknownDimensionError
} from '../src/query/procurement.js';
import { datasetPath, loadProcurementDataset } from '../src/dataset.js';
import { fixtureDataset } from './fixtures.js';

describe('queryProcurementStats', () => {
  it('aggregates every bucket when no filter is given', () => {
    const r = queryProcurementStats(fixtureDataset);
    expect(r.matched_buckets).toBe(5);
    expect(r.totals.n_awards).toBe(43);
    expect(r.totals.n_corporate).toBe(39);
    expect(r.totals.n_masked_individual).toBe(4);
    expect(r.totals.amount_sum_jpy).toBe(16195);
    expect(r.totals.amount_min_jpy).toBe(5);
    expect(r.totals.amount_max_jpy).toBe(4000);
    expect(r.totals.amount_mean_jpy).toBe(377);
  });

  it('filters by fiscal year', () => {
    const r = queryProcurementStats(fixtureDataset, { fiscal_year: 2024 });
    expect(r.matched_buckets).toBe(2);
    expect(r.totals.n_awards).toBe(14);
    expect(r.query.fiscal_year).toBe(2024);
  });

  it('filters by a fiscal-year range', () => {
    const r = queryProcurementStats(fixtureDataset, { fiscal_year_from: 2025, fiscal_year_to: 2025 });
    expect(r.matched_buckets).toBe(3);
    expect(r.totals.n_awards).toBe(29);
  });

  it('matches a prefecture by full name, short name and code', () => {
    for (const value of ['東京都', '東京', '13']) {
      const r = queryProcurementStats(fixtureDataset, { winner_prefecture: value });
      expect(r.matched_buckets, value).toBe(3);
      expect(r.totals.n_awards, value).toBe(36);
    }
  });

  it('matches a sector by substring', () => {
    const r = queryProcurementStats(fixtureDataset, { sector: '情報' });
    expect(r.matched_buckets).toBe(2);
    expect(r.buckets?.every((b) => b.sector === '総務・情報通信')).toBe(true);
  });

  it('returns exact quartiles for a single bucket and says so', () => {
    const r = queryProcurementStats(fixtureDataset, {
      fiscal_year: 2024,
      winner_prefecture: '大阪府',
      sector: '防衛'
    });
    expect(r.matched_buckets).toBe(1);
    expect(r.amount_quartiles_jpy).toMatchObject({ q1: 1000, median: 2000, q3: 3000, exact: true });
    expect(r.amount_quartiles_jpy.method).toContain('exact');
  });

  it('estimates quartiles across buckets, labels them and keeps them ordered', () => {
    const r = queryProcurementStats(fixtureDataset, { fiscal_year: 2024 });
    expect(r.amount_quartiles_jpy.exact).toBe(false);
    expect(r.amount_quartiles_jpy.method).toContain('estimated');
    const { q1, median, q3 } = r.amount_quartiles_jpy;
    expect(q1!).toBeLessThanOrEqual(median!);
    expect(median!).toBeLessThanOrEqual(q3!);
    expect(q1!).toBeGreaterThanOrEqual(r.totals.amount_min_jpy!);
    expect(q3!).toBeLessThanOrEqual(r.totals.amount_max_jpy!);
    expect(r.notes.some((n) => n.includes('estimated'))).toBe(true);
  });

  it('never claims an award ratio', () => {
    const r = queryProcurementStats(fixtureDataset);
    expect(r.award_ratio.available).toBe(false);
    expect(r.award_ratio.reason).toContain('予定価格');
  });

  it('always carries licence, attribution and caveats', () => {
    const r = queryProcurementStats(fixtureDataset);
    expect(r.license).toBeTruthy();
    expect(r.attribution).toContain('出典：調達ポータル（https://www.p-portal.go.jp/）');
    expect(r.attribution).toContain('出典：国税庁法人番号公表サイト（国税庁）');
    expect(r.caveats.length).toBeGreaterThan(0);
    expect(queryProcurementStats(fixtureDataset, { language: 'ja' }).caveats).toEqual(fixtureDataset.caveats_ja);
  });

  it('reports an empty match rather than pretending', () => {
    const r = queryProcurementStats(fixtureDataset, { fiscal_year: 2024, sector: '総務・情報通信', winner_prefecture: '不明' });
    expect(r.matched_buckets).toBe(0);
    expect(r.totals.n_awards).toBe(0);
    expect(r.amount_quartiles_jpy.median).toBeNull();
    expect(r.notes.join(' ')).toContain('k-anonymity');
  });

  it('lists the available values when a dimension is unknown', () => {
    expect(() => queryProcurementStats(fixtureDataset, { fiscal_year: 1999 })).toThrow(UnknownDimensionError);
    try {
      queryProcurementStats(fixtureDataset, { sector: 'nope' });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(UnknownDimensionError);
      expect((err as UnknownDimensionError).available).toEqual(fixtureDataset.sectors);
    }
  });

  it('truncates buckets but keeps totals complete', () => {
    const r = queryProcurementStats(fixtureDataset, { bucket_limit: 2 });
    expect(r.buckets).toHaveLength(2);
    expect(r.buckets_truncated).toBe(true);
    expect(r.totals.n_awards).toBe(43);
    expect(DEFAULT_BUCKET_LIMIT).toBe(25);
  });

  it('can omit buckets entirely', () => {
    const r = queryProcurementStats(fixtureDataset, { include_buckets: false });
    expect(r.buckets).toBeUndefined();
    expect(r.matched_buckets).toBe(5);
  });
});

describe('the shipped dataset', () => {
  const path = datasetPath();
  const present = existsSync(path);

  it.skipIf(!present)('stays inside the 3 MB package budget', () => {
    expect(statSync(path).size).toBeLessThan(3 * 1024 * 1024);
  });

  it.skipIf(!present)('answers a real query and carries no party name', () => {
    const dataset = loadProcurementDataset(path);
    const r = queryProcurementStats(dataset, { fiscal_year: 2025, winner_prefecture: '東京都' });
    expect(r.totals.n_awards).toBeGreaterThan(0);
    expect(r.amount_quartiles_jpy.median).toBeGreaterThan(0);
    const raw = readFileSync(path, 'utf8');
    expect(raw).not.toMatch(/winner_name|担当者|氏名/);
  });
});
