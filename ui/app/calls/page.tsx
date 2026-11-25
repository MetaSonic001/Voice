"use client";

// Call audit: every past call (live calls, voice notes and typed chats) with its recording, what the caller said,
// what the agent replied and why, verification steps, alerts and an integrity check.
import { AlertTriangle, AudioLines, ListChecks, MessageSquareText, Phone, RefreshCw, Search, ShieldCheck, Trash2, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { CallRecordView, ConfirmDelete } from "@/components/call-record";
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
  const [picking, setPicking] = useState(false); // select mode: rows are ticked instead of opened
  const [picked, setPicked] = useState<Set<string>>(new Set());

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

  function toggle(id: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function stopPicking() {
    setPicking(false);
    setPicked(new Set());
  }

  // One at a time through the same endpoint as "Delete call": a live call is refused (409) and simply left.
  async function deletePicked() {
    const ids = [...picked];
    let done = 0;
    const failed: string[] = [];
    for (const id of ids) {
      try {
        await audit.deleteCall(id);
        done += 1;
      } catch (e) {
        failed.push((e as Error).message);
      }
    }
    if (done) toast.success(`${done} call${done === 1 ? "" : "s"} deleted, with their recordings, reviews and fix-list items.`);
    if (failed.length) toast.error(`${failed.length} not deleted: ${failed[0]}`);
    if (selected && picked.has(selected)) router.replace(pathname, { scroll: false });
    stopPicking();
    void load(q, flagged);
  }

  return (
    <main className="mx-auto grid max-w-[1400px] grid-cols-[minmax(0,1fr)] gap-4 p-4 lg:h-[calc(100dvh-3.5rem)] lg:grid-cols-[340px_minmax(0,1fr)]">
      <Card className="no-print flex max-h-[60vh] min-h-0 flex-col gap-0 py-0 lg:max-h-none">
        <div className="space-y-3 border-b p-3">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Calls</p>
            <span className="text-xs text-muted-foreground">{calls ? `${calls.length}${more ? "+" : ""}` : ""}</span>
            <Button size="sm" variant={picking ? "secondary" : "ghost"} className="ml-auto h-7"
                    onClick={() => (picking ? stopPicking() : setPicking(true))} title="Select calls to delete">
              {picking ? <><X /> Done</> : <><ListChecks /> Select</>}
            </Button>
            <Button size="icon" variant="ghost" className="size-7" onClick={() => void load(q, flagged)} title="Refresh">
              <RefreshCw />
            </Button>
          </div>
          {picking && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">{picked.size} selected</span>
              <Button size="sm" variant="ghost" className="h-7 px-2"
                      onClick={() => setPicked(picked.size === (calls?.length ?? 0) ? new Set() : new Set(calls?.map((c) => c.id)))}>
                {picked.size > 0 && picked.size === calls?.length ? "Clear" : "Select all shown"}
              </Button>
              <ConfirmDelete
                title={`Delete ${picked.size} call${picked.size === 1 ? "" : "s"}?`}
                text="Each call is deleted for good: its turns, recordings, review, alerts and fix-list items. This cannot be undone."
                onConfirm={() => void deletePicked()}
                trigger={<Button size="sm" variant="outline" className="ml-auto h-7 text-destructive" disabled={!picked.size}>
                  <Trash2 /> Delete</Button>} />
            </div>
          )}
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
            {calls?.map((c) => (
              <CallRow key={c.id} c={c} active={picking ? picked.has(c.id) : c.id === selected}
                       picking={picking} onClick={() => (picking ? toggle(c.id) : open(c.id))} />
            ))}
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

function CallRow({ c, active, picking, onClick }: { c: CallSummary; active: boolean; picking: boolean; onClick: () => void }) {
  const start = new Date(c.created_at);
  const today = start.toDateString() === new Date().toDateString();
  return (
    <button onClick={onClick} aria-pressed={picking ? active : undefined}
            className={cn("w-full rounded-md border border-transparent px-3 py-2.5 text-left transition-colors",
                          active ? "border-border bg-secondary" : "hover:bg-muted/60")}>
      <div className="flex items-center gap-2 text-sm">
        {picking && <input type="checkbox" checked={active} readOnly tabIndex={-1} aria-hidden className="size-3.5 accent-primary" />}
        {c.live ? <Phone className="size-3.5 text-muted-foreground" /> : <MessageSquareText className="size-3.5 text-muted-foreground" />}
        <span className="min-w-0 truncate font-medium">{c.caller ?? "Number withheld"}</span>
        <ScoreChip score={c.review_score} critical={c.review_critical} attention={c.review_needs_attention} />
        <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
          {/* older calls show the time too: a date alone made a whole day of calls look identical */}
          {today ? "" : `${start.toLocaleDateString([], { day: "numeric", month: "short" })}, `}
          {start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
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
