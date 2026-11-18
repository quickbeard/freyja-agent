import { describe, expect, it } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import {
  activateProfile,
  createProfile,
  deleteProfile,
  type LlmDraft,
  type LlmLastTest,
  type LlmProfile,
  type LlmProfileList,
  type LlmTestResult,
  listProfiles,
  type NewLlmProfile,
  PROVIDERS,
  providerLabel,
  testDraft,
  testProfile,
  toDraft,
  updateProfile,
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

const PASSED = {
  ok: true,
  at: "2026-11-17T09:14:02.118000+00:00",
  step: null,
  reason: null,
  recorded: true,
};

describe("testProfile", () => {
  it("posts to the profile's test and returns the result", async () => {
    const fetchStub = stubFetch({ json: PASSED });
    expect(await testProfile(SAVED.id)).toEqual(PASSED);

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}/test`);
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });
});

describe("testDraft", () => {
  it("posts the values to be tested without saving them", async () => {
    const fetchStub = stubFetch({ json: { ...PASSED, recorded: false } });
    const draft = {
      provider: "openai" as const,
      model: "served-model",
      base_url: "http://10.0.0.5:31000/v1",
      api_key: "sk-recognizable-key-0001",
      responses_api: false,
    };
    expect((await testDraft(draft)).recorded).toBe(false);

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe("/api/llm/test");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(draft);
  });
});

describe("toDraft", () => {
  const typed = {
    provider: "openai" as const,
    model: " served-model ",
    baseUrl: " http://10.0.0.5:31000/v1 ",
    apiKey: "sk-recognizable-key-0001",
    responsesApi: false,
  };

  it("is the form's values, trimmed, with the key as typed", () => {
    expect(toDraft(typed)).toEqual({
      provider: "openai",
      model: "served-model",
      base_url: "http://10.0.0.5:31000/v1",
      api_key: "sk-recognizable-key-0001",
      responses_api: false,
    });
  });

  it("has no base URL when none was typed, and no choice of API where there is none", () => {
    expect(toDraft({ ...typed, provider: "anthropic", baseUrl: "  " })).toEqual(
      {
        provider: "anthropic",
        model: "served-model",
        base_url: null,
        api_key: "sk-recognizable-key-0001",
      },
    );
  });

  it("names the profile whose stored key to use when the key is left empty", () => {
    // The Edit form: an empty key means the key the profile already has (FR-017).
    const draft = toDraft({ ...typed, apiKey: "" }, SAVED.id);
    expect(draft.profile_id).toBe(SAVED.id);
    expect("api_key" in draft).toBe(false);
  });

  it("sends a typed key and not the profile's when both are there", () => {
    const draft = toDraft(typed, SAVED.id);
    expect(draft.api_key).toBe("sk-recognizable-key-0001");
    expect("profile_id" in draft).toBe(false);
  });
});

describe("updateProfile", () => {
  it("patches the profile with the fields given and returns it as it is now", async () => {
    const renamed = { ...SAVED, name: "Renamed" };
    const fetchStub = stubFetch({ json: renamed });
    const changes = { name: "Renamed", base_url: null };
    expect(await updateProfile(SAVED.id, changes)).toEqual(renamed);

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}`);
    expect(init?.method).toBe("PATCH");
    // Only what was given: a key that is not there is not sent, so the stored one is kept.
    expect(JSON.parse(String(init?.body))).toEqual(changes);
  });
});

describe("deleteProfile", () => {
  it("deletes the profile and returns nothing", async () => {
    const fetchStub = stubFetch({ status: 204, text: "" });
    expect(await deleteProfile(SAVED.id)).toBeUndefined();

    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}`);
    expect(init?.method).toBe("DELETE");
    expect(init?.body).toBeUndefined();
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

describe("what the web app receives", () => {
  // Checked when the app is built: a field for a key in anything the service sends would not
  // compile here. The key goes one way, to the service (FR-020).
  type Received =
    | keyof LlmProfile
    | keyof LlmProfileList
    | keyof LlmLastTest
    | keyof LlmTestResult;
  type KeyFields = Extract<Received, `${string}key${string}`>;
  const onlyTheHint: KeyFields extends "key_hint" ? true : false = true;
  // And it is sent in exactly these two shapes.
  const sent: [keyof NewLlmProfile, keyof LlmDraft] = ["api_key", "api_key"];

  it("has no field for a key, only for its last four characters", () => {
    expect(onlyTheHint).toBe(true);
    expect(sent).toEqual(["api_key", "api_key"]);
    expect(Object.keys(SAVED).filter((name) => name.includes("key"))).toEqual([
      "key_hint",
    ]);
  });
});
