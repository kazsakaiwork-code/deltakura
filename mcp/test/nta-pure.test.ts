import { describe, expect, it } from 'vitest';
import {
  ALLOWED_RECORD_FIELDS,
  assertAllowedOutputFields,
  assertNoPersonalFields,
  buildDiffSummary,
  eachDate,
  isIsoDate,
  isValidCorporateNumber,
  labelCloseCause,
  labelKind,
  labelProcess,
  normalizeCorporateNumber,
  shouldMaskName,
  sortChanges,
  toChange
} from '../src/query/nta.js';

describe('corporate number checksum', () => {
  it('accepts real, checksum-valid numbers', () => {
    for (const n of ['1010001005145', '1000020249203', '7000012050002']) {
      expect(isValidCorporateNumber(n), n).toBe(true);
    }
  });

  it('rejects a number whose check digit is wrong', () => {
    expect(isValidCorporateNumber('2010001005145')).toBe(false);
    expect(isValidCorporateNumber('9999999999998')).toBe(false);
    expect(isValidCorporateNumber('1234567890123')).toBe(false);
  });

  it('rejects anything that is not 13 digits', () => {
    expect(isValidCorporateNumber('101000100514')).toBe(false);
    expect(isValidCorporateNumber('abcdefghijklm')).toBe(false);
  });

  it('tolerates spaces, hyphens and full-width digits', () => {
    expect(normalizeCorporateNumber('1010-0010-05145')).toBe('1010001005145');
    expect(normalizeCorporateNumber('１０１０００１００５１４５')).toBe('1010001005145');
    expect(isValidCorporateNumber('１０１０ ００１０ ０５１４５')).toBe(true);
  });
});

describe('code labels', () => {
  it('labels known codes in both languages', () => {
    expect(labelKind('301')).toEqual({ code: '301', ja: '株式会社', en: 'kabushiki kaisha (joint-stock company)' });
    expect(labelProcess('12')).toMatchObject({ ja: '国内所在地の変更' });
    expect(labelCloseCause('11')).toMatchObject({ en: 'dissolved by merger' });
  });

  it('does not invent a label for an unknown code', () => {
    expect(labelKind('999')).toEqual({ code: '999', ja: '不明', en: 'unknown' });
    expect(labelCloseCause('')).toBeNull();
  });
});

describe('personal-data guards', () => {
  it('refuses a layout that gained a natural-person column', () => {
    for (const bad of ['担当者名', '代表者氏名', '連絡先電話番号', 'contact_email', 'person_name']) {
      expect(() => assertNoPersonalFields([...ALLOWED_RECORD_FIELDS, bad]), bad).toThrow(/rule R3/);
    }
  });

  it('allows the publisher columns we simply never emit', () => {
    expect(() => assertNoPersonalFields(['street_number', 'post_code', 'furigana', 'change_cause'])).not.toThrow();
  });

  it('keeps the response allowlist closed', () => {
    expect(() => assertAllowedOutputFields(ALLOWED_RECORD_FIELDS)).not.toThrow();
    expect(() => assertAllowedOutputFields(['street_number'])).toThrow(/allowlist/);
  });

  it('masks a bare personal-looking name only on a non-corporate 法人種別', () => {
    expect(shouldMaskName('499', '山田太郎')).toBe(true);
    expect(shouldMaskName('401', '佐藤花子')).toBe(true);
    // A registered corporation keeps its name: the state publishes it.
    expect(shouldMaskName('301', '山田太郎商店')).toBe(false);
    expect(shouldMaskName('301', '山田太郎')).toBe(false);
    // Corporate tokens are enough to keep the name even on 499.
    expect(shouldMaskName('499', '山田太郎商店')).toBe(false);
    expect(shouldMaskName('499', '架空地区自治会連合協議会')).toBe(false);
  });

  it('never copies a non-allowlisted source field into a change object', () => {
    const change = toChange({
      corporate_number: '1010001005145',
      process_code: '01',
      correct_flag: '0',
      update_date: '2026-09-01',
      change_date: '2026-08-20',
      sequence_number: '1',
      name: 'テスト架空株式会社',
      kind_code: '301',
      prefecture: '東京都',
      prefecture_code: '13',
      city: '文京区',
      street_number: '架空町１−１−１',
      furigana: 'テストホウジン',
      change_cause: '令和8年9月1日 架空町 合併',
      latest_flag: '1',
      file_date: '2026-09-01'
    });
    const json = JSON.stringify(change);
    expect(json).not.toContain('架空町１−１−１');
    expect(json).not.toContain('テストホウジン');
    expect(json).not.toContain('合併');
    expect(change.name).toBe('テスト架空株式会社');
  });
});

describe('sorting and dates', () => {
  it('sorts newest change first, corrections ahead of the row they correct', () => {
    const mk = (change_date: string, sequence_number: number, correct_flag: boolean) =>
      toChange({
        corporate_number: '1010001005145',
        process_code: '01',
        correct_flag: correct_flag ? '1' : '0',
        update_date: '2026-09-01',
        change_date,
        sequence_number: String(sequence_number),
        name: 'x',
        kind_code: '301',
        prefecture: '東京都',
        prefecture_code: '13',
        city: '文京区',
        file_date: '2026-09-01'
      });
    const sorted = sortChanges([mk('2026-08-01', 1, false), mk('2026-09-01', 1, false), mk('2026-09-01', 2, false)]);
    expect(sorted.map((c) => [c.change_date, c.sequence_number])).toEqual([
      ['2026-09-01', 2],
      ['2026-09-01', 1],
      ['2026-08-01', 1]
    ]);
    const corrected = sortChanges([mk('2026-09-01', 1, false), mk('2026-09-01', 1, true)]);
    expect(corrected[0].correct_flag).toBe(true);
  });

  it('validates and enumerates ISO dates', () => {
    expect(isIsoDate('2026-09-01')).toBe(true);
    expect(isIsoDate('2026-9-1')).toBe(false);
    expect(isIsoDate('2026-13-01')).toBe(false);
    expect(eachDate('2026-09-01', '2026-09-03')).toEqual(['2026-09-01', '2026-09-02', '2026-09-03']);
    expect(eachDate('2026-09-01', '2026-08-31')).toEqual([]);
  });
});

describe('buildDiffSummary', () => {
  it('separates "no file published" from "zero changes"', () => {
    const counts = new Map([
      ['2026-09-01', 10],
      ['2026-09-03', 0]
    ]);
    const r = buildDiffSummary('2026-09-01', '2026-09-03', counts, undefined, {
      mode: 'local',
      coverage: { from: '2026-09-01', to: '2026-09-03', days_with_data: 2 }
    });
    expect(r.days).toEqual([
      { date: '2026-09-01', changes: 10 },
      { date: '2026-09-03', changes: 0 }
    ]);
    expect(r.days_with_data).toBe(2);
    expect(r.days_without_file).toBe(1);
    expect(r.total_changes).toBe(10);
  });
});
