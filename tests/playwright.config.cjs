"use strict";

const {defineConfig} = require("@playwright/test");

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "**/obsidian.e2e.spec.cjs",
  timeout: 30_000,
  expect: {timeout: 8_000},
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:9876",
    browserName: "chromium",
    headless: true,
  },
  webServer: {
    command: "python -m tests.e2e_server",
    url: "http://127.0.0.1:9876/api/site",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  reporter: [["list"]],
});
