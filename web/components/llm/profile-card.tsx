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
import { ApiError } from "@/lib/api";
import {
  type LlmProfile,
  type LlmTestResult,
  providerLabel,
  testProfile,
  type UnusableReason,
} from "@/lib/llm";
import { TestResult } from "./test-result";

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

/**
 * One LLM profile, as the service describes it. It never holds or shows a whole key.
 * `onActivate` is offered on a usable profile that is not the active one. A usable profile
 * can be tested from its card; `onTested` is told when a test has ended.
 */
export function ProfileCard({
  profile,
  onActivate,
  onTested,
}: {
  profile: LlmProfile;
  onActivate?: (profile: LlmProfile) => void;
  onTested?: () => void;
}) {
  const canActivate =
    onActivate !== undefined && !profile.active && profile.usable;
  // The result of a test run from this card, and the state of the profile it was for. Once
  // the profile has changed, that result says nothing of it any more.
  const [tested, setTested] = useState<{
    result: LlmTestResult;
    of: string | null;
  } | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const inFlight = useRef(false);

  const fresh = tested !== null && tested.of === profile.updated_at;
  const shown = fresh ? tested.result : profile.last_test;

  async function test() {
    if (inFlight.current) return;
    inFlight.current = true;
    setTesting(true);
    setRefusal(null);
    try {
      const result = await testProfile(profile.id);
      setTested({ result, of: profile.updated_at });
      onTested?.();
    } catch (error) {
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The profile could not be tested.",
      );
    } finally {
      inFlight.current = false;
      setTesting(false);
    }
  }
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
        {/* Announced when it comes from a test run here; otherwise it is the list's. */}
        <div
          role={fresh ? "status" : undefined}
          className="flex flex-col gap-1 text-sm"
        >
          {shown === null ? (
            <p className="text-muted-foreground">
              {profile.source === "environment"
                ? "Not tested since the service started."
                : "Not tested since its last change."}
            </p>
          ) : (
            <TestResult test={shown} />
          )}
          {fresh && !tested.result.recorded && (
            <p className="text-muted-foreground">
              This result was not kept: the profile changed while it was tested.
            </p>
          )}
        </div>
        {refusal && (
          <p role="alert" className="text-sm text-destructive">
            {refusal}
          </p>
        )}
      </CardContent>
      {profile.usable && (
        <CardFooter className="mt-auto gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={testing}
            aria-busy={testing}
            onClick={test}
          >
            {testing ? "Testing…" : "Test"}
          </Button>
          {canActivate && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onActivate(profile)}
            >
              Set active
            </Button>
          )}
        </CardFooter>
      )}
    </Card>
  );
}
