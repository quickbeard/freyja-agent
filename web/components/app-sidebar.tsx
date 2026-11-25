"use client";

import { BotIcon, CpuIcon, ListTodoIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useId } from "react";
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
import { useEvery } from "@/hooks/use-every";
import {
  AGENTS_KEY,
  type AgentList,
  downAgents,
  listAgents,
} from "@/lib/agents";
import {
  LIST_SECONDS,
  listThreads,
  READING,
  THREADS_KEY,
  type ThreadList,
} from "@/lib/tasks";

// The console's sections, in the order the sidebar lists them.
// Tasks first, where a person hands Freyja work (feature 007) and the console opens; then the
// configuration: the agents, and the models, whose address stays `/llm`.
const SECTIONS = [
  { href: "/tasks", label: "Tasks", icon: ListTodoIcon },
  { href: "/agents", label: "Agents", icon: BotIcon },
  { href: "/llm", label: "Models", icon: CpuIcon },
] as const;

const AGENTS = "/agents";
const TASKS = "/tasks";

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
              {/* Feature 007: a page below a route with a parameter, a task's, knows its
                  address only when it is asked for, so the links that mark the current
                  section wait for it, and are shown unmarked until then (Next.js docs,
                  use-pathname.md, "Cache Components"). */}
              <Suspense fallback={<SectionLinks pathname={null} />}>
                <CurrentSectionLinks />
              </Suspense>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}

function CurrentSectionLinks() {
  return <SectionLinks pathname={usePathname()} />;
}

/** The sections, with the one `pathname` is in marked; none while it is not known. */
// Feature 007: how many tasks need a decision, from every section (FR-001, FR-006). The key
// is the one the Tasks section reads the list under, so the two are one reading. On another
// section the sidebar is the list's only reader, so it reads it again on a timer of its own,
// which goes on past a reading that failed. Nothing is counted from a list that could not be
// read: the number is then null, not known, which is not the same as none.
function useTasksWaiting(): number | null {
  const { data, error, isValidating, mutate } = useSWR<ThreadList, unknown>(
    THREADS_KEY,
    listThreads,
    READING,
  );
  useEvery(THREADS_KEY, LIST_SECONDS, () => {
    // Not while a reading is under way: a new one would take its place.
    if (!isValidating) {
      void mutate();
    }
  });
  if (error !== undefined) {
    return null;
  }
  return data === undefined ? 0 : data.needs_decision;
}

function SectionLinks({ pathname }: { pathname: string | null }) {
  const { setOpenMobile } = useSidebar();
  const down = useAgentsDown();
  const warning = useId();
  const waiting = useTasksWaiting();
  const waitingSaid = useId();
  return (
    <SidebarMenu>
      {SECTIONS.map(({ href, label, icon: Icon }) => {
        const active =
          pathname !== null &&
          (pathname === href || pathname.startsWith(`${href}/`));
        const warned = href === AGENTS && down > 0;
        const unknown = href === TASKS && waiting === null;
        const counted = href === TASKS && waiting !== null && waiting > 0;
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
                  aria-describedby={
                    warned
                      ? warning
                      : counted || unknown
                        ? waitingSaid
                        : undefined
                  }
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
              <SidebarMenuBadge className="bg-destructive/10 text-destructive peer-hover/menu-button:text-destructive peer-data-active/menu-button:text-destructive">
                {/* What the number is about is said in words to the eye as well: the
                    badge's colour alone says nothing (005 FR-001a). */}
                <span aria-hidden="true">{down} cannot be used</span>
                <span id={warning} className="sr-only">
                  {down === 1
                    ? "1 agent cannot be used"
                    : `${down} agents cannot be used`}
                </span>
              </SidebarMenuBadge>
            )}
            {counted && (
              <SidebarMenuBadge>
                {/* In words to the eye and to assistive technology, never by the badge
                    alone (FR-001, FR-012a). */}
                <span aria-hidden="true">{waiting} to decide</span>
                <span id={waitingSaid} className="sr-only">
                  {waiting === 1
                    ? "1 task needs a decision"
                    : `${waiting} tasks need a decision`}
                </span>
              </SidebarMenuBadge>
            )}
            {unknown && (
              <SidebarMenuBadge>
                {/* A list that could not be read is not "none": the entry says so, in
                    words (FR-006; Edge Cases, "The list cannot be kept or read"). The
                    eye reads the two words, and assistive technology the sentence they
                    stand in; no part is hidden from either, since nothing here is a
                    number that needs its meaning said beside it. */}
                <span id={waitingSaid}>
                  <span className="sr-only">
                    How many tasks need a decision is{" "}
                  </span>
                  not known
                  <span className="sr-only">
                    : the list of tasks could not be read.
                  </span>
                </span>
              </SidebarMenuBadge>
            )}
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}
