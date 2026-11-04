"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AnswerDetails } from "@/components/answer-details";
import { Badge, Button, Input, Panel, Select } from "@/components/ui";
import { api, type Auth, type Caller, type Citation, type Doc, kb } from "@/lib/api";
import { type CallEvent, LiveCall } from "@/lib/live-call";

type Message = {
  role: "user" | "agent" | "note";
  text: string;
  via?: string; // user messages: typed | voice note | live call (+ the language speech-to-text heard)
  outcome?: string;
  traceId?: string;
  timings?: Record<string, number | null>;
  citations?: Citation[];
};
type Draft = { blob: Blob; url: string };
const PHRASES = new Set(["greeting", "filler", "reprompt", "goodbye", "unclear"]); // canned lines, not answers

const OTHER_NUMBER = "9000000001"; // not registered: the agent asks for the registered mobile number

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
  const [docs, setDocs] = useState<Doc[]>([]);
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
    try {
      const s = await api.newSession(number || null, lang);
      setSessionId(s.id);
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}. Is it running (python app.py)?`);
    }
  }, []);

  useEffect(() => {
    void api.callers()
      .then((list) => {
        setCallers(list);
        setCaller(list[0]?.mobile ?? "");
        return newSession(list[0]?.mobile ?? "", "en");
      })
      .catch(() => newSession("", "en"));
  }, [newSession]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    void kb.list().then(setDocs).catch(() => setDocs([]));
  }, [sessionId]);

  const onAuth = useCallback((a: Auth | null | undefined) => {
    if (!a) return;
    setAuth(a);
    if (a.status === "verified") {
      setVerifiedAs(a.name ?? "verified");
      setCode("");
    }
  }, []);

  // ── text ──
  async function send() {
    const question = text.trim();
    if (!question || !sessionId || busy) return; // one question at a time keeps replies in order
    setText("");
    if (live) {
      call.current?.sendText(question);
      return;
    }
    add({ role: "user", text: question, via: "typed" });
    setBusy(true);
    try {
      const r = await api.ask(question, sessionId);
      add({ role: "agent", text: r.answer, outcome: r.outcome, traceId: r.trace_id, timings: r.timings, citations: r.citations });
      onAuth(r.auth);
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
          add({ role: "note", text: "That recording was too short - press Record, speak, then press Stop." });
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
        add({ role: "note", text: `No speech heard${r.transcript ? ` (the recogniser guessed "${r.transcript}")` : ""} - try again, closer to the microphone.` });
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
        add({ role: "note", text: "(you interrupted - the agent stopped)" });
        break;
      case "end":
      case "closed":
        setLive(false);
        break;
    }
  }, [add, onAuth]);

  async function toggleLive() {
    if (live) {
      call.current?.stop();
      setLive(false);
      return;
    }
    try {
      call.current = new LiveCall(onCallEvent);
      await call.current.start(sessionId, language === "en" ? "en" : "hi", focus);
      setLive(true);
      add({ role: "note", text: "Live call started - just speak. Use headphones so the agent doesn't hear itself." });
    } catch (e) {
      setError(`Live call: ${(e as Error).message}`);
    }
  }

  const callerInfo = callers.find((c) => c.mobile === caller);
  const needsCode = auth && ["otp_sent", "invalid", "expired"].includes(auth.status);

  return (
    <main className="mx-auto flex h-screen max-w-6xl flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">AU Bank assistant · test console</h1>
        <Link href="/kb" className="mr-auto text-sm underline">Knowledge base</Link>
        <label className="flex items-center gap-2 text-sm">
          Calling from
          <Select value={caller} disabled={live} onChange={(e) => { setCaller(e.target.value); void newSession(e.target.value, language); }}>
            {callers.map((c) => (
              <option key={c.mobile} value={c.mobile}>{c.name} · {c.mobile}</option>
            ))}
            <option value={OTHER_NUMBER}>Unregistered number · {OTHER_NUMBER}</option>
            <option value="">Number withheld</option>
          </Select>
        </label>
        <Select value={language} disabled={live} onChange={(e) => { setLanguage(e.target.value); void newSession(caller, e.target.value); }}>
          <option value="en">Greeting: English</option>
          <option value="hi">Greeting: Hindi</option>
        </Select>
        <Button variant="outline" onClick={() => void newSession(caller, language)}>New call</Button>
      </header>

      {error && (
        <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm" role="alert">
          {error} <button className="ml-2 underline" onClick={() => setError("")}>dismiss</button>
        </p>
      )}

      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[1fr_300px]">
        {/* conversation */}
        <Panel className="flex min-h-0 flex-col">
          <CallSummary messages={messages} />
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            {messages.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Ask about products (&quot;What is the FD rate for senior citizens for 13 months?&quot;) or your own
                accounts (&quot;What is my balance?&quot; - you will be asked to verify). Type, record a voice note, or start a live call.
                Open &quot;details&quot; under any answer to see what the AI received, how it understood it, the sources, the checks and where the time went.
              </p>
            )}
            {messages.map((m, i) => <Bubble key={i} m={m} />)}
            <div ref={bottom} />
          </div>
          {draft && (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-sm">
              <span>Voice note:</span>
              <audio controls src={draft.url} className="h-8 max-w-full" />
              <Button type="button" onClick={() => void sendDraft()} disabled={busy}>Send</Button>
              <Button type="button" variant="outline" onClick={discardDraft}>Discard</Button>
            </div>
          )}
          <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={live ? "Type during the call…" : "Type a question…"}
                   disabled={!sessionId} aria-label="Question" />
            <Button type="submit" disabled={busy || !text.trim()}>Send</Button>
            <Button type="button" variant="outline" onClick={() => void toggleRecording()} disabled={busy || live || !sessionId || !!draft}
                    aria-pressed={recording}>
              {recording ? "■ Stop" : "● Record"}
            </Button>
            <Button type="button" variant={live ? "default" : "outline"} onClick={() => void toggleLive()} disabled={!sessionId}>
              {live ? "End call" : "Live call"}
            </Button>
          </form>
          {busy && <p className="mt-2 text-xs text-muted-foreground">Working…</p>}
        </Panel>

        {/* verification + call settings */}
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
          <Panel title="Verification">
            <p className="text-sm">
              {verifiedAs ? <>Verified as <b>{verifiedAs}</b></> : "Not verified"}
            </p>
            {callerInfo && (
              <p className="mt-1 text-xs text-muted-foreground">
                Test customer: {callerInfo.accounts} account{callerInfo.accounts > 1 ? "s" : ""}
                {callerInfo.fd && ", FD"}{callerInfo.loan && ", loan"}{callerInfo.debit_card && ", debit card"}
                {callerInfo.credit_card && ", credit card"}
              </p>
            )}
            {auth?.status === "locked" && <p className="mt-2 text-sm">Verification locked for this call.</p>}
            {needsCode && (
              <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); void submitCode(); }}>
                <label className="block text-xs text-muted-foreground" htmlFor="otp">
                  Keypad: code sent to {auth?.masked_mobile}
                  {auth?.attempts_left !== undefined && ` · ${auth.attempts_left} tries left`}
                </label>
                <div className="flex gap-2">
                  <Input id="otp" value={code} inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                         type="password" placeholder="6 digits" onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
                  <Button type="submit" disabled={code.length !== 6 || busy}>Enter</Button>
                </div>
                <button type="button" className="text-xs underline" onClick={() => void resend()}>Send a new code</button>
              </form>
            )}
          </Panel>

          {auth?.simulated_sms && !verifiedAs && (
            <Panel title="Phone (simulated SMS)">
              <p className="font-mono text-sm">{auth.simulated_sms}</p>
              <p className="mt-2 text-xs text-muted-foreground">Development only. In production the code goes to the customer&apos;s phone by SMS.</p>
            </Panel>
          )}

          <KnowledgeUsed docs={docs} messages={messages} />

          <Panel title="Live call">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={focus}
                     onChange={(e) => { setFocus(e.target.checked); call.current?.setFocus(e.target.checked); }} />
              <span>Focus on my voice<br />
                <span className="text-xs text-muted-foreground">Ignores quieter voices and sounds in the background (people talking, TV).</span>
              </span>
            </label>
            <p className="mt-2 text-xs text-muted-foreground">Session {sessionId ? sessionId.slice(0, 8) : "…"}</p>
          </Panel>
        </div>
      </div>
    </main>
  );
}

function Bubble({ m }: { m: Message }) {
  const [open, setOpen] = useState(false);
  if (m.role === "note") return <p className="text-center text-xs text-muted-foreground">{m.text}</p>;
  const mine = m.role === "user";
  const t = m.timings ?? {};
  const headline = t.voice_to_voice_ms ?? t.total_ms;
  return (
    <div className={mine ? "flex justify-end" : "flex justify-start"}>
      <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${mine ? "bg-foreground text-background" : "border border-border bg-muted"}`}>
        {mine && m.via && <p className="mb-0.5 text-[11px] opacity-70">{m.via}</p>}
        <p className="whitespace-pre-wrap">{m.text}</p>
        {!mine && (m.outcome || m.traceId) && (
          <details className="mt-1 text-xs text-muted-foreground" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
            <summary className="cursor-pointer">
              {m.outcome && <Badge className="mr-1">{m.outcome}</Badge>}
              {headline != null && `${headline} ms${t.voice_to_voice_ms != null ? " voice-to-voice" : ""}`}
              {m.traceId && " · details"}
            </summary>
            {m.citations?.map((c, i) => (
              <p key={i} className="mt-1">{c.marker ? `[${c.marker}] ` : ""}{c.section ?? c.source}{c.fields ? ` (${c.fields.join(", ")})` : ""}</p>
            ))}
            {open && m.traceId && <AnswerDetails traceId={m.traceId} timings={m.timings} />}
          </details>
        )}
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
    <p className="mb-2 border-b border-border pb-2 text-xs text-muted-foreground">
      This call: {answers.length} answer{answers.length > 1 ? "s" : ""}
      {median != null && ` · median ${median} ms (brain)`}
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
      <li key={key} className={`flex items-baseline gap-2 rounded px-1 py-0.5 ${n ? "bg-foreground/10 font-semibold" : "text-muted-foreground"}`}>
        <span className="truncate" title={label}>{label}</span>
        <span className="ml-auto shrink-0 text-xs font-normal">{n ? `used ${n}×` : sub}</span>
      </li>
    );
  };
  return (
    <Panel title="Knowledge base · this call">
      <ul className="space-y-0.5 text-sm">
        {searchable.map((d) => row(d.title, d.title, `${d.chunks} passages`))}
        {row("bank", "Bank data (simulated)", "after verification")}
      </ul>
      <p className="mt-2 text-xs text-muted-foreground">
        Highlighted = cited by an answer in this call. Open an answer&apos;s details for the exact passages.{" "}
        <Link href="/kb" className="underline">Manage →</Link>
      </p>
    </Panel>
  );
}
