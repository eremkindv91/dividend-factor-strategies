const { defineConfig, devices } = require('playwright/test');

const PORT = 4173;

module.exports = defineConfig({
  testDir: './tests/ui',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile-375', use: { ...devices['Pixel 5'], viewport: { width: 375, height: 812 } } },
    { name: 'mobile', use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } } },
    { name: 'mobile-430', use: { ...devices['Pixel 5'], viewport: { width: 430, height: 932 } } },
    { name: 'tablet-768', use: { ...devices['Pixel 5'], viewport: { width: 768, height: 1024 } } },
  ],
  webServer: {
    command: `python3 -m http.server ${PORT} -d site --bind 127.0.0.1`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    stdout: 'ignore',
  },
});
