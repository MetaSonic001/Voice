"use client";

// Top bar shared by every page, and the live alerts (polled every few seconds, shown as toasts).
import { BookOpen, Gauge, Headset, ListChecks, Menu, MessagesSquare, Route, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { audit } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Test console", icon: MessagesSquare },
  { href: "/kb", label: "Knowledge base", icon: BookOpen },
  { href: "/desk", label: "Agent desk", icon: Headset },
  { href: "/calls", label: "Call audit", icon: ShieldCheck },
  { href: "/fixes", label: "Fix list", icon: ListChecks },
  { href: "/journeys", label: "Journeys", icon: Route },
  { href: "/bench", label: "Speed & quality", icon: Gauge },
];

const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path.startsWith(href));

/** One row of links from 1280 px; below that the page name and a menu button that opens the links in a side sheet. */
export function AppHeader() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const current = NAV.find((n) => isActive(path, n.href));
  return (
    <header className="no-print sticky top-0 z-30 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-3 sm:px-4">
        <Link href="/" className="flex min-w-0 shrink-0 items-center gap-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">AU</span>
          <span className="hidden leading-tight sm:block">
            <span className="block text-sm font-semibold">Voice Agent</span>
            <span className="block text-[11px] whitespace-nowrap text-muted-foreground">AU Small Finance Bank</span>
          </span>
        </Link>
        <nav className="hidden min-w-0 items-center gap-0.5 xl:flex" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} aria-current={isActive(path, href) ? "page" : undefined}
                  className={cn("flex items-center gap-2 rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors",
                                isActive(path, href) ? "bg-secondary font-medium text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}>
              <Icon className="size-4" /> {label}
            </Link>
          ))}
        </nav>
        {current && <p className="min-w-0 truncate text-sm font-medium xl:hidden">{current.label}</p>}
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" className="ml-auto xl:hidden" aria-label="Open the menu"><Menu /></Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 gap-0">
            <SheetHeader className="border-b">
              <SheetTitle>Voice Agent</SheetTitle>
              <SheetDescription>AU Small Finance Bank</SheetDescription>
            </SheetHeader>
            <nav className="flex flex-col gap-1 p-3" aria-label="Main">
              {NAV.map(({ href, label, icon: Icon }) => (
                <Link key={href} href={href} onClick={() => setOpen(false)} aria-current={isActive(path, href) ? "page" : undefined}
                      className={cn("flex items-center gap-3 rounded-md px-3 py-2.5 text-sm",
                                    isActive(path, href) ? "bg-secondary font-medium" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  <Icon className="size-4" /> {label}
                </Link>
              ))}
            </nav>
          </SheetContent>
        </Sheet>
      </div>
    </header>
  );
}

const ALERT_POLL_MS = 4000;

/** Shows new alerts (caller wants a person, agent struggling, system health) as they are raised. */
export function AlertWatcher() {
  const router = useRouter();
  const cursor = useRef<number | null>(null);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        if (cursor.current === null) {
          const [c] = await audit.alerts(-1); // start from now: older alerts are in the call record
          cursor.current = c?.id ?? 0;
        } else {
          for (const a of await audit.alerts(cursor.current)) {
            cursor.current = a.id;
            const show = a.severity === "critical" ? toast.error : a.severity === "high" ? toast.warning : toast.info;
            show(a.message, {
              description: `${new Date(a.created_at).toLocaleTimeString()}${a.session_id ? ` · call ${a.session_id.slice(0, 8)}` : ""}`,
              duration: a.severity === "critical" ? Infinity : 12000,
              action: a.session_id ? { label: "Open", onClick: () => router.push(`/calls?id=${a.session_id}`) } : undefined,
            });
          }
        }
      } catch {
        /* brain not reachable: the pages show that themselves */
      }
      if (!stopped) timer = setTimeout(poll, ALERT_POLL_MS);
    }
    let timer = setTimeout(poll, 500);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [router]);
  return null;
}
