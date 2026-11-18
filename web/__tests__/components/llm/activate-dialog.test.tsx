import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { ActivateDialog } from "@/components/llm/activate-dialog";
import { ProfileList } from "@/components/llm/profile-list";
import type { LlmProfile, LlmProfileList } from "@/lib/llm";

const ENVIRONMENT: LlmProfile = {
  id: "environment",
  name: "Environment",
  source: "environment",
  provider: "openai",
  model: "env-model",
  base_url: null,
  responses_api: true,
  key_hint: "9f2c",
  active: true,
  usable: true,
  unusable_reason: null,
  last_test: null,
  created_at: null,
  updated_at: null,
};

const SAVED: LlmProfile = {
  ...ENVIRONMENT,
  id: "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b",
  name: "Local vLLM",
  source: "saved",
  model: "served-model",
  active: false,
};

function list(active: "environment" | "saved"): LlmProfileList {
  return {
    profiles: [
      { ...ENVIRONMENT, active: active === "environment" },
      { ...SAVED, active: active === "saved" },
    ],
    active_id: active === "saved" ? SAVED.id : "environment",
    usable_model: true,
    secret_configured: true,
    limit: 50,
  };
}

const confirm = () =>
  fireEvent.click(screen.getByRole("button", { name: "Set active" }));

