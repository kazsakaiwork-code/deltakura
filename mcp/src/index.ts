/**
 * Library entry point for @deltakura/mcp.
 *
 * The executable is `deltakura-mcp` (dist/cli.js); import this module if you
 * want to mount the server on a transport of your own.
 */
export { createServer, SERVER_NAME, SERVER_VERSION, type ServerDeps } from './server.js';
export { loadConfig, dataMode, DEFAULT_API_BASE, type Config, type DataMode } from './config.js';
export {
  loadProcurementDataset,
  datasetPath,
  setProcurementDataset,
  type ProcurementDataset
} from './dataset.js';
export { lookupCorporateNumber, diffSummary, LocalDataError, RemoteNotAvailableError } from './nta/index.js';
export * from './query/index.js';
