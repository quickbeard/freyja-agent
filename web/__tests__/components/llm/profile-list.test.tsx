import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
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

function list(changes: Partial<LlmProfileList> = {}): Reply {
  return {
    json: {
      profiles: [ENVIRONMENT, SAVED],
      active_id: "environment",
      usable_model: true,
      secret_configured: true,
      limit: 50,
      ...changes,
    },
  };
}

// A cache of its own for every test, so one test's list never reaches another.
function renderList() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ProfileList />
    </SWRConfig>,
  );
}

describe("ProfileList", () => {
  it("says it is loading, then shows one card per profile", async () => {
    const fetchStub = stubFetch(list());
    renderList();
    expect(screen.getByRole("status").textContent).toMatch(/loading/i);
    expect(
      await screen.findByRole("heading", { name: "Environment" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Local vLLM" })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    expect(fetchStub.mock.calls[0][0]).toBe("/api/llm/profiles");
  });

  it("says the service cannot be reached, shows no card, and tries again when asked", async () => {
    const fetchStub = stubFetch({ fails: "Failed to fetch" }, list());
    renderList();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/cannot be reached/);
    expect(screen.queryAllByRole("heading", { level: 3 })).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "Environment" }),
    ).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("shows the service's reason when it refuses", async () => {
    stubFetch({
      status: 409,
      json: {
        detail: { code: "profile_unusable", message: "The service said no." },
      },
    });
    renderList();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/The service said no\./);
  });

  it("does not show earlier profiles as current once the service stops answering", async () => {
    stubFetch(list(), { fails: "Failed to fetch" });
    renderList();
    await screen.findByRole("heading", { name: "Environment" });

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/cannot be reached/);
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Environment" })).toBeNull(),
    );
  });

  it("says when no usable model is active", async () => {
    stubFetch(list({ usable_model: false }));
    renderList();
    await screen.findByRole("heading", { name: "Environment" });
    expect(screen.getByRole("alert").textContent).toMatch(
      /no usable model is active/i,
    );
  });

  it("says nothing of the kind while a usable model is active", async () => {
    stubFetch(list());
    renderList();
    await screen.findByRole("heading", { name: "Environment" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("opens the form from Add, and reads the list again once a profile is saved", async () => {
    const added: LlmProfile = { ...SAVED, id: "f".repeat(32), name: "Claude" };
    const fetchStub = stubFetch(
      list(),
      { status: 201, json: added },
      list({ profiles: [ENVIRONMENT, added, SAVED] }),
    );
    renderList();
    await screen.findByRole("heading", { name: "Local vLLM" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByRole("dialog", { name: "Add a profile" });
    for (const [label, value] of [
      ["Name", "Claude"],
      ["Model id", "served-model"],
      ["API key", "sk-recognizable-key-0001"],
    ]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("heading", { name: "Claude" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(fetchStub.mock.calls.map(([path]) => path)).toEqual([
      "/api/llm/profiles",
      "/api/llm/profiles",
      "/api/llm/profiles",
    ]);
    expect(
      fetchStub.mock.calls.map(([, init]) => init?.method ?? "GET"),
    ).toEqual(["GET", "POST", "GET"]);
    // What was active still is.
    expect(screen.getAllByText("Active")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("sk-recognizable-key-0001");
  });

  it("offers nothing to add while the list could not be read", async () => {
    stubFetch({ fails: "Failed to fetch" });
    renderList();
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  });
});
