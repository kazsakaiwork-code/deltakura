/**
 * Runtime configuration. Everything comes from the environment; nothing is
 * baked in, and no path in this package is absolute.
 */

export const DEFAULT_API_BASE = 'https://api.deltakura.dev/v0';

export interface Config {
  /**
   * Root of a local Deltakura data checkout. When set, the corporate-number
   * tools read `<dir>/nta/normalized/*.csv.gz` and `<dir>/nta/manifest.csv`
   * directly. When unset, those tools fall back to remote mode.
   */
  dataDir?: string;
  /** Base URL of the (not yet deployed) Deltakura read API. */
  apiBase: string;
  /**
   * Remote mode is wired but switched off by default, because the API is not
   * deployed yet. Set DELTAKURA_API_ENABLED=1 once it is live.
   */
  remoteEnabled: boolean;
  /** Safety valve: refuse to stream more than this many bytes of gzipped diffs per call. */
  maxScanBytes: number;
}

function intFromEnv(env: NodeJS.ProcessEnv, key: string, fallback: number): number {
  const raw = env[key];
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const dataDir = env.DELTAKURA_DATA_DIR?.trim();
  return {
    dataDir: dataDir ? dataDir : undefined,
    apiBase: (env.DELTAKURA_API_BASE?.trim() || DEFAULT_API_BASE).replace(/\/+$/, ''),
    remoteEnabled: env.DELTAKURA_API_ENABLED === '1',
    maxScanBytes: intFromEnv(env, 'DELTAKURA_NTA_MAX_SCAN_BYTES', 256 * 1024 * 1024)
  };
}

export type DataMode = 'local' | 'remote';

export function dataMode(config: Config): DataMode {
  return config.dataDir ? 'local' : 'remote';
}
