"use client";

import { useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ApiError } from "@/lib/api";
import { deleteProfile, type LlmProfile } from "@/lib/llm";

function Confirmation({
  profile,
  onClose,
  onDeleted,
}: {
  profile: LlmProfile;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);

  async function confirm() {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setRefusal(null);
    try {
      await deleteProfile(profile.id);
      onDeleted();
      onClose();
    } catch (error) {
      // The service's own words: for the active profile they say what to do first.
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The profile could not be deleted.",
      );
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>Delete {profile.name}?</AlertDialogTitle>
        <AlertDialogDescription>
          The profile and its stored key are removed. This cannot be undone.
        </AlertDialogDescription>
      </AlertDialogHeader>
      {refusal && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}
      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction
          variant="destructive"
          disabled={sending}
          onClick={confirm}
        >
          Delete
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  );
}

/**
 * The confirmation before a saved profile is deleted (FR-010). It is open while it is given
 * a profile. The service refuses the active one, and its refusal is shown here.
 */
export function DeleteDialog({
  profile,
  onClose,
  onDeleted,
}: {
  profile: LlmProfile | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  return (
    <AlertDialog
      open={profile !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent>
        {profile !== null && (
          <Confirmation
            key={profile.id}
            profile={profile}
            onClose={onClose}
            onDeleted={onDeleted}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
