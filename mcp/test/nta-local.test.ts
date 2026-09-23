import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';
import { diffSummaryLocal, lookupCorporateNumberLocal, LocalDataError, resolveNtaRoot } from '../src/nta/local.js';
import { makeNtaFixture, type NtaFixture } from './fixtures.js';

let fixture: NtaFixture;

beforeAll(() => {
  fixture = makeNtaFixture();
});
afterAll(() => fixture.cleanup());

function cfg(dir: string, env: Record<string, string> = {}) {
  return loadConfig({ DELTAKURA_DATA_DIR: dir, ...env } as NodeJS.ProcessEnv);
}

describe('local mode', () => {
  it('accepts either the data root or the nta directory', () => {
    expect(resolveNtaRoot(fixture.dir)).toContain('nta');
    expect(resolveNtaRoot(`${fixture.dir}/nta`)).toContain('nta');
    expect(() => resolveNtaRoot(`${fixture.dir}/nowhere`)).toThrow(LocalDataError);
  });

  it('returns name, address prefecture and the latest change', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '1010001005145');
    expect(r.mode).toBe('local');
    expect(r.found).toBe(true);
    expect(r.checksum_valid).toBe(true);
    expect(r.name).toBe('テスト架空第二株式会社');
    expect(r.address).toEqual({ prefecture: '東京都', prefecture_code: '13', city: '文京区' });
    expect(r.kind).toMatchObject({ code: '301', ja: '株式会社' });
    expect(r.latest_change).toMatchObject({
      change_date: '2026-09-02',
      process: { code: '11', ja: '商号又は名称の変更', en: 'name changed' }
    });
    expect(r.change_count).toBe(2);
  });

  it('orders history newest first and surfaces a closure', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '1000020249203');
    expect(r.history.map((h) => h.change_date)).toEqual(['2026-09-03', '2026-09-01']);
    expect(r.latest_change?.close_date).toBe('2026-09-03');
    expect(r.latest_change?.close_cause).toMatchObject({ code: '01', en: 'liquidation completed' });
  });

  it('never emits a field outside the response allowlist', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '1010001005145');
    const json = JSON.stringify(r);
    // street_number, furigana and change_cause are in the source file and must not travel.
    expect(json).not.toContain('架空町１−１−１');
    expect(json).not.toContain('テストホウジン');
    expect(json).not.toContain('合併');
  });

  it('masks a short, token-free name on a non-corporate 法人種別 (fail-closed)', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '7000012050002');
    expect(r.found).toBe(true);
    expect(r.name).toBeNull();
    expect(JSON.stringify(r)).not.toContain('山田太郎');
    expect(r.latest_change?.name_masked_reason).toContain('fail-closed');
  });

  it('ignores a record whose corporate number failed the checksum at ingest', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '9999999999998');
    expect(r.found).toBe(false);
    expect(r.checksum_valid).toBe(false);
    expect(JSON.stringify(r)).not.toContain('無効番号株式会社');
    expect(r.notes.join(' ')).toContain('check-digit');
  });

  it('says "no change published", not "does not exist"', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '9000012050000');
    expect(r.found).toBe(false);
    expect(r.checksum_valid).toBe(true);
    expect(r.notes.join(' ')).toContain('No change');
    expect(r.notes.join(' ')).toContain('says nothing about whether the corporation exists');
  });

  it('rejects anything that is not 13 digits', async () => {
    await expect(lookupCorporateNumberLocal(cfg(fixture.dir), '123')).rejects.toThrow(/13-digit/);
  });

  it('refuses a file whose layout gained a personal-data column', async () => {
    const tainted = makeNtaFixture({ extraHeader: '担当者名' });
    try {
      await expect(lookupCorporateNumberLocal(cfg(tainted.dir), '1010001005145')).rejects.toThrow(/rule R3/);
    } finally {
      tainted.cleanup();
    }
  });

  it('honours the scan budget', async () => {
    await expect(
      lookupCorporateNumberLocal(cfg(fixture.dir, { DELTAKURA_NTA_MAX_SCAN_BYTES: '10' }), '1010001005145')
    ).rejects.toThrow(/refusing to scan/);
  });

  it('carries the NTA licence, attribution and caveats', async () => {
    const r = await lookupCorporateNumberLocal(cfg(fixture.dir), '1010001005145');
    expect(r.attribution).toContain('国税庁法人番号公表サイト');
    expect(r.license).toContain('公共データ利用規約');
    expect(r.caveats.length).toBeGreaterThan(0);
    const ja = await lookupCorporateNumberLocal(cfg(fixture.dir), '1010001005145', { language: 'ja' });
    expect(ja.caveats[0]).toContain('差分');
  });
});

describe('diff summary (local)', () => {
  it('reads daily counts from the manifest', async () => {
    const r = await diffSummaryLocal(cfg(fixture.dir), '2026-09-01', '2026-09-03');
    expect(r.mode).toBe('local');
    expect(r.days).toEqual([
      { date: '2026-09-01', changes: 1 },
      { date: '2026-09-02', changes: 1 },
      { date: '2026-09-03', changes: 4 }
    ]);
    expect(r.total_changes).toBe(6);
    expect(r.days_with_data).toBe(3);
    expect(r.days_without_file).toBe(0);
    expect(r.mean_changes_per_published_day).toBe(2);
  });

  it('reports dates with no published file instead of zeroing them', async () => {
    const r = await diffSummaryLocal(cfg(fixture.dir), '2026-09-01', '2026-09-06');
    expect(r.days_without_file).toBe(3);
    expect(r.days.map((d) => d.date)).not.toContain('2026-09-05');
    expect(r.notes.join(' ')).toContain('weekends');
  });

  it('groups by change kind when asked, with labels', async () => {
    const r = await diffSummaryLocal(cfg(fixture.dir), '2026-09-01', '2026-09-03', { groupByChangeKind: true });
    const third = r.days.find((d) => d.date === '2026-09-03');
    expect(third?.by_change_kind).toEqual({ '01': 2, '11': 1, '21': 1 });
    expect(r.change_kind_labels?.['21']).toMatchObject({ en: 'registry record closed' });
  });

  it('falls back to scanning the records when there is no manifest', async () => {
    const noManifest = makeNtaFixture({ withManifest: false });
    try {
      const r = await diffSummaryLocal(cfg(noManifest.dir), '2026-09-01', '2026-09-03');
      expect(r.total_changes).toBe(6);
      expect(r.coverage.days_with_data).toBe(0);
    } finally {
      noManifest.cleanup();
    }
  });

  it('notes when the requested range starts before the collected window', async () => {
    const r = await diffSummaryLocal(cfg(fixture.dir), '2026-01-01', '2026-09-03');
    expect(r.notes.join(' ')).toContain('before the collected window');
  });
});
