import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Reply, stubFetch } from "@/__tests__/testing";
import { ProfileCard } from "@/components/llm/profile-card";
import type { LlmProfile } from "@/lib/llm";

const SAVED: LlmProfile = {
  id: "5b1f0c2e9a7d4e3fb6a81c0d2e4f6a8b",
  name: "Local vLLM",
  source: "saved",
  provider: "openai",
  model: "served-model",
  base_url: "http://10.0.0.5:31000/v1",
  responses_api: false,
  key_hint: "a1b2",
  active: false,
  usable: true,
  unusable_reason: null,
  last_test: null,
  created_at: "2026-11-17T09:10:44.501000+00:00",
  updated_at: "2026-11-17T09:13:20.009000+00:00",
};

const ENVIRONMENT: LlmProfile = {
  ...SAVED,
  id: "environment",
  name: "Environment",
  source: "environment",
  provider: "anthropic",
  model: "claude-sonnet-5-5",
  base_url: null,
  responses_api: null,
  key_hint: "9f2c",
  active: true,
  created_at: null,
  updated_at: null,
};

describe("ProfileCard", () => {
  it("shows what the profile is and how its model is reached", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.getByRole("heading", { name: "Local vLLM" })).toBeTruthy();
    expect(screen.getByText("OpenAI-compatible")).toBeTruthy();
    expect(screen.getByText("served-model")).toBeTruthy();
    expect(screen.getByText("http://10.0.0.5:31000/v1")).toBeTruthy();
    expect(screen.getByText("Chat completions")).toBeTruthy();
  });

  it("shows the last four characters of the key and nothing more", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.getByText(/a1b2$/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/sk-/);
  });

  it("names the Responses API when that is the choice", () => {
    render(<ProfileCard profile={{ ...SAVED, responses_api: true }} />);
    expect(screen.getByText("Responses API")).toBeTruthy();
    expect(screen.queryByText("Chat completions")).toBeNull();
  });

  it("shows no choice of API and no address when the profile has none", () => {
    render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.queryByText("Responses API")).toBeNull();
    expect(screen.queryByText("Chat completions")).toBeNull();
    expect(screen.queryByText("Base URL")).toBeNull();
  });

  it("marks the active profile and the one from the environment", () => {
    const { unmount } = render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("From the environment")).toBeTruthy();
    unmount();
    render(<ProfileCard profile={SAVED} />);
    expect(screen.queryByText("Active")).toBeNull();
    expect(screen.queryByText("From the environment")).toBeNull();
  });

  it("shows no character of a key that has no hint", () => {
    render(<ProfileCard profile={{ ...SAVED, key_hint: "" }} />);
    expect(screen.getByText("Not shown")).toBeTruthy();
  });

  it.each([
    ["key_unreadable", /cannot be read with the service's current secret/],
    ["secret_not_configured", /no secret is set on the service/i],
    ["not_configured", /names no key for this provider/],
    ["cannot_build", /no model can be built/i],
  ] as const)("says why a profile is not usable: %s", (reason, text) => {
    render(
      <ProfileCard
        profile={{ ...SAVED, usable: false, unusable_reason: reason }}
      />,
    );
    expect(screen.getByText("Not usable")).toBeTruthy();
    expect(screen.getByText(text)).toBeTruthy();
  });

  it("offers Set active on a usable profile that is not active", () => {
    const onActivate = vi.fn();
    render(<ProfileCard profile={SAVED} onActivate={onActivate} />);
    fireEvent.click(screen.getByRole("button", { name: "Set active" }));
    expect(onActivate).toHaveBeenCalledWith(SAVED);
  });

  it("offers it on the environment's profile when another one is active", () => {
    render(
      <ProfileCard
        profile={{ ...ENVIRONMENT, active: false }}
        onActivate={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Set active" })).toBeTruthy();
  });

  it.each([
    ["the active profile", { ...SAVED, active: true }],
    [
      "a profile that is not usable",
      { ...SAVED, usable: false, unusable_reason: "key_unreadable" as const },
    ],
  ])("does not offer it on %s", (_case, profile) => {
    render(<ProfileCard profile={profile} onActivate={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Set active" })).toBeNull();
  });

  it("offers it only where something can be done with it", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.queryByRole("button", { name: "Set active" })).toBeNull();
  });

  it("gives the environment's profile no way to be edited or deleted", () => {
    render(<ProfileCard profile={ENVIRONMENT} />);
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });
});

describe("ProfileCard, its test", () => {
  const AT = "2026-11-17T09:14:02.118000+00:00";
  const passed = { ok: true, at: AT, step: null, reason: null };
  const failed = {
    ok: false,
    at: AT,
    step: "conversation" as const,
    reason: "Error code: 422 - Input should be a valid string",
  };
  const answer = (result: object, recorded = true): Reply => ({
    json: { ...result, recorded },
  });
  const test = () =>
    fireEvent.click(screen.getByRole("button", { name: "Test" }));

  it("says a saved profile has not been tested since its last change", () => {
    render(<ProfileCard profile={SAVED} />);
    expect(screen.getByText("Not tested since its last change.")).toBeTruthy();
  });

  it("says the environment's profile has not been tested since the service started", () => {
    render(<ProfileCard profile={ENVIRONMENT} />);
    expect(
      screen.getByText("Not tested since the service started."),
    ).toBeTruthy();
  });

  it("says its last test passed, and when the service said it ended", () => {
    const { container } = render(
      <ProfileCard profile={{ ...SAVED, last_test: passed }} />,
    );
    expect(screen.getByText(/Last test passed/)).toBeTruthy();
    // The time is the one the service gave, not one worked out here.
    const time = container.querySelector("time");
    expect(time?.getAttribute("datetime")).toBe(AT);
    expect(time?.textContent).not.toBe("");
    expect(screen.queryByText(/Not tested/)).toBeNull();
  });

  it("says its last test failed, at which step, and why", () => {
    render(<ProfileCard profile={{ ...SAVED, last_test: failed }} />);
    expect(
      screen.getByText(/Last test failed at the conversation/),
    ).toBeTruthy();
    expect(screen.getByText(failed.reason)).toBeTruthy();
  });

  it("names the first step when that is the one that failed", () => {
    render(
      <ProfileCard
        profile={{
          ...SAVED,
          last_test: {
            ...failed,
            step: "first_message",
            reason: "Connection error.",
          },
        }}
      />,
    );
    expect(
      screen.getByText(/Last test failed at the first message/),
    ).toBeTruthy();
  });

  it("tests the profile once, and shows the result when it ends", async () => {
    const fetchStub = stubFetch(answer(passed));
    const onTested = vi.fn();
    render(<ProfileCard profile={SAVED} onTested={onTested} />);
    test();
    // While it runs the card says so and takes no second press.
    const running = screen.getByRole("button", { name: "Testing…" });
    expect((running as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(running);

    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/Last test passed/);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [path, init] = fetchStub.mock.calls[0];
    expect(path).toBe(`/api/llm/profiles/${SAVED.id}/test`);
    expect(init?.method).toBe("POST");
    // The list is read again, so the result is the service's from then on.
    expect(onTested).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Test" })).toBeTruthy();
    expect(screen.queryByText(/Not tested/)).toBeNull();
  });

  it("shows a failure with the step that failed and the endpoint's reason", async () => {
    stubFetch(answer(failed));
    render(<ProfileCard profile={SAVED} />);
    test();

    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/Last test failed at the conversation/);
    expect(result.textContent).toContain(failed.reason);
  });

  it("says when a result was not kept because the profile changed meanwhile", async () => {
    stubFetch(answer(passed, false));
    render(<ProfileCard profile={SAVED} />);
    test();
    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/not kept/);
  });

  it.each([
    [
      "the service's refusal",
      {
        status: 409,
        json: {
          detail: {
            code: "test_busy",
            message: "This profile is already being tested.",
          },
        },
      },
      /already being tested/,
    ],
    [
      "that the service cannot be reached",
      { fails: "Failed to fetch" },
      /cannot be reached/,
    ],
  ] as [
    string,
    Reply,
    RegExp,
  ][])("shows %s when the test could not be run", async (_case, reply, message) => {
    stubFetch(reply);
    const onTested = vi.fn();
    render(<ProfileCard profile={SAVED} onTested={onTested} />);
    test();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(message);
    expect(onTested).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Test" })).toBeTruthy(),
    );
  });

  it("drops a result it showed once the profile has changed", async () => {
    stubFetch(answer(passed));
    const { rerender } = render(<ProfileCard profile={SAVED} />);
    test();
    await screen.findByRole("status");

    rerender(
      <ProfileCard
        profile={{
          ...SAVED,
          model: "another-model",
          updated_at: "2026-11-18T10:00:00.000000+00:00",
        }}
      />,
    );
    expect(screen.getByText("Not tested since its last change.")).toBeTruthy();
    expect(screen.queryByText(/Last test passed/)).toBeNull();
  });

  it("shows a newer result from the list over the one it got itself", async () => {
    // The card's own test failed. Then the profile is tested from somewhere else, which
    // does not change the profile, and the list reports that later result.
    stubFetch(answer(failed));
    const { rerender } = render(<ProfileCard profile={SAVED} listRead={1} />);
    test();
    await screen.findByText(/Last test failed at the conversation/);

    const later = { ...passed, at: "2026-11-18T10:00:00.000000+00:00" };
    rerender(
      <ProfileCard profile={{ ...SAVED, last_test: later }} listRead={2} />,
    );
    expect(screen.getByText(/Last test passed/)).toBeTruthy();
    expect(screen.queryByText(/Last test failed/)).toBeNull();
    expect(document.querySelector("time")?.getAttribute("datetime")).toBe(
      later.at,
    );
  });

  it("keeps its own result until the list has been read again", async () => {
    stubFetch(answer(passed));
    const { rerender } = render(
      <ProfileCard profile={ENVIRONMENT} listRead={1} />,
    );
    test();
    await screen.findByText(/Last test passed/);

    // The same reading of the list, which was made before the test ended.
    rerender(<ProfileCard profile={ENVIRONMENT} listRead={1} />);
    expect(screen.getByText(/Last test passed/)).toBeTruthy();
  });

  it("shows its result when the list was read while the test was running", async () => {
    // The answer comes only when the test lets it.
    let answerNow: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            answerNow = resolve;
          }),
      ),
    );
    const { rerender } = render(<ProfileCard profile={SAVED} listRead={1} />);
    test();

    // Meanwhile the list is read again, for another reason, and knows of no result yet.
    rerender(<ProfileCard profile={SAVED} listRead={2} />);
    answerNow(
      new Response(JSON.stringify({ ...passed, recorded: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    // The result is newer than that reading of the list, so it is shown, and announced.
    const result = await screen.findByRole("status");
    expect(result.textContent).toMatch(/Last test passed/);
    expect(screen.queryByText(/Not tested/)).toBeNull();

    // The next reading, which has the result, takes over.
    rerender(
      <ProfileCard profile={{ ...SAVED, last_test: passed }} listRead={3} />,
    );
    expect(screen.getByText(/Last test passed/)).toBeTruthy();
  });

  it("drops a result the service no longer has, such as the environment's after a restart", async () => {
    stubFetch(answer(passed));
    const { rerender } = render(
      <ProfileCard profile={ENVIRONMENT} listRead={1} />,
    );
    test();
    await screen.findByText(/Last test passed/);

    // Read again, the list has no result for it: the service is what says what is so.
    rerender(<ProfileCard profile={ENVIRONMENT} listRead={2} />);
    expect(
      screen.getByText("Not tested since the service started."),
    ).toBeTruthy();
    expect(screen.queryByText(/Last test passed/)).toBeNull();
  });

  it("keeps saying a result was not kept after the list is read again", async () => {
    stubFetch(answer(passed, false));
    const { rerender } = render(<ProfileCard profile={SAVED} listRead={1} />);
    test();
    await screen.findByText(/This result was not kept/);

    rerender(<ProfileCard profile={SAVED} listRead={2} />);
    expect(screen.getByText(/This result was not kept/)).toBeTruthy();
  });

  it("says when the profile it tested is gone, so the list can be read again", async () => {
    stubFetch({
      status: 404,
      json: {
        detail: {
          code: "profile_not_found",
          message: "There is no profile with this id.",
        },
      },
    });
    const onGone = vi.fn();
    const onTested = vi.fn();
    render(<ProfileCard profile={SAVED} onGone={onGone} onTested={onTested} />);
    test();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("There is no profile with this id.");
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(onTested).not.toHaveBeenCalled();
  });

  it("does not offer a test of a profile that is not usable", () => {
    render(
      <ProfileCard
        profile={{ ...SAVED, usable: false, unusable_reason: "key_unreadable" }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
  });
});

describe("ProfileCard, changing and removing", () => {
  it("offers Edit and Delete on a saved profile", () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    render(<ProfileCard profile={SAVED} onEdit={onEdit} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onEdit).toHaveBeenCalledWith(SAVED);
    expect(onDelete).toHaveBeenCalledWith(SAVED);
  });

  it("offers both on the active profile and on one that is not usable", () => {
    for (const profile of [
      { ...SAVED, active: true },
      { ...SAVED, usable: false, unusable_reason: "key_unreadable" as const },
    ]) {
      const { unmount } = render(
        <ProfileCard profile={profile} onEdit={vi.fn()} onDelete={vi.fn()} />,
      );
      // An unreadable key is put right by entering it again, in the Edit form.
      expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
      unmount();
    }
  });

  it("offers neither on the environment's profile, whatever it is given", () => {
    render(
      <ProfileCard profile={ENVIRONMENT} onEdit={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});

describe("ProfileCard, a profile that is not usable", () => {
  const unreadable: LlmProfile = {
    ...SAVED,
    usable: false,
    unusable_reason: "key_unreadable",
  };

  it("says why, cannot be tested or activated, and can be edited to enter its key again", () => {
    render(
      <ProfileCard
        profile={unreadable}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText("Not usable")).toBeTruthy();
    expect(screen.getByText(/stored key cannot be read/)).toBeTruthy();
    expect(screen.getByText(/Enter the key again/)).toBeTruthy();
    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["Edit", "Delete"]);
    // Its last four characters are still shown, and nothing more of it.
    expect(screen.getByText("Ends in a1b2")).toBeTruthy();
  });

  it("offers nothing on the environment's profile when it is not usable", () => {
    render(
      <ProfileCard
        profile={{
          ...ENVIRONMENT,
          key_hint: "",
          usable: false,
          unusable_reason: "not_configured",
        }}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText("Not usable")).toBeTruthy();
    expect(
      screen.getByText("The environment names no key for this provider."),
    ).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
