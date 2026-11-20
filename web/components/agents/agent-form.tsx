"use client";

import {
  type FormEvent,
  type MouseEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
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
import { type Agent, type Approval, addAgent, changeAgent } from "@/lib/agents";
import { ApiError, isGone } from "@/lib/api";

type FieldName = "name" | "url";
type Errors = Partial<Record<FieldName, string>>;

// The service names a refused value by its own field name.
const FIELD_OF: Record<string, FieldName> = { name: "name", url: "url" };

// A refusal that is about one value is said at that value.
const FIELD_OF_CODE: Record<string, FieldName> = {
  name_taken: "name",
  address_taken: "url",
};

// The refusals that say the agent is not what this form has: the list is read again.
const NOT_AS_THE_FORM_HAS_IT = ["agent_changed", "agent_read_only"];

/**
 * What is said when a request that changes something got no answer. The agent may have been
 * saved, or changed, and the answer lost, so this never says that the request failed
 * (FR-004).
 */
const OUTCOME_NOT_KNOWN = {
  added:
    "The request got no answer, so it is not known whether the agent was added. The list is being read again: look for the agent there before saving again.",
  changed:
    "The request got no answer, so it is not known whether the agent was changed. The list is being read again: look at the agent there before saving again.",
};

// The two choices, in the words the cards use, and what each means for a message.
const CHOICES: { value: Approval; label: string }[] = [
  { value: "required", label: "Requires approval" },
  { value: "none", label: "Runs without approval" },
];
const MEANS: Record<Approval, string> = {
  required:
    "Messages to this agent will require approval: Freyja sends it nothing until a person has approved the message.",
  none: "Messages to this agent will be sent without approval: no person sees them first.",
};

// An agent's state and choice as a confirmation words them, inside a sentence.
const STATE_IN_WORDS: Record<Agent["state"], string> = {
  reachable: "reachable",
  unreachable: "unreachable",
  not_tried: "not tried yet",
  not_in_use: "not in use",
};
const CHOICE_IN_WORDS: Record<Approval, string> = {
  required: "requires approval",
  none: "runs without approval",
};

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

/**
 * What confirming means (FR-011). It names the agent, and for a saved agent shows it as the
 * list has it at that moment. Every value is a text node.
 */
function Confirmation({
  name,
  to,
  listed,
  fromList,
  noticeRef,
}: {
  name: string;
  /** The choice that is about to be saved. */
  to: Approval;
  /** The saved agent that is being changed, as it is shown; none when adding. */
  listed?: Agent;
  /** Whether `listed` is the list's copy, or the form's own while the list is not known. */
  fromList: boolean;
  noticeRef: React.Ref<HTMLDivElement>;
}) {
  return (
    <div
      ref={noticeRef}
      role="note"
      aria-label="Confirmation"
      tabIndex={-1}
      className="grid gap-2 break-words rounded-lg border border-amber-500/50 p-3 text-sm outline-none"
    >
      {listed === undefined ? (
        <p>
          Add {name} to run without approval? Nothing Freyja sends to {name}{" "}
          will be seen by a person first.
        </p>
      ) : to === "none" ? (
        <p>
          Let {name} run without approval? Every message to {name} will be sent
          without a person seeing it first, from the next run. A proposal for{" "}
          {name} that is already waiting for a decision can then only be
          approved.
        </p>
      ) : (
        <p>
          Require approval for {name}? This holds at once, also for runs in
          progress. A paused step that holds a message to {name} that needed no
          approval cannot be continued until the choice is changed back or the
          agent is removed.
        </p>
      )}
      {listed !== undefined && (
        <p className="text-muted-foreground">
          {fromList
            ? "As the list has it now:"
            : "The list cannot be read now. As it was when this form opened:"}{" "}
          {listed.name}, at {listed.url}, {CHOICE_IN_WORDS[listed.approval]},{" "}
          {STATE_IN_WORDS[listed.state]}, {listed.pending_tasks} pending{" "}
          {listed.pending_tasks === 1 ? "task" : "tasks"}.
        </p>
      )}
    </div>
  );
}

function Fields({
  agent,
  live,
  onCancel,
  onSaved,
  onUnknown,
  onStale,
}: {
  /** The saved agent this form changes; none for a form that adds one. */
  agent?: Agent;
  /**
   * That agent as the list has it now: null when the list no longer has it, and undefined
   * while the list is not known.
   */
  live?: Agent | null;
  onCancel: () => void;
  /** Told that the agent was saved, and whether this form is still the one on the page. */
  onSaved: (agent: Agent, shown: boolean) => void;
  onUnknown: () => void;
  onStale?: () => void;
}) {
  const id = useId();
  // The agent as it was when this form opened. Its fields are filled from it once, and the
  // version it sends is this one, whatever the list shows later: a form handed the list's
  // version would send earlier values under a later version, which is what the version is
  // there to stop (FR-012, research R14).
  const [opened] = useState(agent);
  // What is typed lives here and nowhere else: it goes when the form is sent or closed.
  const [name, setName] = useState(opened?.name ?? "");
  const [url, setUrl] = useState(opened?.url ?? "");
  const [approval, setApproval] = useState<Approval>(
    opened?.approval ?? "required",
  );
  const [errors, setErrors] = useState<Errors>({});
  const [refusal, setRefusal] = useState<string | null>(null);
  // The refusal says that the agent is not what this form has. Once the list shows as much,
  // the form says so in its own words, with what to do, and not twice.
  const [refusedAsStale, setRefusedAsStale] = useState(false);
  // Save was pressed with nothing changed: nothing was sent, and the form says so.
  const [unchanged, setUnchanged] = useState(false);
  // What a change means is confirmed first, and so is adding without approval (FR-011).
  const [confirming, setConfirming] = useState(false);
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

  // The list was read again while this form was open, and the agent is no longer the one
  // the fields were filled from. Nothing is saved from such a form, and what it was asking
  // is taken back. What the form learned this way it keeps until it is closed: a later
  // reading of the list that fails makes the list unknown, not the agent as it was.
  const [found, setFound] = useState<"gone" | "later" | null>(null);
  const seen =
    opened === undefined || live === undefined
      ? null
      : live === null
        ? "gone"
        : live.version !== opened.version
          ? "later"
          : null;
  if (seen !== null && seen !== found) {
    // Kept during render, so that no render shows the form as it was before.
    setFound(seen);
  }
  const outdated = seen ?? found;
  const gone = outdated === "gone";
  const stale = outdated !== null;
  const asking = confirming && !stale;

  const notice = useRef<HTMLDivElement>(null);
  // What confirming means is read out, and a key pressed again lands on the notice, where it
  // does nothing, and not on a button.
  useEffect(() => {
    if (asking) notice.current?.focus();
  }, [asking]);

  // Changing a value takes back what was said about it, and what was being asked.
  function changing(field: FieldName, set: (value: string) => void) {
    return (value: string) => {
      set(value);
      setConfirming(false);
      setErrors(({ [field]: _gone, ...rest }) => rest);
    };
  }

  function choose(value: Approval) {
    setApproval(value);
    setConfirming(false);
    setUnchanged(false);
  }

  /** What went wrong with the request, put where it belongs: at a field, or over the form. */
  function refused(error: unknown) {
    const answered = error instanceof ApiError && error.code !== "unreachable";
    const notAsItWas =
      answered &&
      opened !== undefined &&
      (isGone(error) || NOT_AS_THE_FORM_HAS_IT.includes(error.code));
    if (!answered) {
      // No answer came back. The list is what says whether the agent is there, and how.
      onUnknown();
    } else if (notAsItWas) {
      // The service says the agent is not what this form has: the list says what it is.
      onStale?.();
    }
    if (!shown.current) {
      // Nobody is looking at this form any more: what it would say is said to no one.
      return;
    }
    const told: Errors = {};
    let other: string | null = null;
    if (!(error instanceof ApiError) || error.code === "unreachable") {
      other =
        opened === undefined
          ? OUTCOME_NOT_KNOWN.added
          : OUTCOME_NOT_KNOWN.changed;
    } else if (error.code === "invalid") {
      for (const { field, message } of error.fields) {
        // A saved agent's name and address are not this form's to change.
        const at = opened === undefined ? FIELD_OF[field] : undefined;
        if (at) {
          told[at] = message;
        } else {
          other = message;
        }
      }
    } else if (opened === undefined && FIELD_OF_CODE[error.code]) {
      told[FIELD_OF_CODE[error.code]] = error.message;
    } else {
      // The service's own sentence: the agent cannot be used, the limit, a busy instance,
      // an agent that was changed or removed since.
      other = error.message;
    }
    setErrors(told);
    setRefusal(other);
    setRefusedAsStale(notAsItWas);
    // Saving again is asked about again.
    setConfirming(false);
  }

  /** The values of a new agent, or nothing when the form itself finds them wrong. */
  function checked() {
    // What surrounds a value, as a pasted address may have, is not part of it.
    const values = { name: name.trim(), url: url.trim() };
    const found = check(values);
    setErrors(found);
    setRefusal(null);
    return Object.keys(found).length > 0 ? null : values;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || stale) return;
    if (opened !== undefined) {
      setRefusal(null);
      if (approval === opened.approval) {
        // Nothing is sent for nothing.
        setUnchanged(true);
        return;
      }
      // Nothing is sent: the form says what the change means. Saving again, by Enter or by
      // the second press of a double press, changes nothing: only Confirm sends it.
      setConfirming(true);
      return;
    }
    const values = checked();
    if (values === null) return;
    if (approval === "none") {
      setConfirming(true);
      return;
    }
    // One request, and nothing to confirm: the agent will require approval.
    await send(() => addAgent(values));
  }

  async function confirm(event: MouseEvent) {
    // The second press of a double press on Save lands on this button, which took Save's
    // place. That was not a decision; a press of its own is.
    if (event.detail > 1 || inFlight.current || stale) return;
    if (opened !== undefined) {
      // The version the fields were filled from, and only the value that changed.
      const { id: agentId, version } = opened;
      await send(async () => {
        const answer = await changeAgent(agentId, {
          version: version ?? 0,
          approval,
        });
        return answer.agent;
      });
      return;
    }
    const values = checked();
    if (values !== null) {
      await send(() => addAgent({ ...values, approval }));
    }
  }

  async function send(request: () => Promise<Agent>) {
    inFlight.current = true;
    setSending(true);
    try {
      onSaved(await request(), shown.current);
    } catch (error) {
      refused(error);
    } finally {
      inFlight.current = false;
      if (shown.current) {
        setSending(false);
      }
    }
  }

  const editing = opened !== undefined;
  // The agent a confirmation shows: as the list has it now, or as the form has it while the
  // list is not known.
  const listed = live ?? opened;

  return (
    <form onSubmit={submit} noValidate className="grid gap-4">
      <DialogHeader>
        <DialogTitle>
          {editing ? `Edit ${opened.name}` : "Add an agent"}
        </DialogTitle>
        <DialogDescription>
          {editing
            ? "Whether messages to this agent need a person's approval. Changing it sends the agent nothing."
            : "An agent Freyja can hand work to. Its card is read at the address before it is saved; the agent is sent no message."}
        </DialogDescription>
      </DialogHeader>

      {refusal && !(stale && refusedAsStale) && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {refusal}
        </p>
      )}

      {stale && (
        <p
          role="alert"
          className="break-words rounded-lg border border-destructive/40 p-3 text-sm"
        >
          {gone
            ? `${opened?.name} is no longer in the list: it was removed elsewhere while this form was open. Nothing can be saved from this form.`
            : `${opened?.name} was changed elsewhere while this form was open, so nothing can be saved from it. Close the form and open it again to change the agent as it is now.`}
        </p>
      )}

      <Field
        id={`${id}-name`}
        label="Name"
        hint={
          editing
            ? "A name cannot be changed."
            : "Lowercase letters, digits, hyphens, and underscores, starting with a letter; at most 32 characters. A name cannot be changed later."
        }
        error={errors.name}
      >
        <Input
          id={`${id}-name`}
          value={name}
          readOnly={editing}
          onChange={(event) => {
            if (!editing) changing("name", setName)(event.target.value);
          }}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          {...described(`${id}-name`, errors.name)}
        />
      </Field>

      <Field
        id={`${id}-url`}
        label="Address"
        hint={
          editing
            ? "The address as the service keeps it. It is not changed in this form."
            : "A web address on this machine, such as http://localhost:9998."
        }
        error={errors.url}
      >
        <Input
          id={`${id}-url`}
          value={url}
          readOnly={editing}
          onChange={(event) => {
            if (!editing) changing("url", setUrl)(event.target.value);
          }}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          inputMode="url"
          {...described(`${id}-url`, errors.url)}
        />
      </Field>

      <fieldset
        className="grid gap-2"
        aria-describedby={`${id}-approval-means`}
      >
        <legend className="mb-1.5 text-sm font-medium">Approval</legend>
        {CHOICES.map(({ value, label }) => (
          <label key={value} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name={`${id}-approval`}
              value={value}
              checked={approval === value}
              onChange={() => choose(value)}
            />
            {label}
          </label>
        ))}
        <p
          id={`${id}-approval-means`}
          className="rounded-lg border p-3 text-sm"
        >
          {MEANS[approval]}
        </p>
      </fieldset>

      {unchanged && !stale && (
        <output className="text-sm text-muted-foreground">
          Nothing is changed, so nothing was sent.
        </output>
      )}

      {asking && (
        <Confirmation
          name={editing ? opened.name : name.trim()}
          to={approval}
          listed={editing ? listed : undefined}
          fromList={live != null}
          noticeRef={notice}
        />
      )}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        {asking ? (
          // A button of its own, not the form's: Enter in a field does not confirm.
          <Button
            key="confirm"
            type="button"
            disabled={sending}
            aria-busy={sending}
            onClick={confirm}
          >
            {sending ? "Saving…" : "Confirm"}
          </Button>
        ) : (
          // Reading the card can take a while: the button says so and takes no press.
          <Button
            key="save"
            type="submit"
            disabled={sending || stale}
            aria-busy={sending}
          >
            {sending ? "Saving…" : "Save"}
          </Button>
        )}
      </DialogFooter>
    </form>
  );
}

/**
 * The dialog that adds an agent, or changes the approval choice of the saved `agent` it is
 * given. It keeps nothing once it is sent or closed (FR-004).
 */
export function AgentForm({
  open,
  onOpenChange,
  onSaved,
  onUnknown,
  onStale,
  agent,
  live,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (agent: Agent) => void;
  /** Told when a request got no answer, so that whoever holds the list reads it again. */
  onUnknown: () => void;
  /** Told when the service says the agent being changed is no longer as the form has it. */
  onStale?: () => void;
  /** The saved agent to change, as it is when its form is opened. None to add an agent. */
  agent?: Agent;
  /**
   * The agent being changed as the list has it now: null when the list no longer has it,
   * undefined while the list is not known. A confirmation shows this one (FR-011).
   */
  live?: Agent | null;
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
          // Another agent's form is another form: its fields are filled anew.
          key={`${session}:${agent?.id ?? ""}`}
          agent={agent}
          live={live}
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
          onStale={onStale}
        />
      </DialogContent>
    </Dialog>
  );
}
