"use client";

// Guardrails & tests tab (read only): the rules code enforces whatever the wording, and the test conversations a
// developer runs before a change reaches every caller.
import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { journeys, type Reference, type TestCase } from "@/lib/api";

export function ReferenceTab() {
  const [ref, setRef] = useState<Reference | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    journeys.reference().then(setRef).catch((e: Error) => setError(e.message));
  }, []);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!ref) return <Skeleton className="h-64 w-full" />;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4" /> Guardrails</CardTitle>
          <CardDescription>
            Enforced by code on every call, whatever the wording or prompts say. They can&apos;t be switched off here: changing one is
            a code change with its own tests.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          {ref.guardrails.map((g) => (
            <div key={g.name} className="space-y-1 rounded-md border p-3">
              <p className="text-sm font-medium">{g.name}</p>
              <p className="text-sm break-words text-muted-foreground">{g.what}</p>
              <p className="text-[11px] break-words text-muted-foreground">Enforced in {g.where}</p>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Test conversations</CardTitle>
          <CardDescription>
            Run by a developer on the real AI (python -m evals.smoke, and --pack regression) before a change goes to every
            caller. Each turn lists what the caller says and what the reply must or must not contain. There are also{" "}
            {ref.tests.golden_count} product questions with known answers (the golden set).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Pack title="Smoke set" cases={ref.tests.smoke} />
          <Pack title="Regression pack (whole calls from past reviews)" cases={ref.tests.regression} />
        </CardContent>
      </Card>
    </div>
  );
}

function Pack({ title, cases }: { title: string; cases: TestCase[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{title}</p>
        <Badge variant="secondary">{cases.length}</Badge>
        <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>{open ? "Hide" : "Show"}</Button>
      </div>
      {open && cases.map((c) => (
        <div key={c.id} className="space-y-1.5 rounded-md border p-3 text-sm">
          <p className="flex flex-wrap items-center gap-2"><code className="text-xs">{c.id}</code>{c.group && <Badge variant="outline">{c.group}</Badge>}
            <span className="text-xs text-muted-foreground">{c.caller ? `caller ${c.caller}` : "unknown caller"}</span></p>
          <ol className="list-decimal space-y-1 pl-5">
            {c.turns.map((t, i) => (
              <li key={i} className="break-words">
                “{t.say}”
                <span className="block text-xs text-muted-foreground">
                  {t.outcome && `outcome: ${t.outcome.join(" or ")}`}
                  {t.has && ` · must say: ${t.has.join(", ")}`}
                  {t.has_any && ` · one of: ${t.has_any.join(", ")}`}
                  {t.lacks && ` · never: ${t.lacks.join(", ")}`}
                  {t.max_words && ` · at most ${t.max_words} words`}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}
