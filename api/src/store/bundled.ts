/**
 * The offline store: JSON bundled into the Worker at build time by
 * `npm run build-data`. This is what `wrangler dev --local` serves, and what
 * production falls back to when a binding is missing.
 */
import procurementJson from '../data/procurement-stats.json';
import ntaJson from '../data/nta-dev.json';
import ntaSampleJson from '../data/nta-sample.json';
import { toChange, type NtaChange, type ProcurementDatasetLike } from '../shared.js';
import type { Coverage, CorporateStore, DailyCounts, ProcurementStore } from './types.js';

interface NtaDev {
  schema: number;
  /** Content hash of data/published/nta/summary.json. No build timestamp: the
   *  bundle must be reproducible from the repository alone. */
  source_version: string;
  note: string;
  coverage: Coverage;
  daily: Record<string, number>;
  daily_by_kind: Record<string, Record<string, number>>;
}

/** The fixed development sample, committed separately and never rebuilt by a
 *  normal `npm run build-data`. Counts live in nta-dev.json; records live here. */
interface NtaSample {
  schema: number;
  rule: string;
  sample_size: number;
  sample_is_partial: boolean;
  records: Record<string, string>[];
}

const nta = ntaJson as unknown as NtaDev;
const ntaSample = ntaSampleJson as unknown as NtaSample;
const procurement = procurementJson as unknown as ProcurementDatasetLike;

export const bundledProcurementStore: ProcurementStore = {
  kind: 'bundled',
  async dataset() {
    return procurement;
  }
};

export const bundledCorporateStore: CorporateStore = {
  kind: 'bundled',
  partial: ntaSample.sample_is_partial !== false,

  async lookup(corporateNumber: string): Promise<{ changes: NtaChange[]; coverage: Coverage }> {
    const changes: NtaChange[] = [];
    for (const record of ntaSample.records) {
      if (record.corporate_number !== corporateNumber) continue;
      changes.push(toChange(record));
    }
    return { changes, coverage: nta.coverage };
  },

  async dailyCounts(from: string, to: string, byKind: boolean): Promise<DailyCounts> {
    const counts = new Map<string, number>();
    for (const [date, n] of Object.entries(nta.daily)) {
      if (date >= from && date <= to) counts.set(date, n);
    }
    let kinds: Map<string, Record<string, number>> | undefined;
    if (byKind) {
      kinds = new Map();
      for (const [date, bucket] of Object.entries(nta.daily_by_kind)) {
        if (date >= from && date <= to) kinds.set(date, bucket);
      }
    }
    return { counts, byKind: kinds, coverage: nta.coverage };
  }
};

export const bundledMeta = {
  procurement_source_version: procurement.source_version,
  procurement_retrieved_at: procurement.source_retrieved_at,
  nta_source_version: nta.source_version,
  nta_sample_size: ntaSample.sample_size,
  nta_coverage: nta.coverage
};
