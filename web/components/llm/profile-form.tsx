"use client";

import { type FormEvent, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api";
import {
  createProfile,
  type LlmProfile,
  type LlmProvider,
  type LlmTestResult,
  PROVIDERS,
  testDraft,
  toDraft,
  updateProfile,
} from "@/lib/llm";
import { TestResult } from "./test-result";

type FieldName = "name" | "provider" | "model" | "baseUrl" | "apiKey";
type Errors = Partial<Record<FieldName, string>>;

// The service names a refused value by its own field name.
const FIELD_OF: Record<string, FieldName> = {
  name: "name",
  provider: "provider",
  model: "model",
  base_url: "baseUrl",
  api_key: "apiKey",
};

const NAME_LIMIT = 64;

function isWebAddress(value: string): boolean {
  try {
    const { protocol, hostname } = new URL(value);
    return (protocol === "http:" || protocol === "https:") && hostname !== "";
  } catch {
    return false;
  }
}

/**
 * What the form itself can tell is wrong, before anything is sent. A test needs no name:
 * it is the values that reach the model that are tested.
 */
function check(
  values: { name: string; model: string; baseUrl: string; apiKey: string },
  purpose: "save" | "test",
  // A saved profile already has a key: an empty one means the stored key (FR-009, FR-017).
  hasStoredKey: boolean,
): Errors {
  const errors: Errors = {};
  if (purpose === "test") {
    // No name is needed.
  } else if (values.name === "") {
    errors.name = "Enter a name.";
  } else if (values.name.length > NAME_LIMIT) {
    errors.name = `A name has at most ${NAME_LIMIT} characters.`;
  }
  if (values.model === "") {
    errors.model = "Enter the model id.";
  }
  if (values.baseUrl !== "" && !isWebAddress(values.baseUrl)) {
    errors.baseUrl =
      "Enter an http or https address, such as http://10.0.0.5:8000/v1.";
  }
  if (values.apiKey === "" && !hasStoredKey) {
    errors.apiKey = "Enter the API key.";
  }
  return errors;
}

function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p id={`${id}-error`} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** What an input needs to say that its value was refused, and where the reason is. */
function invalid(id: string, error?: string) {
  return error
    ? { "aria-invalid": true, "aria-describedby": `${id}-error` }
    : {};
}

function Fields({
  profile,
  onCancel,
  onSaved,
}: {
  /** The saved profile being edited; none when one is being added. */
  profile?: LlmProfile;
  onCancel: () => void;
  onSaved: (profile: LlmProfile) => void;
}) {
  const id = useId();
  const editing = profile !== undefined;
  const [name, setName] = useState(profile?.name ?? "");
  const [provider, setProvider] = useState<LlmProvider>(
    PROVIDERS.find(({ value }) => value === profile?.provider)?.value ??
      "openai",
  );
  const [model, setModel] = useState(profile?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(profile?.base_url ?? "");
  // The key lives here and nowhere else: it goes when the form is sent or closed (FR-003).
  // The service never sends a stored key, so an edit starts with none.
  const [apiKey, setApiKey] = useState("");
  const [responsesApi, setResponsesApi] = useState(
    profile?.responses_api ?? true,
  );
  // A change to the active profile is confirmed first (FR-010).
  const [confirming, setConfirming] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // A second press arriving before the first one shows as sending still sends nothing.
  const inFlight = useRef(false);
  // The result of testing the values as they were when Test was pressed.
  const [tested, setTested] = useState<LlmTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  // Changing a value takes back what was said about it, and a result for the old values.
  function changing<T>(field: FieldName | null, set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setTested(null);
      setConfirming(false);
      if (field !== null) {
        setErrors(({ [field]: _gone, ...rest }) => rest);
      }
    };
  }

  /** What went wrong with a request, put where it belongs: at a field, or over the form. */
  function refused(error: unknown, fallback: string) {
    const told: Errors = {};
    let other: string | null = null;
    if (!(error instanceof ApiError)) {
      other = fallback;
    } else if (error.code === "name_taken") {
      told.name = error.message;
    } else if (error.code === "invalid") {
      for (const { field, message } of error.fields) {
        const at = FIELD_OF[field];
        if (at) {
          told[at] = message;
        } else {
          other = message;
        }
      }
    } else {
      other = error.message;
    }
    setErrors(told);
    setRefusal(other);
  }

  async function test() {
    if (testing) return;
    const found = check(
      { name, model: model.trim(), baseUrl: baseUrl.trim(), apiKey },
      "test",
      editing,
    );
    setErrors(found);
    setRefusal(null);
    setTested(null);
    if (Object.keys(found).length > 0) return;

    setTesting(true);
    try {
      // Nothing is saved: the values go to be tested as they are in the form.
      setTested(
        await testDraft(
          toDraft(
            { provider, model, baseUrl, apiKey, responsesApi },
            profile?.id,
          ),
        ),
      );
    } catch (error) {
      refused(error, "The values could not be tested.");
    } finally {
      setTesting(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    const values = {
      name: name.trim(),
      model: model.trim(),
      baseUrl: baseUrl.trim(),
      apiKey,
    };
    const found = check(values, "save", editing);
    setErrors(found);
    setRefusal(null);
    if (Object.keys(found).length > 0) return;
    if (profile?.active && !confirming) {
      // Nothing is sent yet: the form first says what the change means.
      setConfirming(true);
      return;
    }

    inFlight.current = true;
    setSending(true);
    try {
      const sent = {
        name: values.name,
        provider,
        model: values.model,
        base_url: values.baseUrl === "" ? null : values.baseUrl,
        // Only an OpenAI-compatible endpoint has the choice.
        ...(provider === "openai" ? { responses_api: responsesApi } : {}),
      };
      const saved = editing
        ? // A key left empty is not sent, so the stored one is kept (FR-009).
          await updateProfile(profile.id, {
            ...sent,
            ...(values.apiKey === "" ? {} : { api_key: values.apiKey }),
          })
        : await createProfile({ ...sent, api_key: values.apiKey });
      onSaved(saved);
    } catch (error) {
      refused(error, "The profile could not be saved.");
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="grid gap-4">
      <DialogHeader>
        <DialogTitle>
          {editing ? `Edit ${profile.name}` : "Add a profile"}
        </DialogTitle>
        <DialogDescription>
          {editing
            ? "Leave the key empty to keep the one that is stored."
            : "A model Freyja can run on. Saving it does not change the profile in use."}
        </DialogDescription>
      </DialogHeader>

      {refusal && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}

      <Field id={`${id}-name`} label="Name" error={errors.name}>
        <Input
          id={`${id}-name`}
          value={name}
          onChange={(event) => changing("name", setName)(event.target.value)}
          autoComplete="off"
          {...invalid(`${id}-name`, errors.name)}
        />
      </Field>

      <Field id={`${id}-provider`} label="Provider" error={errors.provider}>
        <Select
          items={PROVIDERS}
          value={provider}
          onValueChange={(value) =>
            changing("provider", setProvider)(value as LlmProvider)
          }
        >
          <SelectTrigger
            id={`${id}-provider`}
            className="w-full"
            {...invalid(`${id}-provider`, errors.provider)}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PROVIDERS.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field id={`${id}-model`} label="Model id" error={errors.model}>
        <Input
          id={`${id}-model`}
          value={model}
          onChange={(event) => changing("model", setModel)(event.target.value)}
          autoComplete="off"
          {...invalid(`${id}-model`, errors.model)}
        />
      </Field>

      <Field id={`${id}-base-url`} label="Base URL" error={errors.baseUrl}>
        <Input
          id={`${id}-base-url`}
          value={baseUrl}
          onChange={(event) =>
            changing("baseUrl", setBaseUrl)(event.target.value)
          }
          placeholder="Optional: leave empty for the provider's own address"
          autoComplete="off"
          inputMode="url"
          {...invalid(`${id}-base-url`, errors.baseUrl)}
        />
      </Field>

      <Field id={`${id}-api-key`} label="API key" error={errors.apiKey}>
        <Input
          id={`${id}-api-key`}
          type="password"
          value={apiKey}
          onChange={(event) =>
            changing("apiKey", setApiKey)(event.target.value)
          }
          autoComplete="off"
          placeholder={
            editing ? "Leave empty to keep the stored key" : undefined
          }
          {...invalid(`${id}-api-key`, errors.apiKey)}
        />
      </Field>

      {provider === "openai" && (
        <div className="flex items-center justify-between gap-4">
          <div className="grid gap-0.5">
            <Label id={`${id}-responses-api`}>Use the Responses API</Label>
            <p className="text-sm text-muted-foreground">
              Turn off for an endpoint that only serves chat completions.
            </p>
          </div>
          <Switch
            aria-labelledby={`${id}-responses-api`}
            checked={responsesApi}
            onCheckedChange={changing(null, setResponsesApi)}
          />
        </div>
      )}

      {confirming && profile && (
        <p
          role="note"
          className="rounded-lg border border-amber-500/50 p-3 text-sm"
        >
          {profile.name} is the active profile. This change applies from the
          next run; a run in progress finishes on the values it started with.
        </p>
      )}

      {tested && (
        <output className="flex flex-col gap-1 text-sm">
          <TestResult test={tested} subject="The test" />
        </output>
      )}

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          className="sm:mr-auto"
          disabled={testing}
          aria-busy={testing}
          onClick={test}
        >
          {testing ? "Testing…" : "Test"}
        </Button>
        {confirming ? (
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirming(false)}
            >
              Back
            </Button>
            <Button type="submit" disabled={sending}>
              Confirm
            </Button>
          </>
        ) : (
          <>
            <Button type="button" variant="outline" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" disabled={sending}>
              Save
            </Button>
          </>
        )}
      </DialogFooter>
    </form>
  );
}

/**
 * The dialog that adds a profile, or changes the saved `profile` it is given. It keeps
 * nothing once it is sent or closed.
 */
export function ProfileForm({
  open,
  onOpenChange,
  onSaved,
  profile,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (profile: LlmProfile) => void;
  profile?: LlmProfile;
}) {
  // A page stays mounted while it is hidden, so what was typed is thrown away by hand: each
  // time the form opens or closes its fields start again, the key included.
  const [session, setSession] = useState(0);

  function change(next: boolean) {
    setSession((current) => current + 1);
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      {/* Taller than a short window once it says what is wrong, so it scrolls in itself. */}
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        <Fields
          key={session}
          profile={profile}
          onCancel={() => change(false)}
          onSaved={(profile) => {
            change(false);
            onSaved(profile);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
