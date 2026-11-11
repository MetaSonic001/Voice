"use client";

// Quality across calls, from the post-call reviews: averages, critical calls and the problems that keep coming back,
// biggest first, each with a recent example and where to fix it. The judge diagnoses; a person decides what to change.
import { BarChart3, ListChecks } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AREA, label } from "@/components/review-panel";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { audit, DIMENSIONS, type ReviewSummary } from "@/lib/api";

const DIM: Record<string, string> = {
  task_completion: "Task", correctness: "Correctness", conversation: "Conversation", compliance: "Compliance", handoff: "Handoff",
};

export function QualitySummary({ onOpen }: { onOpen: (id: string) => void }) {
  const [s, setS] = useState<ReviewSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    audit.summary(30).then(setS).catch((e) => setError((e as Error).message));
  }, []);

  if (error) return null;  // the calls list already says when the brain cannot be reached
  if (!s || s.reviewed_calls === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart3 className="size-4" /> Quality across calls
          <Link href="/fixes" className="ml-auto flex items-center gap-1 text-xs font-normal text-primary underline-offset-2 hover:underline">
            <ListChecks className="size-3.5" /> Open the fix list
          </Link>
        </CardTitle>
        <CardDescription>
          Last {s.days} days · {s.judged_calls} of {s.reviewed_calls} reviewed calls judged · {s.critical_calls} critical ·{" "}
          {s.needs_review} need review{s.average_score != null ? ` · average ${s.average_score} / 5` : ""}.
          The judge is not yet checked against people's scores: read it as a pointer, not a verdict.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {DIMENSIONS.map((d) => (
            <div key={d} className="rounded-md border p-2">
              <p className="text-[11px] text-muted-foreground">{DIM[d]}</p>
              <p className="text-base font-semibold tabular-nums">{s.average_by_dimension[d] ?? "n/a"}</p>
            </div>
          ))}
        </div>
        {s.recurring_issues.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Recurring problems, biggest first</p>
            {s.recurring_issues.slice(0, 8).map((i) => (
              <button key={i.category} onClick={() => onOpen(i.example.session_id)}
                      className="w-full space-y-1 rounded-md border p-2.5 text-left text-sm hover:bg-muted/50">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline">{label(i.category)}</Badge>
                  <span className="text-xs text-muted-foreground">
                    {i.calls} call{i.calls === 1 ? "" : "s"} · {i.agent_caused} by the agent · fix in: {AREA[i.example.fix_area] ?? i.example.fix_area}
                  </span>
                </div>
                <p className="text-xs break-words text-muted-foreground">e.g. {i.example.what_happened}</p>
                <p className="text-xs break-words"><span className="font-medium">Fix: </span>{i.example.fix}</p>
              </button>
            ))}
          </div>
        ) : <p className="text-sm text-muted-foreground">No recurring problems found in the judged calls.</p>}
      </CardContent>
    </Card>
  );
}
