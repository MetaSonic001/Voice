"use client";

// Test console: talk to the agent by typing, a voice note, or a live call, as one of the test customers.
// Every conversation is saved and can be reviewed (with recording, reasons and checks) under Call audit.
import { Mic, MicOff, Phone, PhoneOff, Plus, Send, ShieldCheck, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AnswerDetails } from "@/components/answer-details";
import { fmtMs, OutcomeBadge } from "@/components/labels";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, type Auth, type Caller, type Citation, desk, type Doc, type HandoffRef, kb } from "@/lib/api";
import { type CallEvent, LiveCall } from "@/lib/live-call";
import { cn } from "@/lib/utils";

type Message = {
  role: "user" | "agent" | "person" | "note";
  text: string;
  via?: string; // user messages: typed | voice note | live call (+ the language speech-to-text heard)
  outcome?: string;
  traceId?: string;
  timings?: Record<string, number | null>;
  citations?: Citation[];
};
type Draft = { blob: Blob; url: string };
type Transfer = HandoffRef & { status: string; position?: number; agent?: string | null; after: number };
const PHRASES = new Set(["greeting", "filler", "reprompt", "code_reminder", "goodbye", "unclear"]); // canned lines, not answers

const OTHER_NUMBER = "9000000001"; // not registered: the agent asks for the registered mobile number
const WITHHELD = "withheld";

