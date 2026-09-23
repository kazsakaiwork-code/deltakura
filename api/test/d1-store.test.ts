/**
 * The D1 corporate store must not claim to be the complete register unless the
 * operator has said so with CORPORATE_RECORDS = "complete". Until then a miss
 * is reported as a miss against a sample.
 */
import { describe, expect, it } from 'vitest';
import { call, harness } from './helpers.js';

/** Just enough of D1 for the corporate store: an empty register with one day of counts. */
function fakeD1(): D1Database {
  const statement = (sql: string) => {
    const stmt = {
      bind: () => stmt,
      all: async () => {
        if (sql.includes('FROM nta_daily_kind')) return { results: [{ file_date: '2026-09-01', process_code: '01', changes: 7 }] };
        if (sql.includes('FROM nta_daily')) return { results: [{ file_date: '2026-09-01', changes: 7 }] };
        return { results: [] };
      },
      first: async () => ({ lo: '2026-09-01', hi: '2026-09-01', n: 1 })
    };
    return stmt;
  };
  return { prepare: statement } as unknown as D1Database;
}

describe('D1 corporate store completeness', () => {
  it('defaults to a sample: complete=false and the sample note', async () => {
    const { status, body } = await call(harness({ DELTAKURA_MODE: 'prod', DB: fakeD1() }), '/v0/corporate/9000012050000');
    expect(status).toBe(404);
    expect(body.data_source).toBe('d1');
    expect(body.complete).toBe(false);
    expect(body.notes.join(' ')).toContain('fixed sample');
  });

  it('reports the complete register only when CORPORATE_RECORDS=complete', async () => {
    const env = { DELTAKURA_MODE: 'prod', DB: fakeD1(), CORPORATE_RECORDS: 'complete' };
    const { body } = await call(harness(env), '/v0/corporate/9000012050000');
    expect(body.complete).toBe(true);
    expect(body.notes.join(' ')).not.toContain('fixed sample');
  });

  it('says so in /v0/health', async () => {
    const { body } = await call(harness({ DELTAKURA_MODE: 'prod', DB: fakeD1() }), '/v0/health');
    expect(body.data_sources.corporate).toBe('d1');
    expect(body.data_sources.corporate_is_sample).toBe(true);
  });

  it('serves daily counts from D1', async () => {
    const { status, body } = await call(
      harness({ DELTAKURA_MODE: 'prod', DB: fakeD1() }),
      '/v0/corporate/diff-summary?from=2026-09-01&to=2026-09-01'
    );
    expect(status).toBe(200);
    expect(body.data_source).toBe('d1');
  });
});
