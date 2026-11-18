import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { DeleteDialog } from "@/components/llm/delete-dialog";
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
  created_at: "2026-11-17T09:10:44.501000+00:00",
  updated_at: "2026-11-17T09:10:44.501000+00:00",
};

const GONE: Reply = { status: 204, text: "" };
const ACTIVE =
  "Profile Local vLLM is the active one and cannot be deleted. Activate another profile first.";

function list(...profiles: LlmProfile[]): Reply {
  const body: LlmProfileList = {
    profiles,
    active_id: profiles.find((profile) => profile.active)?.id ?? "environment",
    usable_model: true,
    secret_configured: true,
    limit: 50,
  };
  return { json: body };
}

const confirm = () =>
  fireEvent.click(
    Array.from(screen.getByRole("alertdialog").querySelectorAll("button")).find(
      (button) => button.textContent === "Delete",
    ) as HTMLElement,
  );

describe("DeleteDialog", () => {
  it("names the profile and says it cannot be undone", () => {
    render(
      <DeleteDialog profile={SAVED} onClose={vi.fn()} onDeleted={vi.fn()} />,
    );
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("Delete Local vLLM?");
    expect(dialog.textContent).toMatch(/cannot be undone/);
  });

  it("shows nothing when no profile is being deleted", () => {
    render(
      <DeleteDialog profile={null} onClose={vi.fn()} onDeleted={vi.fn()} />,
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("sends nothing when cancelled", async () => {
    const fetchStub = stubFetch(GONE);
    const onClose = vi.fn();
    const onDeleted = vi.fn();
    render(
      <DeleteDialog profile={SAVED} onClose={onClose} onDeleted={onDeleted} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(fetchStub).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("deletes once when confirmed, however often it is pressed", async () => {
    const fetchStub = stubFetch(GONE);
    const onClose = vi.fn();
    const onDeleted = vi.fn();
    render(
      <DeleteDialog profile={SAVED} onClose={onClose} onDeleted={onDeleted} />,
    );
    confirm();
    confirm();

    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}`);
    expect(init?.method).toBe("DELETE");
    expect(onClose).toHaveBeenCalled();
  });

  it.each([
    [
      "the refusal for the active profile, as the service worded it",
      {
        status: 409,
        json: { detail: { code: "profile_active", message: ACTIVE } },
      },
      ACTIVE,
    ],
    [
      "that the service cannot be reached",
      { fails: "Failed to fetch" },
      "The Freyja service cannot be reached. Check that it is running, then try again.",
    ],
  ] as [
    string,
    Reply,
    string,
  ][])("shows %s, and stays open", async (_case, reply, message) => {
    stubFetch(reply);
    const onClose = vi.fn();
    const onDeleted = vi.fn();
    render(
      <DeleteDialog profile={SAVED} onClose={onClose} onDeleted={onDeleted} />,
    );
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("Edit and Delete, from the list", () => {
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
  const buttonsOf = (name: string) =>
    Array.from(cardOf(name).querySelectorAll("button")).map(
      (button) => button.textContent,
    );

  it("offers them on a saved profile's card and not on the environment's", async () => {
    stubFetch(list(ENVIRONMENT, SAVED));
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });
    expect(buttonsOf("Local vLLM")).toEqual([
      "Test",
      "Set active",
      "Edit",
      "Delete",
    ]);
    expect(buttonsOf("Environment")).toEqual(["Test"]);
  });

  it("removes the card once the deletion is confirmed", async () => {
    const fetchStub = stubFetch(
      list(ENVIRONMENT, SAVED),
      GONE,
      list(ENVIRONMENT),
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Delete Local vLLM?");
    confirm();

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Local vLLM" })).toBeNull(),
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "DELETE", "GET"]);
  });

  it("reads the list again when the profile to delete is already gone", async () => {
    const fetchStub = stubFetch(
      list(ENVIRONMENT, SAVED),
      {
        status: 404,
        json: {
          detail: {
            code: "profile_not_found",
            message: "There is no profile with this id.",
          },
        },
      },
      list(ENVIRONMENT),
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await screen.findByRole("alertdialog");
    confirm();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("There is no profile with this id.");
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Local vLLM" })).toBeNull(),
    );
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "DELETE", "GET"]);
  });

  it("reads the list again when the profile being changed is gone", async () => {
    const fetchStub = stubFetch(
      list(ENVIRONMENT, SAVED),
      {
        status: 404,
        json: {
          detail: {
            code: "profile_not_found",
            message: "There is no profile with this id.",
          },
        },
      },
      list(ENVIRONMENT),
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const form = await screen.findByRole("dialog", { name: "Edit Local vLLM" });
    fireEvent.change(screen.getByLabelText("Model id"), {
      target: { value: "newer-model" },
    });
    fireEvent.click(
      Array.from(form.querySelectorAll("button")).find(
        (button) => button.textContent === "Save",
      ) as HTMLElement,
    );

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("There is no profile with this id.");
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Local vLLM" })).toBeNull(),
    );
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "PATCH", "GET"]);
  });

  it("opens the form with the profile's values from Edit, and shows the change once saved", async () => {
    const renamed = { ...SAVED, name: "Renamed" };
    const fetchStub = stubFetch(
      list(ENVIRONMENT, SAVED),
      { json: renamed },
      list(ENVIRONMENT, renamed),
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await screen.findByRole("dialog", { name: "Edit Local vLLM" });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByRole("heading", { name: "Renamed" }),
    ).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "PATCH", "GET"]);
  });
});
