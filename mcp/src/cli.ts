#!/usr/bin/env node
/**
 * `deltakura-mcp` - stdio MCP server.
 *
 * Nothing is written to stdout except MCP protocol frames; diagnostics go to
 * stderr, because a stray print on stdout corrupts the stream.
 */
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createServer, SERVER_NAME, SERVER_VERSION } from './server.js';
import { dataMode, loadConfig } from './config.js';

const HELP = `${SERVER_NAME} ${SERVER_VERSION}

Read-only MCP server over Japanese public open data (stdio transport).

Usage:
  deltakura-mcp              start the server on stdio
  deltakura-mcp --version    print the version
  deltakura-mcp --help       print this text

Environment:
  DELTAKURA_DATA_DIR            path to a local Deltakura data directory. When set,
                                the 法人番号 tools read <dir>/nta/normalized/*.csv.gz
                                and <dir>/nta/manifest.csv. When unset, those tools
                                use the hosted API, which is not deployed yet.
  DELTAKURA_API_BASE            override the hosted API base URL.
  DELTAKURA_API_ENABLED=1       actually call the hosted API instead of returning
                                "remote not available yet".
  DELTAKURA_NTA_MAX_SCAN_BYTES  cap on the bytes of gzipped diffs scanned per call.

Procurement statistics ship inside the package and need no configuration.
`;

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  if (argv.includes('--help') || argv.includes('-h')) {
    process.stderr.write(HELP);
    return;
  }
  if (argv.includes('--version') || argv.includes('-v')) {
    process.stderr.write(`${SERVER_VERSION}\n`);
    return;
  }

  const config = loadConfig();
  const server = createServer({ config });
  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write(
    `${SERVER_NAME} ${SERVER_VERSION} ready on stdio (法人番号 tools in ${dataMode(config)} mode)\n`
  );
}

main().catch((err: unknown) => {
  process.stderr.write(`${err instanceof Error ? err.stack ?? err.message : String(err)}\n`);
  process.exit(1);
});
