/**
 * Production store: the corporate-number change register in D1.
 *
 * The schema is in ../../migrations/0001_init.sql. It deliberately has no
 * column that could hold a natural person - not 担当者, not 氏名, not the
 * publisher's free-text 変更事由 - so a personal string cannot be stored, let
 * alone served.
 *
 * Live in the production environment since 2026-09-23 (see README, "D1"). The
 * binding is optional and the Worker falls back to bundled data without it.
 * `partial` stays true unless CORPORATE_RECORDS = "complete".
 */
import { toChange, type NtaChange } from '../shared.js';
import type { Coverage, CorporateStore, DailyCounts } from './types.js';

const CHANGE_COLUMNS = [
  'corporate_number', 'process_code', 'correct_flag', 'update_date', 'change_date',
  'sequence_number', 'name', 'kind_code', 'prefecture', 'prefecture_code', 'city',
  'close_date', 'close_cause', 'successor_corporate_number', 'assignment_date',
  'latest_flag', 'file_date'
].join(', ');

function rowToRecord(row: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(row)) out[k] = v === null || v === undefined ? '' : String(v);
  return out;
}

export function d1CorporateStore(db: D1Database, opts: { partial?: boolean } = {}): CorporateStore {
  return {
    kind: 'd1',
    // True while nta_change holds only a sample (see CORPORATE_RECORDS in env.ts).
    partial: opts.partial ?? true,

    async lookup(corporateNumber: string): Promise<{ changes: NtaChange[]; coverage: Coverage }> {
      const [rows, coverage] = await Promise.all([
        db
          .prepare(
            `SELECT ${CHANGE_COLUMNS} FROM nta_change WHERE corporate_number = ?1 ` +
              'ORDER BY change_date DESC, sequence_number DESC LIMIT 200'
          )
          .bind(corporateNumber)
          .all<Record<string, unknown>>(),
        coverageOf(db)
      ]);
      const changes = (rows.results ?? []).map((r) => toChange(rowToRecord(r)));
      return { changes, coverage };
    },

    async dailyCounts(from: string, to: string, byKind: boolean): Promise<DailyCounts> {
      const counts = new Map<string, number>();
      const daily = await db
        .prepare('SELECT file_date, changes FROM nta_daily WHERE file_date BETWEEN ?1 AND ?2 ORDER BY file_date')
        .bind(from, to)
        .all<{ file_date: string; changes: number }>();
      for (const row of daily.results ?? []) counts.set(row.file_date, Number(row.changes) || 0);

      let kinds: Map<string, Record<string, number>> | undefined;
      if (byKind) {
        kinds = new Map();
        const rows = await db
          .prepare(
            'SELECT file_date, process_code, changes FROM nta_daily_kind ' +
              'WHERE file_date BETWEEN ?1 AND ?2 ORDER BY file_date'
          )
          .bind(from, to)
          .all<{ file_date: string; process_code: string; changes: number }>();
        for (const row of rows.results ?? []) {
          const bucket = kinds.get(row.file_date) ?? {};
          bucket[row.process_code] = Number(row.changes) || 0;
          kinds.set(row.file_date, bucket);
        }
      }

      return { counts, byKind: kinds, coverage: await coverageOf(db) };
    }
  };
}

async function coverageOf(db: D1Database): Promise<Coverage> {
  const row = await db
    .prepare('SELECT MIN(file_date) AS lo, MAX(file_date) AS hi, COUNT(*) AS n FROM nta_daily')
    .first<{ lo: string | null; hi: string | null; n: number }>();
  return {
    from: row?.lo ?? null,
    to: row?.hi ?? null,
    days_with_data: Number(row?.n ?? 0)
  };
}
