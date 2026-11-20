"use client";

import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  type Agent,
  type AgentCheck,
  type AgentCard as AgentClaim,
  checkAgent,
} from "@/lib/agents";
import { ApiError, isGone } from "@/lib/api";
import { CheckResult } from "./check-result";

// The time is the service's own, only put in the reader's words: no clock is read here.
const WHEN = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function When({ time }: { time: string }) {
  return (
    <time dateTime={time} className="text-muted-foreground">
      {WHEN.format(new Date(time))}
    </time>
  );
}

function Value({ label, children }: { label: string; children: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-sm">{children}</dd>
    </div>
  );
}

// What a warning says of each state it is about, in words (FR-001a).
const DOWN = {
  unreachable: { badge: "Unreachable", says: "This agent cannot be reached." },
  not_in_use: { badge: "Not in use", says: "This agent is not in use." },
} as const;

/**
 * The agent's state as this service instance last found it. An agent that cannot be handed
 * work now carries a warning, which says so in words and gives the reason and the time.
 */
function State({ agent }: { agent: Agent }) {
  if (agent.state === "reachable") {
    return (
      <p className="text-sm">
        Reachable
        {agent.checked_at !== null && (
          <>
            {" · "}
            <When time={agent.checked_at} />
          </>
        )}
      </p>
    );
  }
  if (agent.state === "not_tried") {
    return (
      <p className="text-sm text-muted-foreground">
        This instance has not tried the agent yet.
      </p>
    );
  }
  return (
    <div
      role="note"
      aria-label="Warning"
      className="flex flex-col gap-1 rounded-md border border-destructive/40 p-3 text-sm"
    >
      <p>
        <span className="font-medium text-destructive">Warning: </span>
        {DOWN[agent.state].says}
        {agent.checked_at !== null && (
          <>
            {" · "}
            <When time={agent.checked_at} />
          </>
        )}
      </p>
      {/* The reason is text, whatever it holds: nothing is made of it (FR-002). */}
      {agent.reason !== null && (
        <p className="break-words text-muted-foreground">{agent.reason}</p>
      )}
    </div>
  );
}

/**
 * What the agent's card says, shown as the agent's own claim and apart from the approval
 * choice. Every text is a text node: a card is untrusted, and nothing in it is rendered as
 * markup, loaded, or followed as a link (FR-002).
 */
function Claim({ card }: { card: AgentClaim }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-xs text-muted-foreground">
        What the agent says of itself
      </legend>
      <dl className="grid gap-3">
        <Value label="Name">{card.name}</Value>
        <Value label="Description">{card.description}</Value>
        <Value label="Version">{card.version}</Value>
      </dl>
      {card.skills.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">Skills</p>
          <ul className="flex flex-col gap-2">
            {card.skills.map((skill, index) => (
              // A card may name two skills alike, so the place in the list is the key.
              // biome-ignore lint/suspicious/noArrayIndexKey: the list is never reordered.
              <li key={index} className="flex flex-col gap-0.5 text-sm">
                <span className="break-words font-medium">{skill.name}</span>
                <span className="break-words">{skill.description}</span>
                {skill.tags.length > 0 && (
                  <span className="break-words text-xs text-muted-foreground">
                    {skill.tags.join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {card.cut && (
        <p className="text-xs text-muted-foreground">
          Some of this was cut: the service keeps the first 2,000 characters of
          each text, 50 skills, and 50 tags of a skill.
        </p>
      )}
    </fieldset>
  );
}

/**
 * One agent Freyja can hand work to, as the service describes it. Nothing in the card itself
 * changes the agent, and an environment agent's address is not known to the console at all.
 * An agent that is in use can be checked from its card: the service reads its card again
 * and says whether the agent can be used. `onChecked` is told when the list should be read
 * again. A saved agent that is in use has Edit, which hands the agent to whoever opens the
 * form; an environment agent cannot be changed, and a saved agent that is not in use can
 * only be removed (FR-017).
 */
export function AgentCard({
  agent,
  onChecked,
  onGone,
  onEdit,
}: {
  agent: Agent;
  /** Told after any result of a check, and when a check got no answer. */
  onChecked?: () => void;
  /** Told when the service says this agent no longer exists. */
  onGone?: () => void;
  /** Told that the operator asked to change this agent. */
  onEdit?: (agent: Agent) => void;
}) {
  const down = agent.state === "unreachable" || agent.state === "not_in_use";
  // A saved agent the environment overrides has no link to check (FR-013).
  const inUse = agent.state !== "not_in_use";
  const editable = agent.source === "saved" && inUse;
  // What the last check from this card found, or why it was refused. Either stays until the
  // next check starts: the result carries the time the service gave it.
  const [checked, setChecked] = useState<AgentCheck | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  // A second press arriving before the first one shows as checking still sends nothing.
  const inFlight = useRef(false);
  // Reading a card can take a while, and the card can go meanwhile: the list was read again
  // without its agent, or the section was left. What the answer sets here is then set on
  // nothing; whoever holds the list is told all the same.

  async function check() {
    if (inFlight.current) return;
    inFlight.current = true;
    setChecking(true);
    setChecked(null);
    setRefusal(null);
    try {
      const result = await checkAgent(agent.id);
      setChecked(result);
      // The service says what is so now: its state, its time, and its card's details.
      onChecked?.();
    } catch (error) {
      if (isGone(error)) {
        onGone?.();
      } else if (
        !(error instanceof ApiError) ||
        error.code === "unreachable" ||
        error.code === "agent_read_only"
      ) {
        // No answer came back, so whether the card was read is not known; or the service
        // says the agent is not in use, which this card does not show yet.
        onChecked?.();
      }
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The agent could not be checked.",
      );
    } finally {
      inFlight.current = false;
      setChecking(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h3>{agent.name}</h3>
        </CardTitle>
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant={agent.approval === "required" ? "default" : "outline"}
          >
            {agent.approval === "required"
              ? "Requires approval"
              : "Runs without approval"}
          </Badge>
          {agent.source === "environment" && (
            <Badge variant="secondary">From the environment</Badge>
          )}
          {down && (
            <Badge variant="destructive">
              {DOWN[agent.state as keyof typeof DOWN].badge}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {agent.url !== null && (
          <dl className="grid gap-3">
            <Value label="Address">{agent.url}</Value>
          </dl>
        )}
        <State agent={agent} />
        {agent.card === null ? (
          <p className="text-sm text-muted-foreground">
            Its card has not been read yet.
          </p>
        ) : (
          <Claim card={agent.card} />
        )}
        {checked && (
          // A status, as the result of a profile's test is in the LLM section. It is not
          // inside a region marked `aria-live`: the dialog that adds an agent leaves such
          // a region, and what is around it, reachable behind itself.
          <output className="flex flex-col gap-1 text-sm">
            <CheckResult check={checked} />
          </output>
        )}
        {/* The service's sentence is text, whatever it holds: nothing is made of it. */}
        {refusal && (
          <p role="alert" className="break-words text-sm text-destructive">
            {refusal}
          </p>
        )}
      </CardContent>
      {inUse && (
        <CardFooter className="mt-auto flex-wrap gap-2">
          {/* Reading the card can take a while: the button says so and takes no press. */}
          <Button
            variant="outline"
            size="sm"
            disabled={checking}
            aria-busy={checking}
            onClick={check}
          >
            {checking ? "Checking…" : "Check"}
          </Button>
          {editable && (
            <Button variant="outline" size="sm" onClick={() => onEdit?.(agent)}>
              Edit
            </Button>
          )}
        </CardFooter>
      )}
    </Card>
  );
}
