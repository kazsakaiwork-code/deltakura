/**
 * Offline test harness: the Worker's fetch handler is called directly with a
 * fake environment. No wrangler, no miniflare, no network, no account.
 */
import worker from '../src/index.js';
import type { Env } from '../src/env.js';

export class MemoryKV {
  readonly store = new Map<string, string>();
  /** Every key ever written, in order - so a test can count writes, not just keys. */
  readonly writeLog: string[] = [];
  gets = 0;

  get puts(): number {
    return this.writeLog.length;
  }

  async get(key: string): Promise<string | null> {
    this.gets++;
    return this.store.get(key) ?? null;
  }

  async put(key: string, value: string): Promise<void> {
    this.writeLog.push(key);
    this.store.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(): Promise<{ keys: { name: string }[]; list_complete: boolean }> {
    return { keys: [...this.store.keys()].map((name) => ({ name })), list_complete: true };
  }
}

export interface Harness {
  env: Env;
  ctx: ExecutionContext;
  settled(): Promise<void>;
}

export function harness(overrides: Partial<Env> = {}): Harness {
  const waiting: Promise<unknown>[] = [];
  const ctx = {
    waitUntil: (p: Promise<unknown>) => {
      waiting.push(p);
    },
    passThroughOnException: () => {}
  } as unknown as ExecutionContext;

  return {
    env: { DELTAKURA_MODE: 'dev', ...overrides } as Env,
    ctx,
    settled: async () => {
      await Promise.all(waiting.splice(0));
    }
  };
}

export function req(
  path: string,
  init: RequestInit & { origin?: string; ip?: string } = {}
): Request {
  const headers = new Headers(init.headers);
  if (init.origin) headers.set('origin', init.origin);
  headers.set('cf-connecting-ip', init.ip ?? '203.0.113.7');
  if (init.body && !headers.has('content-type')) headers.set('content-type', 'application/json');
  return new Request(`https://api.example.test${path}`, { ...init, headers });
}

export async function call(
  h: Harness,
  path: string,
  init: RequestInit & { origin?: string; ip?: string } = {}
): Promise<{ status: number; body: any; headers: Headers }> {
  const response = await worker.fetch(req(path, init), h.env, h.ctx);
  const text = await response.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body, headers: response.headers };
}
