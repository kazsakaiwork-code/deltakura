/**
 * GET /v0/feeds/nta-diff.json
 *
 * A JSON Feed 1.1 of the 法人番号 register's daily change volume. One item per
 * published day. Aggregate counts only - a feed is a public surface and no
 * record-level data belongs on one.
 */
import type { Env } from '../env.js';
import { errorResponse, json } from '../http.js';
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

export async function handleNtaDiffFeed(request: Request, env: Env): Promise<Response> {
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
