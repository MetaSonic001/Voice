// Small labels shared by the test console and the call audit.
import { Badge } from "@/components/ui/badge";
import type { Grounding } from "@/lib/api";
import { cn } from "@/lib/utils";

const OUTCOME: Record<string, string> = {
  answered: "Answered", refused: "Not answered", clarify: "Asked to clarify", handoff: "Transferred to a person",
  auth_required: "Verification", needs_verification: "Verification", smalltalk: "Small talk", out_of_scope: "Out of scope",
  error: "System error", interrupted: "Interrupted", confirm: "Waiting for your OK", action_done: "Done",
  action_failed: "Not completed", cancelled: "Cancelled", ticket: "Ticket raised", callback: "Callback booked",
};

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const tone = ["answered", "action_done", "ticket"].includes(outcome) ? "ok"
    : ["refused", "error", "action_failed"].includes(outcome) ? "bad" : outcome === "confirm" ? "warn" : "neutral";
  return <Badge variant="outline" className={cn(TONE[tone])}>{OUTCOME[outcome] ?? outcome}</Badge>;
}

const TONE = {
  ok: "border-success/40 text-success",
  warn: "border-warning/50 text-warning",
  bad: "border-destructive/40 text-destructive",
  neutral: "text-muted-foreground",
};

export function GroundingBadge({ g }: { g: Grounding | null | undefined }) {
  if (!g || g.level === "n/a") return null;
  const tone = g.level === "high" ? "ok" : g.level === "medium" ? "warn" : "bad";
  const label = g.level === "none" ? "No answer" : `Grounding: ${g.level}`;
  return (
    <Badge variant="outline" className={cn(TONE[tone])} title={g.basis.join(" · ")}>
      {label}
    </Badge>
  );
}

export function Dot({ tone }: { tone: keyof typeof TONE }) {
  const color = { ok: "bg-success", warn: "bg-warning", bad: "bg-destructive", neutral: "bg-muted-foreground" }[tone];
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", color)} />;
}

export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "-" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
