import { describe, expect, it } from "vitest";
import { stubFetch } from "@/__tests__/testing";
import {
  AGENTS_KEY,
  type Agent,
  type AgentList,
  addAgent,
  downAgents,
  listAgents,
} from "@/lib/agents";
import { ApiError } from "@/lib/api";

const HELLO: Agent = {
  id: "env.hello",
  name: "hello",
  source: "environment",
  approval: "none",
  url: null,
  version: null,
  state: "reachable",
  checked_at: "2026-11-19T09:00:00Z",
  reason: null,
  card: {
    name: "Hello World Agent",
    description: "Just a hello world agent",
    version: "1.0.0",
    skills: [],
    cut: false,
  },
  pending_tasks: 0,
  created_at: null,
  updated_at: null,
};

function agent(changes: Partial<Agent>): Agent {
  return { ...HELLO, ...changes };
}

const LIST: AgentList = { agents: [HELLO], limit: 20, unusable: 0 };

describe("listAgents", () => {
  it("asks for the agents and returns the list", async () => {
    const fetchStub = stubFetch({ json: LIST });
    expect(await listAgents()).toEqual(LIST);
    expect(fetchStub.mock.calls[0][0]).toBe("/api/agents");
    expect(AGENTS_KEY).toBe("/agents");
  });

  it("surfaces a refusal with the service's reason", async () => {
    stubFetch({
      status: 500,
      json: {
        detail: {
          code: "internal",
          message: "The saved agents could not be read.",
        },
      },
    });
    const refused = await listAgents().catch((error: unknown) => error);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).code).toBe("internal");
    expect((refused as ApiError).message).toBe(
      "The saved agents could not be read.",
    );
  });

  it("surfaces a service that cannot be reached", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const failed = await listAgents().catch((error: unknown) => error);
    expect(failed).toBeInstanceOf(ApiError);
    expect((failed as ApiError).code).toBe("unreachable");
  });
});

describe("downAgents", () => {
  it("gives the agents that cannot be handed work now", () => {
    const unreachable = agent({ id: "a", name: "a", state: "unreachable" });
    const notInUse = agent({ id: "b", name: "b", state: "not_in_use" });
    const list: AgentList = {
      agents: [
        HELLO,
        unreachable,
        agent({ id: "c", name: "c", state: "not_tried" }),
        notInUse,
      ],
      limit: 20,
      unusable: 2,
    };
    expect(downAgents(list)).toEqual([unreachable, notInUse]);
  });

  it("gives none when every agent is reachable or not tried yet", () => {
    const list: AgentList = {
      agents: [HELLO, agent({ id: "c", name: "c", state: "not_tried" })],
      limit: 20,
      unusable: 0,
    };
    expect(downAgents(list)).toEqual([]);
    expect(downAgents({ agents: [], limit: 20, unusable: 0 })).toEqual([]);
  });
});

describe("addAgent", () => {
  const ADDRESS = "http://localhost:9998/agents/recognizable-stub";
  const STUB = agent({
    id: "7c9e6679f0a54b3e8d2a1f4b6c8e0a2d",
    name: "stub",
    source: "saved",
    approval: "required",
    url: ADDRESS,
    version: 1,
  });

  it("posts the name and the address as JSON and returns the agent", async () => {
    const fetchStub = stubFetch({ status: 201, json: STUB });
    expect(await addAgent({ name: "stub", url: ADDRESS })).toEqual(STUB);

    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe("/api/agents");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    // The two values and nothing else: the service chooses the approval.
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "stub",
      url: ADDRESS,
    });
  });

  it("sends the address in the body, and in no path or query", async () => {
    const fetchStub = stubFetch({ status: 201, json: STUB });
    await addAgent({ name: "stub", url: ADDRESS });

    for (const [path] of fetchStub.mock.calls) {
      const asked = decodeURIComponent(String(path));
      expect(asked).not.toContain("?");
      expect(asked).not.toContain("9998");
      expect(asked).not.toContain("recognizable-stub");
    }
    expect(String(fetchStub.mock.calls[0][1]?.body)).toContain(ADDRESS);
  });

  it("sends nothing but the two values, whatever else it is handed", async () => {
    const fetchStub = stubFetch({ status: 201, json: STUB });
    const more = { name: "stub", url: ADDRESS, approval: "none", id: "x" };
    await addAgent(more);
    expect(JSON.parse(String(fetchStub.mock.calls[0][1]?.body))).toEqual({
      name: "stub",
      url: ADDRESS,
    });
  });

  it("surfaces a refusal with the service's code and sentence", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "agent_unusable",
          reason: "no_answer",
          message: "Nothing answered at the address.",
        },
      },
    });
    const refused = await addAgent({ name: "stub", url: ADDRESS }).catch(
      (error: unknown) => error,
    );
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).code).toBe("agent_unusable");
    expect((refused as ApiError).message).toBe(
      "Nothing answered at the address.",
    );
  });

  it("surfaces a request that got no answer as unreachable", async () => {
    stubFetch({ fails: "Failed to fetch" });
    const failed = await addAgent({ name: "stub", url: ADDRESS }).catch(
      (error: unknown) => error,
    );
    expect((failed as ApiError).code).toBe("unreachable");
  });
});
