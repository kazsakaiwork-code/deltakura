import type { NtaChange, ProcurementDatasetLike } from '../shared.js';

export interface Coverage {
  from: string | null;
  to: string | null;
  days_with_data: number;
}

export interface DailyCounts {
  counts: Map<string, number>;
  byKind?: Map<string, Record<string, number>>;
  coverage: Coverage;
}

export interface CorporateStore {
  readonly kind: 'bundled' | 'd1';
  /** True when this store holds only a development sample. */
  readonly partial: boolean;
  lookup(corporateNumber: string): Promise<{ changes: NtaChange[]; coverage: Coverage }>;
  dailyCounts(from: string, to: string, byKind: boolean): Promise<DailyCounts>;
}

export interface ProcurementStore {
  readonly kind: 'bundled';
  dataset(): Promise<ProcurementDatasetLike>;
}