describe("ActivateDialog", () => {
  it("names the profile and says where existing conversations will go", () => {
    render(
      <ActivateDialog
        profile={SAVED}
        onClose={vi.fn()}
        onActivated={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("Local vLLM");
    expect(dialog.textContent).toMatch(
      /existing conversations will be sent to its endpoint from their next run/i,
    );
  });

  it("shows nothing when no profile is being activated", () => {
    render(
      <ActivateDialog profile={null} onClose={vi.fn()} onActivated={vi.fn()} />,
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("sends nothing when cancelled", async () => {
    const fetchStub = stubFetch({ json: list("saved") });
    const onClose = vi.fn();
    const onActivated = vi.fn();
    render(
      <ActivateDialog
        profile={SAVED}
        onClose={onClose}
        onActivated={onActivated}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(onActivated).not.toHaveBeenCalled();
  });

  it("sends one activation when confirmed and hands back the list", async () => {
    const fetchStub = stubFetch({ json: list("saved") });
    const onClose = vi.fn();
    const onActivated = vi.fn();
    render(
      <ActivateDialog
        profile={SAVED}
        onClose={onClose}
        onActivated={onActivated}
      />,
    );
    confirm();
    confirm();

    await waitFor(() =>
      expect(onActivated).toHaveBeenCalledWith(list("saved")),
    );
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}/activate`);
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    expect(onClose).toHaveBeenCalled();
  });

  it("activates the environment's profile by its own id", async () => {
    const fetchStub = stubFetch({ json: list("environment") });
    render(
      <ActivateDialog
        profile={{ ...ENVIRONMENT, active: false }}
        onClose={vi.fn()}
        onActivated={vi.fn()}
      />,
    );
    confirm();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe(
      "/api/llm/profiles/environment/activate",
    );
  });

  it.each([
    [
      "a refusal",
      {
        status: 409,
        json: {
          detail: {
            code: "profile_unusable",
            message:
              "Profile Local vLLM cannot be used: its key cannot be read.",
          },
        },
      },
      /cannot be used/,
    ],
    [
      "a service that cannot be reached",
      { fails: "Failed to fetch" },
      /cannot be reached/,
    ],
  ] as [
    string,
    Reply,
    RegExp,
  ][])("shows %s and stays open", async (_case, reply, message) => {
    stubFetch(reply);
    const onClose = vi.fn();
    const onActivated = vi.fn();
    render(
      <ActivateDialog
        profile={SAVED}
        onClose={onClose}
        onActivated={onActivated}
      />,
    );
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(message);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(onActivated).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("ActivateDialog, an untested profile", () => {
  const AT = "2026-11-17T09:14:02.118000+00:00";
  const passed = { ok: true, at: AT, step: null, reason: null };
  const failed = {
    ok: false,
    at: AT,
    step: "conversation" as const,
    reason: "Error code: 422 - Input should be a valid string",
  };
  const open = (profile: LlmProfile, onTested = vi.fn()) =>
    render(
      <ActivateDialog
        profile={profile}
        onClose={vi.fn()}
        onActivated={vi.fn()}
        onTested={onTested}
      />,
    );
  const warning = () => screen.queryByRole("note");

  it("says so when the profile has not been tested since its last change", () => {
    open(SAVED);
    expect(warning()?.textContent).toMatch(
      /has not been tested since its last change/,
    );
    expect(screen.getByRole("button", { name: "Test first" })).toBeTruthy();
  });

  it("says the environment's profile has not been tested since the service started", () => {
    open({ ...ENVIRONMENT, active: false });
    expect(warning()?.textContent).toMatch(
      /Environment has not been tested since the service started/,
    );
    expect(warning()?.textContent).not.toMatch(/its last change/);
  });

  it("says so when the profile's last test failed, and at which step", () => {
    open({ ...SAVED, last_test: failed });
    expect(warning()?.textContent).toMatch(
      /last test failed at the conversation/,
    );
    expect(screen.getByRole("button", { name: "Test first" })).toBeTruthy();
  });

  it("says nothing of it when the last test passed", () => {
    open({ ...SAVED, last_test: passed });
    expect(warning()).toBeNull();
    expect(screen.queryByRole("button", { name: "Test first" })).toBeNull();
  });

  it("still lets the operator activate without testing", async () => {
    const fetchStub = stubFetch({ json: list("saved") });
    open(SAVED);
    confirm();
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe(
      `/api/llm/profiles/${SAVED.id}/activate`,
    );
  });

  it("tests first when asked, and drops the warning once the test passes", async () => {
    const fetchStub = stubFetch({ json: { ...passed, recorded: true } });
    const onTested = vi.fn();
    open(SAVED, onTested);
    fireEvent.click(screen.getByRole("button", { name: "Test first" }));

    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/test passed/i);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(fetchStub.mock.calls[0][0]).toBe(
      `/api/llm/profiles/${SAVED.id}/test`,
    );
    expect(onTested).toHaveBeenCalledTimes(1);
    expect(warning()).toBeNull();
    // Nothing was activated by testing.
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Set active" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("keeps warning when the test it ran failed, with the step and the reason", async () => {
    stubFetch({ json: { ...failed, recorded: true } });
    open(SAVED);
    fireEvent.click(screen.getByRole("button", { name: "Test first" }));

    await waitFor(() =>
      expect(warning()?.textContent).toMatch(
        /last test failed at the conversation/,
      ),
    );
    expect(screen.getByRole("alertdialog").textContent).toContain(
      failed.reason,
    );
    expect(
      (screen.getByRole("button", { name: "Set active" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("shows why a test could not be run", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: {
          code: "test_busy",
          message: "This profile is already being tested.",
        },
      },
    });
    open(SAVED);
    fireEvent.click(screen.getByRole("button", { name: "Test first" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/already being tested/);
  });
});

describe("Set active, from the list", () => {
  function renderList() {
    return render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <ProfileList />
      </SWRConfig>,
    );
  }

  const cardOf = (name: string) =>
    screen
      .getByRole("heading", { name })
      .closest('[data-slot="card"]') as HTMLElement;

  it("moves the active mark to the confirmed profile, and only one card has it", async () => {
    const fetchStub = stubFetch(
      { json: list("environment") },
      { json: list("saved") },
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });
    // Only a card that is not active offers it.
    expect(screen.getAllByRole("button", { name: "Set active" })).toHaveLength(
      1,
    );

    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Local vLLM");
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (button) => button.textContent === "Set active",
      ) as HTMLElement,
    );

    await waitFor(() =>
      expect(cardOf("Local vLLM").textContent).toContain("Active"),
    );
    expect(screen.getAllByText("Active")).toHaveLength(1);
    expect(cardOf("Environment").textContent).not.toContain("Active");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    // One read, one activation, and the list read again.
    expect(fetchStub.mock.calls.map(([path]) => path)).toEqual([
      "/api/llm/profiles",
      `/api/llm/profiles/${SAVED.id}/activate`,
      "/api/llm/profiles",
    ]);
  });

  it("reads the list again when the profile to activate is gone", async () => {
    // Another operator deleted it since this page read the list.
    const fetchStub = stubFetch(
      { json: list("environment") },
      {
        status: 404,
        json: {
          detail: {
            code: "profile_not_found",
            message: "There is no profile with this id.",
          },
        },
      },
      {
        json: {
          ...list("environment"),
          profiles: list("environment").profiles.slice(0, 1),
        },
      },
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (button) => button.textContent === "Set active",
      ) as HTMLElement,
    );

    // The refusal is shown, and the card of the profile that is gone goes away.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("There is no profile with this id.");
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Local vLLM" })).toBeNull(),
    );
    expect(fetchStub.mock.calls.map(([path]) => path)).toEqual([
      "/api/llm/profiles",
      `/api/llm/profiles/${SAVED.id}/activate`,
      "/api/llm/profiles",
    ]);
  });

  it("warns in an open confirmation when the list then says the profile's test failed", async () => {
    const AT = "2026-11-17T09:14:02.118000+00:00";
    const withTest = (last: LlmProfile["last_test"]): LlmProfileList => ({
      ...list("environment"),
      profiles: [
        list("environment").profiles[0],
        { ...SAVED, last_test: last },
      ],
    });
    stubFetch(
      { json: withTest({ ok: true, at: AT, step: null, reason: null }) },
      {
        json: withTest({
          ok: false,
          at: "2026-11-18T10:00:00.000000+00:00",
          step: "conversation",
          reason: "Error code: 422 - Input should be a valid string",
        }),
      },
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    // Its last test passed when the confirmation opened: no warning.
    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.querySelector('[role="note"]')).toBeNull();

    // A test that was still running, or run by someone else, then fails, and the list is
    // read again. The confirmation is about the profile as it is now.
    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() =>
      expect(dialog.querySelector('[role="note"]')?.textContent).toMatch(
        /last test failed at the conversation/,
      ),
    );
    expect(screen.getByRole("button", { name: "Test first" })).toBeTruthy();
  });

  it("asks before saving a change to a profile that became the active one while its form was open", async () => {
    const fetchStub = stubFetch(
      { json: list("environment") },
      { json: list("saved") },
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const form = await screen.findByRole("dialog", { name: "Edit Local vLLM" });
    fireEvent.change(screen.getByLabelText("Model id"), {
      target: { value: "newer-model" },
    });
    // Activated from somewhere else, and the list is read again.
    fireEvent.click(screen.getByText("Refresh"));
    // Found by its markup: the form in front hides the cards from a lookup by role.
    const cardText = () =>
      Array.from(document.querySelectorAll('[data-slot="card"]')).find(
        (card) => card.querySelector("h3")?.textContent === "Local vLLM",
      )?.textContent;
    await waitFor(() => expect(cardText()).toContain("Active"));

    fireEvent.click(
      Array.from(form.querySelectorAll("button")).find(
        (button) => button.textContent === "Save",
      ) as HTMLElement,
    );
    // What was typed is still there, and nothing is sent before the confirmation.
    expect(form.querySelector('[role="note"]')?.textContent).toMatch(
      /Local vLLM is the active profile/,
    );
    expect((screen.getByLabelText("Model id") as HTMLInputElement).value).toBe(
      "newer-model",
    );
    expect(
      fetchStub.mock.calls.filter(([, init]) => init?.method === "PATCH"),
    ).toHaveLength(0);
  });

  it.each([
    [
      "a later test failed",
      {
        last_test: {
          ok: false,
          at: "2026-11-18T10:00:00.000000+00:00",
          step: "conversation" as const,
          reason: "Error code: 422 - Input should be a valid string",
        },
      },
      /last test failed at the conversation/,
    ],
    [
      "the profile was changed",
      {
        model: "another-model",
        updated_at: "2026-11-18T10:00:00.000000+00:00",
        last_test: null,
      },
      /has not been tested since its last change/,
    ],
  ] as [
    string,
    Partial<LlmProfile>,
    RegExp,
  ][])("warns again after its own test passed when the list then says %s", async (_case, now, warning) => {
    const AT = "2026-11-17T09:14:02.118000+00:00";
    const passed = { ok: true, at: AT, step: null, reason: null };
    const listed = (saved: Partial<LlmProfile>): LlmProfileList => ({
      ...list("environment"),
      profiles: [list("environment").profiles[0], { ...SAVED, ...saved }],
    });
    stubFetch(
      { json: listed({}) },
      { json: { ...passed, recorded: true } },
      { json: listed({ last_test: passed }) },
      { json: listed(now) },
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });
    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    const dialog = await screen.findByRole("alertdialog");

    // Tested from the confirmation: it passed, and the warning goes.
    fireEvent.click(screen.getByRole("button", { name: "Test first" }));
    await waitFor(() =>
      expect(dialog.querySelector('[role="note"]')).toBeNull(),
    );
    expect(dialog.textContent).toMatch(/Last test passed/);

    // Then the list says otherwise: the confirmation's own result is no longer the last word.
    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() =>
      expect(dialog.querySelector('[role="note"]')?.textContent).toMatch(
        warning,
      ),
    );
    expect(dialog.textContent).not.toMatch(/Last test passed/);
  });

  it("changes nothing when the confirmation is cancelled", async () => {
    const fetchStub = stubFetch({ json: list("environment") });
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });
    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(cardOf("Environment").textContent).toContain("Active");
  });
});
