/**
 * Store selection.
 *
 * Production reads the corporate register from D1 and the procurement table
 * from R2. Both bindings are optional: without them - which is every run until
 * a Cloudflare account is provisioned - the Worker answers from the JSON bundled
 * at build time, and says so in the response and in /v0/health.
 */
import { isProd, type Env } from '../env.js';
import type { ProcurementDatasetLike } from '../shared.js';
import { bundledCorporateStore, bundledProcurementStore } from './bundled.js';
import { d1CorporateStore } from './d1.js';
import type { CorporateStore, ProcurementStore } from './types.js';

export * from './types.js';
export { bundledMeta } from './bundled.js';

export const PROCUREMENT_R2_KEY = 'stats/procurement-stats.json';

let r2Cache: { dataset: ProcurementDatasetLike; at: number } | undefined;
const R2_CACHE_MS = 10 * 60 * 1000;

function r2ProcurementStore(bucket: R2Bucket): ProcurementStore {
  return {
    kind: 'r2',
    async dataset() {
      const now = Date.now();
      if (r2Cache && now - r2Cache.at < R2_CACHE_MS) return r2Cache.dataset;
      const object = await bucket.get(PROCUREMENT_R2_KEY);
      if (!object) throw new Error(`${PROCUREMENT_R2_KEY} is not in the archive bucket`);
      const dataset = (await object.json()) as ProcurementDatasetLike;
      r2Cache = { dataset, at: now };
      return dataset;
    }
  };
}

export function corporateStore(env: Env): CorporateStore {
  if (isProd(env) && env.DB) return d1CorporateStore(env.DB);
  return bundledCorporateStore;
}

export function procurementStore(env: Env): ProcurementStore {
  if (isProd(env) && env.ARCHIVE) return r2ProcurementStore(env.ARCHIVE);
  return bundledProcurementStore;
}

/** Test seam. */
export function resetProcurementCache(): void {
  r2Cache = undefined;
}
