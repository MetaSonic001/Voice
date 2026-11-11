"use client";

// The automatic review of one call: the free checks, the judge's scores with verified evidence, and a place for a
// person to give their own scores (those labels are how we check the judge: the calibration).
import { CheckCircle2, CircleAlert, OctagonAlert, Play } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { audit, DIMENSIONS, type Dimension, type Review } from "@/lib/api";
import { cn } from "@/lib/utils";

const LABEL: Record<Dimension, string> = {
  task_completion: "Task completion", correctness: "Correctness", conversation: "Conversation", compliance: "Compliance & safety",
  handoff: "Handoff",
};
export const SOURCE: Record<string, string> = {
  agent: "Agent's own mistake", system_failure: "System failure", bank_system: "Bank system", knowledge_gap: "Gap in the documents",
  unclear_caller: "Unclear caller", insufficient_evidence: "Not enough evidence",
};
export const AREA: Record<string, string> = {
  router: "router (understanding)", retrieval: "retrieval", answer_prompt: "answer prompt", bank_templates: "bank answer wording",
  workflow: "workflow", code: "code", knowledge_base: "knowledge base", voice: "voice", none: "-",
};
export const label = (category: string) => category.replace(/_/g, " ");

const CHECK: Record<string, string> = {
  disclosure_said: "Opening disclosure played", auth_before_account_data: "Verified before any account data",
  no_personal_data_in_text: "No personal data left in the text", no_system_errors: "No system errors",
  replies_fast_enough: "Replies fast enough", caller_not_repeating_themselves: "Caller did not have to repeat themselves",
  not_stuck_saying_dont_know: "Not stuck saying “I don't know”", caller_not_asking_for_a_person: "Caller did not ask for a person",
  replies_short_enough: "Replies short enough", main_model_used: "Main model used (no fallback)",
};

export function ScoreChip({ score, critical, attention }: { score: number | null; critical?: boolean | null; attention?: boolean | null }) {
  if (critical) return <Badge variant="outline" className="border-destructive/50 text-destructive"><OctagonAlert className="size-3" /> Critical</Badge>;
  if (score == null) return attention ? <Badge variant="outline" className="border-warning/50 text-warning">Check</Badge> : null;
  const tone = score >= 4 ? "border-success/40 text-success" : score >= 3 ? "border-warning/50 text-warning" : "border-destructive/40 text-destructive";
  return <Badge variant="outline" className={cn("tabular-nums", tone)} title="Average of the judge's scores (1 to 5)">{score.toFixed(1)} / 5</Badge>;
}

