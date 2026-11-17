import type { NextConfig } from "next";

// The web app's deployment configuration is read here and nowhere else (constitution,
// principle IV), and documented in .env.example.
const DEFAULT_API_URL = "http://127.0.0.1:8000";

// Where this server forwards /api/...: the Freyja service. The browser only ever calls the
// web app's own origin, so the service's address never reaches it.
function apiUrl(): string {
  const configured = process.env.FREYJA_API_URL?.trim();
  return (configured || DEFAULT_API_URL).replace(/\/+$/, "");
}

const nextConfig: NextConfig = {
  /* config options here */
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl()}/:path*` }];
  },
  // The console has no page of its own at its address: it opens on the LLM section.
  async redirects() {
    return [{ source: "/", destination: "/llm", permanent: false }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
