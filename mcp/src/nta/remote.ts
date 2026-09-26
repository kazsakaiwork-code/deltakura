/**
 * Remote mode: the hosted Deltakura read API.
 *
 * The API is live at https://deltakura-api.deltakura.workers.dev/v0 (the
 * default base). Remote calls are opt-in: until DELTAKURA_API_ENABLED=1 is set,
 * every call here returns a clear error instead of touching the network, so the
 * server makes no request the user did not switch on and stays testable without
 * a fixture HTTP server.
 */
import type { Config } from '../config.js';
import type { CorporateLookupResult, DiffSummaryResult } from '../query/nta.js';

export class RemoteNotAvailableError extends Error {
  readonly endpoint: string;
  constructor(endpoint: string, detail?: string) {
    super(
      `remote not available: ${endpoint} was not called. ` +
        'Set DELTAKURA_API_ENABLED=1 to call the hosted Deltakura read API, or set ' +
        'DELTAKURA_DATA_DIR to a local Deltakura data directory to answer this from local files instead.' +
        (detail ? ` (${detail})` : '')
    );
    this.name = 'RemoteNotAvailableError';
    this.endpoint = endpoint;
  }
}

async function getJson<T>(config: Config, path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${config.apiBase}${path}`);
  for (const [k, v] of Object.entries(params ?? {})) url.searchParams.set(k, v);
  const endpoint = url.toString();
  if (!config.remoteEnabled) throw new RemoteNotAvailableError(endpoint);

  let response: Response;
  try {
    response = await fetch(endpoint, {
      headers: { accept: 'application/json', 'user-agent': 'deltakura-mcp/0.1.0' }
    });
  } catch (err) {
    throw new RemoteNotAvailableError(endpoint, err instanceof Error ? err.message : String(err));
  }
  if (!response.ok) {
    throw new RemoteNotAvailableError(endpoint, `HTTP ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function lookupCorporateNumberRemote(
  config: Config,
  corporateNumber: string,
  opts: { language?: 'en' | 'ja'; historyLimit?: number } = {}
): Promise<CorporateLookupResult> {
  return getJson<CorporateLookupResult>(config, `/corporate/${encodeURIComponent(corporateNumber)}`, {
    ...(opts.language ? { lang: opts.language } : {}),
    ...(opts.historyLimit ? { history: String(opts.historyLimit) } : {})
  });
}

export function diffSummaryRemote(
  config: Config,
  from: string,
  to: string,
  opts: { language?: 'en' | 'ja'; groupByChangeKind?: boolean } = {}
): Promise<DiffSummaryResult> {
  return getJson<DiffSummaryResult>(config, '/corporate/diff-summary', {
    from,
    to,
    ...(opts.language ? { lang: opts.language } : {}),
    ...(opts.groupByChangeKind ? { group_by: 'change_kind' } : {})
  });
}
