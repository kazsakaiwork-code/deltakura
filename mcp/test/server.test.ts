import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer, SERVER_NAME, SERVER_VERSION } from '../src/server.js';
import { loadConfig } from '../src/config.js';
import type { ProcurementDataset } from '../src/dataset.js';
import { fixtureDataset, makeNtaFixture, type NtaFixture } from './fixtures.js';

const dataset = fixtureDataset as unknown as ProcurementDataset;
let fixture: NtaFixture;
const openClients: Client[] = [];

beforeAll(() => {
  fixture = makeNtaFixture();
});
afterAll(() => fixture.cleanup());
afterEach(async () => {
  await Promise.all(openClients.splice(0).map((c) => c.close()));
});

async function connect(env: NodeJS.ProcessEnv = {} as NodeJS.ProcessEnv): Promise<Client> {
  const server = createServer({ config: loadConfig(env), dataset });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test', version: '0.0.0' });
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  openClients.push(client);
  return client;
}

async function call(client: Client, name: string, args: Record<string, unknown>) {
  const result = (await client.callTool({ name, arguments: args })) as {
    isError?: boolean;
    content: { type: string; text: string }[];
  };
  return { isError: result.isError === true, payload: JSON.parse(result.content[0].text) };
}

describe('MCP surface', () => {
  it('advertises the registry server name and version', async () => {
    const client = await connect();
    expect(SERVER_NAME).toBe('io.github.kazsakaiwork-code/jp-public-signals');
    expect(SERVER_VERSION).toBe('0.1.0');
    expect(client.getServerVersion()).toMatchObject({ name: SERVER_NAME, version: SERVER_VERSION });
  });

  it('exposes exactly three tools, all read-only', async () => {
    const client = await connect();
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name).sort()).toEqual([
      'jp_corporate_diff_summary',
      'jp_corporate_number_lookup',
      'jp_procurement_stats'
    ]);
    for (const tool of tools) {
      expect(tool.annotations?.readOnlyHint, tool.name).toBe(true);
      expect(tool.annotations?.destructiveHint, tool.name).toBe(false);
      expect(tool.description, tool.name).toBeTruthy();
      expect(tool.inputSchema.type, tool.name).toBe('object');
    }
  });

  it('answers jp_procurement_stats with caveats and attribution', async () => {
    const client = await connect();
    const { isError, payload } = await call(client, 'jp_procurement_stats', { fiscal_year: 2025 });
    expect(isError).toBe(false);
    expect(payload.totals.n_awards).toBe(29);
    expect(payload.caveats.length).toBeGreaterThan(0);
    expect(payload.attribution[0]).toContain('調達ポータル');
    expect(payload.award_ratio.available).toBe(false);
  });

  it('turns an unknown filter value into a helpful tool error, not a crash', async () => {
    const client = await connect();
    const { isError, payload } = await call(client, 'jp_procurement_stats', { winner_prefecture: 'Atlantis' });
    expect(isError).toBe(true);
    expect(payload.dimension).toBe('winner_prefecture');
    expect(payload.available).toContain('東京都 (13)');
  });

  it('serves the 法人番号 tools from local files when DELTAKURA_DATA_DIR is set', async () => {
    const client = await connect({ DELTAKURA_DATA_DIR: fixture.dir } as NodeJS.ProcessEnv);
    const lookup = await call(client, 'jp_corporate_number_lookup', { corporate_number: '1010001005145' });
    expect(lookup.isError).toBe(false);
    expect(lookup.payload).toMatchObject({ mode: 'local', found: true, name: 'テスト架空第二株式会社' });

    const summary = await call(client, 'jp_corporate_diff_summary', { from: '2026-09-01', to: '2026-09-03' });
    expect(summary.isError).toBe(false);
    expect(summary.payload.total_changes).toBe(6);
  });

  it('reports "remote not available" when there is no local data directory and remote calls are off', async () => {
    const client = await connect();
    const lookup = await call(client, 'jp_corporate_number_lookup', { corporate_number: '1010001005145' });
    expect(lookup.isError).toBe(true);
    expect(lookup.payload.error).toBe('RemoteNotAvailableError');
    expect(lookup.payload.message).toContain('remote not available');

    const summary = await call(client, 'jp_corporate_diff_summary', { from: '2026-09-01', to: '2026-09-03' });
    expect(summary.isError).toBe(true);
    expect(summary.payload.message).toContain('remote not available');
  });

  it('refuses a date range longer than 400 days', async () => {
    const client = await connect();
    const huge = await call(client, 'jp_corporate_diff_summary', { from: '1900-01-01', to: '9999-12-31' });
    expect(huge.isError).toBe(true);
    expect(huge.payload.message).toContain('maximum is 400');
  });

  it('validates the date range before touching any data', async () => {
    const client = await connect({ DELTAKURA_DATA_DIR: fixture.dir } as NodeJS.ProcessEnv);
    const bad = await call(client, 'jp_corporate_diff_summary', { from: '01/09/2026', to: '2026-09-03' });
    expect(bad.isError).toBe(true);
    expect(bad.payload.message).toContain('ISO dates');

    const reversed = await call(client, 'jp_corporate_diff_summary', { from: '2026-09-03', to: '2026-09-01' });
    expect(reversed.isError).toBe(true);
    expect(reversed.payload.message).toContain('is after');
  });
});
