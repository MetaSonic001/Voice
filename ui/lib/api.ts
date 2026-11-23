// Thin client for the brain API (FastAPI). Set NEXT_PUBLIC_API_URL if the API isn't on 127.0.0.1:8000.
// "/" means the API is on the same address as these pages (on AWS the load balancer routes /v1/* to the brain).

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
export const API = API_URL === "/" ? "" : API_URL.replace(/\/+$/, "");
const KEY = process.env.NEXT_PUBLIC_API_KEY; // local testing only: never ship a real key to a browser

export type Auth = {
  status: string; // otp_sent | need_mobile | not_registered | verified | invalid | expired | locked | ...
  masked_mobile?: string | null;
  attempts_left?: number;
  simulated_sms?: string; // dev only (OTP_DELIVERY=simulated)
  name?: string;
};

export type Citation = { marker?: number; chunk_id?: string; document?: string; section?: string; snippet?: string;
  effective_from?: string | null; source?: string; fields?: string[] };

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
  handoff?: HandoffRef | null;
};

export type HandoffRef = { id: string; department: string; name: string };

export type Caller = { name: string; mobile: string; accounts: number; fd: boolean; loan: boolean; debit_card: boolean;
  credit_card: boolean; scenario?: string | null };

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

// ── Call audit ──
export type Grounding = { level: "high" | "medium" | "low" | "none" | "n/a"; basis: string[]; score?: number };
export type CallSummary = {
  id: string; created_at: string; ended_at: string | null; end_reason: string | null; language: string | null;
  caller: string | null; verified_at: string | null; customer_id: string | null; turns: number; first_at: string | null;
  last_at: string | null; answered: number; unanswered: number; last_question: string | null; alerts: number;
  recordings: number; live: boolean;
  review_score: number | null; review_critical: boolean | null; review_needs_attention: boolean | null;
  review_mode: "judged" | "code_only" | null; review_labelled: boolean | null;
};
export type Turn = {
  id: string; created_at: string; question: string; answer: string; language: string; intent: string; outcome: string;
  total_ms: number; input: string; route: string | null; reason: string | null; why: string[];
  // every call to a bank system during this turn (ids masked to the last 4 digits)
  bank_calls?: { system: string; name: string; method: string; path: string; status: number | string; ms: number; attempts: number }[];
  understood: { question?: string; search?: string; personal?: Record<string, unknown> | null; facts?: Record<string, unknown> | null };
  sources: { chunk_id: string; document: string; section: string; score: number | null; snippet: string }[];
  citations: Citation[]; checks: { sentence: string; unsupported_numbers: string[] }[];
  bank: { fields: string[]; query: Record<string, unknown> } | null; grounding: Grounding | null;
  timings: Record<string, number>; stt_no_speech: number | null; prompt_version?: string; models?: Record<string, string>;
  feedback?: { verdict: string } | null; hash: string | null;
};
export type Recording = { id: string; trace_id: string | null; kind: "call" | "voice_note"; started_at: string; duration_ms: number;
  format: string; bytes: number; sha256: string; deleted_at: string | null; available: boolean };
export type CallEventRow = { created_at: string; kind: string; detail: Record<string, unknown> };
export type AuthEventRow = { created_at: string; event: string; detail: Record<string, unknown> };
export type AlertRow = { id: number; session_id: string | null; created_at: string; kind: string; severity: string; message: string };
export type CallRecord = {
  id: string; created_at: string; ended_at: string | null; end_reason: string | null; language: string | null; caller: string | null;
  customer_id: string | null; verified_at: string | null; chain_hash: string | null; chain_len: number;
  integrity: { status: "intact" | "broken" | "not_sealed"; turns_checked?: number; turn?: number; problem?: string; head?: string };
  turns: Turn[]; events: CallEventRow[]; auth_events: AuthEventRow[]; alerts: AlertRow[]; recordings: Recording[];
  review?: Review | null;
};

