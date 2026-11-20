// Agents as the service describes them (specs/005-web-delegate-agents/contracts/openapi.yaml).
// A saved agent's address is in `url`; an environment agent's is in no field of any type here.
// An address travels in a request's body and in no path or query.

import { api } from "./api";

export type AgentSkill = {
  id: string;
  name: string;
  description: string;
  tags: string[];
};

/** What an agent says of itself: its own claim, never what decides approval. */
export type AgentCard = {
  name: string;
  description: string;
  version: string;
  skills: AgentSkill[];
  /** True when the service cut a text, the skills, or a skill's tags to its limits. */
  cut: boolean;
};

/**
 * The answering service instance's most recent attempt to use the agent: `not_tried` before
 * any, and `not_in_use` for a saved agent the environment overrides.
 */
export type AgentState =
  | "reachable"
  | "unreachable"
  | "not_tried"
  | "not_in_use";

export type Agent = {
  /** 32 hex characters for a saved agent; `env.<name>` for an environment agent. */
  id: string;
  name: string;
  source: "saved" | "environment";
  /** Whether a message to the agent waits for a person: `none` runs without approval. */
  approval: "required" | "none";
  /** A saved agent's address as the service keeps it. Null for an environment agent. */
  url: string | null;
  /** What a change or a removal sends back. Null for an environment agent. */
  version: number | null;
  state: AgentState;
  /** Null while `not_tried` or `not_in_use`. */
  checked_at: string | null;
  /** Why the agent is unreachable or not in use. */
  reason: string | null;
  /** Null for an environment agent whose card the service has not read. */
  card: AgentCard | null;
  /** The agent's pending tasks, on every thread. */
  pending_tasks: number;
  created_at: string | null;
  updated_at: string | null;
};

export type AgentList = {
  /** Environment agents first, in configuration order, then saved agents by name. */
  agents: Agent[];
  /** How many agents can be saved. */
  limit: number;
  /** How many agents are `unreachable` or `not_in_use`. */
  unusable: number;
};

/** The path of the list of agents, and the one key its copy in the browser is kept under. */
export const AGENTS_KEY = "/agents";

export function listAgents(): Promise<AgentList> {
  return api<AgentList>(AGENTS_KEY);
}

/** What an operator enters to add an agent. */
export type NewAgent = { name: string; url: string };

/**
 * Save a new agent. The service reads the agent's card at the address first and saves the
 * agent only when it can be used; the agent is sent no message. Only the two values are
 * sent: a saved agent requires approval, which the service decides and not this request.
 */
export function addAgent({ name, url }: NewAgent): Promise<Agent> {
  return api<Agent>(AGENTS_KEY, { method: "POST", body: { name, url } });
}

/**
 * The agents the console warns about: those that cannot be handed work now. One the service
 * has not tried yet is not among them: nothing is known against it.
 */
export function downAgents(list: AgentList): Agent[] {
  return list.agents.filter(
    (agent) => agent.state === "unreachable" || agent.state === "not_in_use",
  );
}
