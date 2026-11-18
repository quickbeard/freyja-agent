import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { ProfileForm } from "@/components/llm/profile-form";
import type { LlmProfile } from "@/lib/llm";

const KEY = "sk-recognizable-key-0001";

const SAVED: LlmProfile = {
  id: "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b",
  name: "Local vLLM",
  source: "saved",
  provider: "openai",
  model: "served-model",
  base_url: "http://10.0.0.5:31000/v1",
  responses_api: false,
  key_hint: "0001",
  active: false,
  usable: true,
  unusable_reason: null,
  last_test: null,
  created_at: "2026-11-17T09:10:44.501000+00:00",
  updated_at: "2026-11-17T09:10:44.501000+00:00",
};
const CREATED: Reply = { status: 201, json: SAVED };

// The form as the list holds it: a control opens it, and it closes itself.
function Harness({
  onSaved,
  profile,
}: {
  onSaved?: (profile: LlmProfile) => void;
  profile?: LlmProfile;
}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open the form
      </button>
      <ProfileForm
        open={open}
        onOpenChange={setOpen}
        onSaved={onSaved ?? (() => {})}
        profile={profile}
      />
    </>
  );
}

const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement;

function type(name: string, value: string) {
  fireEvent.change(field(name), { target: { value } });
}

// The provider is a listbox: it is opened and chosen from with the keyboard.
async function choose(provider: string) {
  const trigger = screen.getByRole("combobox", { name: "Provider" });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  const option = await screen.findByRole("option", { name: provider });
  fireEvent.keyDown(option, { key: "Enter" });
  await waitFor(() => expect(trigger.textContent).toContain(provider));
}

function fill(values: Record<string, string> = {}) {
  const given = {
    Name: "Local vLLM",
    "Model id": "served-model",
    "Base URL": "http://10.0.0.5:31000/v1",
    "API key": KEY,
    ...values,
  };
  for (const [name, value] of Object.entries(given)) {
    type(name, value);
  }
}

const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

/** What the form says is wrong with a field, or null when it says nothing. */
function errorAt(name: string): string | null {
  const input = field(name);
  if (input.getAttribute("aria-invalid") !== "true") return null;
  const id = input.getAttribute("aria-describedby");
  return id ? (document.getElementById(id)?.textContent ?? null) : null;
}

function sent(fetchStub: ReturnType<typeof stubFetch>, call = 0) {
  const [path, init] = fetchStub.mock.calls[call];
  return { path, method: init?.method, body: JSON.parse(String(init?.body)) };
}

