import { beforeEach, describe, expect, it } from 'vitest';
import { call, harness, MemoryKV } from './helpers.js';
import { resetRateLimits } from '../src/http.js';
import { flushCounters, resetIntentState } from '../src/routes/intent.js';
import type { Env } from '../src/env.js';

beforeEach(() => {
  resetRateLimits();
  resetIntentState();
});

function withKv() {
  const kv = new MemoryKV();
  return { kv, h: harness({ KV_INTENT: kv as unknown as KVNamespace } as Partial<Env>) };
}

const post = (product: unknown, extra: Record<string, unknown> = {}) => ({
  method: 'POST',
  body: JSON.stringify({ product, ...extra })
});

describe('POST /v0/intent', () => {
  it('records a click for a known product', async () => {
    const { kv, h } = withKv();
    const { status, body } = await call(h, '/v0/intent', post('bet_a_report', { client_id: 'client-0001' }));
    await h.settled();
    expect(status).toBe(200);
    expect(body).toMatchObject({ ok: true, product: 'bet_a_report', kind: 'click', counted: true, persisted: true });
    await flushCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get(`count:click:bet_a_report:${body.date}`)).toBe('1');
    expect(kv.store.get('count:click:bet_a_report:total')).toBe('1');
  });

  it('counts one client once per product, kind and day', async () => {
    const { kv, h } = withKv();
    for (let i = 0; i < 5; i++) {
      await call(h, '/v0/intent', post('bet_a_report', { client_id: 'client-repeat' }));
      await h.settled();
    }
    await flushCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get('count:click:bet_a_report:total')).toBe('1');

    const second = await call(h, '/v0/intent', post('bet_a_report', { client_id: 'client-repeat' }));
    expect(second.body.counted).toBe(false);
    expect(second.body.already_counted_today).toBe(true);
  });

  it('counts different clients separately, and views apart from clicks', async () => {
    const { kv, h } = withKv();
    for (const client of ['a-client-1', 'a-client-2', 'a-client-3']) {
      await call(h, '/v0/intent', post('bet_b_watchlist', { client_id: client }));
      await call(h, '/v0/intent', post('bet_b_watchlist', { client_id: client, kind: 'view' }));
      await h.settled();
    }
    await flushCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get('count:click:bet_b_watchlist:total')).toBe('3');
    expect(kv.store.get('count:view:bet_b_watchlist:total')).toBe('3');
  });

  it('rejects an unknown product and lists the real ones', async () => {
    const { h } = withKv();
    const { status, body } = await call(h, '/v0/intent', post('please_charge_me'));
    expect(status).toBe(400);
    expect(body.error).toBe('unknown_product');
    expect(body.allowed_products).toContain('bet_a_report');
  });

  it('rejects a missing product, a bad kind and a non-JSON body', async () => {
    const { h } = withKv();
    expect((await call(h, '/v0/intent', { method: 'POST', body: '{}' })).status).toBe(400);
    expect((await call(h, '/v0/intent', post('bet_a_report', { kind: 'purchase' }))).status).toBe(400);
    expect((await call(h, '/v0/intent', { method: 'POST', body: 'not json' })).status).toBe(400);
  });

  it('buffers counters instead of writing KV on every request', async () => {
    const { kv, h } = withKv();
    for (let i = 0; i < 20; i++) {
      await call(h, '/v0/intent', post('bet_c_registry_diff', { client_id: `buffer-client-${i}` }));
      await h.settled();
    }
    // 20 distinct clients => 20 de-duplication markers (one per client per day),
    // but each of the two counter keys is written at most once a minute, so 20
    // requests cost 2 counter writes, not 40.
    const counterWrites = kv.writeLog.filter((k) => k.startsWith('count:'));
    expect(counterWrites.length).toBeLessThanOrEqual(2);
    expect(kv.writeLog.filter((k) => k.startsWith('dedup:'))).toHaveLength(20);

    await flushCounters(kv as unknown as KVNamespace, { force: true });
    expect(kv.store.get('count:click:bet_c_registry_diff:total')).toBe('20');
    expect(kv.writeLog.filter((k) => k.startsWith('count:')).length).toBeLessThanOrEqual(4);
  });

  it('still answers without a KV binding, and says the count is not persisted', async () => {
    const h = harness();
    const { status, body } = await call(h, '/v0/intent', post('bet_a_api', { client_id: 'no-kv-client' }));
    expect(status).toBe(200);
    expect(body.counted).toBe(true);
    expect(body.persisted).toBe(false);
  });

  it('is rate limited on its own budget', async () => {
    const kv = new MemoryKV();
    const h = harness({ INTENT_RATE_LIMIT_PER_MINUTE: '2', KV_INTENT: kv as unknown as KVNamespace } as Partial<Env>);
    expect((await call(h, '/v0/intent', post('bet_a_report', { client_id: 'limit-client-1' }))).status).toBe(200);
    expect((await call(h, '/v0/intent', post('bet_a_report', { client_id: 'limit-client-2' }))).status).toBe(200);
    expect((await call(h, '/v0/intent', post('bet_a_report', { client_id: 'limit-client-3' }))).status).toBe(429);
  });

  it('stores no raw client id and no raw IP', async () => {
    const { kv, h } = withKv();
    await call(h, '/v0/intent', post('bet_a_report', { client_id: 'secret-client-id-value' }));
    await h.settled();
    await flushCounters(kv as unknown as KVNamespace, { force: true });
    const dump = [...kv.store.keys()].join(' ');
    expect(dump).not.toContain('secret-client-id-value');
    expect(dump).not.toContain('203.0.113.7');
  });
});

describe('GET /v0/intent', () => {
  it('reports counters, the window and the method', async () => {
    const { kv, h } = withKv();
    for (const client of ['get-client-1', 'get-client-2']) {
      await call(h, '/v0/intent', post('bet_a_report', { client_id: client }));
      await h.settled();
    }
    for (const client of ['get-client-1', 'get-client-2', 'get-client-3', 'get-client-4']) {
      await call(h, '/v0/intent', post('bet_a_report', { client_id: client, kind: 'view' }));
      await h.settled();
    }
    await flushCounters(kv as unknown as KVNamespace, { force: true });

    const { status, body } = await call(h, '/v0/intent?days=14');
    expect(status).toBe(200);
    expect(body.window_days).toBe(14);
    expect(body.clicks).toBe(2);
    expect(body.views).toBe(4);
    expect(body.intent_rate).toBe(0.5);
    expect(body.by_product.bet_a_report).toMatchObject({ clicks: 2, views: 4 });
    expect(body.method).toContain('localStorage');
  });

  it('includes counts that have not been flushed yet', async () => {
    const { h } = withKv();
    await call(h, '/v0/intent', post('bet_a_report', { client_id: 'unflushed-client' }));
    await h.settled();
    const { body } = await call(h, '/v0/intent');
    expect(body.clicks).toBe(1);
  });

  it('returns a null rate rather than dividing by zero', async () => {
    const { body } = await call(withKv().h, '/v0/intent');
    expect(body.clicks).toBe(0);
    expect(body.intent_rate).toBeNull();
  });

  it('warns when nothing is persisted', async () => {
    const { body } = await call(harness(), '/v0/intent');
    expect(body.persisted).toBe(false);
    expect(body.warning).toContain('only in this isolate');
  });
});
