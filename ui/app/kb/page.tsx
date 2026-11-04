"use client";

// Knowledge base: what is ingested and searchable, how each file was split into passages, and every change
// (upload, replace with a new version, roll back, disable, re-index, delete). Plus a search box showing exactly
// which passages a question would be answered from.
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, cx, Input, Panel, Select } from "@/components/ui";
import { type Chunk, type Doc, type DocDetail, kb, type SearchHit } from "@/lib/api";

const BUSY = new Set(["queued", "processing"]);
const ACCEPT = ".pdf,.docx,.xlsx,.csv,.md,.txt";
const ALLOWED = /\.(pdf|docx|xlsx|csv|md|txt)$/i;
const MAX_BYTES = 25 * 1024 * 1024;

type Act = (label: string, fn: () => Promise<unknown>) => Promise<void>;

function size(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Why a file can't be uploaded, or "" if it can. The browser's `accept` filter does not apply to dropped files. */
function rejectReason(f: File) {
  if (!ALLOWED.test(f.name)) return `"${f.name}" is not a supported type (PDF, DOCX, XLSX, CSV, MD, TXT).`;
  if (f.size > MAX_BYTES) return `"${f.name}" is ${size(f.size)}; the limit is 25 MB.`;
  return "";
}

export default function KnowledgeBase() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [stats, setStats] = useState<{ searchable_chunks: number; pending_jobs: number } | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, s] = await Promise.all([kb.list(), kb.stats()]);
      setDocs(list);
      setStats(s);
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // While anything is being processed, refresh every 2 s.
  useEffect(() => {
    if (!docs.some((d) => BUSY.has(d.latest_status))) return;
    const t = setInterval(() => void refresh(), 2000);
    return () => clearInterval(t);
  }, [docs, refresh]);

  // A file dropped outside a drop zone would otherwise make the browser leave the page to open it.
  useEffect(() => {
    const stop = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, []);

  const act: Act = async (label, fn) => {
    setError("");
    try {
      await fn();
      setNote(label);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const indexed = docs.filter((d) => d.purpose === "knowledge" && d.enabled).length;

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6">
      <header className="flex flex-wrap items-end gap-x-6 gap-y-2">
        <div className="mr-auto">
          <h1 className="text-xl font-semibold">Knowledge base</h1>
          <p className="text-sm text-muted-foreground">What the agent answers general questions from.</p>
        </div>
        {stats && (
          <dl className="flex gap-6 text-sm">
            <Stat label="documents" value={docs.length} />
            <Stat label="used for answers" value={indexed} />
            <Stat label="searchable passages" value={stats.searchable_chunks} />
            {stats.pending_jobs > 0 && <Stat label="processing" value={stats.pending_jobs} />}
          </dl>
        )}
        <Link href="/" className="text-sm underline underline-offset-4">← Test console</Link>
      </header>

      {error && (
        <p className="flex items-start gap-3 rounded-md border border-foreground/30 bg-muted px-3 py-2 text-sm" role="alert">
          <span className="mr-auto">{error}</span>
          <button className="text-xs underline" onClick={() => setError("")}>dismiss</button>
        </p>
      )}
      {note && !error && <p className="text-sm text-muted-foreground" role="status">{note}</p>}

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <UploadForm onDone={(msg) => { setError(""); setNote(msg); void refresh(); }} onError={setError} />
        <SearchPanel />
      </div>

      <Panel title="Documents">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b border-border">
                <th className="pb-2 pr-4 font-medium">Document</th>
                <th className="pb-2 pr-4 font-medium">Used for</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 pr-4 text-right font-medium">Passages</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const selected = open === d.id;
                const sheets = d.options?.sheets;
                return (
                  <tr key={d.id} onClick={() => setOpen(selected ? null : d.id)}
                      className={cx("cursor-pointer border-b border-border last:border-0 hover:bg-muted/60", selected && "bg-muted")}>
                    <td className="py-2.5 pr-4">
                      <p className="font-medium">{d.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {[
                          d.filename,
                          sheets?.length ? `only sheet${sheets.length > 1 ? "s" : ""}: ${sheets.join(", ")}` : null,
                          d.category,
                          d.effective_from && `valid from ${d.effective_from}`,
                        ].filter(Boolean).join(" · ")}
                      </p>
                    </td>
                    <td className="pr-4">
                      {d.purpose === "data" ? <Badge>stored only</Badge> : d.enabled ? <Badge>answers</Badge>
                        : <Badge className="text-muted-foreground">disabled</Badge>}
                    </td>
                    <td className="pr-4 whitespace-nowrap">
                      {BUSY.has(d.latest_status) ? `${d.latest_status}…` : d.latest_status}
                      <span className="text-muted-foreground"> · v{d.current_version ?? "-"}</span>
                    </td>
                    <td className="pr-4 text-right tabular-nums">{d.chunks}</td>
                    <td className="text-right">
                      <button className="px-2 text-muted-foreground" aria-expanded={selected} aria-label={`Open ${d.title}`}>
                        {selected ? "▴" : "▾"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {docs.length === 0 && (
                <tr><td colSpan={5} className="py-6 text-center text-muted-foreground">Nothing ingested yet. Drop a document above.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {docs.length > 0 && !open && <p className="mt-3 text-xs text-muted-foreground">Click a document to see its passages, versions and actions.</p>}
      </Panel>

      {open && <DocumentView key={open} id={open} act={act} onClose={() => setOpen(null)} />}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-right">
      <dt className="sr-only">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums leading-tight">{value}</dd>
      <dd className="text-xs text-muted-foreground" aria-hidden>{label}</dd>
    </div>
  );
}

/** A dashed box you can drop a file on, or click (or press Enter) to choose one. */
function DropZone({ onFile, compact = false, children }: { onFile: (f: File) => void; compact?: boolean; children: ReactNode }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      role="button" tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; setOver(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false); }}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
      className={cx(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed text-center text-sm transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2",
        compact ? "gap-0.5 px-4 py-3" : "gap-1 px-4 py-8",
        over ? "border-foreground bg-muted" : "border-border hover:border-foreground/40 hover:bg-muted/50",
      )}
    >
      {children}
      <input ref={input} type="file" hidden accept={ACCEPT}
             onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </div>
  );
}

function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cx("flex flex-col gap-1 text-xs text-muted-foreground", className)}>
      <span>{label}{hint && <span className="ml-1 opacity-70">({hint})</span>}</span>
      {children}
    </label>
  );
}

