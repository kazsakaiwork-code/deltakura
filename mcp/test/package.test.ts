/**
 * Guards on the package itself: the publication boundary and the registry
 * metadata must stay true even when someone edits in a hurry.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p: string) => readFileSync(resolve(ROOT, p), 'utf8');
const json = (p: string) => JSON.parse(read(p));

describe('package.json', () => {
  const pkg = json('package.json');

  it('is the agreed name, version and licence', () => {
    expect(pkg.name).toBe('@deltakura/mcp');
    expect(pkg.version).toBe('0.1.0');
    expect(pkg.license).toBe('MIT');
    expect(pkg.private).toBe(false);
  });

  it('exposes the documented binary', () => {
    expect(pkg.bin).toEqual({ 'deltakura-mcp': 'dist/cli.js' });
  });

  it('names no person anywhere', () => {
    expect(pkg.author).toBeUndefined();
    expect(pkg.contributors).toBeUndefined();
    expect(JSON.stringify(pkg)).not.toMatch(/@gmail|@users\.noreply/);
  });

  it('ships only the built server and its data', () => {
    expect(pkg.files).toEqual(['dist', 'data/procurement-stats.json', 'server.json', 'README.md', 'LICENSE']);
  });
});

describe('server.json', () => {
  const server = json('server.json');
  const pkg = json('package.json');

  it('claims the agreed MCP registry namespace', () => {
    expect(server.name).toBe('io.github.kazsakaiwork-code/jp-public-signals');
  });

  it('stays in step with package.json', () => {
    expect(server.version).toBe(pkg.version);
    const npmPackage = server.packages.find((p: { registryType: string }) => p.registryType === 'npm');
    expect(npmPackage.identifier).toBe(pkg.name);
    expect(npmPackage.version).toBe(pkg.version);
  });

  it('declares stdio transport and no remote endpoint', () => {
    for (const p of server.packages) expect(p.transport.type).toBe('stdio');
    expect(server.remotes).toBeUndefined();
  });
});

describe('no maintainer-identifying or machine-specific strings', () => {
  const files = [
    'package.json',
    'server.json',
    'smithery.yaml',
    'README.md',
    'LICENSE',
    'tsconfig.json',
    'scripts/build-data.mjs'
  ];

  it('has no absolute filesystem path and no personal identifier', () => {
    for (const f of files) {
      const text = read(f);
      expect(text, f).not.toMatch(/[A-Za-z]:\\\\?Users/);
      expect(text, f).not.toMatch(/\/home\/[a-z]/);
      expect(text, f).not.toMatch(/@gmail\.com|@outlook\.com|@yahoo\./i);
    }
  });
});
