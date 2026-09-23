/**
 * End-to-end intent contract: site generator -> Worker.
 *
 * An audit found the two sides of this contract had never
 * been connected: the site emitted `bet-a`/`bet-c`/`bet-b`/`pricing` while the
 * Worker allowlisted `bet_a_report`/`bet_c_registry_diff`/..., and the page
 * posted `{product, page, cid, ts}` while the Worker read `{product, kind,
 * client_id}`. Every POST would have been refused with 400 unknown_product, the
 * `view` denominator would never have been collected, and `intent_rate_14d` —
 * the pay-intent metric — would have read zero.
 *
 * So this test does not restate either side. It reads `site/build.py`, takes the
 * product ids and the POST body shape the generator actually emits, and drives
 * the real Worker with them.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { call, harness, MemoryKV } from './helpers.js';
import { resetRateLimits } from '../src/http.js';
import { DEFAULT_PRODUCTS, flushCounters, resetIntentState } from '../src/routes/intent.js';
import type { Env } from '../src/env.js';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const BUILD_PY = readFileSync(resolve(REPO_ROOT, 'site', 'build.py'), 'utf8');

/** The ids the site generator declares as the contract. */
function declaredProducts(): string[] {
  const block = /INTENT_PRODUCTS = \(([\s\S]*?)\)/.exec(BUILD_PY);
  expect(block, 'site/build.py must declare INTENT_PRODUCTS').not.toBeNull();
  return [...block![1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
}

/** The ids the site generator actually renders into buttons. */
function renderedProducts(): string[] {
  const used = [...BUILD_PY.matchAll(/intent_button\(\s*lang,\s*"([^"]+)"/g)].map((m) => m[1]);
  expect(used.length, 'the site must render at least one intent button').toBeGreaterThan(0);
  return [...new Set(used)];
}

/** The JSON keys the generated intent.js posts. */
function postedFields(): string[] {
  const body = /body: JSON\.stringify\(\{([\s\S]*?)\}\)/.exec(BUILD_PY);
  expect(body, 'site/build.py must contain the intent.js fetch body').not.toBeNull();
  return [...body![1].matchAll(/^\s*([a-z_]+):/gm)].map((m) => m[1]);
}

beforeEach(() => {
  resetRateLimits();
  resetIntentState();
});

describe('site <-> Worker intent contract', () => {
  it('the site declares exactly the Worker allowlist', () => {
    expect(declaredProducts()).toEqual([...DEFAULT_PRODUCTS]);
  });

  it('every button the site renders is allowlisted', () => {
    for (const product of renderedProducts()) {
      expect(DEFAULT_PRODUCTS as readonly string[], product).toContain(product);
    }
  });

  it('the page posts the field names the Worker reads', () => {
    expect(postedFields().sort()).toEqual(['client_id', 'kind', 'product']);
  });

  it('the Worker accepts a real click and a real view for every rendered button', async () => {
    const kv = new MemoryKV();
    const h = harness({
      KV_INTENT: kv as unknown as KVNamespace,
      INTENT_RATE_LIMIT_PER_MINUTE: '500'
    } as Partial<Env>);
    const products = renderedProducts();

    for (const product of products) {
      for (const kind of ['view', 'click'] as const) {
        // Byte for byte what site/public/assets/intent.js sends.
        const { status, body } = await call(h, '/v0/intent', {
          method: 'POST',
          body: JSON.stringify({ product, kind, client_id: 'e2e-contract-client-0001' })
        });
        await h.settled();
        expect(status, `${product}/${kind}`).toBe(200);
        expect(body, `${product}/${kind}`).toMatchObject({ ok: true, product, kind, counted: true });
      }
    }

    await flushCounters(kv as unknown as KVNamespace, { force: true });
    for (const product of products) {
      expect(kv.store.get(`count:click:${product}:total`), product).toBe('1');
      expect(kv.store.get(`count:view:${product}:total`), product).toBe('1');
    }

    // The pay-intent metric is computable end to end: a denominator exists.
    const { body: summary } = await call(h, '/v0/intent?days=14');
    expect(summary.views).toBe(products.length);
    expect(summary.clicks).toBe(products.length);
    expect(summary.intent_rate).toBe(1);
  });

  it('still refuses an id the site does not emit', async () => {
    const h = harness({ KV_INTENT: new MemoryKV() as unknown as KVNamespace } as Partial<Env>);
    const { status, body } = await call(h, '/v0/intent', {
      method: 'POST',
      body: JSON.stringify({ product: 'bet-a', kind: 'click', client_id: 'legacy-id-client' })
    });
    expect(status).toBe(400);
    expect(body.error).toBe('unknown_product');
  });
});
