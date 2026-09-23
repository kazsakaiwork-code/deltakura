/**
 * /v0/intent - the pay-intent metric.
 *
 * POST {product, kind?, client_id?} records one distinct visitor's click on a
 * price button (or a view of a page carrying one). GET returns the counters.
 *
 * `DEFAULT_PRODUCTS` below is one half of a contract: the site emits exactly
 * these ids, from `INTENT_PRODUCTS` in `site/build.py`, in a body of exactly
 * `{product, kind, client_id}`. `test/intent-contract.test.ts` parses the site
 * generator and drives this handler with what it found, so the two halves
 * cannot drift. Adding a product means adding it in both places.
 *
 * Two standing rules shape this file:
 *   - "Never write a KV key per request": counters live in memory and are
 *     flushed at most once a minute per key.
 *   - No cookie, no profile, no raw IP: de-duplication uses a random client id
 *     the page keeps in localStorage, hashed with a daily rotating salt.
 */
import { isProd, type Env } from '../env.js';
import { clientIp, errorResponse, ipPrefix, json } from '../http.js';

export const DEFAULT_PRODUCTS = [
  'bet_a_report',
  'bet_a_consultant_plan',
  'bet_a_api',
  'bet_b_watchlist',
  'bet_b_api',
  'bet_c_registry_diff',
  // The pricing page carries prices with checkout disabled and a "tell me when
  // paid plans open" button; that click is the purest pay-intent signal on the
  // site, so it gets its own product id rather than being folded into a bet.
  'pricing_paid_plans'
] as const;

const KINDS = ['click', 'view'] as const;
type Kind = (typeof KINDS)[number];

const DAY_TTL_SECONDS = 60 * 60 * 24 * 400;
// A dedup key is scoped to one UTC day, so it is useless after that day ends.
// 25 h covers a key written at 00:00 UTC plus clock skew, and keeps the salted
// client hash within the project's 24 h retention rule for visitor hashes (+1 h).
const DEDUP_TTL_SECONDS = 60 * 60 * 25;
const FLUSH_INTERVAL_MS = 60_000;
/** A valid body is ~100 bytes. Anything far larger is refused before parsing. */
const MAX_BODY_BYTES = 2048;
/** GET is ~15 KV reads per product per call; a short per-isolate memo stops it being a quota lever. */
const GET_MEMO_MS = 60_000;
const getMemo = new Map<number, { at: number; body: unknown }>();

const pending = new Map<string, number>();
const lastFlush = new Map<string, number>();
const seen = new Set<string>();
const MAX_SEEN = 50_000;

let saltDay = '';
let saltValue = '';

