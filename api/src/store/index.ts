/**
 * Store selection.
 *
 * Production reads the corporate register from D1 when the binding is attached;
 * without it - which is every run until a Cloudflare account is provisioned -
 * the Worker answers from the JSON bundled at build time, and says so in the
 * response and in /v0/health.
 *
 * The procurement table is always bundled. It is small, it changes only when
 * the data is rebuilt, and serving it from the Worker bundle keeps the deploy on
 * free-tier products only (KV + D1). There is deliberately no R2 path: R2 needs
 * a paid subscription and is a post-gate option that requires explicit approval.
 */
import { isProd, type Env } from '../env.js';
import { bundledCorporateStore, bundledProcurementStore } from './bundled.js';
import { d1CorporateStore } from './d1.js';
import type { CorporateStore, ProcurementStore } from './types.js';

export * from './types.js';
export { bundledMeta } from './bundled.js';

export function corporateStore(env: Env): CorporateStore {
  if (isProd(env) && env.DB) return d1CorporateStore(env.DB);
  return bundledCorporateStore;
}

export function procurementStore(_env: Env): ProcurementStore {
  return bundledProcurementStore;
}
