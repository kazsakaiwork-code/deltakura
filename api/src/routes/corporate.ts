/**
 * GET /v0/corporate/:number       one 法人番号
 * GET /v0/corporate/diff-summary  daily change counts over a range
 *
 * Same shaping and the same personal-data guards as the MCP tools: the response
 * objects are built by the shared query layer, not assembled here.
 */
import type { Env } from '../env.js';
import { errorResponse, json } from '../http.js';
import { corporateStore } from '../store/index.js';
import {
  buildDiffSummary,
  buildLookupResult,
  isIsoDate,
  isValidCorporateNumber,
  normalizeCorporateNumber
} from '../shared.js';

const MAX_RANGE_DAYS = 400;

function language(url: URL): 'en' | 'ja' {
  return url.searchParams.get('lang') === 'ja' ? 'ja' : 'en';
}

export async function handleCorporateLookup(request: Request, env: Env, raw: string): Promise<Response> {
  const url = new URL(request.url);
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    decoded = raw; // malformed %-escape: fall through to the 13-digit check, which refuses it
  }
  const corporateNumber = normalizeCorporateNumber(decoded.slice(0, 64));
  if (!/^\d{13}$/.test(corporateNumber)) {
    return errorResponse(
      400,
      'bad_request',
      `"${raw.slice(0, 40)}" is not a 13-digit 法人番号. Corporate numbers are exactly 13 digits (e.g. 1234567890123).`
    );
  }

  const historyLimit = Math.min(Math.max(Number(url.searchParams.get('history') ?? 20) || 20, 0), 100);
  const store = corporateStore(env);

  let changes;
  let coverage;
  try {
    ({ changes, coverage } = await store.lookup(corporateNumber));
  } catch (err) {
    console.error('data store', err);
    return errorResponse(503, 'data_unavailable', 'The data store is temporarily unavailable.');
  }

  const result = buildLookupResult(corporateNumber, changes, {
    mode: 'local',
    coverage,
    language: language(url),
    historyLimit
  });
  if (!isValidCorporateNumber(corporateNumber)) {
    result.notes.push(
      'This number does not pass the NTA check-digit test, so it is not a valid 法人番号 even if it is 13 digits long.'
    );
  }
  if (store.partial) {
    result.notes.push(
      'This deployment serves a fixed sample of corporate records, not the full archive: a miss here is not ' +
        'evidence of anything. Daily change counts (/v0/corporate/diff-summary) are complete for the covered window.'
    );
  }

  const { mode: _mode, ...payload } = result;
  return json(
    { ...payload, data_source: store.kind, complete: !store.partial },
    { status: result.found ? 200 : 404, headers: { 'cache-control': 'public, max-age=900' } }
  );
}

export async function handleDiffSummary(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const from = url.searchParams.get('from') ?? '';
  const to = url.searchParams.get('to') ?? '';
  if (!isIsoDate(from) || !isIsoDate(to)) {
    return errorResponse(400, 'bad_request', 'from and to are required ISO dates (YYYY-MM-DD)', { from, to });
  }
  if (from > to) return errorResponse(400, 'bad_request', `"from" (${from}) is after "to" (${to})`);
  const spanDays = Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
  if (spanDays > MAX_RANGE_DAYS) {
    return errorResponse(400, 'range_too_large', `range is ${spanDays} days; the maximum is ${MAX_RANGE_DAYS}`);
  }

  const byKind = url.searchParams.get('group_by') === 'change_kind';
  const store = corporateStore(env);

  let data;
  try {
    data = await store.dailyCounts(from, to, byKind);
  } catch (err) {
    console.error('data store', err);
    return errorResponse(503, 'data_unavailable', 'The data store is temporarily unavailable.');
  }

  const result = buildDiffSummary(from, to, data.counts, data.byKind, {
    mode: 'local',
    coverage: data.coverage,
    language: language(url)
  });

  const { mode: _mode, ...payload } = result;
  return json(
    { ...payload, data_source: store.kind },
    { headers: { 'cache-control': 'public, max-age=3600' } }
  );
}
