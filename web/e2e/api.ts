// What the Freyja service would answer, for the end-to-end tests. A test hands a page the
// answers to its requests for the profile list and for the agents, so no test needs the
// service itself.

import type { Page, Route } from "@playwright/test";
import type { Agent, AgentCard, AgentCheck, AgentList } from "../lib/agents";
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

/** An agent named in the service's environment: no address, and nothing to change it by. */
export function environmentAgent(
  name: string,
  values: Partial<Agent> = {},
): Agent {
  return {
    id: `env.${name}`,
    name,
    source: "environment",
    approval: "required",
    url: null,
    version: null,
    state: "reachable",
    checked_at: "2026-11-19T09:00:00Z",
    reason: null,
    card: {
      name: `${name} agent`,
      description: `What the ${name} agent says it does.`,
      version: "1.0.0",
      skills: [
        {
          id: "report",
          name: "Report",
          description: "Writes a short report.",
          tags: ["writing"],
        },
      ],
      cut: false,
    },
    pending_tasks: 0,
    created_at: null,
    updated_at: null,
    ...values,
  };
}

/** An agent an operator saved, with its address. */
export function savedAgent(name: string, values: Partial<Agent> = {}): Agent {
  return {
    ...environmentAgent(name),
    id: "0123456789abcdef0123456789abcdef",
    source: "saved",
    url: "http://127.0.0.1:9998",
    version: 1,
    created_at: "2026-11-19T08:50:00Z",
    updated_at: "2026-11-19T08:50:00Z",
    ...values,
  };
}

/** The list as the service sends it: how many agents are down follows from the agents. */
export function agentList(...agents: Agent[]): AgentList {
  return {
    agents,
    limit: 20,
    unusable: agents.filter(
      (agent) => agent.state === "unreachable" || agent.state === "not_in_use",
    ).length,
  };
}

export type AgentsAnswer =
  | { list: AgentList }
  | { refusal: { status: number; code: string; message: string } };

/**
 * Answer this page's requests for the list of agents, in the browser, before they leave it.
 * Each request takes the next answer, and the last one is given to every request after it.
 * A page that is given no answer asks the web app's server, as a deployed console does.
 */
