import { describe, expect, it } from "vitest";
import { type LlmProfileList, listProfiles, providerLabel } from "./llm";
import { stubFetch } from "./testing";

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

describe("providerLabel", () => {
  it("names the three providers and shows any other as written", () => {
    expect(providerLabel("openai")).toBe("OpenAI-compatible");
    expect(providerLabel("anthropic")).toBe("Anthropic");
    expect(providerLabel("openrouter")).toBe("OpenRouter");
    expect(providerLabel("someprovider")).toBe("someprovider");
    expect(providerLabel("")).toBe("Unknown provider");
  });
});
