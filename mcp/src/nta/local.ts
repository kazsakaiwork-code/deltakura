/**
 * Local mode: read the Deltakura NTA store from disk.
 *
 * Layout expected under DELTAKURA_DATA_DIR (either the data root or the nta
 * directory itself):
 *   nta/manifest.csv                      one row per collected diff file
 *   nta/normalized/nta_diff_YYYY-MM.csv.gz  the change records
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { parseCsvText, streamCsvRecords } from './csv.js';
import {
  assertNoPersonalFields,
  buildDiffSummary,
  buildLookupResult,
  eachDate,
  isValidCorporateNumber,
  normalizeCorporateNumber,
  toChange,
  type CorporateLookupResult,
  type DiffSummaryResult,
  type NtaChange
} from '../query/nta.js';
import type { Config } from '../config.js';

export interface Coverage {
  from: string | null;
  to: string | null;
  days_with_data: number;
}

export class LocalDataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LocalDataError';
  }
}

/** Accept either `<data>` or `<data>/nta` as DELTAKURA_DATA_DIR. */
export function resolveNtaRoot(dataDir: string): string {
  const candidates = [join(dataDir, 'nta'), dataDir];
  for (const c of candidates) {
    if (existsSync(join(c, 'normalized')) || existsSync(join(c, 'manifest.csv'))) return c;
  }
  throw new LocalDataError(
    `DELTAKURA_DATA_DIR=${dataDir} does not look like a Deltakura data directory: ` +
      'expected either <dir>/nta/normalized or <dir>/normalized to exist.'
  );
}

interface ManifestEntry {
  date: string;
  records: number;
  status: string;
}

const manifestCache = new Map<string, { mtimeMs: number; entries: ManifestEntry[] }>();

export function readManifest(root: string): ManifestEntry[] {
  const path = join(root, 'manifest.csv');
  if (!existsSync(path)) return [];
  const { mtimeMs } = statSync(path);
  const hit = manifestCache.get(path);
  if (hit && hit.mtimeMs === mtimeMs) return hit.entries;

  const rows = parseCsvText(readFileSync(path, 'utf8'));
  if (!rows.length) return [];
  const header = rows[0].map((h) => h.trim());
  assertNoPersonalFields(header);
  const idx = Object.fromEntries(header.map((h, i) => [h, i]));
  const entries: ManifestEntry[] = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    if (row.length === 1 && row[0] === '') continue;
    const date = row[idx.file_date];
    if (!date) continue;
    entries.push({
      date,
      records: Number(row[idx.records] ?? 0) || 0,
      status: row[idx.status] ?? ''
    });
  }
  entries.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  manifestCache.set(path, { mtimeMs, entries });
  return entries;
}

export function coverageFrom(root: string): Coverage {
  const entries = readManifest(root).filter((e) => e.status === 'ok' || e.status === '');
  if (entries.length) {
    return { from: entries[0].date, to: entries.at(-1)!.date, days_with_data: entries.length };
  }
  // No manifest: fall back to the month range implied by the normalized filenames.
  const months = listNormalized(root).map((f) => monthOf(f)).filter((m): m is string => Boolean(m)).sort();
  if (!months.length) return { from: null, to: null, days_with_data: 0 };
  return { from: `${months[0]}-01`, to: `${months.at(-1)}-31`, days_with_data: 0 };
}

export function listNormalized(root: string): string[] {
  const dir = join(root, 'normalized');
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith('.csv.gz') || f.endsWith('.csv'))
    .sort()
    .map((f) => join(dir, f));
}

function monthOf(path: string): string | undefined {
  const m = /(\d{4}-\d{2})\.csv(\.gz)?$/.exec(path);
  return m?.[1];
}

function assertScanBudget(files: string[], config: Config): void {
  let total = 0;
  for (const f of files) total += statSync(f).size;
  if (total > config.maxScanBytes) {
    throw new LocalDataError(
      `refusing to scan ${total} bytes of normalized diffs (limit ${config.maxScanBytes}). ` +
        'Narrow the date range, or raise DELTAKURA_NTA_MAX_SCAN_BYTES, or use the remote API once it is live.'
    );
  }
}

