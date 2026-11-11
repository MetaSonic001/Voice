"use client";

// Call audit: every past call (live calls, voice notes and typed chats) with its recording, what the caller said,
// what the agent replied and why, verification steps, alerts and an integrity check.
import { AlertTriangle, AudioLines, MessageSquareText, Phone, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { CallRecordView } from "@/components/call-record";
import { QualitySummary } from "@/components/quality-summary";
import { ScoreChip } from "@/components/review-panel";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { audit, type CallSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function CallsPage() {
  return (
    <Suspense fallback={null}>
      <Calls />
    </Suspense>
  );
}

function Calls() {
  const router = useRouter();
  const pathname = usePathname();
  const selected = useSearchParams().get("id");
  const [calls, setCalls] = useState<CallSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [more, setMore] = useState(true);
  const [flagged, setFlagged] = useState(false);

  const load = useCallback(async (query: string, onlyFlagged = false) => {
    try {
      const rows = await audit.calls(query, undefined, onlyFlagged);
      setCalls(rows);
      setMore(rows.length === 50);
      setError("");
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
      setCalls([]);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void load(q, flagged), q ? 300 : 0);
    return () => clearTimeout(t);
  }, [q, flagged, load]);

  async function loadMore() {
    if (!calls?.length) return;
    const rows = await audit.calls(q, calls[calls.length - 1].created_at, flagged);
    setCalls([...calls, ...rows]);
    setMore(rows.length === 50);
  }

  const open = (id: string) => router.replace(`${pathname}?id=${id}`, { scroll: false });

  return (
    <main className="mx-auto grid max-w-[1400px] grid-cols-[minmax(0,1fr)] gap-4 p-4 lg:h-[calc(100vh-3.5rem)] lg:grid-cols-[340px_minmax(0,1fr)]">
      <Card className="no-print flex max-h-[60vh] min-h-0 flex-col gap-0 py-0 lg:max-h-none">
        <div className="space-y-3 border-b p-3">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Calls</p>
            <span className="text-xs text-muted-foreground">{calls ? `${calls.length}${more ? "+" : ""}` : ""}</span>
            <Button size="icon" variant="ghost" className="ml-auto size-7" onClick={() => void load(q, flagged)} title="Refresh">
              <RefreshCw />
            </Button>
          </div>
          <Button size="sm" variant={flagged ? "default" : "outline"} className="w-full" onClick={() => setFlagged(!flagged)}>
            <AlertTriangle /> {flagged ? "Showing calls that need review" : "Only calls that need review"}
          </Button>
          <div className="relative">
            <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search words, number or call id" className="pl-8" />
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
          <div className="space-y-1 p-2">
            {calls === null && Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-16 w-full" />)}
            {error && <p className="p-3 text-sm text-destructive">{error}</p>}
            {calls?.length === 0 && !error && <p className="p-3 text-sm text-muted-foreground">No calls yet. Talk to the agent in the test console first.</p>}
            {calls?.map((c) => <CallRow key={c.id} c={c} active={c.id === selected} onClick={() => open(c.id)} />)}
            {more && calls && calls.length > 0 && (
              <Button variant="ghost" size="sm" className="w-full" onClick={() => void loadMore()}>Load older calls</Button>
            )}
          </div>
        </ScrollArea>
      </Card>

      <div className="min-h-0 min-w-0 overflow-y-auto print-full">
        {selected ? <CallRecordView key={selected} id={selected}
                                    onDeleted={() => { router.replace(pathname, { scroll: false }); void load(q, flagged); }} /> : (
          <div className="space-y-4">
            <QualitySummary onOpen={open} />
            <Card className="grid place-items-center text-center">
              <div className="space-y-2 p-8 text-sm text-muted-foreground">
                <ShieldCheck className="mx-auto size-8" />
                <p className="font-medium text-foreground">Select a call</p>
                <p>See the recording, the conversation, why the agent replied as it did, and the audit trail.</p>
              </div>
            </Card>
          </div>
        )}
      </div>
    </main>
  );
}

function CallRow({ c, active, onClick }: { c: CallSummary; active: boolean; onClick: () => void }) {
  const start = new Date(c.created_at);
  const today = start.toDateString() === new Date().toDateString();
  return (
    <button onClick={onClick}
            className={cn("w-full rounded-md border border-transparent px-3 py-2.5 text-left transition-colors",
                          active ? "border-border bg-secondary" : "hover:bg-muted/60")}>
      <div className="flex items-center gap-2 text-sm">
        {c.live ? <Phone className="size-3.5 text-muted-foreground" /> : <MessageSquareText className="size-3.5 text-muted-foreground" />}
        <span className="min-w-0 truncate font-medium">{c.caller ?? "Number withheld"}</span>
        <ScoreChip score={c.review_score} critical={c.review_critical} attention={c.review_needs_attention} />
        <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
          {today ? start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : start.toLocaleDateString([], { day: "numeric", month: "short" })}
        </span>
      </div>
      <p className="mt-0.5 truncate text-xs text-muted-foreground">{c.last_question ?? "No questions"}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span>{c.turns ?? 0} turn{c.turns === 1 ? "" : "s"}</span>
        {c.verified_at && <span className="flex items-center gap-1 text-success"><ShieldCheck className="size-3" /> verified</span>}
        {c.recordings > 0 && <span className="flex items-center gap-1"><AudioLines className="size-3" /> recorded</span>}
        {c.unanswered > 0 && <span>{c.unanswered} unanswered</span>}
        {c.alerts > 0 && <span className="flex items-center gap-1 text-warning"><AlertTriangle className="size-3" /> {c.alerts}</span>}
      </div>
    </button>
  );
}
