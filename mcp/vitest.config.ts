import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Every test in this package runs offline and touches no private data.
    include: ['test/**/*.test.ts'],
    environment: 'node',
    restoreMocks: true
  }
});
