import { beforeEach, describe, expect, it } from 'vitest';
import { call, harness } from './helpers.js';
import { resetRateLimits } from '../src/http.js';
import { resetIntentState } from '../src/routes/intent.js';
import ntaDev from '../src/data/nta-dev.json';
import ntaSample from '../src/data/nta-sample.json';

const counts = ntaDev as unknown as {
  coverage: { from: string | null; to: string | null };
  daily: Record<string, number>;
};
const sample = ntaSample as unknown as {
  records: { corporate_number: string; name?: string; prefecture?: string }[];
};

beforeEach(() => {
  resetRateLimits();
  resetIntentState();
});

describe('routing and metadata', () => {
  it('serves an index at / and /v0', async () => {
    const h = harness();
    for (const path of ['/', '/v0']) {
      const { status, body } = await call(h, path);
      expect(status, path).toBe(200);
      expect(body.api_version).toBe('v0');
      expect(body.endpoints.map((e: { path: string }) => e.path)).toContain('/v0/health');
      expect(body.attribution.join(' ')).toContain('調達ポータル');
    }
  });

  it('reports health, its data sources and which bindings are attached', async () => {
    const { status, body } = await call(harness(), '/v0/health');
    expect(status).toBe(200);
    expect(body.status).toBe('ok');
    expect(body.mode).toBe('dev');
    expect(body.data_sources).toMatchObject({ procurement: 'bundled', corporate: 'bundled' });
    expect(body.bindings).toEqual({ KV_INTENT: false, KV_METRICS: false, DB: false, ARCHIVE: false });
    expect(body.intent_products).toContain('bet_a_report');
  });

  it('404s with the route list and 405s an unsupported method', async () => {
    const h = harness();
    const missing = await call(h, '/v0/nope');
    expect(missing.status).toBe(404);
    expect(missing.body.endpoints.join(' ')).toContain('/v0/health');

    const bad = await call(h, '/v0/health', { method: 'DELETE' });
    expect(bad.status).toBe(405);
  });

  it('tolerates a trailing slash', async () => {
    expect((await call(harness(), '/v0/health/')).status).toBe(200);
  });
});

describe('CORS', () => {
  it('echoes an allowed origin and answers preflight', async () => {
    const h = harness();
    const allowed = await call(h, '/v0/health', { origin: 'https://deltakura-signals.web.app' });
    expect(allowed.headers.get('access-control-allow-origin')).toBe('https://deltakura-signals.web.app');
    expect(allowed.headers.get('vary')).toBe('Origin');

    const options = await call(h, '/v0/intent', { method: 'OPTIONS', origin: 'https://deltakura-signals.web.app' });
    expect(options.status).toBe(204);
    expect(options.headers.get('access-control-allow-methods')).toContain('POST');
  });

  it('does not trust a domain the project has not registered', async () => {
    for (const origin of ['https://deltakura.dev', 'https://www.deltakura.dev']) {
      resetRateLimits();
      const { headers } = await call(harness(), '/v0/health', { origin });
      expect(headers.get('access-control-allow-origin'), origin).toBeNull();
    }
  });

  it('refuses an origin that is not on the list', async () => {
    const { headers } = await call(harness(), '/v0/health', { origin: 'https://evil.example' });
    expect(headers.get('access-control-allow-origin')).toBeNull();
  });

  it('always allows localhost, for the site being developed', async () => {
    const { headers } = await call(harness(), '/v0/health', { origin: 'http://localhost:5000' });
    expect(headers.get('access-control-allow-origin')).toBe('http://localhost:5000');
  });

  it('honours an ALLOWED_ORIGINS override', async () => {
    const h = harness({ ALLOWED_ORIGINS: 'https://one.example, https://two.example' });
    const ok = await call(h, '/v0/health', { origin: 'https://two.example' });
    expect(ok.headers.get('access-control-allow-origin')).toBe('https://two.example');
    resetRateLimits();
    const no = await call(h, '/v0/health', { origin: 'https://deltakura-signals.web.app' });
    expect(no.headers.get('access-control-allow-origin')).toBeNull();
  });
});

