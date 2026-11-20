"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
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
import { type Agent, addAgent } from "@/lib/agents";
import { ApiError } from "@/lib/api";

type FieldName = "name" | "url";
type Errors = Partial<Record<FieldName, string>>;

// The service names a refused value by its own field name.
const FIELD_OF: Record<string, FieldName> = { name: "name", url: "url" };

// A refusal that is about one value is said at that value.
const FIELD_OF_CODE: Record<string, FieldName> = {
  name_taken: "name",
  address_taken: "url",
};

/**
 * What is said when a request that changes something got no answer. The agent may have been
 * saved and the answer lost, so this never says that the request failed (FR-004).
 */
const OUTCOME_NOT_KNOWN =
  "The request got no answer, so it is not known whether the agent was added. The list is being read again: look for the agent there before saving again.";

/**
 * What the form itself can tell is wrong, before anything is sent: a value that is missing.
 * Every rule about a name or an address is the service's, and is said in its words.
 */
function check(values: { name: string; url: string }): Errors {
  const errors: Errors = {};
  if (values.name === "") {
    errors.name = "Enter a name.";
  }
  if (values.url === "") {
    errors.url = "Enter the address.";
  }
  return errors;
}

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      <p id={`${id}-hint`} className="text-sm text-muted-foreground">
        {hint}
      </p>
      {/* The service's sentence is text, whatever it holds: nothing is made of it. */}
      {error && (
        <p id={`${id}-error`} className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** What an input needs to say what it takes, that its value was refused, and why. */
function described(id: string, error?: string) {
  return error
    ? { "aria-invalid": true, "aria-describedby": `${id}-hint ${id}-error` }
    : { "aria-describedby": `${id}-hint` };
}

function Fields({
  onCancel,
  onSaved,
  onUnknown,
}: {
  onCancel: () => void;
  /** Told that the agent was saved, and whether this form is still the one on the page. */
  onSaved: (agent: Agent, shown: boolean) => void;
  onUnknown: () => void;
}) {
  const id = useId();
  // What is typed lives here and nowhere else: it goes when the form is sent or closed.
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // A second press arriving before the first one shows as sending still sends nothing.
  const inFlight = useRef(false);
  // Reading a card can take a while, and the form can be closed, and opened again, before
  // the answer comes. The answer then belongs to a form that is gone: it changes nothing in
  // the one that is open now.
  const shown = useRef(true);
  useEffect(() => {
    shown.current = true;
    return () => {
      shown.current = false;
    };
  }, []);

  // Changing a value takes back what was said about it.
  function changing(field: FieldName, set: (value: string) => void) {
    return (value: string) => {
      set(value);
      setErrors(({ [field]: _gone, ...rest }) => rest);
    };
  }

  /** What went wrong with the request, put where it belongs: at a field, or over the form. */
  function refused(error: unknown) {
    const answered = error instanceof ApiError && error.code !== "unreachable";
    if (!answered) {
      // No answer came back. The list is what says whether the agent is there now.
      onUnknown();
    }
    if (!shown.current) {
      // Nobody is looking at this form any more: what it would say is said to no one.
      return;
    }
    const told: Errors = {};
    let other: string | null = null;
    if (!(error instanceof ApiError) || error.code === "unreachable") {
      other = OUTCOME_NOT_KNOWN;
    } else if (error.code === "invalid") {
      for (const { field, message } of error.fields) {
        const at = FIELD_OF[field];
        if (at) {
          told[at] = message;
        } else {
          other = message;
        }
      }
    } else if (FIELD_OF_CODE[error.code]) {
      told[FIELD_OF_CODE[error.code]] = error.message;
    } else {
      // The service's own sentence: the agent cannot be used, the limit, a busy instance.
      other = error.message;
    }
    setErrors(told);
    setRefusal(other);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    // What surrounds a value, as a pasted address may have, is not part of it.
    const values = { name: name.trim(), url: url.trim() };
    const found = check(values);
    setErrors(found);
    setRefusal(null);
    if (Object.keys(found).length > 0) return;

    inFlight.current = true;
    setSending(true);
    try {
      // One request, and nothing to confirm: an agent added here requires approval.
      const agent = await addAgent(values);
      onSaved(agent, shown.current);
    } catch (error) {
      refused(error);
    } finally {
      inFlight.current = false;
      if (shown.current) {
        setSending(false);
      }
    }
  }

  return (
    <form onSubmit={submit} noValidate className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Add an agent</DialogTitle>
        <DialogDescription>
          An agent Freyja can hand work to. Its card is read at the address
          before it is saved; the agent is sent no message.
        </DialogDescription>
      </DialogHeader>

      {refusal && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}

      <Field
        id={`${id}-name`}
        label="Name"
        hint="Lowercase letters, digits, hyphens, and underscores, starting with a letter; at most 32 characters. A name cannot be changed later."
        error={errors.name}
      >
        <Input
          id={`${id}-name`}
          value={name}
          onChange={(event) => changing("name", setName)(event.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          {...described(`${id}-name`, errors.name)}
        />
      </Field>

      <Field
        id={`${id}-url`}
        label="Address"
        hint="A web address on this machine, such as http://localhost:9998."
        error={errors.url}
      >
        <Input
          id={`${id}-url`}
          value={url}
          onChange={(event) => changing("url", setUrl)(event.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          inputMode="url"
          {...described(`${id}-url`, errors.url)}
        />
      </Field>

      <p role="note" className="rounded-lg border p-3 text-sm">
        Messages to this agent will require approval: Freyja sends it nothing
        until a person has approved the message.
      </p>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        {/* Reading the card can take a while: the button says so and takes no press. */}
        <Button type="submit" disabled={sending} aria-busy={sending}>
          {sending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * The dialog that adds an agent. It keeps nothing once it is sent or closed (FR-004).
 */
export function AgentForm({
  open,
  onOpenChange,
  onSaved,
  onUnknown,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (agent: Agent) => void;
  /** Told when a request got no answer, so that whoever holds the list reads it again. */
  onUnknown: () => void;
}) {
  // A page stays mounted while it is hidden, so what was typed is thrown away by hand: each
  // time the form opens or closes its fields start again.
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
          onCancel={() => change(false)}
          onSaved={(agent, shown) => {
            // Only the form that asked closes itself. An answer that arrives after its form
            // was closed leaves the form that is open now alone; the list is read either way.
            if (shown) {
              change(false);
            }
            onSaved(agent);
          }}
          onUnknown={onUnknown}
        />
      </DialogContent>
    </Dialog>
  );
}