export async function answerAgents(
  page: Page,
  ...answers: AgentsAnswer[]
): Promise<void> {
  let asked = 0;
  await page.route("**/api/agents", async (route) => {
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
/** A reason as long as the service keeps one: 500 characters. */
export const LONG_REASON =
  "Error code: 400 - the endpoint refused the request. "
    .repeat(10)
    .slice(0, 500);

/**
 * How a test of a model goes, by its id, as `tests/llm_stub.py` behaves: `refuse-conversation`
 * fails at the second step, `reject-key` at the first, and any other id passes. `long-reason`
 * is the stand-in's own: it fails with a reason of the greatest length the service keeps.
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
  if (model === "long-reason") {
    return {
      ok: false,
      at: TESTED_AT,
      step: "conversation",
      reason: LONG_REASON,
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
      // A key sent again is stored under the secret the service has now, so it can be read.
      ...(body.api_key === undefined
        ? {}
        : { usable: true, unusable_reason: null }),
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

  /** What another operator does meanwhile: delete a profile this page still shows. */
  deleteElsewhere(id: string) {
    this.profiles = this.profiles.filter((profile) => profile.id !== id);
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

/**
 * What the stand-in for the Agents routes says when it refuses: the service's own sentences
 * (`src/freyja_agent/api/agents.py`, `delegates/agent_registry.py`, `agent_cards.py`,
 * `config.py`).
 */
export const NOTHING_ANSWERED = "Nothing answered at the address.";
export const SAVED_NAME_TAKEN = "Another saved agent has this name.";
export const ENVIRONMENT_NAME_TAKEN =
  "An agent named in the environment has this name.";
export const AGENT_ADDRESS_TAKEN = "Another saved agent is at this address.";
export const NOT_THIS_MACHINE =
  "The address must be on this machine: localhost or a loopback IP address.";
export const NO_SUCH_AGENT = "There is no agent with this id.";
export const NOT_IN_USE_NOT_CHECKED =
  "This saved agent is not in use, so it cannot be checked.";
export const ALREADY_CHECKED =
  "This agent is already being checked. Wait for that check to end.";
export const AGENT_CHANGED_SINCE =
  "The agent was changed since this page read it. Nothing was changed.";
export const ENVIRONMENT_AGENT_READ_ONLY =
  "An agent named in the environment cannot be changed here.";
export const NOT_IN_USE_READ_ONLY =
  "This saved agent is not in use, so it can only be removed.";

const THIS_MACHINE = /^(localhost|127(\.\d{1,3}){3}|\[::1\])$/;

/**
 * An address in the one form the service keeps it in: scheme and host in lowercase, the port
 * always written, and no trailing slash (005 FR-006). Null for what is no web address.
 */
export function keptAddress(entered: string): string | null {
  let url: URL;
  try {
    url = new URL(entered);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return null;
  }
  const port = url.port || (url.protocol === "https:" ? "443" : "80");
  const path = url.pathname.replace(/\/$/, "");
  return `${url.protocol}//${url.hostname}:${port}${path}`;
}

/** Where an address leads: its port and path, however this machine is written. */
function destination(kept: string): string {
  const url = new URL(kept);
  return `${url.port}${url.pathname.replace(/\/$/, "")}`;
}

/**
 * A stand-in for the service's Agents routes that lives in the test, for pages that change
 * agents: it keeps what a page saved, records what it was asked, and answers as the service
 * would (specs/005-web-delegate-agents/contracts/openapi.yaml). It checks only what a test
 * needs it to refuse; the service's own rules are tested with the service.
 */
export class AgentService {
  agents: Agent[];
  /** Every request the page made to the Agents routes, in order. */
  readonly asked: Asked[] = [];
  /** The ports at which no agent answers: adding one there is refused. */
  private readonly silent = new Set<string>();
  /** The agents, by name, whose card cannot be read now: a check finds them unusable. */
  private readonly down = new Set<string>();
  /** The agents, by name, that are being checked by someone else. */
  private readonly busy = new Set<string>();
  /** What an agent's card says from now on, by name, until a check has read it. */
  private readonly rewritten = new Map<string, Partial<AgentCard>>();
  /** The checks that wait for the test to let them be answered. */
  private waiting: (() => void)[] = [];
  private holding = false;
  private checks = 0;
  private changed = 0;
  private saved = 0;
  private losing = 0;
  private stopped = false;

  constructor(...agents: Agent[]) {
    this.agents = agents;
  }

  /** The requests that changed something. */
  get changes(): Asked[] {
    return this.asked.filter(({ method }) => method !== "GET");
  }

  /** How often the page read the list. */
  get reads(): number {
    return this.asked.filter(({ method }) => method === "GET").length;
  }

  /** The list as the service orders it: the environment's agents, then the saved by name. */
  list(): AgentList {
    const environment = this.agents.filter((a) => a.source === "environment");
    const saved = this.agents
      .filter((a) => a.source === "saved")
      .sort((a, b) => a.name.localeCompare(b.name));
    return agentList(...environment, ...saved);
  }

  /** Whether an agent answers at this port: none does where a test said so. */
  answersAt(port: number, answers: boolean): this {
    if (answers) {
      this.silent.delete(String(port));
    } else {
      this.silent.add(String(port));
    }
    return this;
  }

  /** Whether the agent of this name answers when its card is read again. */
  answers(name: string, answers: boolean): this {
    if (answers) {
      this.down.delete(name);
    } else {
      this.down.add(name);
    }
    return this;
  }

  /** The agent's card says something else from now on: the next check finds other details. */
  rewritesCard(name: string, card: Partial<AgentCard>): this {
    this.rewritten.set(name, card);
    return this;
  }

  /** Someone else is checking the agent of this name, or has stopped. */
  checkedElsewhere(name: string, checking: boolean): this {
    if (checking) {
      this.busy.add(name);
    } else {
      this.busy.delete(name);
    }
    return this;
  }

  /** From now on a check is not answered until `answerChecks` is called. */
  holdChecks(): this {
    this.holding = true;
    return this;
  }

  /** Let every check that waits be answered, and answer later ones at once. */
  answerChecks(): this {
    this.holding = false;
    for (const answer of this.waiting) {
      answer();
    }
    this.waiting = [];
    return this;
  }

  /** What another operator does meanwhile: remove an agent this page still shows. */
  removeElsewhere(name: string): this {
    this.agents = this.agents.filter((agent) => agent.name !== name);
    return this;
  }

  /**
   * What another operator does meanwhile: change an agent this page still shows, which gives
   * it a later version.
   */
  changeElsewhere(name: string, values: Partial<Agent>): this {
    this.agents = this.agents.map((agent) =>
      agent.name === name
        ? { ...agent, ...values, version: (agent.version ?? 0) + 1 }
        : agent,
    );
    return this;
  }

  /** What happens elsewhere: a run, or another console, finds the agent as it is now. */
  found(name: string, values: Partial<Agent>): this {
    this.agents = this.agents.map((agent) =>
      agent.name === name ? { ...agent, ...values } : agent,
    );
    return this;
  }

  /**
   * The next request that changes something is carried out, and its answer is lost on the
   * way back: the page gets none.
   */
  loseNextAnswer(): this {
    this.losing += 1;
    return this;
  }

  /**
   * The service stops: from now on every request goes to the web app's server, which finds
   * nothing at the service's address, as in a deployed console.
   */
  stop(): this {
    this.stopped = true;
    return this;
  }

  private refuse(
    route: Route,
    code: string,
    message: string,
    more: Record<string, string> = {},
  ) {
    return route.fulfill({
      status: 409,
      json: { detail: { code, message, ...more } },
    });
  }

  private add(route: Route, body: Record<string, unknown>) {
    const name = String(body.name);
    const url = keptAddress(String(body.url));
    if (url === null || !THIS_MACHINE.test(new URL(url).hostname)) {
      // A refused value is named by its field and never repeated.
      return route.fulfill({
        status: 422,
        json: {
          detail: [
            {
              type: "agent_address",
              loc: ["body", "url"],
              msg: NOT_THIS_MACHINE,
            },
          ],
        },
      });
    }
    const named = this.agents.find((agent) => agent.name === name);
    if (named !== undefined) {
      return this.refuse(
        route,
        "name_taken",
        named.source === "environment"
          ? ENVIRONMENT_NAME_TAKEN
          : SAVED_NAME_TAKEN,
      );
    }
    const taken = this.agents.some(
      (agent) =>
        agent.url !== null && destination(agent.url) === destination(url),
    );
    if (taken) {
      return this.refuse(route, "address_taken", AGENT_ADDRESS_TAKEN);
    }
    // The card is read before anything is saved.
    if (this.silent.has(new URL(url).port)) {
      return this.refuse(route, "agent_unusable", NOTHING_ANSWERED, {
        reason: "no_answer",
      });
    }
    this.saved += 1;
    const agent = savedAgent(name, {
      id: this.saved.toString(16).padStart(32, "0"),
      url,
      // An agent requires approval unless the request says otherwise.
      approval: body.approval === "none" ? "none" : "required",
    });
    this.agents.push(agent);
    if (this.losing > 0) {
      this.losing -= 1;
      return route.abort("connectionfailed");
    }
    return route.fulfill({ status: 201, json: agent });
  }

  /**
   * Change a saved agent's approval choice, as the service does: at the version the page
   * read, and for a saved agent that is in use only. No agent is contacted for it.
   */
  private change(route: Route, id: string, body: Record<string, unknown>) {
    const agent = this.agents.find((found) => found.id === id);
    if (agent === undefined) {
      return route.fulfill({
        status: 404,
        json: { detail: { code: "agent_not_found", message: NO_SUCH_AGENT } },
      });
    }
    if (agent.source === "environment") {
      return this.refuse(route, "agent_read_only", ENVIRONMENT_AGENT_READ_ONLY);
    }
    if (agent.state === "not_in_use") {
      return this.refuse(route, "agent_read_only", NOT_IN_USE_READ_ONLY);
    }
    if (body.version !== agent.version) {
      return this.refuse(route, "agent_changed", AGENT_CHANGED_SINCE);
    }
    let changed = agent;
    if (body.approval !== undefined && body.approval !== agent.approval) {
      this.changed += 1;
      this.found(agent.name, {
        approval: body.approval === "none" ? "none" : "required",
        version: (agent.version ?? 0) + 1,
        // Each change a minute after the one before it, from eleven o'clock.
        updated_at: new Date(
          Date.UTC(2026, 10, 19, 11, this.changed),
        ).toISOString(),
      });
      changed = this.agents.find((found) => found.id === id) ?? agent;
    }
    if (this.losing > 0) {
      this.losing -= 1;
      return route.abort("connectionfailed");
    }
    return route.fulfill({ json: { agent: changed, given_up: 0 } });
  }

  /**
   * Read an agent's card again, as the service does: the agent's state, its time, and its
   * card's details follow what was found, and nothing else of the agent changes.
   */
  private async check(route: Route, id: string) {
    const agent = this.agents.find((found) => found.id === id);
    if (agent === undefined) {
      return route.fulfill({
        status: 404,
        json: { detail: { code: "agent_not_found", message: NO_SUCH_AGENT } },
      });
    }
    if (agent.state === "not_in_use") {
      return this.refuse(route, "agent_read_only", NOT_IN_USE_NOT_CHECKED);
    }
    if (this.busy.has(agent.name)) {
      return this.refuse(route, "check_busy", ALREADY_CHECKED);
    }
    if (this.holding) {
      await new Promise<void>((resolve) => this.waiting.push(resolve));
    }
    this.checks += 1;
    // Each check a minute after the one before it, from ten o'clock.
    const checked_at = new Date(
      Date.UTC(2026, 10, 19, 10, this.checks),
    ).toISOString();
    let result: AgentCheck;
    if (this.down.has(agent.name)) {
      this.found(agent.name, {
        state: "unreachable",
        reason: NOTHING_ANSWERED,
        checked_at,
      });
      result = {
        usable: false,
        reason: "no_answer",
        message: NOTHING_ANSWERED,
        checked_at,
        recorded: true,
        card_changed: false,
      };
    } else {
      const rewritten = this.rewritten.get(agent.name);
      this.rewritten.delete(agent.name);
      // Details are replaced by different ones only where some were kept or shown before.
      const changed = rewritten !== undefined && agent.card !== null;
      this.found(agent.name, {
        state: "reachable",
        reason: null,
        checked_at,
        card:
          rewritten === undefined || agent.card === null
            ? agent.card
            : { ...agent.card, ...rewritten },
      });
      result = {
        usable: true,
        reason: null,
        message: null,
        checked_at,
        recorded: true,
        card_changed: changed,
      };
    }
    return route.fulfill({ json: result });
  }

  /** Answer this page's requests to the Agents routes, in the browser. */
  async serve(page: Page): Promise<this> {
    await page.route(
      ({ pathname }) =>
        pathname === "/api/agents" || pathname.startsWith("/api/agents/"),
      async (route) => {
        const request = route.request();
        const method = request.method();
        const path = new URL(request.url()).pathname.replace(/^\/api/, "");
        const body = request.postData() ? request.postDataJSON() : undefined;
        this.asked.push({ method, path, body });
        if (this.stopped) {
          return route.continue();
        }
        if (method === "GET" && path === "/agents") {
          return route.fulfill({ json: this.list() });
        }
        if (method === "POST" && path === "/agents") {
          return this.add(route, body as Record<string, unknown>);
        }
        const checked = path.match(/^\/agents\/([^/]+)\/check$/);
        if (method === "POST" && checked) {
          return this.check(route, decodeURIComponent(checked[1]));
        }
        const one = path.match(/^\/agents\/([^/]+)$/);
        if (method === "PATCH" && one) {
          return this.change(
            route,
            decodeURIComponent(one[1]),
            body as Record<string, unknown>,
          );
        }
        return route.fulfill({ status: 404, json: { detail: "Not Found" } });
      },
    );
    return this;
  }
}

/** The card of the profile, or of the agent, with this name. */
export function card(page: Page, name: string) {
  return page
    .locator('[data-slot="card"]')
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
}

/** The sidebar's entry for a section, by the section's name. */
export function section(page: Page, name: string) {
  return page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name, exact: true });
}

/** The number on the sidebar's Agents entry, as the eye sees it; absent with no warning. */
export function agentsBadge(page: Page) {
  return page
    .getByRole("navigation", { name: "Sections" })
    .locator('[data-slot="sidebar-menu-badge"] [aria-hidden="true"]');
}

/** The warning above the cards of the Agents section. */
export function agentsWarning(page: Page) {
  return page.getByRole("note", { name: "Agents that cannot be used" });
}

/** Every card on the page: profiles in the LLM section, agents in the Agents section. */
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
