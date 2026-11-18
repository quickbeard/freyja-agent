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
import {
  activateProfile,
  type LlmProfile,
  type LlmProfileList,
} from "@/lib/llm";

function Confirmation({
  profile,
  onClose,
  onActivated,
}: {
  profile: LlmProfile;
  onClose: () => void;
  onActivated: (list: LlmProfileList) => void;
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
      const list = await activateProfile(profile.id);
      onActivated(list);
      onClose();
    } catch (error) {
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The profile could not be activated.",
      );
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>
          Set {profile.name} as the active profile?
        </AlertDialogTitle>
        <AlertDialogDescription>
          Runs that start from now on will use {profile.name}. Existing
          conversations will be sent to its endpoint from their next run. A run
          in progress finishes on the model it started with.
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
        <AlertDialogAction disabled={sending} onClick={confirm}>
          Set active
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  );
}

/**
 * The confirmation before a profile becomes the one Freyja runs on (FR-012). It is open while
 * it is given a profile.
 */
export function ActivateDialog({
  profile,
  onClose,
  onActivated,
}: {
  profile: LlmProfile | null;
  onClose: () => void;
  onActivated: (list: LlmProfileList) => void;
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
          // Keyed by the profile, so one profile's refusal is never shown for another.
          <Confirmation
            key={profile.id}
            profile={profile}
            onClose={onClose}
            onActivated={onActivated}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
