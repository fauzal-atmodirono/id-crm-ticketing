// Headed, single-worker run against the live Proton CRM. See README.md.
//
// The browser is launched once by global setup (lib/global-setup.ts), where
// the person running the suite signs in by hand; every test connects to that
// same browser. Never run two copies at once (they would share the CDP port).
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // AI turns and async indexing can take a while; a hung test still ends.
  timeout: 4 * 60_000,
  expect: { timeout: 15_000 },
  globalSetup: './lib/global-setup.ts',
  reporter: [
    ['list'],
    ['json', { outputFile: 'results/results.json' }],
    ['html', { outputFolder: 'results/html', open: 'never' }],
  ],
  outputDir: 'results/artifacts',
  use: {
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    trace: 'off',
  },
});
