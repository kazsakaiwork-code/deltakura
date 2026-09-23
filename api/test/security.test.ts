/**
 * Hardening that a public, unauthenticated Worker needs: no error detail leaks,
 * no spoofable client address in production, no unbounded body, no counting
 * with a publicly known salt, and document-safe response headers.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import worker from '../src/index.js';
import type { Env } from '../src/env.js';
import { call, harness, MemoryKV } from './helpers.js';
import { ipPrefix, resetRateLimits } from '../src/http.js';
import { resetIntentState } from '../src/routes/intent.js';

beforeEach(() => {
  resetRateLimits();
  resetIntentState();
});

const intentBody = (extra: Record<string, unknown> = {}) =>
  JSON.stringify({ product: 'bet_a_report', kind: 'click', client_id: 'security-client-0001', ...extra });

describe('response headers', () => {
  it('marks every JSON response as nosniff, unframeable data', async () => {
    for (const path of ['/v0/health', '/v0/does-not-exist', '/v0/corporate/abc']) {
      const { headers } = await call(harness(), path);
      expect(headers.get('x-content-type-options'), path).toBe('nosniff');
      expect(headers.get('content-security-policy'), path).toContain("frame-ancestors 'none'");
    }
  });

  it('adds them to a CORS preflight too', async () => {
    const { status, headers } = await call(harness(), '/v0/intent', { method: 'OPTIONS' });
    expect(status).toBe(204);
    expect(headers.get('x-content-type-options')).toBe('nosniff');
  });
});

describe('error responses carry no internal detail', () => {
  it('returns a generic 500 when a handler throws', async () => {
    const h = harness();
    const exploding = {
      prepare: () => {
        throw new Error('SECRET-INTERNAL-DETAIL at /srv/worker/index.ts:42');
      }
    };
    // The corporate store reads D1 in prod mode; a throwing database stands in for any failure.
    h.env = { ...h.env, DELTAKURA_MODE: 'prod', IP_HASH_SALT: 'x', DB: exploding as unknown as D1Database } as Env;
    const errors: unknown[] = [];
    const original = console.error;
    console.error = (...args: unknown[]) => void errors.push(args);
    try {
      const { status, body } = await call(h, '/v0/corporate/diff-summary?from=2026-09-01&to=2026-09-07');
      expect(status).toBe(503);
      expect(JSON.stringify(body)).not.toContain('SECRET-INTERNAL-DETAIL');
      expect(JSON.stringify(body)).not.toContain('/srv/');
    } finally {
      console.error = original;
    }
    expect(errors.length).toBeGreaterThan(0); // logged server-side instead
  });

  it('answers a malformed percent-escape with 400, not 500', async () => {
    const { status, body } = await call(harness(), '/v0/corporate/%E0%A4%A');
    expect(status).toBe(400);
    expect(body.error).toBe('bad_request');
  });

  it('does not echo an unbounded path back', async () => {
    const long = 'a'.repeat(5000);
    const { status, body } = await call(harness(), `/v0/${long}`);
    expect(status).toBe(404);
    expect(String(body.message).length).toBeLessThan(300);
  });
});

describe('client address', () => {
  it('ignores X-Forwarded-For in production, where only the edge header is trusted', async () => {
    const h = harness({ DELTAKURA_MODE: 'prod', IP_HASH_SALT: 'test-salt', RATE_LIMIT_PER_MINUTE: '1' });
    const spoof = (xff: string) =>
      worker.fetch(
        new Request('https://api.example.test/v0/health', {
          headers: { 'cf-connecting-ip': '198.51.100.9', 'x-forwarded-for': xff }
        }),
        h.env,
        h.ctx
      );
    expect((await spoof('10.0.0.1')).status).toBe(200);
    // A fresh X-Forwarded-For per request must not buy a fresh window.
    expect((await spoof('10.0.0.2')).status).toBe(429);
  });

  it('keys an IPv6 client on its /64, so rotating inside it does not reset the window', async () => {
    const h = harness({ RATE_LIMIT_PER_MINUTE: '1' });
    expect((await call(h, '/v0/health', { ip: '2001:db8:1:2::1' })).status).toBe(200);
    expect((await call(h, '/v0/health', { ip: '2001:db8:1:2:ffff:ffff:ffff:fffe' })).status).toBe(429);
    expect((await call(h, '/v0/health', { ip: '2001:db8:1:3::1' })).status).toBe(200);
  });

  it('coarsens addresses as documented', () => {
    expect(ipPrefix('203.0.113.7', { octetsV4: 2, hextetsV6: 3 })).toBe('203.0/16');
    expect(ipPrefix('2001:db8:abcd:12::1', { octetsV4: 2, hextetsV6: 3 })).toBe('2001:db8:abcd::/48');
    expect(ipPrefix('::1', { octetsV4: 4, hextetsV6: 4 })).toBe('0:0:0:0::/64');
  });
});

describe('POST /v0/intent input limits', () => {
  it('refuses an oversized body before parsing it', async () => {
    const { status, body } = await call(harness(), '/v0/intent', {
      method: 'POST',
      body: intentBody({ padding: 'x'.repeat(10_000) })
    });
    expect(status).toBe(413);
    expect(body.error).toBe('payload_too_large');
  });

  it('refuses to count in production without the secret salt', async () => {
    const kv = new MemoryKV();
    const h = harness({ DELTAKURA_MODE: 'prod', KV_INTENT: kv as unknown as KVNamespace } as Partial<Env>);
    const original = console.error;
    console.error = () => {};
    try {
      const { status, body } = await call(h, '/v0/intent', { method: 'POST', body: intentBody() });
      expect(status).toBe(503);
      expect(body.error).toBe('not_configured');
    } finally {
      console.error = original;
    }
    await h.settled();
    expect(kv.puts).toBe(0);
  });

  it('builds KV keys only from allowlisted parts and a hex hash', async () => {
    const kv = new MemoryKV();
    const h = harness({ KV_INTENT: kv as unknown as KVNamespace } as Partial<Env>);
    const hostile = await call(h, '/v0/intent', {
      method: 'POST',
      body: intentBody({ product: 'bet_a_report:total', client_id: 'x:../count:click:bet_a_api:total' })
    });
    expect(hostile.status).toBe(400);
    const ok = await call(h, '/v0/intent', {
      method: 'POST',
      body: intentBody({ client_id: 'x:../count:click:bet_a_api:total' })
    });
    expect(ok.status).toBe(200);
    await h.settled();
    for (const key of kv.store.keys()) {
      expect(key).toMatch(/^(dedup:\d{4}-\d{2}-\d{2}:(click|view):[a-z_]+:[0-9a-f]{20}|count:(click|view):[a-z_]+:(\d{4}-\d{2}-\d{2}|total))$/);
    }
  });

  it('never keeps a de-duplication hash for more than 25 hours', async () => {
    const ttls: number[] = [];
    const kv = new MemoryKV();
    const put = kv.put.bind(kv);
    (kv as unknown as { put: unknown }).put = async (key: string, value: string, opts?: { expirationTtl?: number }) => {
      if (key.startsWith('dedup:')) ttls.push(opts?.expirationTtl ?? Infinity);
      return put(key, value);
    };
    const h = harness({ KV_INTENT: kv as unknown as KVNamespace } as Partial<Env>);
    await call(h, '/v0/intent', { method: 'POST', body: intentBody() });
    await h.settled();
    expect(ttls.length).toBe(1);
    expect(ttls[0]).toBeLessThanOrEqual(25 * 3600);
  });
});
