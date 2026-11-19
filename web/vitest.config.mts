import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { configDefaults, defineConfig } from "vitest/config";

// As Next.js's Vitest guide gives it (node_modules/next/dist/docs, testing/vitest.md), plus one
// setup file: without Vitest's globals, Testing Library does not unmount between tests itself.
export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // e2e/ holds the Playwright tests (playwright.config.ts). Their *.spec.ts files match
    // Vitest's default pattern, and Vitest cannot run them.
    exclude: [...configDefaults.exclude, "e2e/**"],
    // Measured by `pnpm test:coverage`, which the commit hook and CI run. `pnpm test` does not
    // measure, so a run of one file is not held to the whole suite's numbers.
    coverage: {
      provider: "v8",
      // The app's own code, a file no test loads included. components/ui/ is shadcn's,
      // generated and not edited by hand.
      include: [
        "app/**/*.{ts,tsx}",
        "components/**/*.{ts,tsx}",
        "hooks/**/*.{ts,tsx}",
        "lib/**/*.{ts,tsx}",
        "next.config.ts",
      ],
      exclude: ["components/ui/**"],
      reporter: ["text"],
      // The share of the app the unit tests must run. Measured on 2026-11-19 at 95.05 of
      // statements, 93.67 of branches, 95 of functions, and 96.74 of lines. Raise them as
      // the numbers rise; lower one only with the reason written here.
      thresholds: {
        statements: 90,
        branches: 90,
        functions: 90,
        lines: 90,
      },
    },
  },
});
