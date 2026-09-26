/**
 * Counted feeds: every feed the site links to is served by the Worker, and each
 * fetch is counted once per distinct fetcher, feed and UTC day - with the same
 * privacy rules as the intent counter (salted hash, /16 coarsening, 25 h
 * de-duplication keys, no raw IP or User-Agent stored).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { call, harness, MemoryKV } from './helpers.js';
import { resetRateLimits } from '../src/http.js';
import {
  FEED_DEDUP_TTL_SECONDS,
  flushFeedCounters,
  normalizeUserAgent,
  reportedSubscribers,
  resetFeedState,
  uaFamily
} from '../src/feedcount.js';
import { resetFeedRelay, siteOrigin } from '../src/routes/feeds.js';
import type { Env } from '../src/env.js';

const RSS = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title></channel></rss>';
const JSON_FEED = JSON.stringify({ version: 'https://jsonfeed.org/version/1.1', title: 't', items: [] });

let originFetch: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetRateLimits();
  resetFeedState();
  resetFeedRelay();
  originFetch = vi.fn(async (url: string) =>
    new Response(String(url).endsWith('.json') ? JSON_FEED : RSS, { status: 200 })
  );
  vi.stubGlobal('fetch', originFetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function withKv(extra: Partial<Env> = {}) {
  const kv = new MemoryKV();
  return { kv, h: harness({ KV_METRICS: kv as unknown as KVNamespace, RATE_LIMIT_PER_MINUTE: '1000', ...extra } as Partial<Env>) };
}

const reader = (ua: string, ip = '198.51.100.7', extra: Record<string, string> = {}) => ({
  ip,
  headers: { 'user-agent': ua, ...extra }
});

const today = () => new Date().toISOString().slice(0, 10);

describe('feeds relayed from the site', () => {
  it('serves the site RSS at /v0/feeds/nta-diff.xml with a feed content type', async () => {
    const { h } = withKv();
    const { status, body, headers } = await call(h, '/v0/feeds/nta-diff.xml', reader('Feedly/1.0'));
    expect(status).toBe(200);
    expect(body).toBe(RSS);
    expect(headers.get('content-type')).toContain('application/rss+xml');
    expect(headers.get('x-content-type-options')).toBe('nosniff');
    expect(headers.get('etag')).toMatch(/^"[0-9a-f]{24}"$/);
    expect(originFetch).toHaveBeenCalledOnce();
    expect(String(originFetch.mock.calls[0][0])).toBe('https://deltakura-signals.web.app/feeds/nta-diff.xml');
  });

  it('serves the article RSS and the article JSON Feed', async () => {
    const { h } = withKv();
    const rss = await call(h, '/v0/feeds/articles.xml', reader('Inoreader/1.0'));
    expect(rss.status).toBe(200);
    expect(rss.headers.get('content-type')).toContain('application/rss+xml');
    const jf = await call(h, '/v0/feeds/articles.json', reader('Inoreader/1.0'));
    expect(jf.status).toBe(200);
    expect(jf.headers.get('content-type')).toContain('application/feed+json');
    expect(jf.body.version).toBe('https://jsonfeed.org/version/1.1');
  });

  it('memoises the origin copy and answers a matching If-None-Match with 304', async () => {
    const { h } = withKv();
    const first = await call(h, '/v0/feeds/nta-diff.xml', reader('A/1'));
    const etag = first.headers.get('etag')!;
    const second = await call(h, '/v0/feeds/nta-diff.xml', reader('A/1', undefined, { 'if-none-match': etag }));
    expect(second.status).toBe(304);
    expect(originFetch).toHaveBeenCalledOnce();
  });

  it('redirects to the static file when the site cannot be reached and nothing is memoised', async () => {
    originFetch.mockImplementation(async () => new Response('down', { status: 503 }));
    const { h } = withKv();
    const res = await call(h, '/v0/feeds/articles.xml', { ...reader('A/1'), redirect: 'manual' });
    expect(res.status).toBe(302);
    expect(res.headers.get('location')).toBe('https://deltakura-signals.web.app/feeds/articles.xml');
  });

  it('only ever fetches the fixed feed paths on an https site origin', async () => {
    expect(siteOrigin({} as Env)).toBe('https://deltakura-signals.web.app');
    expect(siteOrigin({ SITE_ORIGIN: 'http://evil.example' } as Env)).toBe('https://deltakura-signals.web.app');
    expect(siteOrigin({ SITE_ORIGIN: 'https://site.example/some/path' } as Env)).toBe('https://site.example');
    const { h } = withKv();
    const unknown = await call(h, '/v0/feeds/..%2Fsecret.xml', reader('A/1'));
    expect(unknown.status).toBe(404);
    const other = await call(h, '/v0/feeds/other.xml', reader('A/1'));
    expect(other.status).toBe(404);
    expect(originFetch).not.toHaveBeenCalled();
  });
});

describe('feed fetch counting', () => {
  it('counts one fetcher once per feed and day, whatever its last two octets', async () => {
    const { kv, h } = withKv();
    for (const ip of ['198.51.100.7', '198.51.7.8', '198.51.200.1']) {
      await call(h, '/v0/feeds/nta-diff.xml', reader('NetNewsWire (RSS Reader; https://netnewswire.com/)', ip));
      await h.settled();
    }
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`feed:count:nta-diff.xml:${today()}`)).toBe('1');
    expect(kv.store.get(`feed:req:nta-diff.xml:${today()}`)).toBe('3');
  });

  it('counts a different User-Agent or a different /16 as another fetcher, and feeds apart', async () => {
    const { kv, h } = withKv();
    await call(h, '/v0/feeds/nta-diff.xml', reader('Reader-A/1', '198.51.100.7'));
    await call(h, '/v0/feeds/nta-diff.xml', reader('Reader-B/1', '198.51.100.7'));
    await call(h, '/v0/feeds/nta-diff.xml', reader('Reader-A/1', '203.0.113.9'));
    await call(h, '/v0/feeds/articles.xml', reader('Reader-A/1', '198.51.100.7'));
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`feed:count:nta-diff.xml:${today()}`)).toBe('3');
    expect(kv.store.get(`feed:count:articles.xml:${today()}`)).toBe('1');
  });

  it('counts the Worker-generated JSON Feed too', async () => {
    const { kv, h } = withKv();
    const res = await call(h, '/v0/feeds/nta-diff.json', reader('Reader-A/1'));
    expect(res.status).toBe(200);
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`feed:count:nta-diff.json:${today()}`)).toBe('1');
  });

  it('keeps crawlers apart and records an aggregator reported subscriber count', async () => {
    const { kv, h } = withKv();
    await call(h, '/v0/feeds/articles.xml', reader('Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'));
    await call(h, '/v0/feeds/articles.xml', reader('Feedly/1.0 (+http://www.feedly.com/fetcher.html; 42 subscribers)', '192.0.2.1'));
    // The same aggregator a little later with a new count is the same fetcher.
    await call(h, '/v0/feeds/articles.xml', reader('Feedly/1.0 (+http://www.feedly.com/fetcher.html; 43 subscribers)', '192.0.2.1'));
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`feed:count:articles.xml:${today()}`)).toBe('1');
    expect(kv.store.get(`feed:crawl:articles.xml:${today()}`)).toBe('1');
    expect(JSON.parse(kv.store.get(`feed:rep:articles.xml:${today()}`)!)).toEqual({ feedly: 43 });

    const stats = await call(h, '/v0/feeds/stats?days=1');
    expect(stats.status).toBe(200);
    const day = stats.body.feeds['articles.xml'].daily[0];
    expect(day).toMatchObject({ distinct_fetchers: 1, crawler_fetchers: 1, requests: 3, reported_subscribers: { feedly: 43 } });
    expect(day.readers_estimate).toBe(43);
  });

  it('does not count HEAD requests, stats calls or unknown paths', async () => {
    const { kv, h } = withKv();
    await call(h, '/v0/feeds/nta-diff.xml', { ...reader('A/1'), method: 'HEAD' });
    await call(h, '/v0/feeds/stats', reader('A/1'));
    await call(h, '/v0/feeds/nope.xml', reader('A/1'));
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect([...kv.store.keys()].filter((k) => k.startsWith('feed:count:') || k.startsWith('feed:req:'))).toEqual([]);
  });

  it('flushes buffered counters at most once a minute per key', async () => {
    const { kv, h } = withKv();
    for (let i = 0; i < 20; i++) {
      await call(h, '/v0/feeds/nta-diff.xml', reader(`Reader-${i}/1`));
    }
    await h.settled();
    const countWrites = kv.writeLog.filter((k) => k === `feed:count:nta-diff.xml:${today()}`).length;
    expect(countWrites).toBeLessThanOrEqual(1);
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`feed:count:nta-diff.xml:${today()}`)).toBe('20');
  });

  it('reports daily counts per feed at /v0/feeds/stats', async () => {
    const { h } = withKv();
    await call(h, '/v0/feeds/nta-diff.xml', reader('A/1'));
    await call(h, '/v0/feeds/nta-diff.xml', reader('B/1'));
    await h.settled();
    const { status, body } = await call(h, '/v0/feeds/stats?days=7');
    expect(status).toBe(200);
    expect(body.window_days).toBe(7);
    expect(Object.keys(body.feeds).sort()).toEqual(['articles.json', 'articles.xml', 'nta-diff.json', 'nta-diff.xml']);
    const rows = body.feeds['nta-diff.xml'].daily;
    expect(rows).toHaveLength(7);
    expect(rows.at(-1)).toMatchObject({ date: today(), distinct_fetchers: 2, requests: 2 });
    expect(body.feeds['nta-diff.xml'].max_daily_distinct_fetchers).toBe(2);
    expect(body.method).toContain('no raw IP');
  });

  it('caps the stats window at 31 days', async () => {
    const { body } = await call(withKv().h, '/v0/feeds/stats?days=400');
    expect(body.window_days).toBe(31);
  });
});

describe('feed counting privacy', () => {
  it('stores no raw address, no User-Agent, and only allowlisted key shapes', async () => {
    const { kv, h } = withKv();
    const ua = 'Distinctive-Reader/9.9 (unique-token-xyz; 7 subscribers)';
    await call(h, '/v0/feeds/nta-diff.xml', reader(ua, '198.51.100.77'));
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    const dump = JSON.stringify([...kv.store.entries()]);
    expect(dump).not.toContain('198.51');
    expect(dump).not.toContain('unique-token-xyz');
    for (const key of kv.store.keys()) {
      expect(key).toMatch(
        /^feed:(dedup:\d{4}-\d{2}-\d{2}:[a-z-]+\.(xml|json):[0-9a-f]{20}|(count|crawl|req|rep):[a-z-]+\.(xml|json):\d{4}-\d{2}-\d{2})$/
      );
    }
  });

  it('never keeps a de-duplication hash for more than 25 hours', async () => {
    const ttls: number[] = [];
    const kv = new MemoryKV();
    const put = kv.put.bind(kv);
    (kv as unknown as { put: unknown }).put = async (key: string, value: string, opts?: { expirationTtl?: number }) => {
      if (key.startsWith('feed:dedup:')) ttls.push(opts?.expirationTtl ?? Infinity);
      return put(key, value);
    };
    const h = harness({ KV_METRICS: kv as unknown as KVNamespace } as Partial<Env>);
    await call(h, '/v0/feeds/nta-diff.xml', reader('A/1'));
    await h.settled();
    expect(ttls).toEqual([FEED_DEDUP_TTL_SECONDS]);
    expect(FEED_DEDUP_TTL_SECONDS).toBeLessThanOrEqual(25 * 3600);
  });

  it('serves but does not count in production without the secret salt', async () => {
    const kv = new MemoryKV();
    const h = harness({ DELTAKURA_MODE: 'prod', KV_METRICS: kv as unknown as KVNamespace } as Partial<Env>);
    const res = await call(h, '/v0/feeds/nta-diff.xml', reader('A/1'));
    expect(res.status).toBe(200);
    await h.settled();
    await flushFeedCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.puts).toBe(0);
  });

  it('normalises User-Agents and parses reported counts conservatively', () => {
    expect(normalizeUserAgent('Feedly/1.0 (+http://x; 42 subscribers)')).toBe(normalizeUserAgent('feedly/1.0 (+http://x; 43 subscribers)'));
    expect(normalizeUserAgent(null)).toBe('-');
    expect(normalizeUserAgent('x'.repeat(1000))).toHaveLength(256);
    expect(uaFamily('Feedly/1.0 (+http://www.feedly.com)')).toBe('feedly');
    expect(uaFamily('<script>/1')).toBe('script');
    expect(uaFamily('')).toBe('unknown');
    expect(reportedSubscribers('Inoreader/1.0 (3 subscribers)')).toBe(3);
    expect(reportedSubscribers('Mozilla/5.0')).toBeNull();
  });
});
