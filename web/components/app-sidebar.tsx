"use client";

import { BotIcon, CpuIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

// The console's sections, in the order the sidebar lists them.
const SECTIONS = [
  { href: "/llm", label: "LLM", icon: CpuIcon },
  { href: "/agents", label: "Agents", icon: BotIcon },
] as const;

export function AppSidebar() {
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();
  return (
    // The generated sidebar draws its edge in the page's border color; the sidebar's own is
    // the one that shows against it.
    <Sidebar className="border-sidebar-border">
      <SidebarHeader>
        <span className="px-2 py-1 font-heading text-base font-semibold">
          Freyja
        </span>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <nav aria-label="Sections">
              <SidebarMenu>
                {SECTIONS.map(({ href, label, icon: Icon }) => {
                  const active =
                    pathname === href || pathname.startsWith(`${href}/`);
                  return (
                    <SidebarMenuItem key={href}>
                      <SidebarMenuButton
                        isActive={active}
                        render={
                          <Link
                            href={href}
                            aria-current={active ? "page" : undefined}
                            // In a narrow window the sidebar is a sheet over the page:
                            // choosing a section closes it, or it would hide the page.
                            onClick={() => setOpenMobile(false)}
                          />
                        }
                      >
                        <Icon aria-hidden="true" />
                        <span>{label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
