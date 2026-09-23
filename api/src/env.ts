/**
 * Worker bindings and vars.
 *
 * Every binding is optional: `wrangler dev --local` runs with none of them and
 * answers from the JSON bundled into the Worker, so the API is developable and
 * testable before a Cloudflare account exists.
 */

export interface Env {
  /** "dev" (bundled JSON) or "prod" (D1/R2, falling back to bundled). */
  DELTAKURA_MODE?: string;
  /** Comma-separated origin allowlist for CORS. */
  ALLOWED_ORIGINS?: string;
  /** Requests per minute per client for read endpoints. */
  RATE_LIMIT_PER_MINUTE?: string;
  /** Requests per minute per client for /v0/intent. */
  INTENT_RATE_LIMIT_PER_MINUTE?: string;
  /** Salt for hashing client IPs. Rotated daily on top of this value; never stored. */
  IP_HASH_SALT?: string;
  /** Release marker surfaced by /v0/health. */
  RELEASE?: string;

  /** Intent counters (the pay-intent metric). */
  KV_INTENT?: KVNamespace;
  /** Public metrics rollups. */
  KV_METRICS?: KVNamespace;
  /** Corporate-number change register in production. */
  DB?: D1Database;
  /** Archive bucket; holds the procurement table in production. */
  ARCHIVE?: R2Bucket;
}

export function isProd(env: Env): boolean {
  return (env.DELTAKURA_MODE ?? 'dev').toLowerCase() === 'prod';
}

export function intVar(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}