export default function Console() {
  const [callers, setCallers] = useState<Caller[]>([]);
  const [caller, setCaller] = useState("");
  const [language, setLanguage] = useState("en");
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState<Auth | null>(null);
  const [verifiedAs, setVerifiedAs] = useState("");
  const [code, setCode] = useState("");
  const [recording, setRecording] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [live, setLive] = useState(false);
  const [focus, setFocus] = useState(true);
  const [muted, setMuted] = useState(false);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const call = useRef<LiveCall | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const add = useCallback((m: Message) => setMessages((prev) => [...prev, m]), []);

  // ── session: one per "call"; a new caller or language starts a new one ──
  const newSession = useCallback(async (number: string, lang: string) => {
    call.current?.stop();
    setLive(false);
    setMessages([]);
    setAuth(null);
    setVerifiedAs("");
    setCode("");
    setError("");
    setTransfer(null);
    try {
      const s = await api.newSession(number && number !== WITHHELD ? number : null, lang);
      setSessionId(s.id);
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}. Is it running (python app.py)?`);
    }
  }, []);

  useEffect(() => {
    void api.callers()
      .then((list) => {
        setCallers(list);
        setCaller(list[0]?.mobile ?? WITHHELD);
        return newSession(list[0]?.mobile ?? "", "en");
      })
      .catch(() => newSession("", "en"));
  }, [newSession]);

  // leaving the page ends a live call and a voice note in progress (both release the microphone)
  useEffect(() => () => {
    call.current?.stop();
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    void kb.list().then(setDocs).catch(() => setDocs([]));
  }, [sessionId]);

  useEffect(() => {
    if (!transfer || live || !["waiting", "active"].includes(transfer.status)) return;
    const t = setInterval(async () => {
      try {
        const s = await desk.status(sessionId, transfer.after);
        const msgs = s.messages ?? [];
        msgs.forEach((m) => add({ role: "person", text: m.text, via: s.agent ?? transfer.name }));
        if (s.status === "active" && transfer.status === "waiting") add({ role: "note", text: `${s.agent} from ${s.department} joined the chat.` });
        if (["completed", "abandoned", "callback"].includes(s.status)) add({ role: "note", text: `${s.agent ?? "The team"} ended the chat.` });
        setTransfer((prev) => prev && { ...prev, status: s.status, position: s.position, agent: s.agent,
                                        after: msgs.length ? msgs[msgs.length - 1].id : prev.after });
      } catch { /* try again on the next tick */ }
    }, 2500);
    return () => clearInterval(t);
  }, [transfer, live, sessionId, add]);

  const onAuth = useCallback((a: Auth | null | undefined) => {
    if (!a) return;
    setAuth(a);
    if (a.status === "verified") {
      setVerifiedAs(a.name ?? "verified");
      setCode("");
    }
  }, []);

  // ── text ──
  async function send(override?: string) {
    const question = (override ?? text).trim();
    if (!question || !sessionId || busy) return; // one question at a time keeps replies in order
    if (!override) setText("");
    if (live) {
      call.current?.sendText(question);
      return;
    }
    add({ role: "user", text: question, via: "typed" });
    if (transfer && ["waiting", "active"].includes(transfer.status)) {  // a person has the chat now, not the AI
      await desk.send(sessionId, question).catch((e: Error) => setError(e.message));
      return;
    }
    setBusy(true);
    try {
      const r = await api.ask(question, sessionId);
      add({ role: "agent", text: r.answer, outcome: r.outcome, traceId: r.trace_id, timings: r.timings, citations: r.citations });
      onAuth(r.auth);
      if (r.handoff) setTransfer({ ...r.handoff, status: "waiting", after: 0 });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // ── keypad code ──
  async function submitCode() {
    if (!/^\d{6}$/.test(code)) return;
    if (live) {
      call.current?.sendCode(code);
      setCode("");
      return;
    }
    setBusy(true);
    try {
      const r = await api.verify(sessionId, code);
      setCode("");
      onAuth(r.auth);
      add({ role: "note", text: r.message });
      if (r.reply) {
        add({ role: "agent", text: r.reply.answer, outcome: r.reply.outcome, traceId: r.reply.trace_id,
              timings: r.reply.timings, citations: r.reply.citations });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    try {
      const r = await api.resend(sessionId);
      onAuth(r.auth);
      add({ role: "note", text: r.message });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // ── voice note: record, listen back, then send or discard ──
  async function toggleRecording() {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const blob = new Blob(chunks, { type: rec.mimeType });
        if (blob.size < 2000) {
          add({ role: "note", text: "That recording was too short. Press Record, speak, then press Stop." });
          return;
        }
        setDraft({ blob, url: URL.createObjectURL(blob) });
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch (e) {
      setError(`Microphone: ${(e as Error).message}`);
    }
  }

  function discardDraft() {
    if (draft) URL.revokeObjectURL(draft.url);
    setDraft(null);
  }

  async function sendDraft() {
    if (!draft) return;
    const audio = draft.blob;
    discardDraft();
    setBusy(true);
    try {
      const r = await api.voiceTurn(audio, sessionId);
      if (r.outcome === "no_speech") {
        add({ role: "note", text: `No speech heard${r.transcript ? ` (the recogniser guessed "${r.transcript}")` : ""}. Try again, closer to the microphone.` });
        return;
      }
      add({ role: "user", text: r.transcript, via: `voice note · heard as ${r.stt_language ?? "?"} · ${r.timings.stt_ms ?? "?"} ms` });
      add({ role: "agent", text: r.answer ?? "", outcome: r.outcome, traceId: r.trace_id, timings: r.timings,
            citations: r.citations });
      onAuth(r.auth);
      if (r.audio_wav_base64) void new Audio(`data:audio/wav;base64,${r.audio_wav_base64}`).play();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // ── live call: continuous two-way audio ──
  const onCallEvent = useCallback((e: CallEvent) => {
    switch (e.type) {
      case "user":
        add({ role: "user", text: String(e.text), via: `live call${e.stt_language ? ` · heard as ${String(e.stt_language)}` : " · typed"}` });
        break;
      case "agent_text":
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "agent" && !last.outcome && !e.phrase) {  // sentences of one answer: one bubble
            return [...prev.slice(0, -1), { ...last, text: `${last.text} ${e.text}` }];
          }
          return [...prev, { role: "agent", text: String(e.text), outcome: e.phrase ? String(e.phrase) : undefined }];
        });
        break;
      case "reply_done":
        setMessages((prev) => {
          const i = prev.findLastIndex((m) => m.role === "agent");
          if (i < 0) return prev;
          const next = [...prev];
          const sections = Array.isArray(e.citations) ? (e.citations as string[]) : [];
          next[i] = { ...next[i], outcome: String(e.outcome), traceId: String(e.trace_id),
                      citations: sections.map((section) => ({ section, document: section.split(" › ")[0] })) };
          return next;
        });
        break;
      case "turn_metrics":
        setMessages((prev) => {
          const i = prev.findLastIndex((m) => m.role === "agent");
          if (i < 0) return prev;
          const next = [...prev];
          next[i] = { ...next[i], timings: { ...next[i].timings, ...(e as Record<string, number | null>) } };
          return next;
        });
        break;
      case "auth":
        onAuth(e as unknown as Auth);
        break;
      case "interrupted":
        add({ role: "note", text: "You interrupted: the agent stopped." });
        break;
      case "mic":
        add({ role: "note", text: `Microphone: ${String(e.device)} · echo cancellation ${String(e.echoCancellation)} (browser offers: ${String(e.offered)}) · auto volume ${String(e.autoGainControl)} · noise suppression ${String(e.noiseSuppression)}` });
        break;
      case "echo_warning":
        add({ role: "note", text: `This browser can't remove the agent's voice from your mic (${String(e.reason)}). Use headphones, or the agent may hear itself.` });
        break;
      case "handoff":
        if (e.status === "waiting") add({ role: "note", text: `On hold for the ${String(e.name)} team: hold music is playing until someone picks up.` });
        if (e.status === "active") add({ role: "note", text: `${String(e.agent)} has joined the call. You're talking to a person now.` });
        break;
      case "busy":
        add({ role: "note", text: `All lines are busy (this server takes ${String(e.limit)} calls at once). Please try again shortly.` });
        break;
      case "end":
      case "closed":
        setLive(false);
        break;
    }
  }, [add, onAuth]);

  function toggleMute() {
    call.current?.setMuted(!muted);
    setMuted(!muted);
  }

  async function toggleLive() {
    if (live) {
      call.current?.stop();
      setLive(false);
      return;
    }
    try {
      setMuted(false); // every call starts unmuted
      call.current = new LiveCall(onCallEvent);
      await call.current.start(sessionId, language === "en" ? "en" : "hi", focus);
      setLive(true);
      add({ role: "note", text: "Live call started: just speak. Use headphones so the agent doesn't hear itself." });
    } catch (e) {
      setError(`Live call: ${(e as Error).message}`);
    }
  }

  const callerInfo = callers.find((c) => c.mobile === caller);
  const needsCode = auth && ["otp_sent", "invalid", "expired"].includes(auth.status);
  const changeCaller = (v: string) => { setCaller(v); void newSession(v, language); };
  const changeLanguage = (v: string) => { setLanguage(v); void newSession(caller, v); };

  return (
    <main className="mx-auto flex h-[calc(100vh-3.5rem)] max-w-[1400px] flex-col gap-4 p-4">
      {/* call setup */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Calling from</Label>
          <Select value={caller} onValueChange={changeCaller} disabled={live}>
            <SelectTrigger className="w-72 bg-background"><SelectValue placeholder="Choose a test caller" /></SelectTrigger>
            <SelectContent>
              {callers.map((c) => <SelectItem key={c.mobile} value={c.mobile}>{c.name} · {c.mobile}</SelectItem>)}
              <SelectItem value={OTHER_NUMBER}>Unregistered number · {OTHER_NUMBER}</SelectItem>
              <SelectItem value={WITHHELD}>Number withheld</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Greeting</Label>
          <Select value={language} onValueChange={changeLanguage} disabled={live}>
            <SelectTrigger className="w-36 bg-background"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="en">English</SelectItem>
              <SelectItem value="hi">Hindi</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" className="bg-background" onClick={() => void newSession(caller, language)}><Plus /> New call</Button>
        {callerInfo?.scenario && <p className="max-w-md self-center text-xs text-muted-foreground">Demo data: {callerInfo.scenario}</p>}
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          {live && <Badge className="gap-1.5 bg-destructive text-white"><span className="size-1.5 animate-pulse rounded-full bg-white" /> Live</Badge>}
          {verifiedAs && <Badge variant="outline" className="border-success/40 text-success"><ShieldCheck /> {verifiedAs}</Badge>}
          {sessionId && (
            <Link href={`/calls?id=${sessionId}`} className="hover:text-foreground hover:underline">
              Call {sessionId.slice(0, 8)} · open in audit →
            </Link>
          )}
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center gap-2">
            <span className="mr-auto">{error}</span>
            <Button size="sm" variant="ghost" onClick={() => setError("")}>Dismiss</Button>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_320px]">
        {/* conversation */}
        <Card className="flex min-h-0 flex-col gap-0 py-0">
          <div className="flex items-center gap-3 border-b px-4 py-3">
            <p className="text-sm font-medium">Conversation</p>
            <CallSummary messages={messages} />
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-4 p-4">
              <p className="mx-auto max-w-md rounded-md bg-muted px-3 py-2 text-center text-xs text-muted-foreground">
                You are talking to AU Small Finance Bank&apos;s virtual assistant. Conversations are recorded for quality and security.
              </p>
              {messages.length === 0 && (
                <div className="mx-auto max-w-lg space-y-2 py-6 text-center text-sm text-muted-foreground">
                  <p>Ask about products, e.g. <span className="text-foreground">&quot;What is the FD rate for senior citizens for 13 months?&quot;</span>,
                    or your own accounts, e.g. <span className="text-foreground">&quot;What is my balance?&quot;</span> (you will be asked to verify).</p>
                  <p>Type, record a voice note, or start a live call. Open <b>Details</b> under any answer for its sources, checks and timings.</p>
                </div>
              )}
              {messages.map((m, i) => <Bubble key={i} m={m} />)}
              {!live && messages.at(-1)?.outcome === "confirm" && !busy && (
                <div className="flex justify-start gap-2 pl-1">
                  <Button size="sm" variant="outline" onClick={() => void send("Yes")}>Yes</Button>
                  <Button size="sm" variant="outline" onClick={() => void send("No")}>No</Button>
                </div>
              )}
              <div ref={bottom} />
            </div>
          </ScrollArea>
          <div className="space-y-2 border-t p-3">
            {transfer && ["waiting", "active"].includes(transfer.status) && !live && (
              <div className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-sm">
                <span className="size-2 animate-pulse rounded-full bg-warning" />
                {transfer.status === "waiting"
                  ? <span>Waiting for the <b>{transfer.name}</b> team{transfer.position ? ` · you're number ${transfer.position}` : ""}. Messages you type go to them.</span>
                  : <span>Chatting with <b>{transfer.agent}</b> ({transfer.name}).</span>}
                <Link href="/desk" className="ml-auto text-xs underline">Open the agent desk →</Link>
              </div>
            )}
            {draft && (
              <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/50 p-2 text-sm">
                <span className="text-xs font-medium">Voice note</span>
                <audio controls src={draft.url} className="h-8 max-w-full" />
                <Button size="sm" onClick={() => void sendDraft()} disabled={busy}><Send /> Send</Button>
                <Button size="sm" variant="ghost" onClick={discardDraft}><Trash2 /> Discard</Button>
              </div>
            )}
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
              <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={live ? "Type during the call…" : "Type a question…"}
                     disabled={!sessionId} aria-label="Question" />
              <Button type="submit" disabled={busy || !text.trim()}><Send /> Send</Button>
              <Button type="button" variant={recording ? "destructive" : "outline"} onClick={() => void toggleRecording()}
                      disabled={busy || live || !sessionId || !!draft} aria-pressed={recording}>
                {recording ? <><Square /> Stop</> : <><Mic /> Record</>}
              </Button>
              {live && (
                <Button type="button" variant={muted ? "secondary" : "outline"} onClick={toggleMute} aria-pressed={muted}
                        title={muted ? "Unmute your microphone" : "Mute your microphone: no sound goes to the agent"}>
                  {muted ? <><MicOff /> Unmute</> : <><Mic /> Mute</>}
                </Button>
              )}
              <Button type="button" variant={live ? "destructive" : "outline"} onClick={() => void toggleLive()} disabled={!sessionId}>
                {live ? <><PhoneOff /> End call</> : <><Phone /> Live call</>}
              </Button>
            </form>
            {busy && <p className="text-xs text-muted-foreground">Working…</p>}
          </div>
        </Card>

        {/* verification, knowledge, call settings */}
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
          <Card className="gap-3">
            <CardHeader>
              <CardTitle className="text-sm">Verification</CardTitle>
              <CardDescription>
                {verifiedAs ? <span className="text-success">Verified as <b>{verifiedAs}</b></span> : "Not verified"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {callerInfo && (
                <p className="text-xs text-muted-foreground">
                  Test customer: {callerInfo.accounts} account{callerInfo.accounts > 1 ? "s" : ""}
                  {callerInfo.fd && ", FD"}{callerInfo.loan && ", loan"}{callerInfo.debit_card && ", debit card"}
                  {callerInfo.credit_card && ", credit card"}
                </p>
              )}
              {auth?.status === "locked" && <p className="text-sm text-destructive">Verification locked for this call.</p>}
              {needsCode && (
                <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void submitCode(); }}>
                  <Label htmlFor="otp" className="text-xs font-normal text-muted-foreground">
                    Keypad: code sent to {auth?.masked_mobile}
                    {auth?.attempts_left !== undefined && ` · ${auth.attempts_left} tries left`}
                  </Label>
                  <div className="flex gap-2">
                    <Input id="otp" value={code} inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                           type="password" placeholder="6 digits" onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
                    <Button type="submit" disabled={code.length !== 6 || busy}>Enter</Button>
                  </div>
                  <Button type="button" variant="link" className="h-auto p-0 text-xs" onClick={() => void resend()}>Send a new code</Button>
                </form>
              )}
            </CardContent>
          </Card>

          {auth?.simulated_sms && !verifiedAs && (
            <Card className="gap-3 border-dashed">
              <CardHeader>
                <CardTitle className="text-sm">Phone (simulated SMS)</CardTitle>
                <CardDescription>Development only. In production the code goes to the customer&apos;s phone.</CardDescription>
              </CardHeader>
              <CardContent><p className="rounded-md bg-muted p-2 font-mono text-sm">{auth.simulated_sms}</p></CardContent>
            </Card>
          )}

          <KnowledgeUsed docs={docs} messages={messages} />

          <Card className="gap-3">
            <CardHeader><CardTitle className="text-sm">Live call</CardTitle></CardHeader>
            <CardContent>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1 accent-primary" checked={focus}
                       onChange={(e) => { setFocus(e.target.checked); call.current?.setFocus(e.target.checked); }} />
                <span>Focus on my voice
                  <span className="block text-xs text-muted-foreground">Ignores quieter voices and sounds in the background (people talking, TV).</span>
                </span>
              </label>
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}

