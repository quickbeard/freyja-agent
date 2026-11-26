// Agents as the service describes them (specs/005-web-delegate-agents/contracts/openapi.yaml).
// A saved agent's address is in `url`; an environment agent's is in no field of any type here.
// An address travels in a request's body and in no path or query; an agent is named in a
// path by its id, and a removal names the version it was read at in a query.

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
  approval: Approval;
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
  /**
   * The agents' approval mode (feature 010): `manual` has every message to every agent wait
   * for a person; `auto` lets an agent whose own mode is Auto run without one.
   */
  mode: AgentsMode;
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

/** Whether a message to an agent waits for a person: `none` runs without approval. */
export type Approval = "required" | "none";

/** The agents' approval mode, the switch of the sidebar's Tasks section (feature 010). */
export type AgentsMode = "manual" | "auto";

/** An agent's own mode as the console names it: Auto is the approval choice `none`. */
export function agentMode(approval: Approval): AgentsMode {
  return approval === "none" ? "auto" : "manual";
}

/** The approval choice an agent's own mode is sent as. */
export function approvalOf(mode: AgentsMode): Approval {
  return mode === "auto" ? "none" : "required";
}

/**
 * Set the agents' approval mode. Manual holds at once, also for runs in progress; Auto
 * holds from the next run. The service contacts no agent.
 */
export async function setAgentsMode(mode: AgentsMode): Promise<AgentsMode> {
  const answer = await api<{ mode: AgentsMode }>(`${AGENTS_KEY}/mode`, {
    method: "PUT",
    body: { mode },
  });
  return answer.mode;
}

/** What an operator enters to add an agent. */
export type NewAgent = { name: string; url: string; approval?: Approval };

/**
 * Save a new agent. The service reads the agent's card at the address first and saves the
 * agent only when it can be used; the agent is sent no message. A saved agent requires
 * approval unless the request says otherwise, so the choice is sent only when it is to run
 * without approval, which the form asks about first.
 */
export function addAgent({ name, url, approval }: NewAgent): Promise<Agent> {
  return api<Agent>(AGENTS_KEY, {
    method: "POST",
    body: approval === "none" ? { name, url, approval } : { name, url },
  });
}

/**
 * A change of a saved agent: the version the page read the agent at, and each value that is
 * to change. A value left out is kept.
 */
export type AgentChange = {
  version: number;
  url?: string;
  approval?: Approval;
};

/** A saved agent after a change, and how many of its pending tasks the change gave up. */
export type AgentChanged = { agent: Agent; given_up: number };

/**
 * Change a saved agent. Exactly the values given are sent: the service refuses the change
 * when the agent is no longer at `version`. It contacts no agent for a change of the
 * approval choice. For a change of address it reads the card at the new address first,
 * refuses an agent it cannot use there, and gives up the agent's pending tasks.
 */
export function changeAgent(
  id: string,
  { version, url, approval }: AgentChange,
): Promise<AgentChanged> {
  const body: AgentChange = { version };
  if (url !== undefined) {
    body.url = url;
  }
  if (approval !== undefined) {
    body.approval = approval;
  }
  return api<AgentChanged>(`${AGENTS_KEY}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

/**
 * Remove a saved agent, at the version the page read it at: the service refuses the removal
 * when the agent was changed since. Returns how many of its pending tasks were given up.
 */
export async function removeAgent(
  id: string,
  version: number,
): Promise<number> {
  const query = new URLSearchParams({ version: String(version) });
  const answer = await api<{ given_up: number }>(
    `${AGENTS_KEY}/${encodeURIComponent(id)}?${query}`,
    { method: "DELETE" },
  );
  return answer.given_up;
}

/** Why an agent's card cannot be used: one code for each way of failing. */
export type CardProblem =
  | "no_answer"
  | "timeout"
  | "no_card"
  | "unreadable"
  | "redirect"
  | "too_large"
  | "auth_required"
  | "no_text"
  | "no_interface";

/** What a check found: the agent's card was read again, and nothing was sent to the agent. */
export type AgentCheck = {
  usable: boolean;
  /** Null when the agent can be used. */
  reason: CardProblem | null;
  /** The service's own sentence for `reason`. */
  message: string | null;
  checked_at: string;
  /** False when the agent was moved or removed while the check ran: nothing was kept. */
  recorded: boolean;
  /** True when the check replaced the card details kept or shown for the agent. */
  card_changed: boolean;
};

/**
 * Have the service read an agent's card again, saved or from the environment. It takes no
 * body: the agent is named by its id, which holds no address.
 */
export function checkAgent(id: string): Promise<AgentCheck> {
  return api<AgentCheck>(`${AGENTS_KEY}/${encodeURIComponent(id)}/check`, {
    method: "POST",
  });
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

// An agent's state and choice as a confirmation words them, inside a sentence.
const STATE_IN_WORDS: Record<Agent["state"], string> = {
  reachable: "reachable",
  unreachable: "unreachable",
  not_tried: "not tried yet",
  not_in_use: "not in use",
};
const CHOICE_IN_WORDS: Record<Approval, string> = {
  required: "requires approval",
  none: "runs without approval",
};

/**
 * A saved agent as a confirmation shows it (FR-011): its name, its address, its approval
 * choice, its state, and how many of its tasks are pending.
 */
export function agentInWords(agent: Agent): string {
  const tasks = agent.pending_tasks === 1 ? "task" : "tasks";
  return `${agent.name}, at ${agent.url}, ${CHOICE_IN_WORDS[agent.approval]}, ${STATE_IN_WORDS[agent.state]}, ${agent.pending_tasks} pending ${tasks}.`;
}
