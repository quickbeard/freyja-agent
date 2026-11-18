import { describe, expect, it } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import {
  activateProfile,
  createProfile,
  type LlmProfile,
  type LlmProfileList,
  listProfiles,
  PROVIDERS,
  providerLabel,
} from "@/lib/llm";

const LIST: LlmProfileList = {
  profiles: [],
  active_id: "environment",
  usable_model: true,
  secret_configured: true,
  limit: 50,
};

describe("listProfiles", () => {
  it("asks for the profiles and returns the list", async () => {
    const fetchStub = stubFetch({ json: LIST });
    expect(await listProfiles()).toEqual(LIST);
    expect(fetchStub.mock.calls[0][0]).toBe("/api/llm/profiles");
  });
});

const SAVED: LlmProfile = {
  id: "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b",
  name: "Local vLLM",
  source: "saved",
  provider: "openai",
  model: "served-model",
  base_url: null,
  responses_api: false,
  key_hint: "0001",
  active: false,
  usable: true,
  unusable_reason: null,
  last_test: null,
  created_at: "2026-11-17T09:10:44.501000+00:00",
  updated_at: "2026-11-17T09:10:44.501000+00:00",
};

describe("createProfile", () => {
  it("posts the values as JSON and returns the saved profile", async () => {
    const fetchStub = stubFetch({ status: 201, json: SAVED });
    const values = {
      name: "Local vLLM",
      provider: "openai" as const,
      model: "served-model",
      base_url: null,
      api_key: "sk-recognizable-key-0001",
      responses_api: false,
    };
    expect(await createProfile(values)).toEqual(SAVED);

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe("/api/llm/profiles");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(String(init?.body))).toEqual(values);
  });
});

describe("activateProfile", () => {
  it("posts to the profile's activation and returns the list", async () => {
    const fetchStub = stubFetch({ json: LIST });
    expect(await activateProfile(SAVED.id)).toEqual(LIST);

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}/activate`);
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });

  it("takes the environment's id as it is", async () => {
    const fetchStub = stubFetch({ json: LIST });
    await activateProfile("environment");
    expect(fetchStub.mock.calls[0][0]).toBe(
      "/api/llm/profiles/environment/activate",
    );
  });
});

describe("PROVIDERS", () => {
  it("lists the three providers a profile may use, with the console's names for them", () => {
    expect(PROVIDERS).toEqual([
      { value: "openai", label: "OpenAI-compatible" },
      { value: "anthropic", label: "Anthropic" },
      { value: "openrouter", label: "OpenRouter" },
    ]);
  });
});

describe("providerLabel", () => {
  it("names the three providers and shows any other as written", () => {
    expect(providerLabel("openai")).toBe("OpenAI-compatible");
    expect(providerLabel("anthropic")).toBe("Anthropic");
    expect(providerLabel("openrouter")).toBe("OpenRouter");
    expect(providerLabel("someprovider")).toBe("someprovider");
    expect(providerLabel("")).toBe("Unknown provider");
  });
});