describe("ProfileForm, adding", () => {
  it("asks for a name, a provider, a model id, a base URL, and a key", () => {
    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Add a profile" })).toBeTruthy();
    expect(field("Name").value).toBe("");
    expect(field("Model id").value).toBe("");
    expect(field("Base URL").value).toBe("");
    expect(field("Base URL").required).toBe(false);
    expect(
      screen.getByRole("combobox", { name: "Provider" }).textContent,
    ).toContain("OpenAI-compatible");
    // The key is typed unseen, and the browser is not asked to remember it.
    expect(field("API key").type).toBe("password");
    expect(field("API key").getAttribute("autocomplete")).toBe("off");
  });

  it("offers the choice of API for an OpenAI-compatible endpoint only, on at first", async () => {
    render(<Harness />);
    const choice = () =>
      screen.queryByRole("switch", { name: /Responses API/ });
    expect(choice()?.getAttribute("aria-checked")).toBe("true");

    await choose("Anthropic");
    expect(choice()).toBeNull();
    await choose("OpenRouter");
    expect(choice()).toBeNull();
    await choose("OpenAI-compatible");
    expect(choice()?.getAttribute("aria-checked")).toBe("true");
  });

  it("says which values are missing and sends nothing", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    save();

    expect(errorAt("Name")).toMatch(/name/i);
    expect(errorAt("Model id")).toMatch(/model/i);
    expect(errorAt("API key")).toMatch(/key/i);
    expect(errorAt("Base URL")).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it.each([
    "not a web address",
    "ftp://files.example/v1",
    "10.0.0.5:31000/v1",
  ])("says a base URL of %s is not a web address and sends nothing", (address) => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill({ "Base URL": address });
    save();

    expect(errorAt("Base URL")).toMatch(/http/);
    expect(errorAt("Name")).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("clears what it said about a field once the field is changed", () => {
    stubFetch(CREATED);
    render(<Harness />);
    save();
    expect(errorAt("Name")).not.toBeNull();
    type("Name", "Local vLLM");
    expect(errorAt("Name")).toBeNull();
    expect(errorAt("Model id")).not.toBeNull();
  });

  it("shows a name that is taken at the name, and sends nothing more", async () => {
    const taken = "Another profile has this name. Choose a different name.";
    const fetchStub = stubFetch({
      status: 409,
      json: { detail: { code: "name_taken", message: taken } },
    });
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    save();

    await waitFor(() => expect(errorAt("Name")).toBe(taken));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
    // The form stays open with what was typed, so only the name needs changing.
    expect(field("Model id").value).toBe("served-model");
    expect(field("API key").value).toBe(KEY);
  });

  it("refuses a name longer than 64 characters itself, and sends nothing", () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill({ Name: "n".repeat(65) });
    save();
    expect(errorAt("Name")).toBe("A name has at most 64 characters.");
    expect(fetchStub).not.toHaveBeenCalled();

    // Exactly 64 is a name.
    type("Name", "n".repeat(64));
    save();
    expect(errorAt("Name")).toBeNull();
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("shows a refused value the form has no field for over the form", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            type: "extra_forbidden",
            loc: ["body", "surprise"],
            msg: "Extra inputs are not permitted",
          },
        ],
      },
    });
    render(<Harness />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Extra inputs are not permitted");
    for (const name of ["Name", "Model id", "Base URL", "API key"]) {
      expect(errorAt(name)).toBeNull();
    }
  });

  it("shows a value the service refuses at its field", async () => {
    stubFetch({
      status: 422,
      json: {
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "model"],
            msg: "String should have at most 200 characters",
          },
          {
            type: "value_error",
            loc: ["body", "base_url"],
            msg: "Value error, must be an http or https address with a host",
          },
        ],
      },
    });
    render(<Harness />);
    fill();
    save();

    await waitFor(() =>
      expect(errorAt("Model id")).toBe(
        "String should have at most 200 characters",
      ),
    );
    expect(errorAt("Base URL")).toMatch(/http or https address/);
    expect(errorAt("Name")).toBeNull();
  });

  it.each([
    [
      "the service has no secret",
      {
        status: 409,
        json: {
          detail: {
            code: "secret_not_configured",
            message: "No profile can be saved: set FREYJA_SECRET_KEY.",
          },
        },
      },
      /FREYJA_SECRET_KEY/,
    ],
    [
      "the service cannot be reached",
      { fails: "Failed to fetch" },
      /cannot be reached/,
    ],
  ] as [
    string,
    Reply,
    RegExp,
  ][])("says so when %s, and stays open", async (_case, reply, message) => {
    stubFetch(reply);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    save();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(message);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(KEY);
  });

  it("sends the values once and hands back the saved profile", async () => {
    const fetchStub = stubFetch(CREATED);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill({ Name: "  Local vLLM ", "Model id": " served-model " });
    fireEvent.click(screen.getByRole("switch", { name: /Responses API/ }));
    save();
    save();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(SAVED));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub)).toEqual({
      path: "/api/llm/profiles",
      method: "POST",
      body: {
        name: "Local vLLM",
        provider: "openai",
        model: "served-model",
        base_url: "http://10.0.0.5:31000/v1",
        api_key: KEY,
        responses_api: false,
      },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends no base URL when none was given, and no choice of API where there is none", async () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    await choose("Anthropic");
    fill({ "Base URL": "  " });
    save();

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(sent(fetchStub).body).toEqual({
      name: "Local vLLM",
      provider: "anthropic",
      model: "served-model",
      base_url: null,
      api_key: KEY,
    });
  });

  it("opens empty again after it is closed, the key included", async () => {
    const fetchStub = stubFetch(CREATED);
    render(<Harness />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fetchStub).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(field("API key").value).toBe("");
    expect(field("Name").value).toBe("");
    expect(field("Model id").value).toBe("");
  });

  it("opens empty again after it is sent, and writes nothing to the browser's storage", async () => {
    stubFetch(CREATED);
    render(<Harness />);
    fill();
    save();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Open the form" }));
    await screen.findByRole("dialog");
    expect(field("API key").value).toBe("");
    expect(field("Name").value).toBe("");
    expect(document.body.textContent).not.toContain(KEY);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("ProfileForm, testing before saving", () => {
  const AT = "2026-11-17T09:14:02.118000+00:00";
  const passed: Reply = {
    json: { ok: true, at: AT, step: null, reason: null, recorded: false },
  };
  const test = () =>
    fireEvent.click(screen.getByRole("button", { name: "Test" }));

  it("sends the form's values to be tested, shows the result, and saves nothing", async () => {
    const fetchStub = stubFetch(passed);
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill({ Name: "" });
    test();

    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/test passed/i);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    // The values as they are in the form: no name is needed to test them.
    expect(sent(fetchStub)).toEqual({
      path: "/api/llm/test",
      method: "POST",
      body: {
        provider: "openai",
        model: "served-model",
        base_url: "http://10.0.0.5:31000/v1",
        api_key: KEY,
        responses_api: true,
      },
    });
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(errorAt("Name")).toBeNull();
    // The key is still in the form, unseen, to be saved with.
    expect(field("API key").value).toBe(KEY);
    expect(document.body.textContent).not.toContain(KEY);
  });

  it("shows a failed test with the step that failed and the reason", async () => {
    stubFetch({
      json: {
        ok: false,
        at: AT,
        step: "conversation",
        reason: "Error code: 422 - Input should be a valid string",
        recorded: false,
      },
    });
    render(<Harness />);
    fill();
    test();

    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/test failed at the conversation/i);
    expect(result.textContent).toContain("Input should be a valid string");
  });

  it("says what is missing for a test and sends nothing", () => {
    const fetchStub = stubFetch(passed);
    render(<Harness />);
    type("Base URL", "not a web address");
    test();

    expect(errorAt("Model id")).toMatch(/model/i);
    expect(errorAt("API key")).toMatch(/key/i);
    expect(errorAt("Base URL")).toMatch(/http/);
    expect(errorAt("Name")).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("takes back a result once a value is changed", async () => {
    stubFetch(passed);
    render(<Harness />);
    fill();
    test();
    await screen.findByRole("status");
    type("Model id", "another-model");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows why a test could not be run, and can still save", async () => {
    const fetchStub = stubFetch(
      {
        status: 409,
        json: {
          detail: {
            code: "test_busy",
            message: "4 tests are already running on this instance.",
          },
        },
      },
      CREATED,
    );
    const onSaved = vi.fn();
    render(<Harness onSaved={onSaved} />);
    fill();
    test();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/4 tests are already running/);

    save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(SAVED));
    expect(sent(fetchStub, 1).path).toBe("/api/llm/profiles");
  });
});

