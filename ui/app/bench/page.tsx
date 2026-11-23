"use client";

// Speed and quality of the setup the brain runs with now, against a benchmark to beat. Turns are grouped by setup
// (models, reasoning effort, speech, search, prompt version), so the numbers move only when the setup changes, not
// turn by turn. The benchmark is pinned by a person (frozen at that moment) or, if nothing is pinned, the last other
// setup with enough real turns. Loaded when the page opens; "Refresh" reloads (no polling).
import { ArrowDownRight, ArrowUpRight, Gauge, Pin, PinOff, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { bench, type Bench, type BenchLatency, type BenchRow, type BenchSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

const HEADLINE = ["voice_to_voice_ms", "first_reply_ms", "understand_ms", "llm_ttft_ms"];
const SETUP_LABELS: Record<string, string> = {
  llm_provider: "LLM provider", understand_model: "Understanding model", understand_effort: "Understanding reasoning",
  answer_model: "Answer model", answer_effort: "Answer reasoning", stt: "Speech to text", tts: "Text to speech",
  search: "Search", vector_store: "Vector store", prompt_version: "Prompt version", rerank: "Reranker",
};

const isLatency = (v: BenchRow["current"]): v is BenchLatency => typeof v === "object" && v !== null;
const when = (iso?: string) => (iso ? new Date(iso).toLocaleString() : "");

function value(v: BenchRow["current"]) {
  if (v === null || v === undefined) return <span className="text-muted-foreground">-</span>;
  if (isLatency(v)) return <span className="tabular-nums">{v.p50} <span className="text-muted-foreground">/ {v.p95}</span> <span className="text-xs text-muted-foreground">({v.n})</span></span>;
  return <span className="tabular-nums">{v}</span>;
}

function Change({ row }: { row: BenchRow }) {
  if (row.delta === null) return <span className="text-muted-foreground">-</span>;
  const Icon = row.delta < 0 ? ArrowDownRight : ArrowUpRight;
  return (
    <span className={cn("inline-flex items-center gap-0.5 tabular-nums",
      row.better === true ? "text-success" : row.better === false ? "text-destructive" : "text-muted-foreground")}>
      {row.delta !== 0 && <Icon className="size-3.5" />}
      {row.delta > 0 ? "+" : ""}{row.delta}{row.group === "latency" ? " ms" : ""}
      {row.delta_pct !== null && <span className="text-xs">({row.delta_pct > 0 ? "+" : ""}{row.delta_pct}%)</span>}
    </span>
  );
}

function Headline({ row }: { row: BenchRow }) {
  const cur = isLatency(row.current) ? row.current : null;
  const base = isLatency(row.baseline) ? row.baseline : null;
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-2">
        <CardDescription className="truncate" title={row.label}>{row.label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{cur ? `${cur.p50} ms` : "-"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-xs text-muted-foreground">
        <div>p95 {cur ? `${cur.p95} ms` : "-"} · {cur?.n ?? 0} turns</div>
        <div className="flex flex-wrap items-center gap-1">Benchmark {base ? `${base.p50} ms` : "-"} <Change row={row} /></div>
      </CardContent>
    </Card>
  );
}

function SetupCard({ title, s, other, badge }: { title: string; s: BenchSummary | null; other: BenchSummary | null; badge?: React.ReactNode }) {
  return (
    <Card className="min-w-0">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">{title} {badge}</CardTitle>
        <CardDescription className="break-words">
          {s ? <>{s.label} · <code className="text-xs">{s.setup_id}</code> · {s.turns} real turns{s.pinned_at ? ` · pinned ${when(s.pinned_at)}` : ""}</>
            : "No setup to compare with yet."}
        </CardDescription>
      </CardHeader>
      {s && (
        <CardContent className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          {Object.entries(SETUP_LABELS).map(([k, label]) => {
            const fallback = k === "rerank" ? "on" : undefined; // the brain writes "rerank" only when it is off
            const changed = other && (other.setup?.[k] ?? fallback) !== (s.setup?.[k] ?? fallback);
            return [
              <span key={`${k}-l`} className="text-muted-foreground">{label}</span>,
              <span key={`${k}-v`} className={cn("min-w-0 break-words", changed && "font-medium text-warning")}>{s.setup?.[k] ?? fallback ?? "-"}</span>,
            ];
          })}
        </CardContent>
      )}
    </Card>
  );
}

function MetricTable({ rows, unit }: { rows: BenchRow[]; unit: string }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="sticky left-0 bg-card">What</TableHead>
            <TableHead>Now {unit}</TableHead>
            <TableHead>Benchmark {unit}</TableHead>
            <TableHead>Change</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell className="sticky left-0 bg-card">{r.label}</TableCell>
              <TableCell>{value(r.current)}</TableCell>
              <TableCell>{value(r.baseline)}</TableCell>
              <TableCell><Change row={r} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export default function BenchPage() {
  const [data, setData] = useState<Bench | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await bench.get());
      setError("");
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      toast.success(done);
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const rows = data?.rows ?? [];
  const byKey = new Map(rows.map((r) => [r.key, r]));
  const few = (s: BenchSummary | null) => s && !s.enough;

  return (
    <main className="mx-auto max-w-[1100px] space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Gauge className="size-5" /> Speed &amp; quality</CardTitle>
          <CardDescription>
            The setup the brain runs with now, against the benchmark to beat. Numbers change only when the setup changes
            (model, reasoning, speech, search or prompt), not with every turn. Speed is the middle value (p50) and the slow
            end (p95) in milliseconds, from real turns only: answers replayed from the eval cache are left out.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy || !data?.current} onClick={() => act(() => bench.pin(), "This setup is now the benchmark.")}>
            <Pin className="size-4" /> Keep this setup as the benchmark
          </Button>
          {data?.baseline_from === "pinned" && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => act(() => bench.unpin(), "Back to the automatic benchmark.")}>
              <PinOff className="size-4" /> Use the automatic benchmark
            </Button>
          )}
          <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => void load()}>
            <RefreshCw className="size-4" /> Refresh
          </Button>
          {error && <p className="w-full text-sm text-destructive">{error}</p>}
          {data && (few(data.current) || few(data.baseline)) && (
            <p className="w-full text-sm text-warning">
              Fewer than {data.min_turns} real turns on {few(data.current) ? "this setup" : "the benchmark"}: treat the comparison as a hint.
            </p>
          )}
          {data?.baseline_is_current && (
            <p className="w-full text-sm text-muted-foreground">The benchmark is this same setup (frozen when it was pinned).</p>
          )}
        </CardContent>
      </Card>

      {!data && !error && <Skeleton className="h-40 w-full" />}

      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {HEADLINE.map((k) => byKey.get(k)).filter((r): r is BenchRow => !!r).map((r) => <Headline key={r.key} row={r} />)}
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <SetupCard title="Now" s={data.current} other={data.baseline} />
            <SetupCard title="Benchmark" s={data.baseline} other={data.current}
              badge={<Badge variant="secondary">{data.baseline_from === "pinned" ? "pinned" : "automatic"}</Badge>} />
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Speed (ms): p50 / p95 (turns)</CardTitle>
              <CardDescription>Green is faster than the benchmark, red is slower. The change compares p50.</CardDescription>
            </CardHeader>
            <CardContent><MetricTable rows={rows.filter((r) => r.group === "latency")} unit="" /></CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Quality</CardTitle>
              <CardDescription>
                Accuracy comes from the smoke, regression and golden runs recorded for each setup (run them after a change). Answer
                confidence is the score of the best passage behind a KB answer; there is no separate model-reported confidence.
                Percentages are shares of turns.
              </CardDescription>
            </CardHeader>
            <CardContent><MetricTable rows={rows.filter((r) => r.group === "quality")} unit="" /></CardContent>
          </Card>

          {data.others.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Other setups</CardTitle>
                <CardDescription>Earlier setups with real turns. Keep one as the benchmark to compare with it instead.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.others.map((o) => (
                  <div key={o.id} className="flex flex-wrap items-center gap-2 text-sm">
                    <code className="text-xs">{o.id}</code>
                    <span className="text-muted-foreground">{o.n} turns · last {when(o.last_at)}</span>
                    <Button size="sm" variant="outline" className="ml-auto h-7" disabled={busy}
                      onClick={() => act(() => bench.pin(o.id), "Benchmark replaced.")}>
                      <Pin className="size-3.5" /> Keep as benchmark
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </main>
  );
}
