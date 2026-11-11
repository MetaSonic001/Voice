"use client";

// One call's full record: summary, recording, the conversation with the reason for every reply, the audit trail
// and the plain-text transcript. Everything shown is what was stored at the time of the call.
import {
  AlertTriangle, AudioLines, ChevronDown, Download, FileText, Lock, Play, Printer, RefreshCw, ShieldAlert, ShieldCheck, Trash2,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { LatencyBars } from "@/components/answer-details";
import { ReviewPanel } from "@/components/review-panel";
import { fmtMs, GroundingBadge, OutcomeBadge } from "@/components/labels";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { audit, type CallRecord, type Recording, type Turn } from "@/lib/api";
import { cn } from "@/lib/utils";

const AUTH_TEXT: Record<string, string> = {
  mobile_requested: "Asked for the registered mobile number", mobile_not_registered: "Number is not registered",
  otp_sent: "One-time code sent", otp_failed: "Wrong code entered", otp_expired: "Code expired",
  locked: "Verification locked (too many wrong codes)", otp_verified: "Caller verified", too_many_codes: "Too many codes requested",
  verification_ended: "Verification ended", verification_expired: "Verification expired (idle)", data_read: "Account data read",
};
const INPUT: Record<string, string> = { speech: "spoken", voice_note: "voice note", typed: "typed", text: "typed", after_verification: "after verification", after_verification_chat: "after verification" };
// Answers with no new question (a yes / no to the agent's question) are shown as short replies.
const time = (s: string) => new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const clock = (sec: number) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;

type Item =
  | { at: string; kind: "turn"; turn: Turn }
  | { at: string; kind: "event"; label: string; text?: string; tone?: "warn" | "bad" }
  | { at: string; kind: "agent_line"; label: string; text: string };

// A permanent delete, asked for once more before it happens.
function ConfirmDelete({ trigger, title, text, onConfirm }: { trigger: ReactNode; title: string; text: string; onConfirm: () => void }) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{text}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-white hover:bg-destructive/90">Delete for good</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function CallRecordView({ id, onDeleted }: { id: string; onDeleted?: () => void }) {
  const [call, setCall] = useState<CallRecord | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("conversation");
  const [audioUrl, setAudioUrl] = useState<string>("");
  const audioRef = useRef<HTMLAudioElement>(null);

  const load = useCallback(async () => {
    try {
      setCall(await audit.call(id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const callRec = call?.recordings.find((r) => r.kind === "call" && r.available);
  useEffect(() => {
    if (!callRec) return;
    let url = "";
    void audit.blob(audit.recordingPath(id, callRec.id)).then((b) => {
      url = URL.createObjectURL(b);
      setAudioUrl(url);
    }).catch((e: Error) => setError(`Recording: ${e.message}`));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id, callRec]);

  const items = useMemo(() => (call ? timeline(call) : []), [call]);

  if (error && !call) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!call) return <div className="space-y-4"><Skeleton className="h-40" /><Skeleton className="h-96" /></div>;

  const start = new Date(call.created_at);
  const last = call.ended_at ?? call.turns.at(-1)?.created_at ?? call.created_at;
  const duration = (new Date(last).getTime() - start.getTime()) / 1000;
  const answered = call.turns.filter((t) => t.outcome === "answered").length;
  const unanswered = call.turns.filter((t) => ["refused", "error"].includes(t.outcome)).length;

  function seek(turn: Turn) {
    if (!callRec || !audioRef.current) return;
    const asked = new Date(turn.created_at).getTime() - turn.total_ms - (turn.timings.stt_ms ?? 0) - (turn.timings.endpoint_ms ?? 0);
    audioRef.current.currentTime = Math.max(0, (asked - new Date(callRec.started_at).getTime()) / 1000 - 2.5);
    void audioRef.current.play();
  }

  async function download() {
    const b = await audit.blob(audit.transcriptPath(id));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `call-${id.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function deleteCall() {
    try {
      await audit.deleteCall(id);
      toast.success("Call deleted, with its recordings, review and fix-list items.");
      onDeleted?.();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function deleteRecording(recordingId: string) {
    try {
      await audit.deleteRecording(id, recordingId);
      toast.success("Recording deleted.");
      setAudioUrl("");
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  function printRecord() {
    setTab("conversation");
    setTimeout(() => window.print(), 100);
  }

  return (
    <div className="space-y-4">
      {/* summary */}
      <Card className="gap-4">
        <CardHeader className="flex flex-row flex-wrap items-start gap-3">
          <div className="mr-auto space-y-1">
            <CardTitle className="flex items-center gap-2 text-base">
              Call <span className="font-mono text-sm text-muted-foreground">{call.id.slice(0, 8)}</span>
              <Integrity call={call} />
            </CardTitle>
            <CardDescription>
              {start.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" })},{" "}
              {time(call.created_at)}{call.ended_at && ` to ${time(call.ended_at)}`} · {clock(duration)}
              {call.end_reason && ` · ${call.end_reason}`}
            </CardDescription>
          </div>
          <div className="no-print flex gap-2">
            <Button size="sm" variant="outline" onClick={() => void load()}><RefreshCw /> Refresh</Button>
            <Button size="sm" variant="outline" onClick={() => void download()}><Download /> Transcript</Button>
            <Button size="sm" variant="outline" onClick={printRecord}><Printer /> Save as PDF</Button>
            <ConfirmDelete
              trigger={<Button size="sm" variant="outline" className="text-destructive"><Trash2 /> Delete call</Button>}
              title="Delete this call for good?"
              text="Its turns, timeline, recordings, quality review and fix-list items are removed from the database, and the audio files are deleted. This cannot be undone."
              onConfirm={() => void deleteCall()} />
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
            <Meta label="Caller" value={call.caller ?? "Number withheld"} />
            <Meta label="Verification" value={call.verified_at ? <span className="flex items-center gap-1 text-success"><ShieldCheck className="size-3.5" /> Verified {time(call.verified_at)}</span> : "Not verified"} />
            <Meta label="Turns" value={`${call.turns.length} turn${call.turns.length === 1 ? "" : "s"} · ${answered} answered${unanswered ? ` · ${unanswered} not answered` : ""}`} />
            <Meta label="Recording" value={callRec ? `Live call, ${clock(callRec.duration_ms / 1000)}` : call.recordings.some((r) => r.kind === "voice_note") ? "Voice notes" : "None (typed)"} />
          </dl>
        </CardContent>
      </Card>

      <ReviewPanel callId={call.id} review={call.review} onChange={() => void load()} />

      {call.alerts.length > 0 && (
        <Alert className="border-warning/50">
          <AlertTriangle className="text-warning" />
          <AlertTitle>{call.alerts.length} alert{call.alerts.length > 1 ? "s" : ""} in this call</AlertTitle>
          <AlertDescription>
            {call.alerts.map((a) => <p key={a.id}>{time(a.created_at)} · {a.message}</p>)}
          </AlertDescription>
        </Alert>
      )}

      {callRec && (
        <Card className="no-print gap-3 py-4">
          <CardContent className="flex flex-wrap items-center gap-4">
            <AudioLines className="size-5 text-muted-foreground" />
            {audioUrl ? <audio ref={audioRef} controls src={audioUrl} className="h-10 min-w-0 flex-1" />
                      : <Skeleton className="h-10 flex-1" />}
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Lock className="size-3" /> Stereo: caller left, agent right · encrypted at rest
            </p>
            <ConfirmDelete
              trigger={<Button size="sm" variant="ghost" className="text-destructive"><Trash2 /> Delete recording</Button>}
              title="Delete this recording for good?"
              text="The recording and its audio file are deleted. The call's transcript and review stay. This cannot be undone."
              onConfirm={() => void deleteRecording(callRec.id)} />
          </CardContent>
        </Card>
      )}
      {call.recordings.filter((r) => r.kind === "voice_note" && r.available).length > 0 && (
        <Card className="no-print gap-2 py-3">
          <CardContent className="space-y-1.5 text-sm">
            <p className="text-xs font-medium text-muted-foreground">Voice notes in this call</p>
            {call.recordings.filter((r) => r.kind === "voice_note" && r.available).map((r) => (
              <div key={r.id} className="flex items-center gap-2">
                <AudioLines className="size-4 text-muted-foreground" />
                <span>{time(r.started_at)} · {clock(r.duration_ms / 1000)}</span>
                <ConfirmDelete
                  trigger={<Button size="sm" variant="ghost" className="ml-auto text-destructive"><Trash2 /> Delete</Button>}
                  title="Delete this voice note for good?"
                  text="The recording and its audio file are deleted. This cannot be undone."
                  onConfirm={() => void deleteRecording(r.id)} />
              </div>
            ))}
          </CardContent>
        </Card>
      )}
      {call.recordings.some((r) => !r.available && r.deleted_at) && (
        <p className="text-xs text-muted-foreground">A recording of this call was deleted by the retention policy.</p>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="no-print">
          <TabsTrigger value="conversation">Conversation</TabsTrigger>
          <TabsTrigger value="trail">Audit trail</TabsTrigger>
          <TabsTrigger value="transcript">Transcript</TabsTrigger>
        </TabsList>

        <TabsContent value="conversation" className="mt-3">
          <Card className="py-4">
            <CardContent className="space-y-5">
              {items.length === 0 && <p className="text-sm text-muted-foreground">Nothing was said in this call.</p>}
              {items.map((it, i) => <TimelineItem key={i} it={it} call={call} canSeek={!!callRec && !!audioUrl} onSeek={seek} />)}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="trail" className="mt-3">
          <Card className="py-2"><CardContent className="px-2"><Trail call={call} /></CardContent></Card>
        </TabsContent>

        <TabsContent value="transcript" className="mt-3">
          <TranscriptPreview id={id} onDownload={() => void download()} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium">{value}</dd>
    </div>
  );
}

function Integrity({ call }: { call: CallRecord }) {
  const i = call.integrity;
  const badge = i.status === "intact"
    ? <Badge variant="outline" className="border-success/40 text-success"><ShieldCheck /> Record intact</Badge>
    : i.status === "broken"
      ? <Badge variant="outline" className="border-destructive/50 text-destructive"><ShieldAlert /> Record altered</Badge>
      : <Badge variant="outline" className="text-muted-foreground">Not sealed</Badge>;
  const text = i.status === "intact"
    ? `All ${i.turns_checked} turns match their SHA-256 chain. Chain head ${i.head?.slice(0, 16)}…`
    : i.status === "broken" ? `Turn ${i.turn}: ${i.problem}.` : "These turns were saved before tamper evidence was switched on.";
  return (
    <Tooltip>
      <TooltipTrigger asChild><span>{badge}</span></TooltipTrigger>
      <TooltipContent className="max-w-xs">{text}</TooltipContent>
    </Tooltip>
  );
}

function timeline(call: CallRecord): Item[] {
  // A turn is placed at the moment the caller asked (it is saved when the reply is complete).
  const items: Item[] = call.turns.map((t) => ({ at: new Date(new Date(t.created_at).getTime() - t.total_ms).toISOString(), kind: "turn", turn: t }));
  for (const e of call.events) {
    const d = e.detail as Record<string, string>;
    if (e.kind === "disclosure") items.push({ at: e.created_at, kind: "agent_line", label: "Opening disclosure", text: d.text });
    else if (e.kind === "call_started") items.push({ at: e.created_at, kind: "event", label: "Live call started" });
    else if (e.kind === "call_ended") items.push({ at: e.created_at, kind: "event", label: `Call ended: ${d.reason ?? ""}` });
    else if (e.kind === "interrupted") items.push({ at: e.created_at, kind: "event", label: "Caller interrupted the agent", text: d.while_saying ? `while it was saying "${d.while_saying}"` : undefined });
    else if (e.kind === "error") items.push({ at: e.created_at, kind: "event", label: `System error (${d.source})`, text: d.error, tone: "bad" });
    else if (e.kind === "card_action") items.push({ at: e.created_at, kind: "event", tone: d.done ? undefined : "bad",
      label: `Card action: ${String(d.action).replace("_", " ")} ${d.card}`, text: d.done ? `done, reference ${d.reference}` : "did not complete" });
    else if (e.kind === "ticket") items.push({ at: e.created_at, kind: "event", label: `Ticket ${d.id} raised`, text: `(${d.kind}, ${d.department})` });
    else if (e.kind === "handoff_requested") items.push({ at: e.created_at, kind: "event", tone: "warn", label: `Transfer to ${d.department} requested`, text: d.reason });
    else if (e.kind === "on_hold") items.push({ at: e.created_at, kind: "event", label: "Caller on hold (music)" });
    else if (e.kind === "handoff_accepted") items.push({ at: e.created_at, kind: "event", label: `${d.agent} picked up` });
    else if (e.kind === "handoff_completed") items.push({ at: e.created_at, kind: "event", label: `${d.agent ?? "Agent"} completed it: ${d.disposition}`, text: d.notes });
  }
  for (const a of call.auth_events) {
    if (a.event === "data_read") continue; // shown with the answer that read it
    items.push({ at: a.created_at, kind: "event", label: AUTH_TEXT[a.event] ?? a.event,
                 tone: ["locked", "otp_failed"].includes(a.event) ? "warn" : undefined });
  }
  for (const a of call.alerts) items.push({ at: a.created_at, kind: "event", label: `Alert: ${a.message}`, tone: a.severity === "critical" ? "bad" : "warn" });
  return items.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
}

function TimelineItem({ it, call, canSeek, onSeek }: { it: Item; call: CallRecord; canSeek: boolean; onSeek: (t: Turn) => void }) {
  if (it.kind === "event") {
    return (
      <div className={cn("flex items-center gap-3 text-xs", it.tone === "bad" ? "text-destructive" : it.tone === "warn" ? "text-warning" : "text-muted-foreground")}>
        <span className="h-px flex-1 bg-border" />
        <span>{time(it.at)} · {it.label}{it.text && <span className="opacity-80"> {it.text}</span>}</span>
        <span className="h-px flex-1 bg-border" />
      </div>
    );
  }
  if (it.kind === "agent_line") {
    return <AgentBubble at={it.at} label={it.label}><p>{it.text}</p></AgentBubble>;
  }
  return <TurnBlock t={it.turn} call={call} canSeek={canSeek} onSeek={onSeek} />;
}

function AgentBubble({ at, label, children, extra }: { at: string; label: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">AI</span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="text-xs text-muted-foreground">{label} · {time(at)}</p>
        <div className="rounded-2xl rounded-tl-sm border bg-card px-4 py-2.5 text-sm leading-relaxed">{children}</div>
        {extra}
      </div>
    </div>
  );
}

// Citation markers like [1] stay in the stored record (the checks and the audit trail use them) but are not shown in the
// conversation: each sentence is listed under the source it came from instead.
const MARKERS = /\s*\[\d+\]/g;
export const plainAnswer = (text: string) => text.replace(MARKERS, "").replace(/\s+([.,;:?!])/g, "$1");

function sentencesBySource(answer: string): Map<number, string[]> {
  const out = new Map<number, string[]>();
  for (const m of answer.matchAll(/([^.!?\[\]]+[^\[\]]*?)((?:\s*\[\d+\])+)/g)) {
    const sentence = m[1].replace(/^[\s.,;:]+/, "").trim();
    for (const n of m[2].match(/\d+/g) ?? []) out.set(Number(n), [...(out.get(Number(n)) ?? []), sentence]);
  }
  return out;
}

function Sources({ t }: { t: Turn }) {
  const kb = t.citations.filter((c) => c.marker != null && c.document);
  const bank = t.citations.filter((c) => c.source && c.fields?.length);
  if (!kb.length && !bank.length) return null;
  const used = sentencesBySource(t.answer);
  const score = Object.fromEntries(t.sources.map((s) => [s.chunk_id, s.score]));
  return (
    <details className="group">
      <summary className="cursor-pointer list-none font-medium text-foreground marker:hidden">
        <ChevronDown className="mr-1 inline size-3 transition-transform group-open:rotate-180" />
        Where the answer came from ({kb.length + bank.length})
      </summary>
      <div className="mt-1.5 space-y-1.5">
      {kb.map((c) => (
        <div key={c.marker} className="rounded-md border bg-background px-2.5 py-2">
          <p className="text-foreground">
            <span className="font-medium">{c.document}</span>
            {(() => {  // the last heading of the section, unless it only repeats the document's name
              const part = String(c.section ?? "").split(" › ").slice(-1)[0].split("; ")[0];
              return part && part !== c.document ? <span className="text-muted-foreground"> › {part}</span> : null;
            })()}
            {c.effective_from && <span className="text-muted-foreground"> · valid from {c.effective_from}</span>}
            {c.chunk_id && score[c.chunk_id] != null && <span className="text-muted-foreground"> · match {Number(score[c.chunk_id]).toFixed(2)}</span>}
          </p>
          {(used.get(c.marker!) ?? []).map((s, i) => <p key={i} className="mt-1 text-foreground">Used for: “{s}”</p>)}
          {c.snippet && <p className="mt-1 line-clamp-2 text-muted-foreground">Source text: {c.snippet}</p>}
        </div>
      ))}
      {bank.map((c, i) => (
        <p key={i} className="rounded-md border bg-background px-2.5 py-2">
          <span className="font-medium text-foreground">Bank records</span> ({c.source}): {c.fields!.join(", ")}
        </p>
      ))}
      </div>
    </details>
  );
}

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

// The bank systems this turn called: which department's system, what was asked for, and whether it worked.
function BankCalls({ t }: { t: Turn }) {
  const calls = t.bank_calls ?? [];
  if (!calls.length) {
    return t.bank?.fields?.length
      ? <p className="text-muted-foreground">Bank data: used what was read earlier in this call (no new bank calls this turn).</p>
      : null;
  }
  const systems = [...new Set(calls.map((c) => c.name))];
  const failed = calls.filter((c) => typeof c.status !== "number" || c.status >= 400).length;
  return (
    <details className="group">
      <summary className="cursor-pointer list-none font-medium text-foreground marker:hidden">
        <ChevronDown className="mr-1 inline size-3 transition-transform group-open:rotate-180" />
        Bank systems called ({calls.length} call{calls.length === 1 ? "" : "s"} to {systems.length} system{systems.length === 1 ? "" : "s"}
        {failed ? `, ${failed} failed` : ""})
      </summary>
      <div className="mt-1.5 space-y-1">
        {calls.map((c, i) => {
          const ok = typeof c.status === "number" && c.status < 400;
          return (
            <p key={i} className="flex flex-wrap items-center gap-x-2 rounded-md border bg-background px-2.5 py-1.5">
              <span className="font-medium text-foreground">{c.name}</span>
              <span className="font-mono text-[11px] text-muted-foreground">{c.method} {c.path}</span>
              <span className={cn("ml-auto tabular-nums", ok ? "text-success" : "text-destructive")}>
                {String(c.status)} · {c.ms} ms{c.attempts > 1 ? ` · ${c.attempts} tries` : ""}
              </span>
            </p>
          );
        })}
      </div>
    </details>
  );
}

function TurnBlock({ t, call, canSeek, onSeek }: { t: Turn; call: CallRecord; canSeek: boolean; onSeek: (t: Turn) => void }) {
  const [more, setMore] = useState(false);
  const note = call.recordings.find((r) => r.kind === "voice_note" && r.trace_id === t.id && r.available);
  const asked = new Date(new Date(t.created_at).getTime() - t.total_ms).toISOString();
  const n = call.turns.findIndex((x) => x.id === t.id) + 1;  // the same numbering the post-call judge uses
  const prev = n > 1 ? call.turns[n - 2] : null;
  // After the keypad code the agent picks the caller's question up again by itself: the caller did not say it twice.
  // (Labelled since 7 Nov; older calls: the same question right after a turn that asked for verification.)
  const reask = t.input.startsWith("after_verification")
    || (!!prev && prev.outcome === "auth_required" && norm(prev.question) === norm(t.question));
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (typeof window !== "undefined" && window.location.hash === `#turn-${n}`) ref.current?.scrollIntoView({ block: "center" });
  }, [n]);
  return (
    <div ref={ref} id={`turn-${n}`} className="scroll-mt-20 space-y-3">
      <p className="flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
        <span className="rounded border px-1.5 py-0.5">Turn {n}</span><span className="h-px flex-1 bg-border" />
      </p>
      {/* caller (or the agent picking the question up again after verification) */}
      {reask ? (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="h-px flex-1 bg-border" />
          <span className="max-w-[80%] text-center">After verification, the agent picked up the caller&apos;s question again: “{t.question}”</span>
          <span className="h-px flex-1 bg-border" />
        </div>
      ) : (
      <div className="flex flex-row-reverse gap-3">
        <span className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-secondary text-[10px] font-semibold">C</span>
        <div className="flex max-w-[80%] flex-col items-end space-y-1.5">
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            {canSeek && <Button variant="link" size="sm" className="h-auto gap-1 p-0 text-xs no-print" onClick={() => onSeek(t)}><Play className="size-3" /> Play from here</Button>}
            Caller · {INPUT[t.input] ?? t.input} · {time(asked)}
          </p>
          <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">{t.question}</div>
          {note && <VoiceNote callId={call.id} rec={note} />}
        </div>
      </div>
      )}

      {/* agent + why */}
      <AgentBubble at={t.created_at} label="Agent" extra={
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <OutcomeBadge outcome={t.outcome} />
            <GroundingBadge g={t.grounding} />
            <span>reply in {fmtMs(t.timings.voice_to_voice_ms ?? t.total_ms)}</span>
            {t.feedback?.verdict && <Badge variant="secondary">tester: {t.feedback.verdict}</Badge>}
          </div>
          <div className="rounded-md border-l-2 border-primary/30 bg-muted/50 px-3 py-2 text-xs">
            <p className="mb-1 font-medium text-foreground">Why the agent said this</p>
            <ul className="space-y-0.5 text-muted-foreground">
              {t.why.filter((line) => !line.startsWith("Source [")).map((line, i) => <li key={i}>{line}</li>)}
            </ul>
            <div className="mt-2 space-y-1.5"><Sources t={t} /><BankCalls t={t} /></div>
            <Button variant="link" size="sm" className="no-print mt-1 h-auto gap-1 p-0 text-xs" onClick={() => setMore(!more)}>
              <ChevronDown className={cn("size-3 transition-transform", more && "rotate-180")} /> {more ? "Less" : "Evidence and timings"}
            </Button>
          </div>
          {more && <Evidence t={t} />}
        </div>
      }>
        <p className="whitespace-pre-wrap">{t.answer ? plainAnswer(t.answer) : <span className="text-muted-foreground">(no reply: the caller interrupted)</span>}</p>
      </AgentBubble>
    </div>
  );
}

function Evidence({ t }: { t: Turn }) {
  return (
    <div className="space-y-3 rounded-md border p-3 text-xs">
      {t.sources.length > 0 && (
        <div>
          <p className="mb-1 font-medium">Passages given to the AI</p>
          {t.sources.map((s) => (
            <div key={s.chunk_id} className="mb-1.5">
              <p><span className="font-mono">{s.score?.toFixed(2)}</span> · {s.document} › {s.section}</p>
              <p className="line-clamp-2 text-muted-foreground">{s.snippet}</p>
            </div>
          ))}
        </div>
      )}
      {t.checks.length > 0 && (
        <div>
          <p className="mb-1 font-medium">Sentence checks</p>
          {t.checks.map((c, i) => (
            <p key={i} className={c.unsupported_numbers.length ? "text-destructive" : "text-muted-foreground"}>
              {c.unsupported_numbers.length ? "✗" : "✓"} {c.sentence}
            </p>
          ))}
        </div>
      )}
      {t.bank && <p><span className="font-medium">Bank data read:</span> {t.bank.fields.join(", ") || "none"}</p>}
      <div>
        <p className="mb-1 font-medium">Where the time went (ms)</p>
        <LatencyBars t={t.timings} />
      </div>
      <p className="text-muted-foreground">
        Prompt {t.prompt_version ?? "-"} · {Object.values(t.models ?? {}).join(", ")}
        {t.hash && <> · turn hash <span className="font-mono">{t.hash.slice(0, 16)}…</span></>}
      </p>
    </div>
  );
}

function VoiceNote({ callId, rec }: { callId: string; rec: Recording }) {
  const [url, setUrl] = useState("");
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  if (url) return <audio controls autoPlay src={url} className="h-8" />;
  return (
    <Button variant="outline" size="sm" className="no-print h-7 text-xs"
            onClick={() => void audit.blob(audit.recordingPath(callId, rec.id)).then((b) => setUrl(URL.createObjectURL(b)))}>
      <Play /> Voice note and reply ({clock(rec.duration_ms / 1000)})
    </Button>
  );
}

function Trail({ call }: { call: CallRecord }) {
  type Row = { at: string; what: string; detail: string };
  const rows: Row[] = [];
  for (const t of call.turns) {
    rows.push({ at: t.created_at, what: `Turn: ${t.outcome}`, detail: `${t.intent} · "${t.question.slice(0, 80)}" · grounding ${t.grounding?.level ?? "-"} · ${fmtMs(t.total_ms)} · hash ${t.hash?.slice(0, 12) ?? "none"}` });
  }
  for (const e of call.events) rows.push({ at: e.created_at, what: e.kind.replaceAll("_", " "), detail: Object.entries(e.detail).map(([k, v]) => `${k}: ${String(v)}`).join(" · ") });
  for (const a of call.auth_events) rows.push({ at: a.created_at, what: `Verification: ${AUTH_TEXT[a.event] ?? a.event}`, detail: Object.entries(a.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ") });
  for (const a of call.alerts) rows.push({ at: a.created_at, what: `Alert (${a.severity})`, detail: a.message });
  for (const r of call.recordings) rows.push({ at: r.started_at, what: `Recording: ${r.kind.replace("_", " ")}`, detail: `${clock(r.duration_ms / 1000)} · ${Math.round(r.bytes / 1024)} KB · sha256 ${r.sha256.slice(0, 16)}…${r.deleted_at ? " · deleted (retention)" : ""}` });
  rows.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
  return (
    <Table>
      <TableHeader>
        <TableRow><TableHead className="w-24">Time</TableHead><TableHead className="w-56">Event</TableHead><TableHead>Detail</TableHead></TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={i}>
            <TableCell className="font-mono text-xs tabular-nums">{time(r.at)}</TableCell>
            <TableCell className="text-xs font-medium capitalize">{r.what}</TableCell>
            <TableCell className="text-xs whitespace-normal text-muted-foreground">{r.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function TranscriptPreview({ id, onDownload }: { id: string; onDownload: () => void }) {
  const [text, setText] = useState("");
  useEffect(() => {
    void audit.blob(audit.transcriptPath(id)).then((b) => b.text()).then(setText).catch((e: Error) => setText(`Could not load: ${e.message}`));
  }, [id]);
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="flex flex-row items-center">
        <CardDescription className="mr-auto flex items-center gap-2"><FileText className="size-4" /> Plain-text transcript, as downloaded</CardDescription>
        <Button size="sm" variant="outline" onClick={onDownload}><Download /> Download .txt</Button>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">{text || "Loading…"}</pre>
      </CardContent>
    </Card>
  );
}
