import type { Metadata } from "next";
import { ProfileList } from "@/components/llm/profile-list";

export const metadata: Metadata = { title: "LLM" };

export default function LlmPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-semibold">LLM</h1>
        <p className="text-sm text-muted-foreground">
          The profiles Freyja can run on. The active one is the model the
          orchestrator uses.
        </p>
      </div>
      <ProfileList />
    </div>
  );
}
