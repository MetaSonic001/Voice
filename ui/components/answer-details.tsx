"use client";

// Everything about one answer, for testing: what the AI received, how it understood it, what it found, the checks,
// where the time went, and a correct / wrong verdict. Loaded from the answer's trace when opened.
import { Check, ExternalLink, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { GroundingBadge } from "@/components/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type Grounding, type Trace, traceUrl } from "@/lib/api";

// Steps in the order they happen. Vector search, keyword search and embedding run in parallel.
export const STEPS: [string, string][] = [
  ["endpoint_ms", "Waiting for end of speech"],
  ["stt_ms", "Speech → text"],
  ["understand_ms", "Understanding (LLM)"],
  ["embed_ms", "Embed question ∥"],
  ["dense_ms", "Vector search ∥"],
  ["keyword_ms", "Keyword search ∥"],
  ["rerank_ms", "Rerank passages"],
  ["bank_ms", "Bank data"],
  ["llm_ttft_ms", "Answer: first token"],
  ["first_sentence_ms", "Answer: first checked sentence"],
  ["llm_ms", "Answer: complete"],
  ["total_ms", "Brain total"],
  ["first_audio_ms", "Caller hears something"],
  ["voice_to_voice_ms", "Caller hears the answer"],
];

export function LatencyBars({ t }: { t: Record<string, number | null | undefined> }) {
  const steps = STEPS.filter(([k]) => typeof t[k] === "number");
  if (!steps.length) return null;
  const longest = Math.max(1, ...steps.map(([k]) => Number(t[k])));
  return (
    <div className="space-y-1">
      {steps.map(([k, label]) => (
        <div key={k} className="grid grid-cols-[11rem_1fr_4rem] items-center gap-2">
          <span className="truncate">{label}</span>
          <span className="h-1.5 rounded-full bg-primary/70" style={{ width: `${Math.max(2, (100 * Number(t[k])) / longest)}%` }} />
          <span className="text-right font-mono tabular-nums">{t[k]}</span>
        </div>
      ))}
      <p className="pt-1 text-muted-foreground">∥ = run at the same time. LLM steps include any wait for the provider.</p>
    </div>
  );
}

export function AnswerDetails({ traceId, timings }: { traceId: string; timings?: Record<string, number | null> }) {
  const [trace, setTrace] = useState<(Trace & { data: { grounding?: Grounding } }) | null>(null);
  const [error, setError] = useState("");
  const [verdict, setVerdict] = useState<string>("");

  useEffect(() => {
    void api.trace(traceId).then((t) => {
      setTrace(t);
      setVerdict(t.data.feedback?.verdict ?? "");
    }).catch((e: Error) => setError(e.message));
  }, [traceId]);

  if (error) return <p className="mt-2 text-xs text-destructive">Could not load details: {error}</p>;
  if (!trace) return <p className="mt-2 text-xs text-muted-foreground">Loading details…</p>;

  const d = trace.data;
  const t = { ...(d.timings ?? {}), ...(d.voice ?? {}), ...(timings ?? {}) };
  const used = new Set(d.context_chunk_ids ?? []);
  const u = d.understanding;

  async function judge(v: "correct" | "wrong") {
    await api.feedback(traceId, v);
    setVerdict(v);
  }

  return (
    <div className="mt-2 space-y-3 rounded-md border bg-background p-3 text-xs">
      <Section title="Sent to the AI as (after masking)">
        <p className="font-mono">{trace.question}</p>
      </Section>

      {u && (
        <Section title="Understood as">
          <div className="flex flex-wrap gap-1">
            <Badge variant="secondary">{u.language}</Badge>
            <Badge variant="secondary">{u.intent}</Badge>
            {d.personal && <Badge variant="secondary">topic: {String(d.personal.query.topic)}</Badge>}
            {d.personal && Boolean(d.personal.query.account) && <Badge variant="secondary">account: {String(d.personal.query.account)}</Badge>}
            <GroundingBadge g={d.grounding} />
          </div>
          {u.standalone_question !== trace.question && <p className="mt-1">Rewritten: {u.standalone_question}</p>}
          {u.intent === "general" && <p className="mt-1 text-muted-foreground">Search keywords: {u.search_query_en}</p>}
        </Section>
      )}

      <Section title="Where the time went (ms)"><LatencyBars t={t} /></Section>

      {d.retrieval && (
        <Section title="Passages found (reranked; ● = given to the AI)">
          {d.retrieval.reranked.slice(0, 6).map((h) => (
            <p key={h.chunk_id} className="truncate" title={h.snippet}>
              <span className={used.has(h.chunk_id) ? "text-foreground" : "text-muted-foreground"}>{used.has(h.chunk_id) ? "●" : "○"}</span>{" "}
              <span className="font-mono">{h.rerank_score?.toFixed(2)}</span> · {h.section} - {h.snippet.replace(/\s+/g, " ").slice(0, 90)}
            </p>
          ))}
        </Section>
      )}

      {d.checks && d.checks.length > 0 && (
        <Section title="Checks (every number must be in its cited source)">
          {d.checks.map((c, i) => (
            <p key={i} className="flex gap-1.5">
              {c.unsupported_numbers.length ? <X className="size-3.5 shrink-0 text-destructive" /> : <Check className="size-3.5 shrink-0 text-success" />}
              <span>{c.unsupported_numbers.length ? `unsupported: ${c.unsupported_numbers.join(", ")} · ` : ""}{c.sentence}</span>
            </p>
          ))}
        </Section>
      )}

      {d.personal && (
        <Section title="Bank data read">
          <p>{d.personal.fields.join(", ") || "none"} (answer filled from a template, not written by the AI)</p>
        </Section>
      )}

      {(d.reason || d.error) && <Section title="Why"><p>{d.reason ?? d.error}</p></Section>}

      <div className="flex flex-wrap items-center gap-2 border-t pt-2">
        <span className="text-muted-foreground">Was this right?</span>
        <Button size="sm" variant={verdict === "correct" ? "default" : "outline"} className="h-7" onClick={() => void judge("correct")}>
          <ThumbsUp /> Correct
        </Button>
        <Button size="sm" variant={verdict === "wrong" ? "default" : "outline"} className="h-7" onClick={() => void judge("wrong")}>
          <ThumbsDown /> Wrong
        </Button>
        <a className="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground" href={traceUrl(traceId)} target="_blank" rel="noreferrer">
          Full trace <ExternalLink className="size-3" />
        </a>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="mb-1 font-medium text-foreground">{title}</p>
      {children}
    </div>
  );
}
