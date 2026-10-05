import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  workers: 3,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:3100', trace: 'retain-on-failure' },
  projects: [
    {
      name: 'desktop-light',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 },
        colorScheme: 'light',
      },
    },
    {
      name: 'desktop-dark',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 },
        colorScheme: 'dark',
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
        colorScheme: 'light',
        reducedMotion: 'reduce',
      },
    },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 3100',
    url: 'http://127.0.0.1:3100',
    reuseExistingServer: false,
    env: {
      VITE_API_BASE_URL: '/api',
      VITE_COMPANY_ID: '00000000-0000-4000-8000-000000000001',
      VITE_SITE_ID: '00000000-0000-4000-8000-000000000002',
      VITE_REPORTING_PERIOD_ID: '00000000-0000-4000-8000-000000000003',
      VITE_METRIC_DEFINITION_ID: '00000000-0000-4000-8000-000000000102',
    },
  },
})
