// What the Freyja service would answer, for the end-to-end tests. A test hands a page the
// answers to its requests for the profile list, so no test needs the service itself.

import type { Page } from "@playwright/test";
import type { LlmProfile, LlmProfileList } from "../lib/llm";

/** The environment's profile, as a service configured only through its environment lists it. */
export function environmentProfile(
  values: Partial<LlmProfile> = {},
): LlmProfile {
  return {
    id: "environment",
    name: "Environment",
    source: "environment",
    provider: "openai",
    model: "env-model",
    base_url: "http://10.0.0.5:31000/v1",
    responses_api: false,
    key_hint: "9f2c",
    active: true,
    usable: true,
    unusable_reason: null,
    last_test: null,
    created_at: null,
    updated_at: null,
    ...values,
  };
}

/** A profile an operator saved. */
export function savedProfile(
  name: string,
  values: Partial<LlmProfile> = {},
): LlmProfile {
  return {
    id: "0123456789abcdef0123456789abcdef",
    name,
    source: "saved",
    provider: "anthropic",
    model: "claude-sonnet-5-5",
    base_url: null,
    responses_api: null,
    key_hint: "0001",
    active: false,
    usable: true,
    unusable_reason: null,
    last_test: null,
    created_at: "2026-11-17T08:00:00Z",
    updated_at: "2026-11-17T08:00:00Z",
    ...values,
  };
}

/** The list as the service sends it: which profile is active follows from the profiles. */
export function profileList(...profiles: LlmProfile[]): LlmProfileList {
  const active = profiles.find((profile) => profile.active);
  return {
    profiles,
    active_id: active?.id ?? "environment",
    usable_model: active?.usable ?? false,
    secret_configured: true,
    limit: 50,
  };
}

export type Answer =
  | { list: LlmProfileList }
  | { refusal: { status: number; code: string; message: string } };

/**
 * Answer this page's requests for the profile list, in the browser, before they leave it.
 * Each request takes the next answer, and the last one is given to every request after it.
 * A page that is given no answer asks the web app's server, as a deployed console does.
 */
export async function answerProfiles(
  page: Page,
  ...answers: Answer[]
): Promise<void> {
  let asked = 0;
  await page.route("**/api/llm/profiles", async (route) => {
    const answer = answers[Math.min(asked, answers.length - 1)];
    asked += 1;
    if ("list" in answer) {
      await route.fulfill({ json: answer.list });
    } else {
      const { status, code, message } = answer.refusal;
      await route.fulfill({ status, json: { detail: { code, message } } });
    }
  });
}

/** The card of the profile with this name. */
export function card(page: Page, name: string) {
  return page
    .locator('[data-slot="card"]')
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
}

/** Every profile card on the page. */
export function cards(page: Page) {
  return page.locator('[data-slot="card"]');
}

/**
 * What the page itself announces as an alert. Next.js keeps an alert element of its own
 * outside the page, to announce navigations, which is not one of these.
 */
export function alerts(page: Page) {
  return page.getByRole("main").getByRole("alert");
}