export function products(env: Env & { INTENT_PRODUCTS?: string }): string[] {
  const configured = (env.INTENT_PRODUCTS ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return configured.length ? configured : [...DEFAULT_PRODUCTS];
}

function today(now = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

async function clientHash(env: Env, raw: string, day: string): Promise<string> {
  if (saltDay !== day) {
    saltDay = day;
    saltValue = `${env.IP_HASH_SALT ?? 'deltakura-dev-salt'}:${day}`;
  }
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${saltValue}:${raw}`));
  return [...new Uint8Array(digest).slice(0, 10)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function bump(key: string, by = 1): void {
  pending.set(key, (pending.get(key) ?? 0) + by);
  getMemo.clear();
}

export async function flushCounters(kv: KVNamespace | undefined, opts: { force?: boolean; now?: number } = {}): Promise<number> {
  if (!kv) return 0;
  const now = opts.now ?? Date.now();
  let written = 0;
  for (const [key, delta] of [...pending]) {
    if (!opts.force && now - (lastFlush.get(key) ?? 0) < FLUSH_INTERVAL_MS) continue;
    pending.delete(key);
    lastFlush.set(key, now);
    const current = Number(await kv.get(key)) || 0;
    await kv.put(key, String(current + delta), key.endsWith(':total') ? {} : { expirationTtl: DAY_TTL_SECONDS });
    written++;
  }
  return written;
}

/** Counter value including whatever has not been flushed yet. */
async function readCounter(kv: KVNamespace | undefined, key: string): Promise<number> {
  const stored = kv ? Number(await kv.get(key)) || 0 : 0;
  return stored + (pending.get(key) ?? 0);
}

export async function handleIntentPost(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  if (isProd(env) && !env.IP_HASH_SALT) {
    // Fail closed: without the secret salt every stored hash would be keyed by a
    // value published in this repository, i.e. reversible for an IPv4 address.
    console.error('IP_HASH_SALT is not set; refusing to count');
    return errorResponse(503, 'not_configured', 'Intent counting is not configured on this deployment.');
  }
  const declared = Number(request.headers.get('content-length') ?? 0);
  if (declared > MAX_BODY_BYTES) {
    return errorResponse(413, 'payload_too_large', `Body is limited to ${MAX_BODY_BYTES} bytes.`);
  }
  let body: unknown;
  try {
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) {
      return errorResponse(413, 'payload_too_large', `Body is limited to ${MAX_BODY_BYTES} bytes.`);
    }
    body = JSON.parse(text);
  } catch {
    return errorResponse(400, 'bad_request', 'Body must be JSON: {"product": "...", "kind": "click", "client_id": "..."}');
  }
  const payload = (body ?? {}) as { product?: unknown; kind?: unknown; client_id?: unknown };

  const product = typeof payload.product === 'string' ? payload.product.trim() : '';
  const allowed = products(env);
  if (!product) return errorResponse(400, 'bad_request', 'product is required', { allowed_products: allowed });
  if (!allowed.includes(product)) {
    return errorResponse(400, 'unknown_product', `unknown product "${product.slice(0, 64)}"`, { allowed_products: allowed });
  }

  const kind: Kind = payload.kind === 'view' ? 'view' : 'click';
  if (payload.kind !== undefined && !KINDS.includes(payload.kind as Kind)) {
    return errorResponse(400, 'bad_request', 'kind must be "click" or "view"', { allowed_kinds: KINDS });
  }

  const day = today();
  // Without a client id the fallback is the address coarsened to /16 (IPv4) or
  // /48 (IPv6) - the retention rule in the privacy policy - so a whole block
  // counts once. That undercounts; it never stores a full address's hash.
  const rawClient =
    typeof payload.client_id === 'string' && payload.client_id.length >= 8
      ? payload.client_id.slice(0, 128)
      : `ip:${ipPrefix(clientIp(request, env), { octetsV4: 2, hextetsV6: 3 })}`;
  const hash = await clientHash(env, rawClient, day);

  const dedupKey = `dedup:${day}:${kind}:${product}:${hash}`;
  let counted = false;
  if (!seen.has(dedupKey)) {
    if (seen.size > MAX_SEEN) seen.clear();
    seen.add(dedupKey);
    const alreadyStored = env.KV_INTENT ? await env.KV_INTENT.get(dedupKey) : null;
    if (!alreadyStored) {
      counted = true;
      bump(`count:${kind}:${product}:${day}`);
      bump(`count:${kind}:${product}:total`);
      if (env.KV_INTENT) {
        ctx.waitUntil(env.KV_INTENT.put(dedupKey, '1', { expirationTtl: DEDUP_TTL_SECONDS }));
      }
    }
  }

  ctx.waitUntil(flushCounters(env.KV_INTENT));

  return json({
    ok: true,
    product,
    kind,
    counted,
    already_counted_today: !counted,
    persisted: Boolean(env.KV_INTENT),
    date: day,
    note: counted
      ? 'Recorded. One distinct client is counted once per product, kind and day.'
      : 'Already counted for this client, product and kind today.'
  });
}

export async function handleIntentGet(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const days = Math.min(Math.max(Math.trunc(Number(url.searchParams.get('days') ?? 14)) || 14, 1), 90);
  const now = Date.now();
  const memo = getMemo.get(days);
  if (memo && now - memo.at < GET_MEMO_MS) return json(memo.body);
  const dates: string[] = [];
  for (let i = days - 1; i >= 0; i--) dates.push(today(now - i * 86_400_000));

  const list = products(env);
  const byProduct: Record<string, { clicks: number; views: number; clicks_total: number; views_total: number }> = {};
  let clicks = 0;
  let views = 0;

  for (const product of list) {
    let c = 0;
    let v = 0;
    for (const date of dates) {
      c += await readCounter(env.KV_INTENT, `count:click:${product}:${date}`);
      v += await readCounter(env.KV_INTENT, `count:view:${product}:${date}`);
    }
    byProduct[product] = {
      clicks: c,
      views: v,
      clicks_total: await readCounter(env.KV_INTENT, `count:click:${product}:total`),
      views_total: await readCounter(env.KV_INTENT, `count:view:${product}:total`)
    };
    clicks += c;
    views += v;
  }

  const body = {
    window_days: days,
    from: dates[0],
    to: dates.at(-1),
    clicks,
    views,
    intent_rate: views > 0 ? Number((clicks / views).toFixed(4)) : null,
    by_product: byProduct,
    persisted: Boolean(env.KV_INTENT),
    method:
      'Distinct clients per product, kind and day. A client is a random id the page keeps in ' +
      'localStorage, hashed with a daily rotating salt; there is no cookie and no raw IP is stored. ' +
      'Counters are buffered in memory and flushed to KV at most once a minute per key, so the ' +
      'newest minute may be slightly behind.',
    ...(env.KV_INTENT ? {} : { warning: 'No KV binding is attached: these counters live only in this isolate.' })
  };
  getMemo.set(days, { at: now, body });
  return json(body);
}

/** Test seam. */
export function resetIntentState(): void {
  pending.clear();
  lastFlush.clear();
  seen.clear();
  getMemo.clear();
  saltDay = '';
}
