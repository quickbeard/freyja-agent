import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "@/next.config";

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

describe("the build the container image is made from", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  // The setting is read once, when the configuration is loaded, so each case loads it anew.
  async function output(value: string | undefined) {
    vi.stubEnv("FREYJA_WEB_STANDALONE", value);
    vi.resetModules();
    return (await import("@/next.config")).default.output;
  }

  it("is the ordinary one unless it is asked for", async () => {
    expect(await output(undefined)).toBeUndefined();
    expect(await output("")).toBeUndefined();
    expect(await output("0")).toBeUndefined();
    expect(await output("true")).toBeUndefined();
  });

  it("is a standalone server when FREYJA_WEB_STANDALONE is 1", async () => {
    expect(await output("1")).toBe("standalone");
    expect(await output(" 1 ")).toBe("standalone");
  });
});

describe("the web app's address alone", () => {
  it("leads to the LLM section, and not for good", async () => {
    expect(await nextConfig.redirects?.()).toEqual([
      { source: "/", destination: "/llm", permanent: false },
    ]);
  });
});