// ── Post-call review: free code checks always, an LLM judge on a risk-weighted slice, a person's labels for calibration ──
export const DIMENSIONS = ["task_completion", "correctness", "conversation", "compliance", "handoff"] as const;
export type Dimension = (typeof DIMENSIONS)[number];
export type ReviewCheck = { name: string; ok: boolean; severity: "info" | "warn" | "fail"; detail: string };
export type Review = {
  session_id: string; mode: "judged" | "code_only"; reason: string; checks: ReviewCheck[]; model: string | null;
  score: number | null; critical: boolean; needs_review: boolean;
  judge: null | {
    dimension_scores: Record<Dimension, number>; evidence: { dimension: Dimension; dimensions?: Dimension[]; turn: number; quote: string }[];
    critical_failure: boolean; needs_human_review: boolean; failure_categories: string[]; recommended_fix: string;
    dropped_evidence?: number; findings?: Finding[];
  };
  human: null | { scores: Partial<Record<Dimension, number>>; critical: boolean; note: string };
};
// One problem the judge found: what happened, the quote (checked against the transcript), why it matters, the fix, where.
export type Finding = {
  category: string; source: "agent" | "system_failure" | "bank_system" | "knowledge_gap" | "unclear_caller" | "insufficient_evidence";
  turn: number; quote: string; quote_verified: boolean; what_happened: string; why_it_matters: string; fix: string; fix_area: string;
};
// The fix list: each problem the judge found is a to-do a person marks done (kept when its call is deleted).
export type FixItem = {
  id: number; created_at: string; session_id: string | null; turn: number | null; category: string; source: Finding["source"];
  fix_area: string; what_happened: string; why_it_matters: string; fix: string; quote: string;
  status: "open" | "done" | "wont_fix"; status_note: string; status_at: string | null;
};
export type ReviewSummary = {
  days: number; reviewed_calls: number; judged_calls: number; critical_calls: number; needs_review: number;
  average_score: number | null; average_by_dimension: Record<Dimension, number | null>;
  recurring_issues: { category: string; calls: number; agent_caused: number;
                      example: { session_id: string; what_happened: string; fix: string; fix_area: string } }[];
  issues_by_source: Record<string, number>; agent_issues_by_fix_area: Record<string, number>;
};
export type Calibration = {
  labelled_calls: number; exact_agreement: Record<Dimension, number | null>;
  agreement_within_one_point: Record<Dimension, number | null>;
  judge_minus_human: Record<Dimension, number | null>;  // above 0 = the judge is more generous than people
  critical_failure_recall: number | null; missed_critical_calls: string[]; false_positives: number; target: string;
};

