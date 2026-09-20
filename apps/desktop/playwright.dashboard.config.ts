import { defineConfig, devices } from '@playwright/test'

/**
 * Chromium smoke test for the already-running dashboard.
 *
 * Deliberately has no webServer: the test must target the real dashboard URL
 * (https://hermes-agent.nousresearch.com) and must not start a replacement
 * server. Install the Chromium browser separately when missing:
 *   npx playwright install chromium
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/dashboard-real.spec.ts',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'https://hermes-agent.nousresearch.com',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  reporter: 'list',
})