describe("ProfileForm, editing", () => {
  const AT = "2026-11-17T09:14:02.118000+00:00";
  const NEW_KEY = "sk-another-key-for-tests-0002";
  const changed: LlmProfile = { ...SAVED, model: "newer-model" };
  const answer: Reply = { json: changed };

  it("opens with the profile's values and an empty key", () => {
    render(<Harness profile={SAVED} />);
    expect(
      screen.getByRole("dialog", { name: "Edit Local vLLM" }),
    ).toBeTruthy();
    expect(field("Name").value).toBe("Local vLLM");
    expect(field("Model id").value).toBe("served-model");
    expect(field("Base URL").value).toBe("http://10.0.0.5:31000/v1");
    expect(
      screen.getByRole("combobox", { name: "Provider" }).textContent,
    ).toContain("OpenAI-compatible");
    expect(
      screen
        .getByRole("switch", { name: /Responses API/ })
        .getAttribute("aria-checked"),
    ).toBe("false");
    // The stored key is never sent to the page, so there is nothing to show.
    expect(field("API key").value).toBe("");
    expect(field("API key").placeholder).toMatch(/keep/i);
  });

  it("opens with no base URL and no choice of API for a profile that has none", () => {
    render(
      <Harness
        profile={{
          ...SAVED,
          provider: "anthropic",
          base_url: null,
          responses_api: null,
        }}
      />,
    );
    expect(field("Base URL").value).toBe("");
    expect(screen.queryByRole("switch", { name: /Responses API/ })).toBeNull();
  });

  it("sends no key when the key is left empty, so the stored one is kept", async () => {
    const fetchStub = stubFetch(answer);
    const onSaved = vi.fn();
    render(<Harness profile={SAVED} onSaved={onSaved} />);
    type("Model id", "newer-model");
    save();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(changed));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub)).toEqual({
      path: `/api/llm/profiles/${SAVED.id}`,
      method: "PATCH",
      body: {
        name: "Local vLLM",
        provider: "openai",
        model: "newer-model",
        base_url: "http://10.0.0.5:31000/v1",
        responses_api: false,
      },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends a key that was typed, to replace the stored one", async () => {
    const fetchStub = stubFetch(answer);
    render(<Harness profile={SAVED} />);
    type("API key", NEW_KEY);
    type("Base URL", "");
    save();

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(sent(fetchStub).body).toEqual({
      name: "Local vLLM",
      provider: "openai",
      model: "served-model",
      base_url: null,
      responses_api: false,
      api_key: NEW_KEY,
    });
  });

  it("still needs a name and a model id, and no key", () => {
    const fetchStub = stubFetch(answer);
    render(<Harness profile={SAVED} />);
    type("Name", " ");
    type("Model id", "");
    save();

    expect(errorAt("Name")).toMatch(/name/i);
    expect(errorAt("Model id")).toMatch(/model/i);
    expect(errorAt("API key")).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("shows a name that is taken at the name", async () => {
    const taken = "Another profile has this name. Choose a different name.";
    stubFetch({
      status: 409,
      json: { detail: { code: "name_taken", message: taken } },
    });
    render(<Harness profile={SAVED} />);
    type("Name", "Other");
    save();
    await waitFor(() => expect(errorAt("Name")).toBe(taken));
  });

  it("saves a profile that is not active without asking", async () => {
    const fetchStub = stubFetch(answer);
    render(<Harness profile={SAVED} />);
    type("Model id", "newer-model");
    save();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("asks before changing the active profile, and sends nothing until confirmed", async () => {
    const fetchStub = stubFetch({ json: { ...changed, active: true } });
    const onSaved = vi.fn();
    render(<Harness profile={{ ...SAVED, active: true }} onSaved={onSaved} />);
    type("Model id", "newer-model");
    save();

    // It says what confirming means.
    const note = screen.getByRole("note");
    expect(note.textContent).toContain("Local vLLM");
    expect(note.textContent).toMatch(/applies from the next run/);
    expect(fetchStub).not.toHaveBeenCalled();

    // Going back sends nothing and leaves the form as it was.
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.queryByRole("note")).toBeNull();
    expect(field("Model id").value).toBe("newer-model");
    expect(fetchStub).not.toHaveBeenCalled();

    save();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(sent(fetchStub).method).toBe("PATCH");
    expect(sent(fetchStub).body.model).toBe("newer-model");
  });

  it("sends nothing when Save is pressed twice in a row on the active profile", () => {
    const fetchStub = stubFetch({ json: { ...changed, active: true } });
    render(<Harness profile={{ ...SAVED, active: true }} />);
    type("Model id", "newer-model");
    const pressed = screen.getByRole("button", { name: "Save" });
    fireEvent.click(pressed, { detail: 1 });
    // The second press of a double press lands where the first one did.
    fireEvent.click(pressed, { detail: 2 });
    expect(screen.getByRole("note")).toBeTruthy();
    expect(fetchStub).not.toHaveBeenCalled();

    // Nor does it when it lands on the button that confirms, as part of the same double
    // press: confirming takes a press of its own.
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }), {
      detail: 2,
    });
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.getByRole("note")).toBeTruthy();
  });

  it("sends nothing when Enter is pressed twice in a field of the active profile", () => {
    const fetchStub = stubFetch({ json: { ...changed, active: true } });
    render(<Harness profile={{ ...SAVED, active: true }} />);
    type("Model id", "newer-model");
    const form = field("Model id").closest("form") as HTMLFormElement;
    fireEvent.submit(form);
    expect(screen.getByRole("note")).toBeTruthy();
    fireEvent.submit(form);
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.getByRole("note")).toBeTruthy();
  });

  it("does not ask when what the form says about the active profile is wrong", () => {
    const fetchStub = stubFetch(answer);
    render(<Harness profile={{ ...SAVED, active: true }} />);
    type("Model id", "");
    save();
    expect(errorAt("Model id")).toMatch(/model/i);
    expect(screen.queryByRole("note")).toBeNull();
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("tests with the stored key when the key is left empty", async () => {
    const fetchStub = stubFetch({
      json: { ok: true, at: AT, step: null, reason: null, recorded: false },
    });
    render(<Harness profile={SAVED} />);
    type("Model id", "newer-model");
    fireEvent.click(screen.getByRole("button", { name: "Test" }));

    await screen.findByRole("status");
    // The profile's id in place of a key: the service uses the key it has (FR-017).
    expect(sent(fetchStub)).toEqual({
      path: "/api/llm/test",
      method: "POST",
      body: {
        provider: "openai",
        model: "newer-model",
        base_url: "http://10.0.0.5:31000/v1",
        responses_api: false,
        profile_id: SAVED.id,
      },
    });
    expect(errorAt("API key")).toBeNull();
  });

  it("tests with a key that was typed, and not the stored one", async () => {
    const fetchStub = stubFetch({
      json: { ok: true, at: AT, step: null, reason: null, recorded: false },
    });
    render(<Harness profile={SAVED} />);
    type("API key", NEW_KEY);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));

    await screen.findByRole("status");
    expect(sent(fetchStub).body.api_key).toBe(NEW_KEY);
    expect("profile_id" in sent(fetchStub).body).toBe(false);
  });
});

