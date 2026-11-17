import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
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

  it("marks a section for a page inside it", () => {
    navigation.pathname = "/llm/anything";
    renderSidebar();
    expect(
      screen.getByRole("link", { name: "LLM" }).getAttribute("aria-current"),
    ).toBe("page");
  });
});
