/**
 * Feed fetch counter: how many distinct readers poll each feed, per UTC day.
 *
 * Every feed the site links to is served through this Worker, so a fetch can be
 * counted without a beacon, a cookie or a stored address. The rules mirror the
 * intent counter (routes/intent.ts):
 *
 *   - One distinct fetcher = a salted hash of (normalised User-Agent, client
 *     address coarsened to /16 for IPv4 or /48 for IPv6). The salt is the
 *     IP_HASH_SALT secret plus the UTC day, so a hash cannot be linked across
 *     days. The raw address and the raw User-Agent are never stored.
 *   - The de-duplication key lives at most 25 hours (KV TTL), then only the
 *     per-feed daily totals remain.
 *   - Counters are buffered in memory and flushed at most once a minute per key,
 *     so traffic never turns into one KV write per request.
 *
 * Known crawlers are counted apart from readers, and aggregators that report
 * "N subscribers" in their User-Agent (Feedly, Inoreader, ...) have that
 * number kept per UA family and day, because one aggregator fetch stands for
 * many people. Only the family token (e.g. "feedly") and the number are stored.
 *
 * The daily count is an upper bound on distinct readers for the same reasons as
 * the intent count (a reader behind two /16 networks counts twice; KV is
 * eventually consistent) and a lower bound for readers behind one aggregator
 * that reports nothing.
 */
import { isProd, type Env } from './env.js';
import { clientIp, ipPrefix, json } from './http.js';

/** Every feed the Worker serves. KV keys are built from these ids only. */
export const FEED_IDS = ['nta-diff.xml', 'nta-diff.json', 'articles.xml', 'articles.json'] as const;
export type FeedId = (typeof FEED_IDS)[number];

const DAY_TTL_SECONDS = 60 * 60 * 24 * 400;
/** A de-duplication hash is useless after its UTC day; 25 h covers skew (same rule as intent). */
export const FEED_DEDUP_TTL_SECONDS = 60 * 60 * 25;
const FLUSH_INTERVAL_MS = 60_000;
/**
 * KV writes on the free plan are about a thousand a day, shared with the intent
 * counter. Past this many new-fetcher writes in one isolate and day, new
 * fetchers are de-duplicated in memory only (a few may then count twice across
 * isolates) instead of spending the write budget.
 */
const MAX_DEDUP_WRITES_PER_ISOLATE_DAY = 400;
const STATS_MEMO_MS = 60_000;
const MAX_STATS_DAYS = 31;

const CRAWLER =
  /(googlebot|bingbot|yandex|baiduspider|duckduckbot|applebot|gptbot|claudebot|anthropic-ai|ccbot|perplexitybot|ahrefsbot|semrushbot|mj12bot|petalbot|bytespider|facebookexternalhit|slurp|dotbot|amazonbot)/i;
const REPORTED = /(\d{1,7})\s+(?:subscribers?|readers?)\b/i;

const pendingCounts = new Map<string, number>();
const pendingReported = new Map<string, Record<string, number>>();
const lastFlush = new Map<string, number>();
const seen = new Set<string>();
const MAX_SEEN = 50_000;
let dedupWrites = { day: '', n: 0 };
const statsMemo = new Map<number, { at: number; body: unknown }>();

