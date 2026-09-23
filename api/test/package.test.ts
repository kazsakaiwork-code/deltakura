/**
 * Guards on the deployment surface: the publication boundary and the
 * "nothing is deployed" rule must survive a hurried edit.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p: string) => readFileSync(resolve(ROOT, p), 'utf8');

describe('package.json', () => {
  const pkg = JSON.parse(read('package.json'));

  it('is private: this Worker is deployed, never published to a registry', () => {
    expect(pkg.private).toBe(true);
  });

  it('has no deploy script that could run by accident', () => {
    expect(pkg.scripts.deploy).toContain('exit 1');
    expect(pkg.scripts.deploy).toContain('operator approval');
  });

  it('names no person', () => {
    expect(pkg.author).toBeUndefined();
    expect(JSON.stringify(pkg)).not.toMatch(/@gmail|@users\.noreply/);
  });
});

describe('wrangler.toml', () => {
  const toml = read('wrangler.toml');

  // Split into [top level, staging, production] at the environment headers.
  const prodAt = toml.indexOf('[env.production]');
  const stagingAt = toml.indexOf('[env.staging]');
  const idsIn = (text: string) =>
    [...text.matchAll(/^\s*(?:database_)?id\s*=\s*"([^"]+)"/gm)].map((m) => m[1]);

  it('never carries an account id', () => {
    expect(toml).not.toMatch(/account_id/);
    for (const binding of ['KV_INTENT', 'KV_METRICS', 'DB']) {
      expect(toml, binding).toContain(binding);
    }
  });

  it('keeps placeholders for the top level and staging', () => {
    expect(stagingAt).toBeGreaterThan(0);
    expect(prodAt).toBeGreaterThan(stagingAt);
    const ids = idsIn(toml.slice(0, prodAt));
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) expect(id, id).toMatch(/^REPLACE_WITH_/);
  });

  it('gives production real resource ids (KV: 32 hex, D1: UUID), nothing else', () => {
    const ids = idsIn(toml.slice(prodAt));
    expect(ids).toHaveLength(3);
    for (const id of ids) {
      expect(id, id).toMatch(/^([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/);
    }
  });

  it('does not let production claim the complete register', () => {
    expect(toml.slice(prodAt)).toMatch(/CORPORATE_RECORDS\s*=\s*"sample"/);
  });

  it('binds free-tier products only: no R2 bucket in any environment', () => {
    const active = toml
      .split('\n')
      .map((line) => line.replace(/#.*$/, ''))
      .join('\n');
    expect(active).not.toMatch(/r2_buckets/);
    expect(active).not.toMatch(/ARCHIVE/);
  });

  it('defaults to dev mode and declares no custom route', () => {
    expect(toml).toMatch(/DELTAKURA_MODE\s*=\s*"dev"/);
    expect(toml).not.toMatch(/^\s*routes\s*=/m);
  });

  it('declares the three named environments', () => {
    expect(toml).toContain('name = "deltakura-api"');
    expect(toml).toContain('name = "deltakura-api-stg"');
  });
});

describe('no maintainer-identifying or machine-specific strings', () => {
  const files = [
    'package.json',
    'wrangler.toml',
    'tsconfig.json',
    'README.md',
    'scripts/build-data.mjs',
    'scripts/d1-seed.mjs',
    'migrations/0001_init.sql'
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

describe('the D1 schema cannot hold a natural person', () => {
  const sql = read('migrations/0001_init.sql');

  it('has no personal-data column', () => {
    const columns = [...sql.matchAll(/^\s{2}([a-z_]+)\s+(TEXT|INTEGER)/gm)].map((m) => m[1]);
    expect(columns).toContain('corporate_number');
    for (const c of columns) {
      expect(c, c).not.toMatch(/担当|氏名|代表|連絡先|電話|mail|phone|person|street|change_cause|furigana/i);
    }
  });
});
