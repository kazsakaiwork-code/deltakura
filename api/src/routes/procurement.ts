/**
 * GET /v0/procurement/stats
 *
 * Same engine as the MCP tool `jp_procurement_stats`, so the two cannot drift.
 */
import type { Env } from '../env.js';
import { errorResponse, json } from '../http.js';
import { procurementStore } from '../store/index.js';
import { queryProcurementStats, UnknownDimensionError, type ProcurementQuery } from '../shared.js';

function intParam(params: URLSearchParams, name: string): number | undefined {
  const raw = params.get(name);
  if (raw === null || raw.trim() === '') return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n)) throw new TypeError(`${name} must be a number, got "${raw}"`);
  return Math.trunc(n);
}

function boolParam(params: URLSearchParams, name: string): boolean | undefined {
  const raw = params.get(name);
  if (raw === null) return undefined;
  return !/^(0|false|no)$/i.test(raw.trim());
}

export async function handleProcurementStats(request: Request, env: Env): Promise<Response> {
  const params = new URL(request.url).searchParams;
  let query: ProcurementQuery;
  try {
    query = {
      fiscal_year: intParam(params, 'fiscal_year'),
      fiscal_year_from: intParam(params, 'fiscal_year_from'),
      fiscal_year_to: intParam(params, 'fiscal_year_to'),
      sector: params.get('sector') ?? undefined,
      winner_prefecture: params.get('winner_prefecture') ?? params.get('prefecture') ?? undefined,
      include_buckets: boolParam(params, 'include_buckets'),
      bucket_limit: intParam(params, 'bucket_limit'),
      language: params.get('lang') === 'ja' ? 'ja' : 'en'
    };
  } catch (err) {
    return errorResponse(400, 'bad_request', err instanceof Error ? err.message : String(err));
  }

  const store = procurementStore(env);
  let dataset;
  try {
    dataset = await store.dataset();
  } catch (err) {
    return errorResponse(503, 'data_unavailable', err instanceof Error ? err.message : String(err));
  }

  try {
    const result = queryProcurementStats(dataset, query);
    return json(
      { ...result, data_source: store.kind },
      { headers: { 'cache-control': 'public, max-age=3600' } }
    );
  } catch (err) {
    if (err instanceof UnknownDimensionError) {
      return errorResponse(400, 'unknown_value', err.message, {
        dimension: err.dimension,
        value: err.value,
        available: err.available
      });
    }
    return errorResponse(500, 'query_failed', err instanceof Error ? err.message : String(err));
  }
}