function Bubble({ m }: { m: Message }) {
  const [open, setOpen] = useState(false);
  if (m.role === "note") return <p className="text-center text-xs text-muted-foreground">{m.text}</p>;
  if (m.role === "person") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] space-y-1">
          <p className="text-[11px] text-muted-foreground">{m.via} · bank team</p>
          <div className="rounded-2xl rounded-bl-sm border border-primary/30 bg-secondary px-4 py-2.5 text-sm leading-relaxed">{m.text}</div>
        </div>
      </div>
    );
  }
  const mine = m.role === "user";
  const t = m.timings ?? {};
  const headline = t.voice_to_voice_ms ?? t.total_ms;
  const answer = !mine && m.outcome && !PHRASES.has(m.outcome);
  return (
    <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[80%] space-y-1", mine && "items-end")}>
        {mine && m.via && <p className="text-right text-[11px] text-muted-foreground">{m.via}</p>}
        <div className={cn("rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                           mine ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm border bg-card")}>
          <p className="whitespace-pre-wrap">{m.text}</p>
        </div>
        {!mine && (answer || m.traceId) && (
          <div className="flex flex-wrap items-center gap-1.5 px-1 text-xs text-muted-foreground">
            {answer && <OutcomeBadge outcome={m.outcome!} />}
            {headline != null && <span>{fmtMs(headline)}{t.voice_to_voice_ms != null ? " voice-to-voice" : ""}</span>}
            {m.citations?.map((c, i) => (
              <span key={i} className="truncate">· {c.marker ? `[${c.marker}] ` : ""}{c.section ?? c.source}{c.fields ? ` (${c.fields.join(", ")})` : ""}</span>
            ))}
            {m.traceId && (
              <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={() => setOpen(!open)}>
                {open ? "Hide details" : "Details"}
              </Button>
            )}
          </div>
        )}
        {open && m.traceId && <AnswerDetails traceId={m.traceId} timings={m.timings} />}
      </div>
    </div>
  );
}

