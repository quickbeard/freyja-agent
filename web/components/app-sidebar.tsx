"use client";

import { BotIcon, CpuIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId } from "react";
import useSWR from "swr";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  AGENTS_KEY,
  type AgentList,
  downAgents,
  listAgents,
} from "@/lib/agents";

// The console's sections, in the order the sidebar lists them.
const SECTIONS = [
  { href: "/llm", label: "LLM", icon: CpuIcon },
  { href: "/agents", label: "Agents", icon: BotIcon },
] as const;

const AGENTS = "/agents";

/**
 * How many agents cannot be handed work now, as the list was last read; none while it could
 * not be read. The key is the one the Agents section reads the list under, so the two share
 * one reading: the console asks once when it opens on any section, and whenever the Agents
 * section reads the list the number here is that reading's (FR-001a).
 */
function useAgentsDown(): number {
  const { data, error } = useSWR<AgentList, unknown>(AGENTS_KEY, listAgents, {
    // Like the section: a reading that failed is not tried again behind the operator's back.
    shouldRetryOnError: false,
  });
  // A number read before the service stopped answering is not shown as if it were current.
  return data === undefined || error !== undefined
    ? 0
    : downAgents(data).length;
}

export function AppSidebar() {
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();
  const down = useAgentsDown();
  const warning = useId();
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
                  const warned = href === AGENTS && down > 0;
                  return (
                    <SidebarMenuItem key={href}>
                      <SidebarMenuButton
                        isActive={active}
                        render={
                          <Link
                            href={href}
                            aria-current={active ? "page" : undefined}
                            // The entry keeps its name; what the number means is said
                            // beside it, for whoever does not see where the number stands.
                            aria-describedby={warned ? warning : undefined}
                            // In a narrow window the sidebar is a sheet over the page:
                            // choosing a section closes it, or it would hide the page.
                            onClick={() => setOpenMobile(false)}
                          />
                        }
                      >
                        <Icon aria-hidden="true" />
                        <span>{label}</span>
                      </SidebarMenuButton>
                      {warned && (
                        <SidebarMenuBadge
                          id={warning}
                          className="bg-destructive/10 text-destructive peer-hover/menu-button:text-destructive peer-data-active/menu-button:text-destructive"
                        >
                          <span aria-hidden="true">{down}</span>
                          <span className="sr-only">
                            {down === 1
                              ? "1 agent cannot be used"
                              : `${down} agents cannot be used`}
                          </span>
                        </SidebarMenuBadge>
                      )}
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