export function ReviewPanel({ callId, review, onChange }: { callId: string; review: Review | null | undefined; onChange: () => void }) {
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      await audit.runReview(callId);
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!review) {
    return (
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-3">
          <div className="mr-auto space-y-1">
            <CardTitle className="text-base">Quality review</CardTitle>
            <CardDescription>Not reviewed yet. Calls are reviewed automatically when they end.</CardDescription>
          </div>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run()}><Play /> Review now</Button>
        </CardHeader>
      </Card>
    );
  }
  const failed = review.checks.filter((c) => !c.ok);
  return (
    <Card className="gap-4">
      <CardHeader className="flex flex-row flex-wrap items-start gap-3">
        <div className="mr-auto min-w-0 space-y-1">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Quality review <ScoreChip score={review.score} critical={review.critical} attention={review.needs_review} />
            {review.human && <Badge variant="secondary">Labelled by a person</Badge>}
          </CardTitle>
          <CardDescription className="break-words">
            {review.mode === "judged" ? `Judged by ${review.model}` : "Automatic checks only"} · {review.reason}
          </CardDescription>
        </div>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void run()}><Play /> Run again</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {review.needs_review && (
          <Alert className="border-warning/50">
            <CircleAlert className="text-warning" />
            <AlertTitle>A person should look at this call</AlertTitle>
            <AlertDescription>
              {review.critical ? "A critical failure was found (by a code check or the judge). " : ""}
              {failed.length ? failed.map((c) => c.detail || CHECK[c.name] || c.name).join(" · ") : review.judge?.recommended_fix}
            </AlertDescription>
          </Alert>
        )}

        <ul className="grid gap-x-6 gap-y-1.5 text-sm md:grid-cols-2">
          {review.checks.map((c) => (
            <li key={c.name} className="flex min-w-0 items-start gap-2">
              {c.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
                : <CircleAlert className={cn("mt-0.5 size-4 shrink-0", c.severity === "fail" ? "text-destructive" : "text-warning")} />}
              <span className="min-w-0 break-words">{CHECK[c.name] ?? c.name}{!c.ok && c.detail ? <span className="text-muted-foreground"> · {c.detail}</span> : null}</span>
            </li>
          ))}
        </ul>

        {review.judge && (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {DIMENSIONS.map((d) => {
                const v = review.judge!.dimension_scores[d];
                return (
                  <div key={d} className="rounded-md border p-2.5">
                    <p className="text-[11px] text-muted-foreground">{LABEL[d]}</p>
                    <p className="text-lg font-semibold tabular-nums">{v ? `${v} / 5` : "n/a"}</p>
                    {review.human?.scores[d] != null && <p className="text-[11px] text-muted-foreground">Person: {review.human.scores[d]} / 5</p>}
                  </div>
                );
              })}
            </div>
            {review.judge.failure_categories.length > 0 && (
              <div className="flex flex-wrap gap-1.5">{review.judge.failure_categories.map((c) => <Badge key={c} variant="outline">{c.replace(/_/g, " ")}</Badge>)}</div>
            )}
            {review.judge.recommended_fix && <p className="text-sm"><span className="font-medium">Most important fix: </span>{review.judge.recommended_fix}</p>}
            {(review.judge.findings ?? []).length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">What went wrong ({review.judge.findings!.length})</p>
                {review.judge.findings!.map((f, i) => (
                  <div key={i} className="space-y-1 rounded-md border p-2.5 text-sm">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline">{label(f.category)}</Badge>
                      <span className={cn("text-xs", f.source === "agent" ? "text-warning" : "text-muted-foreground")}>{SOURCE[f.source] ?? f.source}</span>
                      <span className="text-xs text-muted-foreground">
                        · <a href={`#turn-${f.turn}`} className="underline underline-offset-2"
                             onClick={(e) => { e.preventDefault(); document.getElementById(`turn-${f.turn}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>
                          turn {f.turn}</a> · fix in: {AREA[f.fix_area] ?? f.fix_area}
                      </span>
                    </div>
                    <p className="break-words">{f.what_happened}</p>
                    {f.quote && <blockquote className="border-l-2 pl-3 text-xs break-words text-muted-foreground">“{shortQuote(f.quote)}”</blockquote>}
                    <p className="text-xs text-muted-foreground break-words">Why it matters: {f.why_it_matters}</p>
                    <p className="text-xs break-words"><span className="font-medium">Fix: </span>{f.fix}</p>
                  </div>
                ))}
              </div>
            )}
            {review.judge.evidence.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">Evidence (every quote was checked against the transcript)</p>
                {review.judge.evidence.map((e, i) => (
                  <blockquote key={i} className="border-l-2 pl-3 text-sm break-words">
                    “{shortQuote(e.quote)}” <span className="text-xs text-muted-foreground">· turn {e.turn} · {(e.dimensions ?? [e.dimension]).map((d) => LABEL[d]).join(", ")}</span>
                  </blockquote>
                ))}
              </div>
            )}
            {!!review.judge.dropped_evidence && (
              <p className="text-xs text-muted-foreground">{review.judge.dropped_evidence} quote(s) the judge gave were not in the transcript and were dropped.</p>
            )}
            <LabelForm callId={callId} review={review} onSaved={onChange} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// A quote is checked in full on the brain; on screen a long one is shortened so the evidence list stays readable.
function shortQuote(q: string, words = 18): string {
  const parts = q.split(/\s+/);
  return parts.length > words ? `${parts.slice(0, words).join(" ")}…` : q;
}

function LabelForm({ callId, review, onSaved }: { callId: string; review: Review; onSaved: () => void }) {
  const [scores, setScores] = useState<Partial<Record<Dimension, number>>>(review.human?.scores ?? {});
  const [critical, setCritical] = useState(review.human?.critical ?? false);
  const [note, setNote] = useState(review.human?.note ?? "");

  async function save() {
    try {
      await audit.labelReview(callId, { scores, critical, note });
      toast.success("Saved. Thank you: this is how the judge is checked.");
      onSaved();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }
  return (
    <details className="rounded-md border p-3" open={!review.human && review.needs_review}>
      <summary className="cursor-pointer text-sm font-medium">Your own scores (checks the judge)</summary>
      <div className="mt-3 space-y-3">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {DIMENSIONS.map((d) => (
            <label key={d} className="space-y-1 text-xs text-muted-foreground">
              {LABEL[d]}
              <select value={scores[d] ?? ""} onChange={(e) => setScores({ ...scores, [d]: e.target.value ? Number(e.target.value) : undefined })}
                      className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground">
                <option value="">-</option>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={critical} onChange={(e) => setCritical(e.target.checked)} /> Tick if, in your view, this call had a critical failure
        </label>
        <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" maxLength={500} />
        <Button size="sm" onClick={() => void save()}>Save my scores</Button>
      </div>
    </details>
  );
}