export const audit = {
  calls: (q = "", before?: string, needsReview = false) =>
    call<CallSummary[]>(`/v1/calls?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}${before ? `&before=${encodeURIComponent(before)}` : ""}${needsReview ? "&needs_review=true" : ""}`,
                        { headers: headers(false) }),
  runReview: (id: string) => call<Review>(`/v1/calls/${id}/review`, { method: "POST", headers: headers(false) }),
  labelReview: (id: string, body: { scores: Partial<Record<Dimension, number>>; critical: boolean; note: string }) =>
    post<Review>(`/v1/calls/${id}/review/human`, body),
  calibration: () => call<Calibration>("/v1/reviews/calibration", { headers: headers(false) }),
  summary: (days = 30) => call<ReviewSummary>(`/v1/reviews/summary?days=${days}`, { headers: headers(false) }),
  fixes: (status: "open" | "done" | "wont_fix" | "all" = "open") => call<FixItem[]>(`/v1/fixes?status=${status}`, { headers: headers(false) }),
  setFix: (id: number, status: FixItem["status"], note = "") =>
    call<FixItem>(`/v1/fixes/${id}`, { method: "PATCH", headers: headers(true), body: JSON.stringify({ status, note }) }),
  call: (id: string) => call<CallRecord>(`/v1/calls/${id}`, { headers: headers(false) }),
  deleteCall: (id: string) => call<{ deleted: string }>(`/v1/calls/${id}`, { method: "DELETE", headers: headers(false) }),
  deleteRecording: (id: string, recordingId: string) =>
    call<{ deleted: string }>(`/v1/calls/${id}/recordings/${recordingId}`, { method: "DELETE", headers: headers(false) }),
  alerts: (after: number) => call<AlertRow[]>(`/v1/alerts?after=${after}`, { headers: headers(false) }),
  /** Recordings and the transcript are fetched with the API key header, then handed to the browser as a blob. */
  blob: async (path: string) => {
    const res = await fetch(API + path, { headers: headers(false) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
    return res.blob();
  },
  recordingPath: (callId: string, recId: string) => `/v1/calls/${callId}/recordings/${recId}`,
  transcriptPath: (callId: string) => `/v1/calls/${callId}/transcript.txt`,
};

// ── Agent desk (people who take over from the AI) ──
export type HandoffNote = {
  headline?: string; ai_summary?: { text: string; mood: string; label: string; model: string } | null;
  summary: string; reason: string; department: string; language: string; verified: boolean; customer: string | null;
  segment: string | null; rm: string | null; caller: string | null; asked: string[]; done: string[]; alerts: string[];
  facts: Record<string, string>;
};
export type Handoff = {
  id: string; session_id: string; department: string; department_name: string; priority: number; reason: string;
  note: HandoffNote; status: "waiting" | "active" | "completed" | "abandoned" | "callback"; channel: "call" | "chat";
  created_at: string; accepted_at: string | null; ended_at: string | null; agent_name: string | null;
  disposition: string | null; agent_notes: string | null; wait_s: number; caller: string | null; live?: boolean;
};
export type DeskTurn = { at: string; caller: string; agent: string; outcome: string; language: string };
export type HandoffDetail = Handoff & { messages: HandoffMessage[]; transcript: DeskTurn[]; verification: { verified: boolean; verified_at: string | null } };
export type HandoffMessage = { id: number; sender: "customer" | "agent"; text: string; created_at: string };
export type CustomerHandoff = { status: string; department?: string; agent?: string | null; position?: number; messages?: HandoffMessage[] };

export const desk = {
  board: () => call<{ handoffs: Handoff[]; agents: string[]; departments: Record<string, string> }>("/v1/desk/handoffs", { headers: headers(false) }),
  get: (id: string) => call<HandoffDetail>(`/v1/desk/handoffs/${id}`, { headers: headers(false) }),
  accept: (id: string, agent_name: string) => post<Handoff>(`/v1/desk/handoffs/${id}/accept`, { agent_name }),
  message: (id: string, text: string) => post<HandoffMessage>(`/v1/desk/handoffs/${id}/messages`, { text }),
  complete: (id: string, disposition: string, notes: string) => post<Handoff>(`/v1/desk/handoffs/${id}/complete`, { disposition, notes }),
  // the customer's side of a chat that was handed over
  status: (sessionId: string, after = 0) => call<CustomerHandoff>(`/v1/handoffs/${sessionId}?after=${after}`),
  send: (sessionId: string, text: string) => post<HandoffMessage>(`/v1/handoffs/${sessionId}/messages`, { text }),
};

// Same-address API ("/"): build ws:// or wss:// from this page's own address.
export const wsUrl = (path: string) =>
  (API || window.location.origin).replace(/^http/, "ws") + path + (KEY ? `?key=${encodeURIComponent(KEY)}` : "");
export const traceUrl = (id: string) => `${API}/v1/traces/${id}`;

// ───────── speed and quality per setup (/bench; brain services/benchmark.py) ─────────

export type BenchLatency = { n: number; p50: number; p95: number };
export type BenchSummary = {
  setup_id: string;
  setup: Record<string, string>;
  label: string;
  turns: number;
  first_at: string;
  last_at: string;
  enough: boolean;
  judged_calls: number;
  latency: Record<string, BenchLatency>;
  quality: Record<string, number | null>;
  pinned_at?: string;
};
export type BenchRow = {
  group: "latency" | "quality";
  key: string;
  label: string;
  lower_is_better: boolean | null;
  current: BenchLatency | number | null;
  baseline: BenchLatency | number | null;
  delta: number | null;
  delta_pct: number | null;
  better: boolean | null;
};
export type Bench = {
  current_setup_id: string;
  current: BenchSummary | null;
  baseline: BenchSummary | null;
  baseline_from: "pinned" | "auto";
  baseline_is_current: boolean;
  min_turns: number;
  rows: BenchRow[];
  others: { id: string; n: number; last_at: string }[];
};

export const bench = {
  get: () => call<Bench>("/v1/bench", { headers: headers(false) }),
  pin: (setupId?: string, label = "") => post<{ setup_id: string; label: string }>("/v1/bench/baseline", { setup_id: setupId ?? null, label }),
  unpin: () => call<{ baseline_from: string }>("/v1/bench/baseline", { method: "DELETE", headers: headers(false) }),
};
