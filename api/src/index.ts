/**
 * Deltakura v0 read API — a Cloudflare Worker.
 *
 * Read-only apart from /v0/intent, which increments an anonymous counter.
 * Nothing here authenticates a user, stores a raw IP, sets a cookie, or returns
 * an individual's data.
 *
 * Endpoints
 *   GET  /v0/health
 *   GET  /v0/procurement/stats
 *   GET  /v0/corporate/diff-summary
 *   GET  /v0/corporate/:number
 *   GET  /v0/feeds/nta-diff.json
 *   POST /v0/intent
 *   GET  /v0/intent
 */
import { isProd, type Env } from './env.js';
import {
  allowedOrigins,
  corsHeaders,
  errorResponse,
  intentLimit,
  json,
  preflight,
  rateLimit,
  rateLimitHeaders,
  readLimit,
  SECURITY_HEADERS
} from './http.js';
import { handleProcurementStats } from './routes/procurement.js';
import { handleCorporateLookup, handleDiffSummary } from './routes/corporate.js';
import { handleNtaDiffFeed } from './routes/feeds.js';
import { handleIntentGet, handleIntentPost, products } from './routes/intent.js';
import { bundledMeta, corporateStore, procurementStore } from './store/index.js';

// A Worker entry module may only export the default handler (plus entrypoint
// classes); any other named export makes workerd refuse to start.
const API_VERSION = 'v0';
const RELEASE_FALLBACK = '0.1.0';

const ROUTES = [
  { method: 'GET', path: '/v0/health', description: 'liveness, data sources and build metadata' },
  {
    method: 'GET',
    path: '/v0/procurement/stats',
    description: 'national procurement award statistics',
    params: ['fiscal_year', 'fiscal_year_from', 'fiscal_year_to', 'sector', 'winner_prefecture', 'include_buckets', 'bucket_limit', 'lang']
  },
  { method: 'GET', path: '/v0/corporate/{corporate_number}', description: 'one 法人番号', params: ['history', 'lang'] },
  {
    method: 'GET',
    path: '/v0/corporate/diff-summary',
    description: 'daily register-change counts',
    params: ['from', 'to', 'group_by=change_kind', 'lang']
  },
  { method: 'GET', path: '/v0/feeds/nta-diff.json', description: 'JSON Feed of daily change volume', params: ['days'] },
  { method: 'POST', path: '/v0/intent', description: 'record a pay-intent click', params: ['body: {product, kind, client_id}'] },
  { method: 'GET', path: '/v0/intent', description: 'pay-intent counters', params: ['days'] }
];

function index(request: Request, env: Env): Response {
  return json({
    service: 'deltakura-api',
    api_version: API_VERSION,
    release: env.RELEASE ?? RELEASE_FALLBACK,
    documentation: 'https://github.com/kazsakaiwork-code/deltakura/tree/main/api',
    endpoints: ROUTES,
    attribution: [
      '出典：調達ポータル（https://www.p-portal.go.jp/）',
      '出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）'
    ],
    note: 'Every response that carries a number also carries its licence, attribution and caveats.',
    cors_allowed_origins: allowedOrigins(env),
    ...(new URL(request.url).pathname === '/' ? { hint: 'The API lives under /v0.' } : {})
  });
}

async function health(env: Env): Promise<Response> {
  const corporate = corporateStore(env);
  const stats = procurementStore(env);
  return json({
    status: 'ok',
    api_version: API_VERSION,
    release: env.RELEASE ?? RELEASE_FALLBACK,
    mode: isProd(env) ? 'prod' : 'dev',
    time: new Date().toISOString(),
    data_sources: {
      procurement: stats.kind,
      corporate: corporate.kind,
      corporate_is_sample: corporate.partial
    },
    bindings: {
      KV_INTENT: Boolean(env.KV_INTENT),
      KV_METRICS: Boolean(env.KV_METRICS),
      DB: Boolean(env.DB)
    },
    bundled: bundledMeta,
    intent_products: products(env)
  });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const cors = corsHeaders(request, env);
    try {
      if (request.method === 'OPTIONS') return withHeaders(preflight(request, env), SECURITY_HEADERS);

      const url = new URL(request.url);
      const path = url.pathname.replace(/\/+$/, '') || '/';

      if (request.method !== 'GET' && request.method !== 'POST' && request.method !== 'HEAD') {
        return withHeaders(errorResponse(405, 'method_not_allowed', `${request.method} is not supported`), cors);
      }

      const isIntent = path === '/v0/intent';
      const verdict = await rateLimit(request, env, {
        limit: isIntent ? intentLimit(env) : readLimit(env),
        scope: isIntent ? 'intent' : 'read'
      });
      const limitHeaders = rateLimitHeaders(verdict);
      if (!verdict.allowed) {
        return withHeaders(
          errorResponse(429, 'rate_limited', `Too many requests. Limit is ${verdict.limit} per minute.`, {
            retry_after_seconds: verdict.retryAfterSeconds
          }),
          { ...cors, ...limitHeaders }
        );
      }

      const response = await route(request, env, ctx, path);
      return withHeaders(response, { ...cors, ...limitHeaders });
    } catch (err) {
      // The detail goes to the Worker's own log, never to the client: an
      // exception message can carry a binding name, a query or a stack frame.
      console.error('unhandled', err);
      return withHeaders(errorResponse(500, 'internal_error', 'Internal error.'), cors);
    }
  }
};

async function route(request: Request, env: Env, ctx: ExecutionContext, path: string): Promise<Response> {
  if (path === '/' || path === '/v0') return index(request, env);
  if (path === '/v0/health') return health(env);

  if (path === '/v0/procurement/stats') return handleProcurementStats(request, env);
  if (path === '/v0/corporate/diff-summary') return handleDiffSummary(request, env);
  if (path === '/v0/feeds/nta-diff.json') return handleNtaDiffFeed(request, env);

  if (path === '/v0/intent') {
    if (request.method === 'POST') return handleIntentPost(request, env, ctx);
    return handleIntentGet(request, env);
  }

  const corporate = /^\/v0\/corporate\/([^/]+)$/.exec(path);
  if (corporate) return handleCorporateLookup(request, env, corporate[1]);

  return errorResponse(404, 'not_found', `No route for ${request.method} ${path.slice(0, 120)}`, {
    endpoints: ROUTES.map((r) => `${r.method} ${r.path}`)
  });
}

function withHeaders(response: Response, headers: Record<string, string>): Response {
  const merged = new Headers(response.headers);
  for (const [k, v] of Object.entries(headers)) merged.set(k, v);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: merged });
}