function CallSummary({ messages }: { messages: Message[] }) {
  const answers = messages.filter((m) => m.role === "agent" && m.outcome && !PHRASES.has(m.outcome));
  if (!answers.length) return null;
  const times = answers.map((m) => m.timings?.total_ms).filter((v): v is number => typeof v === "number").sort((a, b) => a - b);
  const median = times.length ? times[Math.floor(times.length / 2)] : null;
  const outcomes = answers.reduce<Record<string, number>>((acc, m) => ({ ...acc, [m.outcome!]: (acc[m.outcome!] ?? 0) + 1 }), {});
  return (
    <p className="ml-auto text-xs text-muted-foreground">
      {answers.length} answer{answers.length > 1 ? "s" : ""}
      {median != null && ` · median ${fmtMs(median)} (brain)`}
      {" · "}{Object.entries(outcomes).map(([k, v]) => `${k} ${v}`).join(", ")}
    </p>
  );
}

function KnowledgeUsed({ docs, messages }: { docs: Doc[]; messages: Message[] }) {
  // How often each document was cited in this call's answers (bank data for personal answers).
  const used = new Map<string, number>();
  for (const m of messages) {
    for (const c of m.citations ?? []) {
      const key = c.source ? "bank" : (c.document ?? c.section?.split(" › ")[0] ?? "");
      used.set(key, (used.get(key) ?? 0) + 1);
    }
  }
  const searchable = docs.filter((d) => d.purpose === "knowledge" && d.enabled);
  const row = (key: string, label: string, sub: string) => {
    const n = used.get(key) ?? 0;
    return (
      <li key={key} className={cn("flex items-baseline gap-2 rounded-md px-2 py-1", n ? "bg-secondary font-medium" : "text-muted-foreground")}>
        <span className="truncate" title={label}>{label}</span>
        <span className="ml-auto shrink-0 text-xs font-normal">{n ? `used ${n}×` : sub}</span>
      </li>
    );
  };
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="text-sm">Knowledge used in this call</CardTitle>
        <CardDescription>Highlighted = cited by an answer. <Link href="/kb" className="underline">Manage →</Link></CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-0.5 text-sm">
          {searchable.map((d) => row(d.title, d.title, `${d.chunks} passages`))}
          {row("bank", "Bank data (simulated)", "after verification")}
        </ul>
      </CardContent>
    </Card>
  );
}
