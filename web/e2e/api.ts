// What the Freyja service would answer, for the end-to-end tests. A test hands a page the
// answers to its requests for the profile list, so no test needs the service itself.

import type { Page, Route } from "@playwright/test";
import type { LlmLastTest, LlmProfile, LlmProfileList } from "../lib/llm";

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

/** When the stand-in says a test ended. */
export const TESTED_AT = "2026-11-18T09:14:02.118000+00:00";
/** What the stand-in's endpoints say when they refuse. */
export const REFUSES_EARLIER_TURNS =
  "Error code: 422 - Input should be a valid string: this endpoint takes no earlier turns as input";
export const REJECTS_KEY = "Error code: 401 - Incorrect API key provided.";

/**
 * How a test of a model goes, by its id, as `tests/llm_stub.py` behaves: `refuse-conversation`
 * fails at the second step, `reject-key` at the first, and any other id passes.
 */
export function testOf(model: string): LlmLastTest {
  if (model === "refuse-conversation") {
    return {
      ok: false,
      at: TESTED_AT,
      step: "conversation",
      reason: REFUSES_EARLIER_TURNS,
    };
  }
  if (model === "reject-key") {
    return {
      ok: false,
      at: TESTED_AT,
      step: "first_message",
      reason: REJECTS_KEY,
    };
  }
  return { ok: true, at: TESTED_AT, step: null, reason: null };
}

/** A request a page made to the stand-in. */
export type Asked = { method: string; path: string; body: unknown };

/**
 * A stand-in for the service's profile routes that lives in the test, for pages that change
 * profiles: it keeps what a page saved and activated, and answers as the service would
 * (specs/004-web-llm-profiles/contracts/openapi.yaml). It checks only what a test needs it to
 * refuse; the service's own rules are tested with the service.
 */
export class ProfileService {
  profiles: LlmProfile[];
  /** Every request the page made, in order. */
  readonly asked: Asked[] = [];
  private saved = 0;
  private edits = 0;

  constructor(...profiles: LlmProfile[]) {
    this.profiles = profiles.length > 0 ? profiles : [environmentProfile()];
  }

  /** The requests that changed something. */
  get changes(): Asked[] {
    return this.asked.filter(({ method }) => method !== "GET");
  }

  /** The list as the service orders it: the environment's, then the saved ones by name. */
  list(): LlmProfileList {
    const [environment] = this.profiles.filter(
      (p) => p.source === "environment",
    );
    const saved = this.profiles
      .filter((p) => p.source === "saved")
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    return profileList(environment, ...saved);
  }

  private refuse(route: Route, status: number, code: string, message: string) {
    return route.fulfill({ status, json: { detail: { code, message } } });
  }

  private add(route: Route, body: Record<string, unknown>) {
    const name = String(body.name).trim();
    const taken = this.profiles.some(
      (profile) => profile.name.toLowerCase() === name.toLowerCase(),
    );
    if (taken) {
      return this.refuse(
        route,
        409,
        "name_taken",
        "Another profile has this name. Choose a different name.",
      );
    }
    this.saved += 1;
    const key = String(body.api_key);
    const profile = savedProfile(name, {
      id: this.saved.toString(16).padStart(32, "0"),
      provider: String(body.provider),
      model: String(body.model).trim(),
      base_url: (body.base_url as string | null) ?? null,
      // Reported for a provider that has the choice, as the service does.
      responses_api:
        body.provider === "openai"
          ? (body.responses_api ?? true) === true
          : null,
      // The service keeps the key and shows its last four characters.
      key_hint: key.length >= 8 ? key.slice(-4) : "",
    });
    this.profiles.push(profile);
    return route.fulfill({ status: 201, json: profile });
  }

  private activate(route: Route, id: string) {
    const target = this.profiles.find((profile) => profile.id === id);
    if (target === undefined) {
      return this.refuse(
        route,
        404,
        "profile_not_found",
        "There is no profile with this id.",
      );
    }
    if (!target.usable) {
      return this.refuse(
        route,
        409,
        "profile_unusable",
        `Profile ${target.name} cannot be used. The profile that was active still is.`,
      );
    }
    this.profiles = this.profiles.map((profile) => ({
      ...profile,
      active: profile.id === id,
    }));
    return route.fulfill({ json: this.list() });
  }