describe('rate limiting', () => {
  it('allows up to the limit then returns 429 with Retry-After', async () => {
    const h = harness({ RATE_LIMIT_PER_MINUTE: '3' });
    for (let i = 0; i < 3; i++) {
      const r = await call(h, '/v0/health');
      expect(r.status, `request ${i}`).toBe(200);
      expect(r.headers.get('x-ratelimit-limit')).toBe('3');
    }
    const blocked = await call(h, '/v0/health');
    expect(blocked.status).toBe(429);
    expect(blocked.body.error).toBe('rate_limited');
    expect(Number(blocked.headers.get('retry-after'))).toBeGreaterThan(0);
  });

  it('counts clients separately', async () => {
    const h = harness({ RATE_LIMIT_PER_MINUTE: '1' });
    expect((await call(h, '/v0/health', { ip: '198.51.100.1' })).status).toBe(200);
    expect((await call(h, '/v0/health', { ip: '198.51.100.1' })).status).toBe(429);
    expect((await call(h, '/v0/health', { ip: '198.51.100.2' })).status).toBe(200);
  });

  it('keeps the read and intent budgets separate', async () => {
    const h = harness({ RATE_LIMIT_PER_MINUTE: '1', INTENT_RATE_LIMIT_PER_MINUTE: '5' });
    expect((await call(h, '/v0/health')).status).toBe(200);
    expect((await call(h, '/v0/health')).status).toBe(429);
    expect((await call(h, '/v0/intent')).status).toBe(200);
  });
});

describe('GET /v0/procurement/stats', () => {
  it('answers with totals, caveats and attribution', async () => {
    const { status, body } = await call(harness(), '/v0/procurement/stats?fiscal_year=2025&winner_prefecture=東京都');
    expect(status).toBe(200);
    expect(body.totals.n_awards).toBeGreaterThan(0);
    expect(body.amount_quartiles_jpy.median).toBeGreaterThan(0);
    expect(body.caveats.length).toBeGreaterThan(0);
    expect(body.attribution.join(' ')).toContain('p-portal.go.jp');
    expect(body.award_ratio.available).toBe(false);
    expect(body.data_source).toBe('bundled');
  });

  it('accepts a prefecture code and a sector substring', async () => {
    const h = harness();
    const byCode = await call(h, '/v0/procurement/stats?fiscal_year=2025&winner_prefecture=13&include_buckets=false');
    const byName = await call(h, '/v0/procurement/stats?fiscal_year=2025&winner_prefecture=東京都&include_buckets=false');
    expect(byCode.body.totals).toEqual(byName.body.totals);
    expect(byCode.body.buckets).toBeUndefined();

    const sector = await call(h, '/v0/procurement/stats?fiscal_year=2025&sector=防衛&bucket_limit=3');
    expect(sector.status).toBe(200);
    expect(sector.body.buckets.length).toBeLessThanOrEqual(3);
  });

  it('rejects an unknown filter value with the list of real ones', async () => {
    const { status, body } = await call(harness(), '/v0/procurement/stats?sector=Atlantis');
    expect(status).toBe(400);
    expect(body.error).toBe('unknown_value');
    expect(body.dimension).toBe('sector');
    expect(body.available.length).toBeGreaterThan(0);
  });

  it('rejects a non-numeric fiscal year', async () => {
    const { status, body } = await call(harness(), '/v0/procurement/stats?fiscal_year=last-year');
    expect(status).toBe(400);
    expect(body.message).toContain('must be a number');
  });

  it('serves Japanese caveats on lang=ja', async () => {
    const { body } = await call(harness(), '/v0/procurement/stats?fiscal_year=2025&lang=ja&include_buckets=false');
    expect(body.caveats[0]).toContain('落札率');
  });
});

