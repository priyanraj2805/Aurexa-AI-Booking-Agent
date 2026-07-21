import { defineConfig, devices } from '@playwright/test';

const e2eProfile = process.env.E2E_PROFILE ?? 'gating';
const legacyFlakyTag = /@legacy-flaky/;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  grep: e2eProfile === 'legacy' ? legacyFlakyTag : undefined,
  grepInvert: e2eProfile === 'legacy' ? undefined : legacyFlakyTag,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:5174',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5174',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
});
