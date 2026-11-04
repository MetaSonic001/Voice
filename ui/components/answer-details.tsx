"use client";

// Everything about one answer, for testing: what the AI received, how it understood it, what it found, the checks,
// where the time went, and a correct / wrong verdict. Loaded from the answer's trace when opened.
import { useEffect, useState } from "react";

import { Badge, Button } from "@/components/ui";
import { api, type Trace, traceUrl } from "@/lib/api";

// Steps in the order they happen. Vector search, keyword search and embedding run in parallel.
const STEPS: [string, string][] = [
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

export function AnswerDetails({ traceId, timings }: { traceId: string; timings?: Record<string, number | null> }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState("");
  const [verdict, setVerdict] = useState<string>("");

  useEffect(() => {
    void api.trace(traceId).then((t) => {
      setTrace(t);
      setVerdict(t.data.feedback?.verdict ?? "");
    }).catch((e: Error) => setError(e.message));
  }, [traceId]);

  if (error) return <p className="mt-2">Could not load details: {error}</p>;
  if (!trace) return <p className="mt-2">Loading details…</p>;

  const d = trace.data;
  const t = { ...(d.timings ?? {}), ...(d.voice ?? {}), ...(timings ?? {}) };
  const steps = STEPS.filter(([k]) => typeof t[k] === "number");
  const longest = Math.max(1, ...steps.map(([k]) => Number(t[k])));
  const used = new Set(d.context_chunk_ids ?? []);
  const u = d.understanding;

  async function judge(v: "correct" | "wrong") {
    await api.feedback(traceId, v);
    setVerdict(v);
  }

  return (
    <div className="mt-2 space-y-3">
      <Section title="Sent to the AI as (after masking)">
        <p className="font-mono">{trace.question}</p>
      </Section>

      {u && (
        <Section title="Understood as">
          <p>
            <Badge className="mr-1">{u.language}</Badge><Badge className="mr-1">{u.intent}</Badge>
            {d.personal && <Badge className="mr-1">topic: {String(d.personal.query.topic)}</Badge>}
            {d.personal && Boolean(d.personal.query.account) && <Badge>account: {String(d.personal.query.account)}</Badge>}
          </p>
          {u.standalone_question !== trace.question && <p className="mt-1">Rewritten: {u.standalone_question}</p>}
          {u.intent === "general" && <p className="mt-1">Search keywords: {u.search_query_en}</p>}
        </Section>
      )}

      {steps.length > 0 && (
        <Section title="Where the time went (ms)">
          <div className="space-y-1">
            {steps.map(([k, label]) => (
              <div key={k} className="grid grid-cols-[10.5rem_1fr_3.5rem] items-center gap-2">
                <span>{label}</span>
                <span className="h-2 rounded bg-foreground/60" style={{ width: `${Math.max(2, (100 * Number(t[k])) / longest)}%` }} />
                <span className="text-right font-mono">{t[k]}</span>
              </div>
            ))}
          </div>
          <p className="mt-1">∥ = run at the same time. LLM steps include any wait for the provider (Groq free tier throttles).</p>
        </Section>
      )}

      {d.retrieval && (
        <Section title="Passages found (reranked; ● = given to the AI)">
          {d.retrieval.reranked.slice(0, 6).map((h) => (
            <p key={h.chunk_id} className="truncate" title={h.snippet}>
              {used.has(h.chunk_id) ? "●" : "○"} {h.rerank_score?.toFixed(2)} · {h.section} — {h.snippet.replace(/\s+/g, " ").slice(0, 90)}
            </p>
          ))}
        </Section>
      )}

      {d.checks && d.checks.length > 0 && (
        <Section title="Checks (every number must be in its cited source)">
          {d.checks.map((c, i) => (
            <p key={i}>{c.unsupported_numbers.length ? `✗ unsupported: ${c.unsupported_numbers.join(", ")}` : "✓"} {c.sentence}</p>
          ))}
        </Section>
      )}

      {d.personal && (
        <Section title="Bank data read">
          <p>{d.personal.fields.join(", ") || "none"} (answer filled from a template, not written by the AI)</p>
        </Section>
      )}

      {(d.reason || d.error) && <Section title="Why"><p>{d.reason ?? d.error}</p></Section>}

      <div className="flex flex-wrap items-center gap-2">
        <span>Was this right?</span>
        <Button variant={verdict === "correct" ? "default" : "outline"} className="h-7 px-2 text-xs" onClick={() => void judge("correct")}>✓ Correct</Button>
        <Button variant={verdict === "wrong" ? "default" : "outline"} className="h-7 px-2 text-xs" onClick={() => void judge("wrong")}>✗ Wrong</Button>
        <a className="ml-auto underline" href={traceUrl(traceId)} target="_blank" rel="noreferrer">full trace (JSON)</a>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-0.5 font-semibold text-foreground">{title}</p>
      {children}
    </div>
  );
}
