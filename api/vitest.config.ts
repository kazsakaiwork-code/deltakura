import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // The Worker's fetch handler is exercised directly on Node's web globals:
    // no wrangler, no miniflare, no network, no Cloudflare account.
    include: ['test/**/*.test.ts'],
    environment: 'node',
    restoreMocks: true,
    // Module-level state (rate-limit windows, intent buffers) is per file.
    fileParallelism: false
  }
});