describe('GET /v0/corporate/:number', () => {
  it('returns the record for a number in the bundled sample', async () => {
    const target = sample.records[0];
    const { status, body } = await call(harness(), `/v0/corporate/${target.corporate_number}`);
    expect(status).toBe(200);
    expect(body.found).toBe(true);
    expect(body.corporate_number).toBe(target.corporate_number);
    expect(body.latest_change).toBeTruthy();
    expect(body.attribution).toContain('国税庁');
    expect(body.data_source).toBe('bundled');
    expect(body.complete).toBe(false);
  });

  it('never leaks a field outside the response allowlist', async () => {
    const target = sample.records[0];
    const { body } = await call(harness(), `/v0/corporate/${target.corporate_number}`);
    const json = JSON.stringify(body);
    for (const forbidden of ['street_number', 'furigana', 'change_cause', 'post_code', 'raw_hash']) {
      expect(json, forbidden).not.toContain(forbidden);
    }
  });

  it('404s for a number with no published change, without claiming it does not exist', async () => {
    const { status, body } = await call(harness(), '/v0/corporate/9000012050000');
    expect(status).toBe(404);
    expect(body.found).toBe(false);
    expect(body.notes.join(' ')).toContain('says nothing about whether the corporation exists');
  });

  it('400s on anything that is not 13 digits', async () => {
    const { status, body } = await call(harness(), '/v0/corporate/12345');
    expect(status).toBe(400);
    expect(body.message).toContain('13-digit');
  });

  it('flags a number that fails the NTA check digit', async () => {
    const { body } = await call(harness(), '/v0/corporate/9999999999998');
    expect(body.checksum_valid).toBe(false);
    expect(body.notes.join(' ')).toContain('check-digit');
  });
});

describe('GET /v0/corporate/diff-summary', () => {
  const from = Object.keys(counts.daily).sort()[0];
  const to = Object.keys(counts.daily).sort().at(-1)!;

  it('returns daily counts over the collected window', async () => {
    const { status, body } = await call(harness(), `/v0/corporate/diff-summary?from=${from}&to=${to}`);
    expect(status).toBe(200);
    expect(body.days.length).toBeGreaterThan(0);
    expect(body.total_changes).toBeGreaterThan(0);
    expect(body.days_without_file).toBeGreaterThan(0);
    expect(body.notes.join(' ')).toContain('weekends');
    expect(body.attribution).toContain('国税庁');
  });

  it('groups by change kind on request, with labels', async () => {
    const { body } = await call(
      harness(),
      `/v0/corporate/diff-summary?from=${from}&to=${to}&group_by=change_kind`
    );
    expect(body.days[0].by_change_kind).toBeTruthy();
    expect(body.change_kind_labels['01'].en).toBe('newly assigned');
  });

  it('rejects missing, malformed, reversed and oversized ranges', async () => {
    const h = harness();
    expect((await call(h, '/v0/corporate/diff-summary')).status).toBe(400);
    expect((await call(h, '/v0/corporate/diff-summary?from=2026-9-1&to=2026-09-03')).status).toBe(400);
    expect((await call(h, '/v0/corporate/diff-summary?from=2026-09-03&to=2026-09-01')).status).toBe(400);
    const huge = await call(h, '/v0/corporate/diff-summary?from=2020-01-01&to=2026-09-01');
    expect(huge.status).toBe(400);
    expect(huge.body.error).toBe('range_too_large');
  });
});

describe('GET /v0/feeds/nta-diff.json', () => {
  it('is a JSON Feed with one item per published day', async () => {
    const { status, body, headers } = await call(harness(), '/v0/feeds/nta-diff.json?days=120');
    expect(status).toBe(200);
    expect(headers.get('content-type')).toContain('application/feed+json');
    expect(body.version).toBe('https://jsonfeed.org/version/1.1');
    expect(body.items.length).toBeGreaterThan(0);
    const item = body.items[0];
    expect(item.id).toContain('/v0/corporate/diff-summary');
    expect(item.content_text).toContain('国税庁');
    expect(item._deltakura.changes).toBeGreaterThan(0);
    expect(body._deltakura.attribution).toContain('国税庁');
    expect(body._deltakura.caveats.length).toBeGreaterThan(0);
  });

  it('carries no record-level data', async () => {
    const { body } = await call(harness(), '/v0/feeds/nta-diff.json?days=120');
    const json = JSON.stringify(body);
    expect(json).not.toContain('corporate_number');
    for (const record of sample.records.slice(0, 20)) {
      if (record.name) expect(json).not.toContain(record.name);
    }
  });

  it('orders items newest first', async () => {
    const { body } = await call(harness(), '/v0/feeds/nta-diff.json?days=120');
    const dates = body.items.map((i: { _deltakura: { date: string } }) => i._deltakura.date);
    expect([...dates].sort().reverse()).toEqual(dates);
  });
});
