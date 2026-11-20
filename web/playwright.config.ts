import { defineConfig, devices } from "@playwright/test";

// The end-to-end tests run against the built app, which Playwright builds and serves itself
// on a port of its own, so a run never meets a dev server on 3000. The address is the test
// server's, not a deployment's: what a deployment configures stays in next.config.ts.
const PORT = 3100;
const BASE_URL = `http://localhost:${PORT}`;
// Where the built app forwards /api/...: an address nothing listens on. The tests hand a
// page the service's answers themselves (e2e/api.ts), and the ones about a stopped service
// let the request through to find nothing there, whatever runs on this machine's port 8000.
// Feature 007: only e2e/stream.spec.ts puts a stand-in for the service there, to see a run's
// stream pass the forwarding as it is produced. It runs in projects of its own, after every
// other spec, so that nothing answers at this address while the others run.
export const STAND_IN_PORT = 3109;
const ABSENT_SERVICE = `http://127.0.0.1:${STAND_IN_PORT}`;
const STREAM_SPEC = "**/stream.spec.ts";

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  // End-to-end tests are *.spec.ts files under e2e/ and nothing else. The unit tests are
  // *.test.ts and *.test.tsx files run by Vitest, which leaves e2e/ alone in turn
  // (vitest.config.mts).
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* On CI, also print each test to the job log; the HTML report is kept as an artifact. */
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "html",
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('')`. */
    baseURL: BASE_URL,

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: "on-first-retry",
  },

  /* The browsers the console's users have. Add a project here to test in another. */
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      testIgnore: STREAM_SPEC,
    },

    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
      testIgnore: STREAM_SPEC,
    },

    // Feature 007: the stream through the forwarding, in each browser, one after the other,
    // once the specs that need the forwarding's address empty have run.
    {
      name: "stream-chromium",
      use: { ...devices["Desktop Chrome"] },
      testMatch: STREAM_SPEC,
      dependencies: ["chromium", "firefox"],
    },

    {
      name: "stream-firefox",
      use: { ...devices["Desktop Firefox"] },
      testMatch: STREAM_SPEC,
      dependencies: ["stream-chromium"],
    },
  ],

  /* Build the app and serve it before the tests start, as Next.js's guide recommends. */
  webServer: {
    command: `pnpm build && pnpm start --port ${PORT}`,
    url: BASE_URL,
    env: { FREYJA_API_URL: ABSENT_SERVICE },
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
