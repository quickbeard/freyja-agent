"use client";

import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { type LlmProfileList, listProfiles, PROFILES_PATH } from "@/lib/llm";
import { ProfileCard } from "./profile-card";

function reason(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The profiles could not be loaded.";
}

/** The LLM section: every profile as a card, read from the service in the browser. */
export function ProfileList() {
  const { data, error, isLoading, isValidating, mutate } = useSWR<
    LlmProfileList,
    unknown
  >(PROFILES_PATH, listProfiles, {
    // A failure is shown, with a way to try again, and not retried behind the operator's back.
    shouldRetryOnError: false,
  });

  if (error !== undefined) {
    // Whatever was loaded before is not shown: it may no longer be true.
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 p-4"
      >
        <p className="text-sm">{reason(error)}</p>
        <Button
          variant="outline"
          size="sm"
          disabled={isValidating}
          onClick={() => mutate()}
        >
          Try again
        </Button>
      </div>
    );
  }

  if (isLoading || data === undefined) {
    return (
      <output className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <span className="sr-only">Loading profiles</span>
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </output>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {!data.usable_model && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/40 p-4 text-sm"
        >
          No usable model is active, so every run ends with an error. Activate a
          usable profile to continue.
        </p>
      )}
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          disabled={isValidating}
          onClick={() => mutate()}
        >
          Refresh
        </Button>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data.profiles.map((profile) => (
          <ProfileCard key={profile.id} profile={profile} />
        ))}
      </div>
    </div>
  );
}
