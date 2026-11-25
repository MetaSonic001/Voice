"use client";

// Agent desk: people from the bank's teams pick up calls and chats the AI handed over. The board shows who is
// waiting (most urgent first); the brief says who it is, whether they are verified, what they asked, what was
// already done and why they were transferred, so the caller never has to repeat themselves.
import { Clock, Headset, Mic, MicOff, PhoneOff, Send, ShieldAlert, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Dot } from "@/components/labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { desk, type Handoff, type HandoffDetail, type HandoffMessage } from "@/lib/api";
import { DeskCall } from "@/lib/desk-call";
import { cn } from "@/lib/utils";

const DISPOSITIONS: [string, string][] = [["resolved", "Resolved"], ["ticket_raised", "Ticket raised"],
  ["callback", "Callback arranged"], ["transferred", "Transferred to another team"], ["no_response", "Customer didn't respond"]];
const PRIORITY: Record<number, { label: string; tone: "bad" | "warn" | "neutral" }> = {
  1: { label: "Critical", tone: "bad" }, 2: { label: "High", tone: "warn" }, 3: { label: "Normal", tone: "neutral" } };
const wait = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`);

export default function DeskPage() {
  const [board, setBoard] = useState<Handoff[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [me, setMe] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const seen = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const b = await desk.board();
      setBoard(b.handoffs);
      setAgents(b.agents);
      setError("");
      for (const h of b.handoffs) {  // a new caller waiting: tell whoever is looking at the desk
        if (h.status === "waiting" && !seen.current.has(h.id)) {
          if (seen.current.size) toast.warning(`${h.department_name}: a ${h.channel} is waiting`, { description: h.reason });
          seen.current.add(h.id);
        }
      }
      if (!seen.current.size) seen.current.add("_");
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    try { setMe(localStorage.getItem("desk-agent") ?? ""); } catch { /* private mode */ }
    void load();
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [load]);

  function pickMe(name: string) {
    setMe(name);
    try { localStorage.setItem("desk-agent", name); } catch { /* ignore */ }
  }

  const waiting = board.filter((h) => h.status === "waiting");
  const active = board.filter((h) => h.status === "active");
  const recent = board.filter((h) => !["waiting", "active"].includes(h.status));
  const current = board.find((h) => h.id === selected) ?? null;

  return (
    <main className="mx-auto grid max-w-[1400px] gap-4 p-3 sm:p-4 lg:h-[calc(100dvh-3.5rem)] lg:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="flex max-h-[55dvh] min-h-0 flex-col gap-0 py-0 lg:max-h-none">
        <div className="space-y-3 border-b p-3">
          <div className="flex items-center gap-2">
            <Headset className="size-4" />
            <p className="text-sm font-medium">Agent desk</p>
            <Badge variant="secondary" className="ml-auto">{waiting.length} waiting</Badge>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">You are</Label>
            <Select value={me} onValueChange={pickMe}>
              <SelectTrigger className="w-full"><SelectValue placeholder="Choose your name (demo agents)" /></SelectTrigger>
              <SelectContent>{agents.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-3">
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Group title="Waiting" items={waiting} selected={selected} onPick={setSelected}
                   empty="Nobody is waiting. When the AI transfers a call or chat, it appears here." />
            <Group title="With an agent" items={active} selected={selected} onPick={setSelected} />
            <Group title="Recent" items={recent.slice(0, 15)} selected={selected} onPick={setSelected} />
          </div>
        </ScrollArea>
      </Card>

      <div className="min-h-0 min-w-0 lg:overflow-y-auto">
        {current ? <HandoffView key={current.id} h={current} me={me} onChange={() => void load()} /> : (
          <Card className="grid h-full place-items-center text-center">
            <div className="space-y-2 p-8 text-sm text-muted-foreground">
              <Headset className="mx-auto size-8" />
              <p className="font-medium text-foreground">Pick a call or chat from the list</p>
              <p>You&apos;ll see why it was transferred, who the caller is and what the AI already did.</p>
            </div>
          </Card>
        )}
      </div>
    </main>
  );
}

function Group({ title, items, selected, onPick, empty }: { title: string; items: Handoff[]; selected: string | null;
  onPick: (id: string) => void; empty?: string }) {
  if (!items.length && !empty) return null;
  return (
    <div className="space-y-1">
      <p className="px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</p>
      {!items.length && <p className="px-1 text-xs text-muted-foreground">{empty}</p>}
      {items.map((h) => {
        const p = PRIORITY[h.priority] ?? PRIORITY[3];
        return (
          <button key={h.id} onClick={() => onPick(h.id)}
                  className={cn("w-full rounded-md border border-transparent px-3 py-2.5 text-left transition-colors",
                                selected === h.id ? "border-border bg-secondary" : "hover:bg-muted/60")}>
            <div className="flex items-center gap-2 text-sm">
              <Dot tone={h.status === "waiting" ? p.tone : "neutral"} />
              <span className="font-medium">{h.department_name}</span>
              <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
                <Clock className="size-3" />{wait(h.wait_s)}
              </span>
            </div>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{h.note.customer ?? "Not verified"} · {h.reason}</p>
            <div className="mt-1 flex gap-1.5 text-[11px]">
              <Badge variant="outline" className="h-5 px-1.5 text-[11px]">{h.channel === "call" ? "Live call" : "Chat"}</Badge>
              {h.status === "waiting" && h.priority < 3 && <Badge variant="outline" className={cn("h-5 px-1.5 text-[11px]",
                p.tone === "bad" ? "border-destructive/40 text-destructive" : "border-warning/50 text-warning")}>{p.label}</Badge>}
              {h.agent_name && <span className="text-muted-foreground">{h.agent_name}</span>}
              {h.disposition && <span className="text-muted-foreground">{h.disposition.replace("_", " ")}</span>}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function HandoffView({ h, me, onChange }: { h: Handoff; me: string; onChange: () => void }) {
  const [messages, setMessages] = useState<HandoffMessage[]>([]);
  const [text, setText] = useState("");
  const [disposition, setDisposition] = useState("resolved");
  const [notes, setNotes] = useState("");
  const [onCall, setOnCall] = useState(false);
  const [muted, setMuted] = useState(false);
  const call = useRef<DeskCall | null>(null);
  const n = h.note;
  const mine = h.status === "active" && h.agent_name === me;
  const [detail, setDetail] = useState<HandoffDetail | null>(null);

  useEffect(() => {
    // the full conversation and the AI summary (which arrives a moment after the transfer starts); chats also poll messages
    const poll = () => desk.get(h.id).then((d) => { setDetail(d); if (h.channel === "chat") setMessages(d.messages); }).catch(() => {});
    void poll();
    const t = setInterval(poll, h.channel === "chat" ? 2000 : 5000);
    return () => clearInterval(t);
  }, [h.id, h.channel]);

  useEffect(() => () => call.current?.hangUp(), []);

  async function accept() {
    if (!me) return toast.error("Choose your name first");
    try {
      if (h.channel === "call") {
        if (!h.live) return toast.error("The caller is no longer on the line");
        call.current = new DeskCall(() => { setOnCall(false); onChange(); });
        await call.current.join(h.id, me);
        setOnCall(true);
      } else {
        await desk.accept(h.id, me);
      }
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function send() {
    if (!text.trim()) return;
    await desk.message(h.id, text.trim()).catch((e: Error) => toast.error(e.message));
    setText("");
    setMessages((await desk.get(h.id)).messages);
  }

  async function complete() {
    call.current?.hangUp();
    await desk.complete(h.id, disposition, notes).catch((e: Error) => toast.error(e.message));
    toast.success("Marked complete");
    onChange();
  }

  function toggleMute() {
    if (!call.current) return;
    call.current.muted = !call.current.muted;
    setMuted(call.current.muted);
  }

  return (
    <div className="space-y-4">
      <Card className="gap-4">
        <CardHeader className="flex flex-row flex-wrap items-start gap-3">
          <div className="mr-auto space-y-1">
            <CardTitle className="flex items-center gap-2 text-base">
              {h.department_name}
              <Badge variant="outline">{h.channel === "call" ? "Live call" : "Chat"}</Badge>
              <Badge variant="outline" className="capitalize">{h.status}</Badge>
            </CardTitle>
            <CardDescription>Waiting {wait(h.wait_s)} · transferred {new Date(h.created_at).toLocaleTimeString()}</CardDescription>
          </div>
          {h.status === "waiting" && (
            <Button onClick={() => void accept()} disabled={!me}>
              <Headset /> {h.channel === "call" ? "Accept and join the call" : "Accept chat"}
            </Button>
          )}
          {onCall && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={toggleMute}>{muted ? <><MicOff /> Unmute</> : <><Mic /> Mute</>}</Button>
              <Button variant="destructive" onClick={() => { call.current?.hangUp(); setOnCall(false); }}><PhoneOff /> Hang up</Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {n.headline && <p className="text-sm font-medium break-words">{n.headline}</p>}
          <div className="rounded-md border-l-2 border-primary/40 bg-muted/50 px-3 py-2 text-sm">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Brief from the record (not written by the AI)</p>
            <p className="break-words">{n.summary}</p>
          </div>
          {(detail?.note.ai_summary ?? n.ai_summary) && (
            <div className="rounded-md border border-dashed px-3 py-2 text-sm">
              <p className="mb-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                AI summary <Badge variant="outline" className="capitalize">caller sounds {(detail?.note.ai_summary ?? n.ai_summary)!.mood}</Badge>
              </p>
              <p className="whitespace-pre-line break-words">{(detail?.note.ai_summary ?? n.ai_summary)!.text}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">Written by AI from the conversation; every figure in it was checked against the record.</p>
            </div>
          )}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
            <Meta label="Caller" value={<span className="flex items-center gap-1"><UserRound className="size-3.5" />{n.customer ?? "Unknown"}</span>} />
            <Meta label="Verification" value={n.verified
              ? <span className="flex items-center gap-1 text-success"><ShieldCheck className="size-3.5" /> Verified (code)</span>
              : <span className="flex items-center gap-1 text-warning"><ShieldAlert className="size-3.5" /> Not verified</span>} />
            <Meta label="Number" value={h.caller ?? n.caller ?? "Withheld"} />
            <Meta label="Language" value={{ en: "English", hi: "Hindi", hinglish: "Hinglish" }[n.language] ?? n.language} />
            <Meta label="Segment" value={n.segment ?? "-"} />
            {n.rm && <Meta label="Relationship manager" value={n.rm} />}
            <Meta label="Reason" value={n.reason} />
          </dl>
          {!n.verified && <p className="text-xs text-warning">Not verified: don&apos;t share account details until you verify the caller.</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <List title="What they asked" items={n.asked} empty="-" />
            <List title="Already done on this call" items={n.done} empty="Nothing changed on the account" />
          </div>
          {n.alerts.length > 0 && <List title="Alerts" items={n.alerts} />}
          {detail && detail.transcript.length > 0 && (
            <details className="rounded-md border p-3">
              <summary className="cursor-pointer text-sm font-medium">Full conversation so far ({detail.transcript.length} turns, read-only)</summary>
              <div className="mt-3 max-h-80 space-y-3 overflow-y-auto text-sm">
                {detail.transcript.map((t, i) => (
                  <div key={i} className="space-y-1">
                    <p className="text-[11px] text-muted-foreground tabular-nums">{new Date(t.at).toLocaleTimeString()}</p>
                    <p className="break-words"><span className="font-medium">Caller: </span>{t.caller}</p>
                    {t.agent && <p className="break-words text-muted-foreground"><span className="font-medium">Agent: </span>{t.agent}</p>}
                  </div>
                ))}
              </div>
            </details>
          )}
          <Link href={`/calls?id=${h.session_id}`} className="text-sm underline underline-offset-4">Open the full call record and recording →</Link>
        </CardContent>
      </Card>

      {h.channel === "chat" && (h.status === "active" || messages.length > 0) && (
        <Card className="gap-3">
          <CardHeader><CardTitle className="text-sm">Chat with the customer</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-72 space-y-2 overflow-y-auto">
              {messages.map((m) => (
                <div key={m.id} className={cn("flex", m.sender === "agent" ? "justify-end" : "justify-start")}>
                  <p className={cn("max-w-[85%] rounded-2xl px-3 py-2 text-sm [overflow-wrap:anywhere] sm:max-w-[75%]",
                                   m.sender === "agent" ? "bg-primary text-primary-foreground" : "border bg-card")}>{m.text}</p>
                </div>
              ))}
              {!messages.length && <p className="text-xs text-muted-foreground">No messages yet. Say hello.</p>}
            </div>
            {mine && (
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type to the customer…" />
                <Button type="submit" disabled={!text.trim()}><Send /> Send</Button>
              </form>
            )}
          </CardContent>
        </Card>
      )}

      {h.status === "active" && (mine || onCall) && (
        <Card className="gap-3">
          <CardHeader><CardTitle className="text-sm">Wrap up</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <Select value={disposition} onValueChange={setDisposition}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>{DISPOSITIONS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
            </Select>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes for the record (what you did, what's next)" />
            <Button onClick={() => void complete()}>Complete</Button>
          </CardContent>
        </Card>
      )}
      {h.status !== "waiting" && h.status !== "active" && (
        <p className="text-sm text-muted-foreground">
          {h.status === "completed" ? [`Completed by ${h.agent_name ?? "-"}`, h.disposition?.replace("_", " "), h.agent_notes]
              .filter(Boolean).join(" · ")
            : h.status === "abandoned" ? "The caller hung up while waiting." : "A callback was booked."}
        </p>
      )}
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

function List({ title, items, empty }: { title: string; items: string[]; empty?: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">{title}</p>
      {items.length ? <ul className="list-disc space-y-0.5 pl-4 text-sm">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
        : <p className="text-sm text-muted-foreground">{empty}</p>}
    </div>
  );
}
