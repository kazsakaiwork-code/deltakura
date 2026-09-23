/**
 * CORS, JSON responses and IP-based rate limiting.
 *
 * Rate limiting is deliberately in-isolate: the free plan allows roughly a
 * thousand KV writes a day per namespace, and a KV write per request would
 * exhaust that in minutes ("Worker cost discipline"). One
 * isolate's window is therefore a best-effort control against a single noisy
 * client, not a distributed quota - which is the right trade while the whole
 * service is free and read-only.
 */
import { intVar, type Env } from './env.js';

const DEFAULT_ORIGINS = [
  'https://deltakura.dev',
  'https://www.deltakura.dev',
  'https://deltakura-signals.web.app',
  'https://deltakura-signals.firebaseapp.com'
];

const LOCAL_ORIGIN = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;

export function allowedOrigins(env: Env): string[] {
  const configured = (env.ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return configured.length ? configured : DEFAULT_ORIGINS;
}

export function corsHeaders(request: Request, env: Env): Record<string, string> {
  const origin = request.headers.get('origin');
  const base: Record<string, string> = {
    'access-control-allow-methods': 'GET, POST, OPTIONS',
    'access-control-allow-headers': 'content-type',
    'access-control-max-age': '86400',
    vary: 'Origin'
  };
  if (!origin) return base;
  const list = allowedOrigins(env);
  if (list.includes('*')) return { ...base, 'access-control-allow-origin': '*' };
  if (list.includes(origin) || LOCAL_ORIGIN.test(origin)) {
    return { ...base, 'access-control-allow-origin': origin };
  }
  return base;
}

export function json(
  body: unknown,
  init: { status?: number; headers?: Record<string, string> } = {}
): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status: init.status ?? 200,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      ...init.headers
    }
  });
}

export function errorResponse(
  status: number,
  error: string,
  message: string,
  extra: Record<string, unknown> = {},
  headers: Record<string, string> = {}
): Response {
  return json({ error, message, ...extra }, { status, headers });
}

export function preflight(request: Request, env: Env): Response {
  return new Response(null, { status: 204, headers: corsHeaders(request, env) });
}

/* --------------------------------------------------------- rate limiting --- */

interface Window {
  count: number;
  resetAt: number;
}

const buckets = new Map<string, Window>();
const MAX_TRACKED_CLIENTS = 20_000;

/**
 * A per-isolate, per-day salt. The raw IP is hashed with it and never stored,
 * and the salt itself dies with the isolate, so no visitor is trackable across
 * days or across isolates (legal_boundaries section 2.7).
 */
let saltDay = '';
let saltValue = '';

async function clientKey(request: Request, env: Env): Promise<string> {
  const ip =
    request.headers.get('cf-connecting-ip') ??
    request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ??
    'unknown';
  const day = new Date().toISOString().slice(0, 10);
  if (saltDay !== day) {
    saltDay = day;
    saltValue = `${env.IP_HASH_SALT ?? 'deltakura-dev-salt'}:${day}:${crypto.randomUUID()}`;
  }
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${saltValue}:${ip}`));
  return [...new Uint8Array(digest).slice(0, 12)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export interface RateVerdict {
  allowed: boolean;
  limit: number;
  remaining: number;
  retryAfterSeconds: number;
  key: string;
}

export async function rateLimit(
  request: Request,
  env: Env,
  opts: { limit: number; scope: string; now?: number } = { limit: 60, scope: 'read' }
): Promise<RateVerdict> {
  const now = opts.now ?? Date.now();
  const key = `${opts.scope}:${await clientKey(request, env)}`;
  let window = buckets.get(key);
  if (!window || window.resetAt <= now) {
    window = { count: 0, resetAt: now + 60_000 };
    if (buckets.size > MAX_TRACKED_CLIENTS) buckets.clear();
    buckets.set(key, window);
  }
  window.count++;
  const remaining = Math.max(0, opts.limit - window.count);
  return {
    allowed: window.count <= opts.limit,
    limit: opts.limit,
    remaining,
    retryAfterSeconds: Math.max(1, Math.ceil((window.resetAt - now) / 1000)),
    key
  };
}

export function rateLimitHeaders(verdict: RateVerdict): Record<string, string> {
  return {
    'x-ratelimit-limit': String(verdict.limit),
    'x-ratelimit-remaining': String(verdict.remaining),
    ...(verdict.allowed ? {} : { 'retry-after': String(verdict.retryAfterSeconds) })
  };
}

export function readLimit(env: Env): number {
  return intVar(env.RATE_LIMIT_PER_MINUTE, 60);
}

export function intentLimit(env: Env): number {
  return intVar(env.INTENT_RATE_LIMIT_PER_MINUTE, 20);
}

/** Test seam: drop every rate-limit window. */
export function resetRateLimits(): void {
  buckets.clear();
  saltDay = '';
}
