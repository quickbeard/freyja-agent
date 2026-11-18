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
import { Button } from "@/components/ui/button";
import { ApiError, isGone } from "@/lib/api";
import {
  activateProfile,
  type LlmProfile,
  type LlmProfileList,
  type LlmTestResult,
  testProfile,
} from "@/lib/llm";
import { outcome, TestResult } from "./test-result";

function Confirmation({
  profile,
  onClose,
  onActivated,
  onTested,
  onGone,
}: {
  profile: LlmProfile;
  onClose: () => void;
  onActivated: (list: LlmProfileList) => void;
  onTested?: () => void;
  onGone?: () => void;
}) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  // A test run from here says more than the profile's last one, for as long as the profile
  // is the one that was tested and the list knows of no later test.
  const [ran, setRan] = useState<{
    result: LlmTestResult;
    of: string | null;
  } | null>(null);
  const [testing, setTesting] = useState(false);
  const tested =
    ran !== null &&
    ran.of === profile.updated_at &&
    !(
      profile.last_test !== null &&
      Date.parse(profile.last_test.at) > Date.parse(ran.result.at)
    )
      ? ran.result
      : null;
  const last = tested ?? profile.last_test;

  async function testFirst() {
    if (testing) return;
    setTesting(true);
    setRefusal(null);
    try {
      const result = await testProfile(profile.id);
      setRan({ result, of: profile.updated_at });
      onTested?.();
    } catch (error) {
      if (isGone(error)) onGone?.();
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The profile could not be tested.",
      );
    } finally {
      setTesting(false);
    }
  }

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
      if (isGone(error)) onGone?.();
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
      {/* Activation does not depend on a test (FR-012): this warns and offers one. */}
      {(last === null || !last.ok) && (
        <div
          role="note"
          className="flex flex-col items-start gap-2 rounded-lg border border-amber-500/50 p-3 text-sm"
        >
          <p>
            {last === null
              ? `${profile.name} has not been tested since ${
                  profile.source === "environment"
                    ? "the service started"
                    : "its last change"
                }.`
              : `${profile.name}'s ${outcome(last, "last test")}.`}{" "}
            You can still set it active.
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={testing}
            aria-busy={testing}
            onClick={testFirst}
          >
            {testing ? "Testing…" : "Test first"}
          </Button>
        </div>
      )}
      {tested && (
        <output className="flex flex-col gap-1 text-sm">
          <TestResult test={tested} />
        </output>
      )}
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
  onTested,
  onGone,
}: {
  profile: LlmProfile | null;
  onClose: () => void;
  onActivated: (list: LlmProfileList) => void;
  /** Told when a test run from the confirmation has ended. */
  onTested?: () => void;
  /** Told when the service says the profile no longer exists. */
  onGone?: () => void;
}) {
  return (
    <AlertDialog
      open={profile !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {/* Taller than a short, narrow window once it shows a long reason: it scrolls in itself,
          so its buttons can always be reached. */}
      <AlertDialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        {profile !== null && (
          // Keyed by the profile, so one profile's refusal is never shown for another.
          <Confirmation
            key={profile.id}
            profile={profile}
            onClose={onClose}
            onActivated={onActivated}
            onGone={onGone}
            onTested={onTested}
          />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
