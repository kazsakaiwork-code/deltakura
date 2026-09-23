/**
 * The Worker and the MCP server answer the same questions, so they share one
 * query layer. `mcp/src/query/` is pure - no node builtins - which is why it can
 * be bundled into a Worker unchanged.
 *
 * Both directories ship in the same repository (`kazsakaiwork-code/deltakura`),
 * so this relative import is stable; the MCP package also exports it publicly
 * as `@deltakura/mcp/query`.
 */
export * from '../../mcp/src/query/index.js';
