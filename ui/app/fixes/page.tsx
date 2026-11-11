"use client";

// The fix list: every problem the post-call judge found, as a to-do. Each shows what happened in which call and turn,
// why it matters, the fix and where to make it; a person marks it done (or won't fix) once it is handled.
import { CheckCircle2, ExternalLink, ListChecks, RotateCcw, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { AREA, label, SOURCE } from "@/components/review-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { audit, type FixItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type Tab = "open" | "done" | "wont_fix" | "all";
const TABS: { key: Tab; label: string }[] = [
  { key: "open", label: "To fix" }, { key: "done", label: "Done" }, { key: "wont_fix", label: "Won't fix" }, { key: "all", label: "All" },
];

export default function FixesPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [agentOnly, setAgentOnly] = useState(true);
  const [items, setItems] = useState<FixItem[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await audit.fixes(tab));
      setError("");
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
      setItems([]);
    }
  }, [tab]);
  useEffect(() => void load(), [load]);

  const shown = useMemo(() => (items ?? []).filter((f) => !agentOnly || f.source === "agent"), [items, agentOnly]);
  const byArea = useMemo(() => {
    const out = new Map<string, number>();
    for (const f of shown) out.set(f.fix_area, (out.get(f.fix_area) ?? 0) + 1);
    return [...out.entries()].sort((a, b) => b[1] - a[1]);
  }, [shown]);

  async function setStatus(f: FixItem, status: FixItem["status"]) {
    try {
      await audit.setFix(f.id, status);
      toast.success(status === "done" ? "Marked done." : status === "wont_fix" ? "Marked won't fix." : "Reopened.");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <main className="mx-auto max-w-[1100px] space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><ListChecks className="size-5" /> Fix list</CardTitle>
          <CardDescription>
            Problems the post-call judge found, one per call and turn. Open the call to see the turn, make the change, then mark it
            done. The judge is a pointer, not a verdict: check each one.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          {TABS.map((t) => (
            <Button key={t.key} size="sm" variant={tab === t.key ? "default" : "outline"} onClick={() => setTab(t.key)}>{t.label}</Button>
          ))}
          <label className="ml-auto flex items-center gap-2 text-sm">
            <input type="checkbox" checked={agentOnly} onChange={(e) => setAgentOnly(e.target.checked)} />
            Only the agent's own mistakes
          </label>
          {byArea.length > 0 && (
            <div className="flex w-full flex-wrap gap-1.5 pt-1 text-xs text-muted-foreground">
              Where: {byArea.map(([a, n]) => <Badge key={a} variant="secondary">{AREA[a] ?? a} · {n}</Badge>)}
            </div>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {items === null && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-32 w-full" />)}
      {items && shown.length === 0 && !error && (
        <p className="p-6 text-center text-sm text-muted-foreground">Nothing here. Problems appear after calls are reviewed on the Call audit page.</p>
      )}
      {shown.map((f) => (
        <Card key={f.id} className={cn(f.status !== "open" && "opacity-75")}>
          <CardContent className="space-y-2 pt-5 text-sm">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline">{label(f.category)}</Badge>
              <span className={cn("text-xs", f.source === "agent" ? "text-warning" : "text-muted-foreground")}>{SOURCE[f.source] ?? f.source}</span>
              <span className="text-xs text-muted-foreground">· fix in: {AREA[f.fix_area] ?? f.fix_area}</span>
              {f.status !== "open" && <Badge variant="secondary">{f.status === "done" ? "Done" : "Won't fix"}</Badge>}
              <span className="ml-auto text-xs text-muted-foreground">{new Date(f.created_at).toLocaleString()}</span>
            </div>
            <p className="break-words font-medium">{f.what_happened}</p>
            {f.quote && <blockquote className="border-l-2 pl-3 text-xs break-words text-muted-foreground">“{f.quote}”</blockquote>}
            {f.why_it_matters && <p className="text-xs break-words text-muted-foreground">Why it matters: {f.why_it_matters}</p>}
            <p className="break-words"><span className="font-medium">Fix: </span>{f.fix}</p>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {f.session_id ? (
                <Button asChild size="sm" variant="outline">
                  <Link href={`/calls?id=${f.session_id}${f.turn ? `#turn-${f.turn}` : ""}`}>
                    <ExternalLink /> Open call {f.session_id.slice(0, 8)}{f.turn ? ` · turn ${f.turn}` : ""}
                  </Link>
                </Button>
              ) : <span className="text-xs text-muted-foreground">The call was deleted (clean slate); the to-do is kept.</span>}
              <span className="ml-auto flex gap-2">
                {f.status === "open" ? (
                  <>
                    <Button size="sm" onClick={() => void setStatus(f, "done")}><CheckCircle2 /> Mark done</Button>
                    <Button size="sm" variant="ghost" onClick={() => void setStatus(f, "wont_fix")}><XCircle /> Won't fix</Button>
                  </>
                ) : <Button size="sm" variant="ghost" onClick={() => void setStatus(f, "open")}><RotateCcw /> Reopen</Button>}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </main>
  );
}
