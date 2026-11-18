import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfileCard } from "@/components/llm/profile-card";
import type { LlmProfile } from "@/lib/llm";

const SAVED: LlmProfile = {
  id: "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b",
  name: "Local vLLM",
  source: "saved",
  provider: "openai",
  model: "served-model",
  base_url: "http://10.0.0.5:31000/v1",
  responses_api: false,
  key_hint: "a1b2",
  active: false,
  usable: true,
  unusable_reason: null,
  last_test: null,
  created_at: "2026-11-17T09:10:44.501000+00:00",
  updated_at: "2026-11-17T09:13:20.009000+00:00",
};

const ENVIRONMENT: LlmProfile = {
  ...SAVED,
  id: "environment",
  name: "Environment",
  source: "environment",
  provider: "anthropic",
  model: "claude-sonnet-5-5",
  base_url: null,
  responses_api: null,
  key_hint: "9f2c",
  active: true,
  created_at: null,
  updated_at: null,
};

describe("ProfileCard", () => {
  it("shows what the profile is and how its model is reached", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.getByRole("heading", { name: "Local vLLM" })).toBeTruthy();
    expect(screen.getByText("OpenAI-compatible")).toBeTruthy();
    expect(screen.getByText("served-model")).toBeTruthy();
    expect(screen.getByText("http://10.0.0.5:31000/v1")).toBeTruthy();
    expect(screen.getByText("Chat completions")).toBeTruthy();
  });

  it("shows the last four characters of the key and nothing more", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.getByText(/a1b2$/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/sk-/);
  });

  it("names the Responses API when that is the choice", () => {
    render(<ProfileCard profile={{ ...SAVED, responses_api: true }} />);
    expect(screen.getByText("Responses API")).toBeTruthy();
    expect(screen.queryByText("Chat completions")).toBeNull();
  });

  it("shows no choice of API and no address when the profile has none", () => {
    render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.queryByText("Responses API")).toBeNull();
    expect(screen.queryByText("Chat completions")).toBeNull();
    expect(screen.queryByText("Base URL")).toBeNull();
  });

  it("marks the active profile and the one from the environment", () => {
    const { unmount } = render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("From the environment")).toBeTruthy();
    unmount();
    render(<ProfileCard profile={SAVED} />);
    expect(screen.queryByText("Active")).toBeNull();
    expect(screen.queryByText("From the environment")).toBeNull();
  });

  it("shows no character of a key that has no hint", () => {
    render(<ProfileCard profile={{ ...SAVED, key_hint: "" }} />);
    expect(screen.getByText("Not shown")).toBeTruthy();
  });

  it.each([
    ["key_unreadable", /cannot be read with the service's current secret/],
    ["secret_not_configured", /no secret is set on the service/i],
    ["not_configured", /names no key for this provider/],
    ["cannot_build", /no model can be built/i],
  ] as const)("says why a profile is not usable: %s", (reason, text) => {
    render(
      <ProfileCard
        profile={{ ...SAVED, usable: false, unusable_reason: reason }}
      />,
    );
    expect(screen.getByText("Not usable")).toBeTruthy();
    expect(screen.getByText(text)).toBeTruthy();
  });

  it("gives the environment's profile no way to be edited or deleted", () => {
    render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });
});
