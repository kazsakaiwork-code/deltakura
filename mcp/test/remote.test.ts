import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadConfig, dataMode, DEFAULT_API_BASE } from '../src/config.js';
import { diffSummary, lookupCorporateNumber, RemoteNotAvailableError } from '../src/nta/index.js';

const emptyEnv = {} as NodeJS.ProcessEnv;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('mode selection', () => {
  it('is remote when DELTAKURA_DATA_DIR is unset', () => {
    expect(dataMode(loadConfig(emptyEnv))).toBe('remote');
  });

  it('is local when DELTAKURA_DATA_DIR is set', () => {
    expect(dataMode(loadConfig({ DELTAKURA_DATA_DIR: '/somewhere' } as NodeJS.ProcessEnv))).toBe('local');
  });

  it('defaults to the live workers.dev API base and trims trailing slashes', () => {
    expect(loadConfig(emptyEnv).apiBase).toBe(DEFAULT_API_BASE);
    expect(DEFAULT_API_BASE).toBe('https://deltakura-api.deltakura.workers.dev/v0');
    expect(loadConfig({ DELTAKURA_API_BASE: 'https://example.test/v0//' } as NodeJS.ProcessEnv).apiBase).toBe(
      'https://example.test/v0'
    );
  });
});

describe('remote mode is opt-in', () => {
  it('fails clearly for a lookup and touches no network', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const err = await lookupCorporateNumber(loadConfig(emptyEnv), '1010001005145').catch((e) => e);
    expect(err).toBeInstanceOf(RemoteNotAvailableError);
    expect(err.message).toContain('remote not available');
    expect(err.message).toContain('https://deltakura-api.deltakura.workers.dev/v0/corporate/1010001005145');
    expect(err.message).toContain('DELTAKURA_API_ENABLED=1');
    expect(err.message).toContain('DELTAKURA_DATA_DIR');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('fails clearly for a diff summary and touches no network', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const err = await diffSummary(loadConfig(emptyEnv), '2026-09-01', '2026-09-03').catch((e) => e);
    expect(err).toBeInstanceOf(RemoteNotAvailableError);
    expect(err.endpoint).toContain('/corporate/diff-summary');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('only calls out once DELTAKURA_API_ENABLED=1, and reports a failure honestly', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('nope', { status: 503, statusText: 'Service Unavailable' }));
    const config = loadConfig({ DELTAKURA_API_ENABLED: '1', DELTAKURA_API_BASE: 'https://example.test/v0' } as NodeJS.ProcessEnv);
    const err = await lookupCorporateNumber(config, '1010001005145').catch((e) => e);
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(err).toBeInstanceOf(RemoteNotAvailableError);
    expect(err.message).toContain('HTTP 503');
  });

  it('calls the workers.dev host by default once enabled', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ corporate_number: '1010001005145', found: true }));
    await lookupCorporateNumber(loadConfig({ DELTAKURA_API_ENABLED: '1' } as NodeJS.ProcessEnv), '1010001005145');
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      'https://deltakura-api.deltakura.workers.dev/v0/corporate/1010001005145'
    );
  });

  it('never points at the unregistered api.deltakura.dev host', () => {
    expect(DEFAULT_API_BASE).not.toContain('api.deltakura.dev');
  });

  it('returns the API payload when the API answers', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      Response.json({ corporate_number: '1010001005145', found: true, mode: 'remote' })
    );
    const config = loadConfig({ DELTAKURA_API_ENABLED: '1', DELTAKURA_API_BASE: 'https://example.test/v0' } as NodeJS.ProcessEnv);
    const r = await lookupCorporateNumber(config, '1010001005145');
    expect(r).toMatchObject({ corporate_number: '1010001005145', found: true, mode: 'remote' });
  });
});
