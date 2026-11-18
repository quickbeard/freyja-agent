"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import {
  type LlmProfile,
  type LlmProfileList,
  listProfiles,
  PROFILES_PATH,
} from "@/lib/llm";
import { ActivateDialog } from "./activate-dialog";
import { DeleteDialog } from "./delete-dialog";
import { ProfileCard } from "./profile-card";
import { ProfileForm } from "./profile-form";

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
  const [adding, setAdding] = useState(false);
  const [activating, setActivating] = useState<LlmProfile | null>(null);
  // The profile stays while its form closes, so the form does not turn into another one.
  const [editing, setEditing] = useState<{
    profile: LlmProfile;
    open: boolean;
  } | null>(null);
  const [deleting, setDeleting] = useState<LlmProfile | null>(null);

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
      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={isValidating}
          onClick={() => mutate()}
        >
          Refresh
        </Button>
        <Button size="sm" onClick={() => setAdding(true)}>
          Add
        </Button>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data.profiles.map((profile) => (
          <ProfileCard
            key={profile.id}
            profile={profile}
            onActivate={setActivating}
            onTested={() => mutate()}
            onEdit={(chosen) => setEditing({ profile: chosen, open: true })}
            onDelete={setDeleting}
          />
        ))}
      </div>
      {/* After either change the list is read again: the service says what is so. */}
      <ProfileForm
        open={adding}
        onOpenChange={setAdding}
        onSaved={() => mutate()}
      />
      {editing && (
        <ProfileForm
          profile={editing.profile}
          open={editing.open}
          onOpenChange={(open) => setEditing({ ...editing, open })}
          onSaved={() => mutate()}
        />
      )}
      <DeleteDialog
        profile={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={() => mutate()}
      />
      <ActivateDialog
        profile={activating}
        onClose={() => setActivating(null)}
        onActivated={(list) => mutate(list)}
        onTested={() => mutate()}
      />
    </div>
  );
}
