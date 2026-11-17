import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

const navigation = vi.hoisted(() => ({ pathname: "/llm" }));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
}));

function renderSidebar() {
  return render(
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarTrigger />
      </SidebarProvider>
    </TooltipProvider>,
  );
}

describe("AppSidebar", () => {
  beforeEach(() => {
    navigation.pathname = "/llm";
  });

  it("lists the console's sections as links", () => {
    renderSidebar();
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["LLM", "Agents"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/llm",
      "/agents",
    ]);
  });

  it("marks the section in use and no other", () => {
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
    ).toBeNull();
  });

  it("follows the address", () => {
    navigation.pathname = "/agents";
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBeNull();
  });

  it("closes over a narrow window once a section is chosen", async () => {
    // Under the sidebar's breakpoint it is a sheet over the page, closed at first.
    vi.stubGlobal("innerWidth", 500);
    renderSidebar();
    expect(screen.queryByRole("link", { name: "Agents" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
    const agents = await screen.findByRole("link", { name: "Agents" });
    // The link's own navigation needs a router, which a unit test has none of.
    agents.addEventListener("click", (event) => event.preventDefault());
    fireEvent.click(agents);

    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Agents" })).toBeNull(),
    );
  });

  it("marks a section for a page inside it", () => {
    navigation.pathname = "/llm/anything";
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBe("page");
  });
});
