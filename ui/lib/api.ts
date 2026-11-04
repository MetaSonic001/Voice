// Thin client for the brain API (FastAPI). Set NEXT_PUBLIC_API_URL if the API isn't on 127.0.0.1:8000.

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const KEY = process.env.NEXT_PUBLIC_API_KEY; // local testing only: never ship a real key to a browser

export type Auth = {
  status: string; // otp_sent | need_mobile | not_registered | verified | invalid | expired | locked | ...
  masked_mobile?: string | null;
  attempts_left?: number;
  simulated_sms?: string; // dev only (OTP_DELIVERY=simulated)
  name?: string;
};

export type Citation = { marker?: number; document?: string; section?: string; snippet?: string; source?: string; fields?: string[] };

export type Reply = {
  trace_id: string;
  outcome: string;
  intent: string;
  language: string;
  answer: string;
  route?: string | null;
  reason?: string | null;
  citations: Citation[];
  timings: Record<string, number | null>;
  auth?: Auth | null;
};

export type Caller = { name: string; mobile: string; accounts: number; fd: boolean; loan: boolean; debit_card: boolean; credit_card: boolean };

export type VoiceTurn = {
  transcript: string;
  stt_language?: string | null;
  outcome: string;
  answer?: string;
  language?: string;
  trace_id?: string;
  timings: Record<string, number | null>;
  citations?: Citation[];
  auth?: Auth | null;
  audio_wav_base64: string | null;
};

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["content-type"] = "application/json";
  if (KEY) h["x-api-key"] = KEY;
  return h;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? String(body.detail) : `HTTP ${res.status}`);
  return body as T;
}

const post = <T,>(path: string, data: unknown) => call<T>(path, { method: "POST", headers: headers(), body: JSON.stringify(data) });

// The stored record of one answer (GET /v1/traces/{id}): what the AI received, how it understood it, what was
// found, the checks, and every timing.
export type Trace = {
  id: string;
  question: string; // as sent onward: masked
  answer: string;
  outcome: string;
  intent: string;
  language: string;
  total_ms: number;
  data: {
    understanding?: { language: string; intent: string; standalone_question: string; search_query_en: string; personal?: Record<string, unknown> };
    retrieval?: { reranked: { section: string; rerank_score: number | null; dense_rank: number | null; keyword_rank: number | null; snippet: string; chunk_id: string }[] };
    context_chunk_ids?: string[];
    checks?: { sentence: string; markers: number[]; unsupported_numbers: string[] }[];
    personal?: { query: Record<string, unknown>; fields: string[] };
    timings?: Record<string, number | null>;
    voice?: Record<string, number | null>;
    reason?: string | null;
    error?: string;
    feedback?: { verdict: string; note: string };
    models?: Record<string, string>;
  };
};

// ── Knowledge base ──
export type Doc = {
  id: string;
  title: string;
  category: string | null;
  purpose: "knowledge" | "data";
  enabled: boolean;
  current_version: number | null;
  file_type: string | null;
  filename: string | null;
  effective_from: string | null;
  options: { sheets?: string[] } | null;
  chunks: number;
  latest_status: string; // queued | processing | indexed | failed | superseded
  updated_at: string;
};
export type DocVersion = {
  version_no: number; filename: string; file_type: string; size_bytes: number; status: string; error: string | null;
  effective_from: string | null; options: { sheets?: string[] } | null; report: Record<string, unknown> | null; created_at: string; chunks: number;
};
export type DocDetail = Doc & { searchable: boolean; versions: DocVersion[] };
export type Chunk = { id: string; ordinal: number; kind: string; section_path: string; text: string; word_count: number; active: boolean };
export type SearchHit = { section: string; document: string; rerank_score: number | null; dense_rank: number | null; keyword_rank: number | null; text: string; chunk_id: string };

const send = <T,>(method: string, path: string, body?: unknown) =>
  call<T>(path, { method, headers: headers(), body: body === undefined ? undefined : JSON.stringify(body) });

export const kb = {
  list: () => call<Doc[]>("/v1/documents"),
  get: (id: string) => call<DocDetail>(`/v1/documents/${id}`),
  chunks: (id: string) => call<{ count: number; chunks: Chunk[] }>(`/v1/documents/${id}/chunks`),
  upload: (file: File, fields: Record<string, string>) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(fields).forEach(([k, v]) => v && form.append(k, v));
    return call<Doc & { deduplicated: boolean }>("/v1/documents", { method: "POST", headers: headers(false), body: form });
  },
  replace: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<unknown>(`/v1/documents/${id}/versions`, { method: "POST", headers: headers(false), body: form });
  },
  update: (id: string, patch: Partial<Pick<Doc, "title" | "category" | "enabled" | "effective_from">>) => send<Doc>("PATCH", `/v1/documents/${id}`, patch),
  activate: (id: string, version: number) => send<unknown>("POST", `/v1/documents/${id}/versions/${version}/activate`),
  reindex: (id: string) => send<unknown>("POST", `/v1/documents/${id}/reindex`),
  remove: (id: string, purge: boolean) => send<unknown>("DELETE", `/v1/documents/${id}${purge ? "?purge=true" : ""}`),
  search: (query: string) => post<{ reranked: SearchHit[]; timings: Record<string, number> }>("/v1/search", { query }),
  stats: () => call<{ documents: number; searchable_chunks: number; pending_jobs: number }>("/v1/stats"),
};

export const api = {
  trace: (id: string) => call<Trace>(`/v1/traces/${id}`),
  feedback: (id: string, verdict: "correct" | "wrong") => post<unknown>(`/v1/traces/${id}/feedback`, { verdict, note: "" }),
  callers: () => call<Caller[]>("/v1/demo/callers"),
  newSession: (caller_number: string | null, language: string) =>
    post<{ id: string }>("/v1/sessions", { caller_number, language }),
  ask: (question: string, session_id: string) => post<Reply>("/v1/ask", { question, session_id }),
  verify: (session_id: string, code: string) =>
    post<{ auth: Auth; message: string; reply: Reply | null }>("/v1/auth/verify", { session_id, code }),
  resend: (session_id: string) => post<{ auth: Auth; message: string }>("/v1/auth/resend", { session_id }),
  voiceTurn: (audio: Blob, session_id: string) => {
    const form = new FormData();
    form.append("file", audio, "question.webm");
    form.append("session_id", session_id);
    return call<VoiceTurn>("/v1/voice/turn", { method: "POST", headers: headers(false), body: form });
  },
};

export const wsUrl = (path: string) => API.replace(/^http/, "ws") + path + (KEY ? `?key=${encodeURIComponent(KEY)}` : "");
export const traceUrl = (id: string) => `${API}/v1/traces/${id}`;