  /** Change a saved profile: only the fields sent, as the service does. */
  private update(route: Route, id: string, body: Record<string, unknown>) {
    const target = this.profiles.find((profile) => profile.id === id);
    if (target === undefined) {
      return this.refuse(
        route,
        404,
        "profile_not_found",
        "There is no profile with this id.",
      );
    }
    if (target.source === "environment") {
      return this.refuse(
        route,
        409,
        "profile_read_only",
        "The environment's profile cannot be changed or deleted here.",
      );
    }
    const name =
      body.name === undefined ? target.name : String(body.name).trim();
    const taken = this.profiles.some(
      (profile) =>
        profile.id !== id && profile.name.toLowerCase() === name.toLowerCase(),
    );
    if (taken) {
      return this.refuse(
        route,
        409,
        "name_taken",
        "Another profile has this name. Choose a different name.",
      );
    }
    const provider =
      body.provider === undefined ? target.provider : String(body.provider);
    const changed: LlmProfile = {
      ...target,
      name,
      provider,
      model:
        body.model === undefined ? target.model : String(body.model).trim(),
      base_url:
        body.base_url === undefined
          ? target.base_url
          : (body.base_url as string | null),
      responses_api:
        provider !== "openai"
          ? null
          : body.responses_api === undefined
            ? (target.responses_api ?? true)
            : body.responses_api === true,
      // A key left out keeps the stored one, and so its last four characters.
      key_hint:
        body.api_key === undefined
          ? target.key_hint
          : String(body.api_key).slice(-4),
    };
    // A change to how the model is reached clears the last test; a new name keeps it.
    const reach =
      body.api_key !== undefined ||
      changed.provider !== target.provider ||
      changed.model !== target.model ||
      changed.base_url !== target.base_url ||
      changed.responses_api !== target.responses_api;
    this.edits += 1;
    const updated: LlmProfile = {
      ...changed,
      last_test: reach ? null : target.last_test,
      updated_at: `2026-11-18T11:00:${String(this.edits).padStart(2, "0")}.000000+00:00`,
    };
    this.profiles = this.profiles.map((profile) =>
      profile.id === id ? updated : profile,
    );
    return route.fulfill({ json: updated });
  }

  /** Delete a saved profile that is not the active one. */
  private remove(route: Route, id: string) {
    const target = this.profiles.find((profile) => profile.id === id);
    if (target === undefined) {
      return this.refuse(
        route,
        404,
        "profile_not_found",
        "There is no profile with this id.",
      );
    }
    if (target.source === "environment") {
      return this.refuse(
        route,
        409,
        "profile_read_only",
        "The environment's profile cannot be changed or deleted here.",
      );
    }
    if (target.active) {
      return this.refuse(
        route,
        409,
        "profile_active",
        `Profile ${target.name} is the active one and cannot be deleted. Activate another profile first.`,
      );
    }
    this.profiles = this.profiles.filter((profile) => profile.id !== id);
    return route.fulfill({ status: 204 });
  }

  /** Test a listed profile and keep the result with it, as the service does. */
  private test(route: Route, id: string) {
    const target = this.profiles.find((profile) => profile.id === id);
    if (target === undefined) {
      return this.refuse(
        route,
        404,
        "profile_not_found",
        "There is no profile with this id.",
      );
    }
    const result = testOf(target.model);
    this.profiles = this.profiles.map((profile) =>
      profile.id === id ? { ...profile, last_test: result } : profile,
    );
    return route.fulfill({ json: { ...result, recorded: true } });
  }

  /** What another operator does meanwhile: change a profile, which clears its test. */
  change(id: string, values: Partial<LlmProfile>) {
    this.profiles = this.profiles.map((profile) =>
      profile.id === id
        ? {
            ...profile,
            ...values,
            last_test: null,
            updated_at: "2026-11-18T10:00:00.000000+00:00",
          }
        : profile,
    );
  }

  /** Answer this page's requests to the profile routes, in the browser. */
  async serve(page: Page): Promise<this> {
    await page.route("**/api/llm/**", async (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname.replace(/^\/api/, "");
      const body = request.postData() ? request.postDataJSON() : undefined;
      this.asked.push({ method, path, body });

      const activation = path.match(/^\/llm\/profiles\/([^/]+)\/activate$/);
      if (method === "GET" && path === "/llm/profiles") {
        return route.fulfill({ json: this.list() });
      }
      if (method === "POST" && path === "/llm/profiles") {
        return this.add(route, body as Record<string, unknown>);
      }
      if (method === "POST" && activation) {
        return this.activate(route, decodeURIComponent(activation[1]));
      }
      const one = path.match(/^\/llm\/profiles\/([^/]+)$/);
      if (method === "PATCH" && one) {
        return this.update(
          route,
          decodeURIComponent(one[1]),
          body as Record<string, unknown>,
        );
      }
      if (method === "DELETE" && one) {
        return this.remove(route, decodeURIComponent(one[1]));
      }
      const test = path.match(/^\/llm\/profiles\/([^/]+)\/test$/);
      if (method === "POST" && test) {
        return this.test(route, decodeURIComponent(test[1]));
      }
      if (method === "POST" && path === "/llm/test") {
        // Values that are not saved: tested, and nothing is kept.
        const { model } = body as { model: string };
        return route.fulfill({ json: { ...testOf(model), recorded: false } });
      }
      return route.fulfill({ status: 404, json: { detail: "Not Found" } });
    });
    return this;
  }
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
