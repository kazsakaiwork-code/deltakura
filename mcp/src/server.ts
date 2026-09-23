/**
 * The Deltakura MCP server: three read-only tools over Japanese public open data.
 *
 * Read-only by construction. Nothing here writes, posts, sends, deletes or
 * authenticates, and no tool can return an individual's personal data.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { loadConfig, dataMode, type Config } from './config.js';
import { loadProcurementDataset, type ProcurementDataset } from './dataset.js';
import { queryProcurementStats, UnknownDimensionError } from './query/procurement.js';
import { diffSummary, lookupCorporateNumber } from './nta/index.js';
import { isIsoDate } from './query/nta.js';

export const SERVER_NAME = 'io.github.kazsakaiwork-code/jp-public-signals';
export const SERVER_VERSION = '0.1.0';

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true
} as const;

const languageSchema = z
  .enum(['en', 'ja'])
  .optional()
  .describe('Language for the caveat strings. Default "en". The data labels themselves stay Japanese.');

export interface ServerDeps {
  config?: Config;
  dataset?: ProcurementDataset;
}

function textResult(payload: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(payload, null, 2) }] };
}

function errorResult(err: unknown) {
  const message = err instanceof Error ? err.message : String(err);
  const name = err instanceof Error ? err.name : 'Error';
  return {
    isError: true,
    content: [{ type: 'text' as const, text: JSON.stringify({ error: name, message }, null, 2) }]
  };
}

export function createServer(deps: ServerDeps = {}): McpServer {
  const config = deps.config ?? loadConfig();
  let dataset = deps.dataset;
  const getDataset = (): ProcurementDataset => (dataset ??= loadProcurementDataset());

  const server = new McpServer(
    { name: SERVER_NAME, version: SERVER_VERSION },
    {
      instructions:
        'Read-only access to Japanese public open data held by Deltakura. Deltakura is an ' +
        'UNOFFICIAL archive: it is not an official source and is not affiliated with, ' +
        'endorsed by or connected to 国税庁, デジタル庁 or any other government body.\n' +
        '- jp_procurement_stats: national procurement award statistics (調達ポータル 落札実績, FY2013-), ' +
        'aggregated by fiscal year x winner-registered prefecture x ministry sector. Award counts, ' +
        'amount quartiles and sums. 落札率 is NOT available: the source does not publish 予定価格.\n' +
        '- jp_corporate_number_lookup / jp_corporate_diff_summary: the 国税庁 法人番号 daily change files, ' +
        'retained past the publisher\'s ~40-day window. Corporations and public bodies only.\n' +
        'Every response carries its licence, attribution and caveats. Reproduce the attribution string ' +
        'whenever you show a number from this server. No tool here returns data about an individual.'
    }
  );

  server.registerTool(
    'jp_procurement_stats',
    {
      title: 'Japanese national procurement award statistics',
      description:
        'Query award statistics derived from 調達ポータル (the Japanese national procurement portal) open ' +
        'data, FY2013 onward. Filter by fiscal_year (年度, the calendar year the fiscal year starts in), ' +
        'sector (a coarse bucket derived from the procuring 府省 - it describes WHO BOUGHT, not what was ' +
        'sold; the source has no 業種 field) and winner_prefecture (the winner\'s REGISTERED head-office ' +
        '都道府県, joined from the 法人番号 register - not where the work is performed). Returns award ' +
        'counts, corporate vs masked-individual counts, amount sum/mean/min/max and amount quartiles in ' +
        'yen, plus the licence, attribution and the caveats that must be shown with any number. ' +
        '落札率 (award ratio) is NOT available from this source. Omitting a filter aggregates over that ' +
        'dimension; quartiles over more than one bucket are estimated and labelled as such.',
      inputSchema: {
        fiscal_year: z
          .number()
          .int()
          .optional()
          .describe('年度, e.g. 2025 means 2025-04-01 to 2026-03-31. Omit to cover every year.'),
        fiscal_year_from: z.number().int().optional().describe('Inclusive start of a 年度 range. Ignored if fiscal_year is given.'),
        fiscal_year_to: z.number().int().optional().describe('Inclusive end of a 年度 range. Ignored if fiscal_year is given.'),
        sector: z
          .string()
          .optional()
          .describe('Ministry-derived sector label, exact or a substring, e.g. "防衛", "国土交通・運輸", "厚生・労働".'),
        winner_prefecture: z
          .string()
          .optional()
          .describe('Winner\'s registered 都道府県: "東京都", "東京", the 2-digit code "13", or "不明" for masked individuals.'),
        include_buckets: z.boolean().optional().describe('Include the matching per-bucket rows. Default true.'),
        bucket_limit: z.number().int().min(0).max(200).optional().describe('How many buckets to return. Default 25, max 200.'),
        language: languageSchema
      },
      annotations: { ...READ_ONLY, openWorldHint: false }
    },
    async (args) => {
      try {
        return textResult(queryProcurementStats(getDataset(), args));
      } catch (err) {
        if (err instanceof UnknownDimensionError) {
          return {
            isError: true,
            content: [
              {
                type: 'text' as const,
                text: JSON.stringify(
                  { error: err.name, dimension: err.dimension, value: err.value, available: err.available },
                  null,
                  2
                )
              }
            ]
          };
        }
        return errorResult(err);
      }
    }
  );

  server.registerTool(
    'jp_corporate_number_lookup',
    {
      title: 'Japanese corporate number (法人番号) lookup',
      description:
        'Look up a 13-digit 法人番号 in the 国税庁 corporate-number change register that Deltakura retains ' +
        'past the publisher\'s ~40-day window. Returns the registered name, the address 都道府県 (and 市区町村), ' +
        'the 法人種別, and the latest change kind and date, with a short change history. ' +
        'Corporations and public bodies only: 法人番号 are not issued to 個人事業主 and this tool never ' +
        'returns data about an individual. "Not found" means no change was published for that number inside ' +
        'the collected window - never that the corporation does not exist. ' +
        `Data source: local files when DELTAKURA_DATA_DIR is set, otherwise the hosted Deltakura API ` +
        `(currently ${dataMode(config) === 'local' ? 'local mode' : 'remote mode, which is not live yet'}).`,
      inputSchema: {
        corporate_number: z.string().describe('13-digit 法人番号, e.g. "1234567890123". Spaces and hyphens are tolerated.'),
        history_limit: z.number().int().min(0).max(100).optional().describe('How many past changes to return. Default 20.'),
        language: languageSchema
      },
      annotations: { ...READ_ONLY, openWorldHint: dataMode(config) === 'remote' }
    },
    async ({ corporate_number, history_limit, language }) => {
      try {
        return textResult(
          await lookupCorporateNumber(config, corporate_number, { language, historyLimit: history_limit })
        );
      } catch (err) {
        return errorResult(err);
      }
    }
  );

  server.registerTool(
    'jp_corporate_diff_summary',
    {
      title: 'Japanese corporate register daily change counts',
      description:
        'Daily counts of 法人番号 register changes over a date range, from the 国税庁 差分 files Deltakura ' +
        'collects nightly. Use it to see registration activity over time - new assignments, name changes, ' +
        'address changes, closures. Optionally break each day down by 処理区分 (change kind). ' +
        'Dates with no file are reported separately: the publisher issues none on weekends, Japanese public ' +
        'holidays or 29 Dec - 3 Jan. Aggregate counts only, no personal data. ' +
        `Data source: local files when DELTAKURA_DATA_DIR is set, otherwise the hosted Deltakura API ` +
        `(currently ${dataMode(config) === 'local' ? 'local mode' : 'remote mode, which is not live yet'}).`,
      inputSchema: {
        from: z.string().describe('Inclusive start date, ISO YYYY-MM-DD.'),
        to: z.string().describe('Inclusive end date, ISO YYYY-MM-DD.'),
        group_by_change_kind: z
          .boolean()
          .optional()
          .describe('Also return per-day counts by 処理区分 code. Slower: it reads the records, not just the manifest.'),
        language: languageSchema
      },
      annotations: { ...READ_ONLY, openWorldHint: dataMode(config) === 'remote' }
    },
    async ({ from, to, group_by_change_kind, language }) => {
      try {
        if (!isIsoDate(from) || !isIsoDate(to)) {
          throw new Error(`from and to must be ISO dates (YYYY-MM-DD); got "${from}" and "${to}"`);
        }
        if (from > to) throw new Error(`"from" (${from}) is after "to" (${to})`);
        return textResult(await diffSummary(config, from, to, { language, groupByChangeKind: group_by_change_kind }));
      } catch (err) {
        return errorResult(err);
      }
    }
  );

  return server;
}
