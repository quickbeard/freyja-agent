"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, isGone } from "@/lib/api";
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
  onEdit,
  onDelete,
  onGone,
  listRead,
}: {
  profile: LlmProfile;
  onActivate?: (profile: LlmProfile) => void;
  onTested?: () => void;
  /** Told when the service says this profile no longer exists. */
  onGone?: () => void;
  /** How often the list has been read, so the card knows when the list is newer than it. */
  listRead?: number;
  /** Offered on a saved profile only: the environment's is read-only (FR-008). */
  onEdit?: (profile: LlmProfile) => void;
  onDelete?: (profile: LlmProfile) => void;
}) {
  const saved = profile.source === "saved";
  const canEdit = saved && onEdit !== undefined;
  const canDelete = saved && onDelete !== undefined;
  const canActivate =
    onActivate !== undefined && !profile.active && profile.usable;
  // The result of a test run from this card, and the state of the profile it was for. Once
  // the profile has changed, that result says nothing of it any more.
  const [tested, setTested] = useState<{
    result: LlmTestResult;
    of: string | null;
    /** The reading of the list the card had when the result came. */
    read: number | undefined;
  } | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const inFlight = useRef(false);
  // The list's latest reading, for when a result arrives: the list may be read while a test
  // runs, and that reading is older than the result, not newer.
  const latestRead = useRef(listRead);
  useEffect(() => {
    latestRead.current = listRead;
  }, [listRead]);

  // The card's own result is shown only until the service says more. A test does not change
  // the profile, so the list may report a later one, run from the confirmation or by someone
  // else. And once the list has been read again, a result the service kept is the list's to
  // show: when it is not there any more, as after a restart for the environment's profile,
  // it is not shown.
  const own =
    tested?.result.recorded === true && tested.of === profile.updated_at;
  const listHasLater =
    own &&
    profile.last_test !== null &&
    Date.parse(profile.last_test.at) >= Date.parse(tested.result.at);
  const listReadSince =
    own &&
    listRead !== undefined &&
    tested.read !== undefined &&
    listRead > tested.read;
  const fresh = own && !listHasLater && !listReadSince;
  const shown = fresh ? tested.result : profile.last_test;
  // A result the service did not keep, because the profile changed while it was tested, is
  // nobody's but the card's: it is not of the profile as it is now, so the list will never
  // have it. It stays beside what the list says, until the next test from here.
  const unkept = tested !== null && !tested.result.recorded ? tested : null;

  async function test() {
    if (inFlight.current) return;
    inFlight.current = true;
    setTesting(true);
    setRefusal(null);
    setTested((last) => (last?.result.recorded ? last : null));
    try {
      const result = await testProfile(profile.id);
      setTested({
        result,
        of: profile.updated_at,
        read: latestRead.current,
      });
      onTested?.();
    } catch (error) {
      if (isGone(error)) onGone?.();
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
        </div>
        {unkept && (
          <output className="flex flex-col gap-1 text-sm">
            <TestResult test={unkept.result} subject="The test" />
            <p className="text-muted-foreground">
              This result was not kept: the profile changed while it was tested.
            </p>
          </output>
        )}
        {refusal && (
          <p role="alert" className="text-sm text-destructive">
            {refusal}
          </p>
        )}
      </CardContent>
      {(profile.usable || canEdit || canDelete) && (
        <CardFooter className="mt-auto flex-wrap gap-2">
          {profile.usable && (
            <Button
              variant="outline"
              size="sm"
              disabled={testing}
              aria-busy={testing}
              onClick={test}
            >
              {testing ? "Testing…" : "Test"}
            </Button>
          )}
          {canActivate && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onActivate(profile)}
            >
              Set active
            </Button>
          )}
          {canEdit && (
            <Button variant="outline" size="sm" onClick={() => onEdit(profile)}>
              Edit
            </Button>
          )}
          {canDelete && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onDelete(profile)}
            >
              Delete
            </Button>
          )}
        </CardFooter>
      )}
    </Card>
  );
}