function UploadForm({ onDone, onError }: { onDone: (msg: string) => void; onError: (msg: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [purpose, setPurpose] = useState("knowledge");
  const [sheets, setSheets] = useState("");
  const [effective, setEffective] = useState("");
  const [busy, setBusy] = useState(false);
  const isSheet = !!file && /\.(xlsx|csv)$/i.test(file.name);

  function pick(f: File) {
    const why = rejectReason(f);
    if (why) return onError(why);
    setFile(f);
  }

  function reset() {
    setFile(null); setTitle(""); setCategory(""); setSheets(""); setEffective(""); setPurpose("knowledge");
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    try {
      const r = await kb.upload(file, { title, category, purpose, sheets: isSheet ? sheets : "", effective_from: effective });
      onDone(r.deduplicated ? `"${file.name}" is already in the knowledge base (same content), so nothing was added.`
                            : `Uploaded "${file.name}". It becomes searchable when its status says indexed.`);
      reset();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Add a document">
      {!file ? (
        <>
          <DropZone onFile={pick}>
            <span className="font-medium">Drop a file here</span>
            <span className="text-muted-foreground">or <span className="underline underline-offset-4">choose a file</span></span>
          </DropZone>
          <p className="mt-2 text-xs text-muted-foreground">
            PDF (with a text layer), DOCX, XLSX, CSV, Markdown or TXT, up to 25 MB. The same file is never stored twice.
          </p>
        </>
      ) : (
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void upload(); }}>
          <div className="flex items-center gap-3 rounded-md border border-border px-3 py-2 text-sm">
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs uppercase">{file.name.split(".").pop()}</span>
            <span className="min-w-0 flex-1 truncate font-medium" title={file.name}>{file.name}</span>
            <span className="text-xs text-muted-foreground">{size(file.size)}</span>
            <button type="button" className="text-xs underline" onClick={reset} disabled={busy}>change</button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Title" hint="default: file name">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file.name.replace(/\.[^.]+$/, "")} />
            </Field>
            <Field label="Category" hint="optional">
              <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. rates" />
            </Field>
            <Field label="Use">
              <Select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                <option value="knowledge">Use for answers</option>
                <option value="data">Store only (never searched)</option>
              </Select>
            </Field>
            <Field label="Valid from" hint="optional">
              <Input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} />
            </Field>
            {isSheet && (
              <Field label="Sheets to include" hint="comma-separated; empty = all" className="sm:col-span-2">
                <Input value={sheets} onChange={(e) => setSheets(e.target.value)} placeholder="e.g. Acct Debit card Mapping" />
              </Field>
            )}
          </div>
          {purpose === "data" && (
            <p className="text-xs text-muted-foreground">Store only keeps the file (e.g. customer data) without ever making it searchable.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={reset} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? "Uploading…" : "Upload"}</Button>
          </div>
        </form>
      )}
    </Panel>
  );
}

function DocumentView({ id, act, onClose }: { id: string; act: Act; onClose: () => void }) {
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [filter, setFilter] = useState("");
  const [title, setTitle] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const ref = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    const d = await kb.get(id);
    setDoc(d);
    setTitle(d.title);
    setValidFrom(d.versions.find((v) => v.version_no === d.current_version)?.effective_from ?? "");
    setChunks(d.current_version ? (await kb.chunks(id).catch(() => ({ chunks: [] as Chunk[] }))).chunks : []);
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (doc) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [doc?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!doc) return <Panel><p className="text-sm text-muted-foreground">Loading…</p></Panel>;
  const shown = filter ? chunks.filter((c) => `${c.section_path} ${c.text}`.toLowerCase().includes(filter.toLowerCase())) : chunks;
  const run = async (label: string, fn: () => Promise<unknown>) => { await act(label, fn); await load(); };
  const replace = (f: File) => {
    const why = rejectReason(f);
    if (why) return void act("", () => Promise.reject(new Error(why)));
    void run(`Uploaded a new version of ${doc.title}`, () => kb.replace(id, f));
  };

  return (
    <section ref={ref} className="scroll-mt-4 rounded-lg border border-border">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <h2 className="text-base font-semibold">{doc.title}</h2>
        <Badge>{doc.searchable ? "used for answers" : "not used for answers"}</Badge>
        <button className="ml-auto text-sm text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">✕ close</button>
      </header>

      <div className="grid gap-6 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Details</h3>
          <div className="flex items-end gap-2">
            <Field label="Title" className="flex-1">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Button variant="outline" disabled={title === doc.title || !title.trim()}
                    onClick={() => void run("Renamed", () => kb.update(id, { title }))}>Save</Button>
          </div>
          <div className="flex items-end gap-2">
            <Field label="Valid from" hint="shown to the AI with this source" className="flex-1">
              <Input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
            </Field>
            <Button variant="outline" disabled={!validFrom || validFrom === (doc.effective_from ?? "")}
                    onClick={() => void run(`Valid from ${validFrom}`, () => kb.update(id, { effective_from: validFrom }))}>Save</Button>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">New version</h3>
          <DropZone onFile={replace} compact>
            <span>Drop a new file here, or <span className="underline underline-offset-4">choose one</span></span>
            <span className="text-xs text-muted-foreground">The old version stays until the new one is indexed.</span>
          </DropZone>
          <div className="flex flex-wrap gap-2">
            {doc.purpose === "knowledge" && (
              <Button variant="outline" onClick={() => void run(`${doc.enabled ? "Disabled" : "Enabled"} ${doc.title}`, () => kb.update(id, { enabled: !doc.enabled }))}>
                {doc.enabled ? "Disable" : "Enable"}
              </Button>
            )}
            <Button variant="outline" onClick={() => void run("Re-indexing", () => kb.reindex(id))}>Re-index</Button>
            <Button variant="outline"
                    onClick={() => window.confirm(`Delete "${doc.title}"? It stops being used for answers at once; files are kept for audit.`) &&
                      void act(`Deleted ${doc.title}`, () => kb.remove(id, false)).then(onClose)}>
              Delete
            </Button>
            <Button variant="ghost" className="text-muted-foreground"
                    onClick={() => window.confirm(`Permanently delete "${doc.title}", its files and passages? This cannot be undone.`) &&
                      void act(`Permanently deleted ${doc.title}`, () => kb.remove(id, true)).then(onClose)}>
              Delete permanently
            </Button>
          </div>
        </div>
      </div>

      <div className="border-t border-border p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Versions</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr><th className="pb-1 pr-4 font-medium">Version</th><th className="pr-4 font-medium">File</th><th className="pr-4 font-medium">Status</th>
                <th className="pr-4 text-right font-medium">Passages</th><th className="pr-4 font-medium">Uploaded</th><th className="pr-4 font-medium">Parser</th><th /></tr>
            </thead>
            <tbody>
              {doc.versions.map((v) => {
                const r = v.report as Record<string, unknown> | null;
                const sheets = v.options?.sheets;
                return (
                  <tr key={v.version_no} className="border-t border-border align-top">
                    <td className="py-1.5 pr-4">v{v.version_no}{v.version_no === doc.current_version && <span className="text-muted-foreground"> current</span>}</td>
                    <td className="pr-4">{v.filename}<span className="text-muted-foreground"> · {size(v.size_bytes)}</span>
                      {sheets?.length ? <span className="text-muted-foreground"> · only {sheets.join(", ")}</span> : null}</td>
                    <td className="pr-4">{v.status}{v.error && <span className="text-muted-foreground"> · {v.error}</span>}</td>
                    <td className="pr-4 text-right tabular-nums">{v.chunks}</td>
                    <td className="pr-4 whitespace-nowrap">{new Date(v.created_at).toLocaleString()}</td>
                    <td className="pr-4">
                      {r ? (
                        <details>
                          <summary className="cursor-pointer">{String(r.parser ?? "report")}{r.total_ms != null && ` · ${r.total_ms} ms`}</summary>
                          <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{JSON.stringify(r, null, 1)}</pre>
                        </details>
                      ) : "-"}
                    </td>
                    <td className="text-right">
                      {v.version_no !== doc.current_version && v.chunks > 0 && (
                        <button className="underline" onClick={() => void run(`Rolled back to v${v.version_no}`, () => kb.activate(id, v.version_no))}>make current</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="border-t border-border p-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Passages <span className="normal-case tracking-normal">({shown.length}{filter && ` of ${chunks.length}`})</span>
          </h3>
          <Input className="ml-auto h-8 w-64" placeholder="Filter passages…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
          {shown.map((c) => (
            <div key={c.id} className="rounded-md border border-border p-3 text-xs">
              <p className="mb-1.5 text-muted-foreground">#{c.ordinal} · {c.kind} · {c.word_count} words · {c.section_path}</p>
              <p className="whitespace-pre-wrap leading-relaxed">{c.text}</p>
            </div>
          ))}
          {shown.length === 0 && <p className="text-xs text-muted-foreground">No passages{filter && " match the filter"}.</p>}
        </div>
      </div>
    </section>
  );
}

function SearchPanel() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    try {
      setHits((await kb.search(query)).reranked);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Which passages would a question use?">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <Input placeholder="e.g. senior citizen FD rate for 13 months" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" disabled={busy || !query.trim()}>{busy ? "Searching…" : "Search"}</Button>
      </form>
      {!hits && <p className="mt-2 text-xs text-muted-foreground">Runs the same search the agent uses, without asking the AI.</p>}
      {hits && (
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex items-center text-xs text-muted-foreground">
            <span className="mr-auto">The top 5 above the relevance threshold are what the AI answers from.</span>
            <button className="underline" onClick={() => setHits(null)}>clear</button>
          </div>
          <div className="max-h-[26rem] space-y-2 overflow-y-auto pr-1">
            {hits.slice(0, 8).map((h, i) => (
              <div key={h.chunk_id} className="rounded-md border border-border p-2.5 text-xs">
                <p className="mb-1 flex flex-wrap gap-x-2 text-muted-foreground">
                  <span className="font-medium text-foreground">#{i + 1}</span>
                  <span>relevance {h.rerank_score?.toFixed(2)}</span>
                  <span>vector #{h.dense_rank ?? "-"}</span>
                  <span>keyword #{h.keyword_rank ?? "-"}</span>
                </p>
                <p className="mb-1 text-muted-foreground">{h.section}</p>
                <p className="whitespace-pre-wrap">{h.text.slice(0, 500)}{h.text.length > 500 && "…"}</p>
              </div>
            ))}
            {hits.length === 0 && <p className="text-xs text-muted-foreground">No passages found.</p>}
          </div>
        </div>
      )}
    </Panel>
  );
}
