import type { Metadata } from "next";
import { AgentList } from "@/components/agents/agent-list";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-semibold">Agents</h1>
        <p className="text-sm text-muted-foreground">
          The agents Freyja can hand work to. An agent that requires approval
          gets no message until a person has approved it.
        </p>
      </div>
      <AgentList />
    </div>
  );
}
