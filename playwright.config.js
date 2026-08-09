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
    { name: 'mobile', use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `python3 -m http.server ${PORT} -d site --bind 127.0.0.1`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    stdout: 'ignore',
  },
});
