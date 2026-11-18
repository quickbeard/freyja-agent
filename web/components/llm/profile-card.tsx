import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type LlmProfile, providerLabel, type UnusableReason } from "@/lib/llm";

const UNUSABLE: Record<UnusableReason, string> = {
  key_unreadable:
    "Its stored key cannot be read with the service's current secret. Enter the key again to use it.",
  secret_not_configured:
    "No secret is set on the service, so its stored key cannot be read.",
  not_configured: "The environment names no key for this provider.",
  cannot_build: "No model can be built from these values.",
};

function Value({ label, children }: { label: string; children: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-sm">{children}</dd>
    </div>
  );
}

/** One LLM profile, as the service describes it. It never holds or shows a whole key. */
export function ProfileCard({ profile }: { profile: LlmProfile }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h3>{profile.name}</h3>
        </CardTitle>
        <div className="flex flex-wrap gap-1.5">
          {profile.active && <Badge>Active</Badge>}
          {profile.source === "environment" && (
            <Badge variant="secondary">From the environment</Badge>
          )}
          {!profile.usable && <Badge variant="destructive">Not usable</Badge>}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <dl className="grid gap-3">
          <Value label="Provider">{providerLabel(profile.provider)}</Value>
          <Value label="Model">{profile.model}</Value>
          {profile.base_url !== null && (
            <Value label="Base URL">{profile.base_url}</Value>
          )}
          {profile.responses_api !== null && (
            <Value label="API">
              {profile.responses_api ? "Responses API" : "Chat completions"}
            </Value>
          )}
          <Value label="API key">
            {profile.key_hint ? `Ends in ${profile.key_hint}` : "Not shown"}
          </Value>
        </dl>
        {profile.unusable_reason !== null && (
          <p className="text-sm text-destructive">
            {UNUSABLE[profile.unusable_reason]}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
