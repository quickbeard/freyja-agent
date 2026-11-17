import type { Metadata } from "next";

export const metadata: Metadata = { title: "Agents" };

export default function AgentsPage() {
  return (
    <div className="flex flex-col gap-1">
      <h1 className="font-heading text-2xl font-semibold">Agents</h1>
      <p className="text-sm text-muted-foreground">
        Agents are not managed from the console yet. They are configured where
        the service is deployed.
      </p>
    </div>
  );
}