const lookupCache = new Map<string, CorporateLookupResult>();
const LOOKUP_CACHE_MAX = 500;

export async function lookupCorporateNumberLocal(
  config: Config,
  input: string,
  opts: { language?: 'en' | 'ja'; historyLimit?: number } = {}
): Promise<CorporateLookupResult> {
  const root = resolveNtaRoot(config.dataDir!);
  const corporateNumber = normalizeCorporateNumber(input);
  if (!/^\d{13}$/.test(corporateNumber)) {
    throw new LocalDataError(
      `"${input}" is not a 13-digit 法人番号. Corporate numbers are exactly 13 digits (e.g. 1234567890123).`
    );
  }

  const files = listNormalized(root);
  if (!files.length) {
    throw new LocalDataError(`no normalized diff files under ${join(root, 'normalized')}`);
  }
  // Checked before the cache, so a tightened budget is honoured on every call.
  assertScanBudget(files, config);

  const cacheKey = `${root}|${corporateNumber}|${opts.language ?? 'en'}|${opts.historyLimit ?? 20}`;
  const cached = lookupCache.get(cacheKey);
  if (cached) return cached;

  const changes: NtaChange[] = [];
  let checkedHeader = false;
  for (const file of files) {
    await streamCsvRecords(file, (record) => {
      if (!checkedHeader) {
        assertNoPersonalFields(Object.keys(record));
        checkedHeader = true;
      }
      if (record.corporate_number !== corporateNumber) return;
      // Fail-closed: a record whose corporate number does not pass the NTA
      // checksum is not a corporation we are willing to describe.
      if (record.corporate_number_valid && record.corporate_number_valid !== 'Y') return;
      changes.push(toChange(record));
    });
  }

  const result = buildLookupResult(corporateNumber, changes, {
    mode: 'local',
    coverage: coverageFrom(root),
    language: opts.language,
    historyLimit: opts.historyLimit
  });
  if (!isValidCorporateNumber(corporateNumber)) {
    result.notes.push(
      'This number does not pass the NTA check-digit test, so it is not a valid 法人番号 even if it is 13 digits long.'
    );
  }

  if (lookupCache.size >= LOOKUP_CACHE_MAX) lookupCache.clear();
  lookupCache.set(cacheKey, result);
  return result;
}

export async function diffSummaryLocal(
  config: Config,
  from: string,
  to: string,
  opts: { language?: 'en' | 'ja'; groupByChangeKind?: boolean } = {}
): Promise<DiffSummaryResult> {
  const root = resolveNtaRoot(config.dataDir!);
  const coverage = coverageFrom(root);
  const counts = new Map<string, number>();
  const byKind = opts.groupByChangeKind ? new Map<string, Record<string, number>>() : undefined;

  const manifest = readManifest(root);
  for (const entry of manifest) {
    if (entry.date < from || entry.date > to) continue;
    if (entry.status && entry.status !== 'ok') continue;
    counts.set(entry.date, entry.records);
  }

  const needScan = byKind !== undefined || manifest.length === 0;
  if (needScan) {
    const wanted = new Set(eachDate(from, to));
    const months = new Set([...wanted].map((d) => d.slice(0, 7)));
    const files = listNormalized(root).filter((f) => {
      const m = monthOf(f);
      return !m || months.has(m);
    });
    assertScanBudget(files, config);
    const scanned = new Map<string, number>();
    for (const file of files) {
      await streamCsvRecords(file, (record) => {
        const date = record.file_date;
        if (!date || !wanted.has(date)) return;
        scanned.set(date, (scanned.get(date) ?? 0) + 1);
        if (byKind) {
          const bucket = byKind.get(date) ?? {};
          const code = record.process_code || 'unknown';
          bucket[code] = (bucket[code] ?? 0) + 1;
          byKind.set(date, bucket);
        }
      });
    }
    for (const [date, n] of scanned) if (!counts.has(date)) counts.set(date, n);
  }

  return buildDiffSummary(from, to, counts, byKind, {
    mode: 'local',
    coverage,
    language: opts.language
  });
}
