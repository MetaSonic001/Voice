"use client";

// Top bar shared by every page, and the live alerts (polled every few seconds, shown as toasts).
import { BookOpen, Gauge, Headset, ListChecks, MessagesSquare, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { audit } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Test console", icon: MessagesSquare },
  { href: "/kb", label: "Knowledge base", icon: BookOpen },
  { href: "/desk", label: "Agent desk", icon: Headset },
  { href: "/calls", label: "Call audit", icon: ShieldCheck },
  { href: "/fixes", label: "Fix list", icon: ListChecks },
  { href: "/bench", label: "Speed & quality", icon: Gauge },
];

export function AppHeader() {
  const path = usePathname();
  return (
    <header className="no-print sticky top-0 z-30 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-4">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">AU</span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold">Voice Agent</span>
            <span className="block text-[11px] text-muted-foreground">AU Small Finance Bank</span>
          </span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href}
                    className={cn("flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
                                  active ? "bg-secondary font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
                <Icon className="size-4" />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
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
