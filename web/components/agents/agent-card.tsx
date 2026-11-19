"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Agent, AgentCard as AgentClaim } from "@/lib/agents";

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
 * One agent Freyja can hand work to, as the service describes it. Nothing here changes the
 * agent, and an environment agent's address is not known to the console at all.
 */
export function AgentCard({ agent }: { agent: Agent }) {
  const down = agent.state === "unreachable" || agent.state === "not_in_use";
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
      </CardContent>
    </Card>
  );
}
