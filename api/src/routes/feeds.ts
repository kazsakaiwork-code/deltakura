/**
 * The feeds, all served (and counted) by this Worker.
 *
 *   GET /v0/feeds/nta-diff.json   JSON Feed 1.1 of the 法人番号 register's daily
 *                                 change volume, generated here from the store.
 *   GET /v0/feeds/nta-diff.xml    the site's weekly RSS         relayed from the
 *   GET /v0/feeds/articles.xml    the site's article RSS        static site, so
 *   GET /v0/feeds/articles.json   the site's article JSON Feed  site/build.py
 *                                 stays their single source.
 *
 * Every GET is counted by src/feedcount.ts (distinct fetchers per feed and UTC
 * day, salted hash, no raw IP). Aggregate counts only - a feed is a public
 * surface and no record-level data belongs on one.
 */
import type { Env } from '../env.js';
import { countFeedFetch } from '../feedcount.js';
import { errorResponse, json, SECURITY_HEADERS } from '../http.js';
import { corporateStore } from '../store/index.js';
import {
  eachDate,
  labelProcess,
  NTA_ATTRIBUTION,
  NTA_CAVEATS_EN,
  NTA_LICENSE,
  NTA_SOURCE_URL
} from '../shared.js';

const DEFAULT_DAYS = 30;
const MAX_DAYS = 120;

export async function handleNtaDiffFeed(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  await countFeedFetch(request, env, ctx, 'nta-diff.json');
  const url = new URL(request.url);
  const days = Math.min(Math.max(Number(url.searchParams.get('days') ?? DEFAULT_DAYS) || DEFAULT_DAYS, 1), MAX_DAYS);
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - (days - 1) * 86_400_000).toISOString().slice(0, 10);

  const store = corporateStore(env);
  let data;
  try {
    data = await store.dailyCounts(from, to, true);
  } catch (err) {
    console.error('data store', err);
    return errorResponse(503, 'data_unavailable', 'The data store is temporarily unavailable.');
  }

  const home = `${url.origin}/v0`;
  const items = eachDate(from, to)
    .filter((date) => data.counts.has(date))
    .reverse()
    .map((date) => {
      const total = data.counts.get(date) ?? 0;
      const kinds = data.byKind?.get(date);
      const breakdown = kinds
        ? Object.entries(kinds)
            .sort((a, b) => b[1] - a[1])
            .map(([code, n]) => `${labelProcess(code).en} (${labelProcess(code).ja}): ${n}`)
            .join(', ')
        : undefined;
      return {
        id: `${url.origin}/v0/corporate/diff-summary?from=${date}&to=${date}`,
        url: `${url.origin}/v0/corporate/diff-summary?from=${date}&to=${date}`,
        title: `${date}: ${total.toLocaleString('en-US')} corporate-register changes`,
        content_text:
          `The 国税庁 法人番号 register published ${total.toLocaleString('en-US')} changes for ${date}.` +
          (breakdown ? ` By 処理区分: ${breakdown}.` : '') +
          ` ${NTA_ATTRIBUTION}`,
        date_published: `${date}T00:00:00Z`,
        _deltakura: {
          date,
          changes: total,
          ...(kinds ? { by_change_kind: kinds } : {})
        }
      };
    });

  return json(
    {
      version: 'https://jsonfeed.org/version/1.1',
      title: 'Deltakura Registry Diff — 法人番号 daily changes',
      home_page_url: home,
      feed_url: `${url.origin}/v0/feeds/nta-diff.json`,
      description:
        'Daily volume of changes published in the Japanese corporate-number register. ' +
        'The publisher keeps its diff files for about 40 days; this archive does not.',
      language: 'en',
      authors: [{ name: 'Deltakura' }],
      items,
      _deltakura: {
        from,
        to,
        days_with_data: items.length,
        coverage: data.coverage,
        data_source: store.kind,
        license: NTA_LICENSE,
        attribution: NTA_ATTRIBUTION,
        source_url: NTA_SOURCE_URL,
        caveats: NTA_CAVEATS_EN
      }
    },
    {
      headers: {
        'content-type': 'application/feed+json; charset=utf-8',
        'cache-control': 'public, max-age=3600'
      }
    }
  );
}

/* ------------------------------------------------- feeds relayed from the site --- */

const DEFAULT_SITE_ORIGIN = 'https://deltakura-signals.web.app';

/** The static feeds this Worker relays. Fixed paths: no caller-supplied URL is ever fetched. */
export const STATIC_FEEDS = {
  'nta-diff.xml': { path: '/feeds/nta-diff.xml', type: 'application/rss+xml; charset=utf-8' },
  'articles.xml': { path: '/feeds/articles.xml', type: 'application/rss+xml; charset=utf-8' },
  'articles.json': { path: '/feeds/articles.json', type: 'application/feed+json; charset=utf-8' }
} as const;
export type StaticFeedId = keyof typeof STATIC_FEEDS;

/** The site rebuilds a few times a day at most; ten minutes keeps origin fetches rare. */
const RELAY_MEMO_MS = 10 * 60_000;
const RELAY_TIMEOUT_MS = 5_000;
const MAX_FEED_BYTES = 2 * 1024 * 1024;
const relayMemo = new Map<string, { at: number; body: string; etag: string }>();

export function siteOrigin(env: Env): string {
  const configured = (env.SITE_ORIGIN ?? '').trim();
  if (configured) {
    try {
      const u = new URL(configured);
      if (u.protocol === 'https:' && !u.username && !u.password) return u.origin;
    } catch {
      /* fall through to the default */
    }
  }
  return DEFAULT_SITE_ORIGIN;
}

async function etagOf(body: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
  return `"${[...new Uint8Array(digest).slice(0, 12)].map((b) => b.toString(16).padStart(2, '0')).join('')}"`;
}

async function fetchFromSite(url: string, type: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      headers: { accept: type.split(';')[0], 'user-agent': 'deltakura-api feed relay' },
      redirect: 'follow',
      signal: AbortSignal.timeout(RELAY_TIMEOUT_MS)
    });
    if (!response.ok) return null;
    const body = await response.text();
    return body.length > 0 && body.length <= MAX_FEED_BYTES ? body : null;
  } catch {
    return null;
  }
}

/**
 * Count the fetch, then answer with the site's current copy of the feed. If the
 * site cannot be reached, a memoised copy is served; with none, a 302 (so the
 * reader keeps this URL) sends the reader to the static file instead of failing.
 */
export async function handleStaticFeed(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  id: StaticFeedId
): Promise<Response> {
  await countFeedFetch(request, env, ctx, id);
  const { path, type } = STATIC_FEEDS[id];
  const source = `${siteOrigin(env)}${path}`;
  const now = Date.now();

  let entry = relayMemo.get(source);
  if (!entry || now - entry.at >= RELAY_MEMO_MS) {
    const body = await fetchFromSite(source, type);
    if (body !== null) {
      entry = { at: now, body, etag: await etagOf(body) };
      relayMemo.set(source, entry);
    }
  }
  if (!entry) {
    return new Response(null, {
      status: 302,
      headers: { location: source, 'cache-control': 'no-store', ...SECURITY_HEADERS }
    });
  }

  const headers = {
    'content-type': type,
    'cache-control': 'public, max-age=900',
    etag: entry.etag,
    ...SECURITY_HEADERS
  };
  const etag = entry.etag;
  const inm = request.headers.get('if-none-match');
  if (inm && inm.split(',').some((t) => t.trim() === etag)) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(request.method === 'HEAD' ? null : entry.body, { status: 200, headers });
}

/** Test seam. */
export function resetFeedRelay(): void {
  relayMemo.clear();
}
