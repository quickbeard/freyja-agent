import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "./next.config";

async function rewrites() {
  const rules = await nextConfig.rewrites?.();
  if (!Array.isArray(rules)) {
    throw new Error("next.config.ts has no list of rewrites");
  }
  return rules;
}

describe("the path from the browser to the service", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("forwards /api to the service on this machine by default", async () => {
    vi.stubEnv("FREYJA_API_URL", undefined);
    expect(await rewrites()).toEqual([
      { source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" },
    ]);
  });

  it("forwards /api to FREYJA_API_URL when it is set", async () => {
    vi.stubEnv("FREYJA_API_URL", "http://127.0.0.1:8100");
    expect(await rewrites()).toEqual([
      { source: "/api/:path*", destination: "http://127.0.0.1:8100/:path*" },
    ]);
  });

  it("ignores a trailing slash and a blank value", async () => {
    vi.stubEnv("FREYJA_API_URL", "http://127.0.0.1:8100/");
    expect((await rewrites())[0].destination).toBe(
      "http://127.0.0.1:8100/:path*",
    );
    vi.stubEnv("FREYJA_API_URL", "  ");
    expect((await rewrites())[0].destination).toBe(
      "http://127.0.0.1:8000/:path*",
    );
  });
});

describe("the web app's address alone", () => {
  it("leads to the LLM section, and not for good", async () => {
    expect(await nextConfig.redirects?.()).toEqual([
      { source: "/", destination: "/llm", permanent: false },
    ]);
  });
});