function today(now = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

/** Lower-case, version- and subscriber-count-insensitive, bounded. */
export function normalizeUserAgent(ua: string | null): string {
  if (!ua) return '-';
  return (
    ua
      .toLowerCase()
      .replace(/\b\d{1,7}\s+(subscribers?|readers?)\b/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 256) || '-'
  );
}

/** The product token of a User-Agent ("Feedly/1.0 (...)" -> "feedly"), [a-z0-9._-] only. */
export function uaFamily(ua: string | null): string {
  const first = (ua ?? '').trim().split(/[\/\s;(]/)[0] ?? '';
  return first.toLowerCase().replace(/[^a-z0-9._-]/g, '').slice(0, 32) || 'unknown';
}

export function reportedSubscribers(ua: string | null): number | null {
  const m = REPORTED.exec(ua ?? '');
  return m ? Number(m[1]) : null;
}

async function fetcherHash(env: Env, raw: string, day: string): Promise<string> {
  const salt = `${env.IP_HASH_SALT ?? 'deltakura-dev-salt'}:${day}:feed`;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${salt}:${raw}`));
  return [...new Uint8Array(digest).slice(0, 10)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function bump(key: string, by = 1): void {
  pendingCounts.set(key, (pendingCounts.get(key) ?? 0) + by);
  statsMemo.clear();
}

function noteReported(key: string, family: string, n: number): void {
  const current = pendingReported.get(key) ?? {};
  current[family] = Math.max(current[family] ?? 0, n);
  pendingReported.set(key, current);
  statsMemo.clear();
}

/**
 * Record one fetch of `feed`. Returns whether it was a new distinct fetcher.
 * Never throws into the response path: a counting failure must not cost a
 * reader the feed.
 */
export async function countFeedFetch(request: Request, env: Env, ctx: ExecutionContext, feed: FeedId): Promise<boolean> {
  if (request.method !== 'GET') return false;
  // Without the secret salt a stored hash would be keyed by a value published in
  // this repository. The feed is still served; it is just not counted.
  if (isProd(env) && !env.IP_HASH_SALT) return false;
  try {
    const day = today();
    const kv = env.KV_METRICS;
    const ua = request.headers.get('user-agent');
    const crawler = CRAWLER.test(ua ?? '');
    bump(`feed:req:${feed}:${day}`);
    // Kept as the day's maximum per UA family, from every fetch (buffered).
    const reported = crawler ? null : reportedSubscribers(ua);
    if (reported !== null) noteReported(`feed:rep:${feed}:${day}`, uaFamily(ua), reported);

    const net = ipPrefix(clientIp(request, env), { octetsV4: 2, hextetsV6: 3 });
    const hash = await fetcherHash(env, `${normalizeUserAgent(ua)}|${net}`, day);
    const dedupKey = `feed:dedup:${day}:${feed}:${hash}`;
    let counted = false;
    if (!seen.has(dedupKey)) {
      if (seen.size > MAX_SEEN) seen.clear();
      seen.add(dedupKey);
      const stored = kv ? await kv.get(dedupKey) : null;
      if (!stored) {
        counted = true;
        bump(`feed:${crawler ? 'crawl' : 'count'}:${feed}:${day}`);
        if (kv) {
          if (dedupWrites.day !== day) dedupWrites = { day, n: 0 };
          if (dedupWrites.n < MAX_DEDUP_WRITES_PER_ISOLATE_DAY) {
            dedupWrites.n++;
            ctx.waitUntil(kv.put(dedupKey, '1', { expirationTtl: FEED_DEDUP_TTL_SECONDS }));
          }
        }
      }
    }
    ctx.waitUntil(flushFeedCounters(kv).catch((err) => console.error('feed counter flush', err)));
    return counted;
  } catch (err) {
    console.error('feed counter', err);
    return false;
  }
}

export async function flushFeedCounters(kv: KVNamespace | undefined, opts: { force?: boolean; now?: number } = {}): Promise<number> {
  if (!kv) return 0;
  const now = opts.now ?? Date.now();
  let written = 0;
  const due = (key: string) => opts.force || now - (lastFlush.get(key) ?? 0) >= FLUSH_INTERVAL_MS;
  for (const [key, delta] of [...pendingCounts]) {
    if (!due(key)) continue;
    pendingCounts.delete(key);
    lastFlush.set(key, now);
    const current = Number(await kv.get(key)) || 0;
    await kv.put(key, String(current + delta), { expirationTtl: DAY_TTL_SECONDS });
    written++;
  }
  for (const [key, families] of [...pendingReported]) {
    if (!due(key)) continue;
    pendingReported.delete(key);
    lastFlush.set(key, now);
    const current = parseReported(await kv.get(key));
    for (const [family, n] of Object.entries(families)) current[family] = Math.max(current[family] ?? 0, n);
    await kv.put(key, JSON.stringify(current), { expirationTtl: DAY_TTL_SECONDS });
    written++;
  }
  return written;
}

function parseReported(raw: string | null): Record<string, number> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (/^[a-z0-9._-]{1,32}$/.test(k) && Number.isFinite(Number(v))) out[k] = Number(v);
    }
    return out;
  } catch {
    return {};
  }
}

async function readCount(kv: KVNamespace | undefined, key: string): Promise<number> {
  const stored = kv ? Number(await kv.get(key)) || 0 : 0;
  return stored + (pendingCounts.get(key) ?? 0);
}

async function readReported(kv: KVNamespace | undefined, key: string): Promise<Record<string, number>> {
  const stored = kv ? parseReported(await kv.get(key)) : {};
  for (const [family, n] of Object.entries(pendingReported.get(key) ?? {})) {
    stored[family] = Math.max(stored[family] ?? 0, n);
  }
  return stored;
}

/** GET /v0/feeds/stats?days=14 - daily fetch counts per feed. Aggregates only. */
export async function handleFeedStats(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const days = Math.min(Math.max(Math.trunc(Number(url.searchParams.get('days') ?? 14)) || 14, 1), MAX_STATS_DAYS);
  const now = Date.now();
  const memo = statsMemo.get(days);
  if (memo && now - memo.at < STATS_MEMO_MS) return json(memo.body);

  const dates: string[] = [];
  for (let i = days - 1; i >= 0; i--) dates.push(today(now - i * 86_400_000));
  const kv = env.KV_METRICS;

  const feeds: Record<string, unknown> = {};
  for (const feed of FEED_IDS) {
    const daily = [];
    let maxDistinct = 0;
    let maxEstimate = 0;
    for (const date of dates) {
      const distinct = await readCount(kv, `feed:count:${feed}:${date}`);
      const crawlers = await readCount(kv, `feed:crawl:${feed}:${date}`);
      const requests = await readCount(kv, `feed:req:${feed}:${date}`);
      const reported = await readReported(kv, `feed:rep:${feed}:${date}`);
      // Each reporting aggregator is already one distinct fetcher; add the rest
      // of the readers it reports.
      const estimate = distinct + Object.values(reported).reduce((s, n) => s + Math.max(0, n - 1), 0);
      maxDistinct = Math.max(maxDistinct, distinct);
      maxEstimate = Math.max(maxEstimate, estimate);
      daily.push({
        date,
        distinct_fetchers: distinct,
        crawler_fetchers: crawlers,
        requests,
        reported_subscribers: reported,
        readers_estimate: estimate
      });
    }
    feeds[feed] = { daily, max_daily_distinct_fetchers: maxDistinct, max_daily_readers_estimate: maxEstimate };
  }

  const body = {
    window_days: days,
    from: dates[0],
    to: dates.at(-1),
    feeds,
    persisted: Boolean(kv),
    method:
      'A distinct fetcher is a salted hash of the normalised User-Agent and the client address ' +
      'coarsened to /16 (IPv4) or /48 (IPv6), counted once per feed and UTC day. The salt rotates ' +
      'daily; no raw IP address or User-Agent is stored, and the hash itself expires after 25 hours. ' +
      'Known crawlers are counted separately. reported_subscribers is the "N subscribers" figure an ' +
      'aggregator states in its User-Agent, per UA family. readers_estimate = distinct_fetchers plus ' +
      'the extra readers each reporting aggregator states. Read the counts as estimates, not exact figures.',
    ...(kv ? {} : { warning: 'No KV binding is attached: these counters live only in this isolate.' })
  };
  statsMemo.set(days, { at: now, body });
  return json(body);
}

/** Test seam. */
export function resetFeedState(): void {
  pendingCounts.clear();
  pendingReported.clear();
  lastFlush.clear();
  seen.clear();
  statsMemo.clear();
  dedupWrites = { day: '', n: 0 };
}
