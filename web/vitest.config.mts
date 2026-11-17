import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

// As Next.js's Vitest guide gives it (node_modules/next/dist/docs, testing/vitest.md), plus one
// setup file: without Vitest's globals, Testing Library does not unmount between tests itself.
export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
