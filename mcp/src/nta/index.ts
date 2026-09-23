/**
 * Mode dispatch: local files when DELTAKURA_DATA_DIR is set, otherwise the
 * hosted API (which is not live yet and says so).
 */
import { dataMode, type Config } from '../config.js';
import type { CorporateLookupResult, DiffSummaryResult } from '../query/nta.js';
import { diffSummaryLocal, lookupCorporateNumberLocal } from './local.js';
import { diffSummaryRemote, lookupCorporateNumberRemote } from './remote.js';

export { LocalDataError } from './local.js';
export { RemoteNotAvailableError } from './remote.js';

export function lookupCorporateNumber(
  config: Config,
  corporateNumber: string,
  opts: { language?: 'en' | 'ja'; historyLimit?: number } = {}
): Promise<CorporateLookupResult> {
  return dataMode(config) === 'local'
    ? lookupCorporateNumberLocal(config, corporateNumber, opts)
    : lookupCorporateNumberRemote(config, corporateNumber, opts);
}

export function diffSummary(
  config: Config,
  from: string,
  to: string,
  opts: { language?: 'en' | 'ja'; groupByChangeKind?: boolean } = {}
): Promise<DiffSummaryResult> {
  return dataMode(config) === 'local'
    ? diffSummaryLocal(config, from, to, opts)
    : diffSummaryRemote(config, from, to, opts);
}
