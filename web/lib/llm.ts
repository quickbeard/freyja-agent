// LLM profiles as the service describes them (specs/004-web-llm-profiles/contracts/openapi.yaml).
// No type here has a field for an API key: the service never sends one.

import { api } from "./api";

export type LlmTestStep = "first_message" | "conversation";

export type LlmLastTest = {
  ok: boolean;
  at: string;
  /** The step that failed; null when the test passed. */
  step: LlmTestStep | null;
  /** Why it failed; null when the test passed. */
  reason: string | null;
};

export type UnusableReason =
  | "key_unreadable"
  | "secret_not_configured"
  | "not_configured"
  | "cannot_build";

export type LlmProfile = {
  /** 32 hex characters for a saved profile; `environment` for the environment's. */
  id: string;
  name: string;
  source: "saved" | "environment";
  provider: string;
  model: string;
  base_url: string | null;
  /** True: the Responses API. False: chat completions. Null: the provider has no choice. */
  responses_api: boolean | null;
  /** The key's last four characters; empty when there is nothing to show. */
  key_hint: string;
  active: boolean;
  usable: boolean;
  unusable_reason: UnusableReason | null;
  /** Null when not tested since the profile's last change. */
  last_test: LlmLastTest | null;
  created_at: string | null;
  updated_at: string | null;
};

export type LlmProfileList = {
  profiles: LlmProfile[];
  /** The id of the one active profile; `environment` when no saved one is. */
  active_id: string;
  /** False when the active profile is not usable: runs then end with an error. */
  usable_model: boolean;
  /** False when profiles cannot be saved because the service has no secret. */
  secret_configured: boolean;
  limit: number;
};

/** The path of the profile list, and the key its copy in the browser is kept under. */
export const PROFILES_PATH = "/llm/profiles";

export function listProfiles(): Promise<LlmProfileList> {
  return api<LlmProfileList>(PROFILES_PATH);
}

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI-compatible",
  anthropic: "Anthropic",
  openrouter: "OpenRouter",
};

/** What the console calls a provider. One the console does not know is shown as written. */
export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? (provider || "Unknown provider");
}