describe("ProfileForm, a profile whose key cannot be read", () => {
  const NEW_KEY = "sk-another-key-for-tests-0002";
  const unreadable: LlmProfile = {
    ...SAVED,
    usable: false,
    unusable_reason: "key_unreadable",
  };

  it("says the key has to be entered again", () => {
    render(<Harness profile={unreadable} />);
    expect(screen.getByRole("note").textContent).toMatch(
      /stored key cannot be read.*Enter the key again/,
    );
    expect(field("API key").placeholder).toMatch(/Enter the key again/);
  });

  it("does not save or test without the key, since there is none to keep", () => {
    const fetchStub = stubFetch({ json: SAVED });
    render(<Harness profile={unreadable} />);
    save();
    expect(errorAt("API key")).toMatch(/again/);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(errorAt("API key")).toMatch(/again/);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("sends the key that was entered, which makes the profile usable", async () => {
    const usable = { ...SAVED, key_hint: "0002" };
    const fetchStub = stubFetch({ json: usable });
    const onSaved = vi.fn();
    render(<Harness profile={unreadable} onSaved={onSaved} />);
    type("API key", NEW_KEY);
    save();

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(usable));
    expect(sent(fetchStub).method).toBe("PATCH");
    expect(sent(fetchStub).body.api_key).toBe(NEW_KEY);
  });

  it("says nothing of the kind for a profile whose key can be read", () => {
    render(<Harness profile={SAVED} />);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
