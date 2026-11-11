"use client";

// Knowledge base: what is ingested and searchable, how each file was split into passages, and every change
// (upload, replace with a new version, roll back, disable, re-index, delete). Plus a search box showing exactly
// which passages a question would be answered from.
import { ChevronDown, ChevronUp, FileUp, RefreshCw, Search, Trash2, Upload, X } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type Chunk, type Doc, type DocDetail, kb, type SearchHit } from "@/lib/api";
import { cn } from "@/lib/utils";

const BUSY = new Set(["queued", "processing"]);
const ACCEPT = ".pdf,.docx,.xlsx,.csv,.md,.txt";
const ALLOWED = /\.(pdf|docx|xlsx|csv|md|txt)$/i;
const MAX_MB = 100;

type Act = (label: string, fn: () => Promise<unknown>) => Promise<void>;

function size(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Why a file can't be uploaded, or "" if it can. The browser's `accept` filter does not apply to dropped files. */
function rejectReason(f: File) {
  if (!ALLOWED.test(f.name)) return `"${f.name}" is not a supported type (PDF, DOCX, XLSX, CSV, MD, TXT).`;
  if (f.size > MAX_MB * 1024 * 1024) return `"${f.name}" is ${size(f.size)}; the limit is ${MAX_MB} MB.`;
  return "";
}

export default function KnowledgeBase() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [stats, setStats] = useState<{ searchable_chunks: number; pending_jobs: number } | null>(null);
  const [error, setError] = useState("");
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
      if (label) toast.success(label);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const indexed = docs.filter((d) => d.purpose === "knowledge" && d.enabled).length;

  return (
    <main className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="mr-auto">
          <h1 className="text-xl font-semibold tracking-tight">Knowledge base</h1>
          <p className="text-sm text-muted-foreground">What the agent answers general questions from.</p>
        </div>
        {stats && (
          <dl className="flex gap-8">
            <Stat label="documents" value={docs.length} />
            <Stat label="used for answers" value={indexed} />
            <Stat label="searchable passages" value={stats.searchable_chunks} />
            {stats.pending_jobs > 0 && <Stat label="processing" value={stats.pending_jobs} />}
          </dl>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex items-center gap-2">
            <span className="mr-auto">{error}</span>
            <Button size="sm" variant="ghost" onClick={() => setError("")}>Dismiss</Button>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <UploadForm onDone={(msg) => { setError(""); toast.success(msg); void refresh(); }} onError={setError} />
        <SearchPanel />
      </div>

      <Card className="gap-0 py-0">
        <CardHeader className="border-b py-4">
          <CardTitle className="text-sm">Documents</CardTitle>
          <CardDescription>Click a document to see its passages, versions and actions.</CardDescription>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-6">Document</TableHead>
              <TableHead>Used for</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Passages</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {docs.map((d) => {
              const selected = open === d.id;
              const sheets = d.options?.sheets;
              return (
                <TableRow key={d.id} onClick={() => setOpen(selected ? null : d.id)} data-state={selected ? "selected" : undefined}
                          className="cursor-pointer">
                  <TableCell className="pl-6">
                    <p className="font-medium">{d.title}</p>
                    <p className="text-xs whitespace-normal text-muted-foreground">
                      {[d.filename, sheets?.length ? `only sheet${sheets.length > 1 ? "s" : ""}: ${sheets.join(", ")}` : null,
                        d.category, d.effective_from && `valid from ${d.effective_from}`].filter(Boolean).join(" · ")}
                    </p>
                  </TableCell>
                  <TableCell>
                    {d.purpose === "data" ? <Badge variant="secondary">Stored only</Badge>
                      : d.enabled ? <Badge variant="outline" className="border-success/40 text-success">Answers</Badge>
                        : <Badge variant="outline" className="text-muted-foreground">Disabled</Badge>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <span className={cn(d.latest_status === "failed" && "text-destructive")}>
                      {BUSY.has(d.latest_status) ? `${d.latest_status}…` : d.latest_status}
                    </span>
                    <span className="text-muted-foreground"> · v{d.current_version ?? "-"}</span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{d.chunks}</TableCell>
                  <TableCell className="pr-4 text-muted-foreground">{selected ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}</TableCell>
                </TableRow>
              );
            })}
            {docs.length === 0 && (
              <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">Nothing ingested yet. Drop a document above.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {open && <DocumentView key={open} id={open} act={act} onClose={() => setOpen(null)} />}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-right">
      <dt className="sr-only">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums leading-tight">{value}</dd>
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
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed text-center text-sm transition-colors",
        "outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        compact ? "gap-1 px-4 py-4" : "gap-2 px-4 py-10",
        over ? "border-primary bg-secondary" : "border-border hover:border-primary/40 hover:bg-muted/50",
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
    <div className={cn("space-y-1.5", className)}>
      <Label className="text-xs font-normal text-muted-foreground">{label}{hint && <span className="opacity-70"> ({hint})</span>}</Label>
      {children}
    </div>
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
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-sm">Add a document</CardTitle>
        <CardDescription>PDF (with a text layer), DOCX, XLSX, CSV, Markdown or TXT, up to {MAX_MB} MB. The same file is never stored twice.</CardDescription>
      </CardHeader>
      <CardContent>
        {!file ? (
          <DropZone onFile={pick}>
            <FileUp className="size-6 text-muted-foreground" />
            <span className="font-medium">Drop a file here</span>
            <span className="text-muted-foreground">or <span className="text-foreground underline underline-offset-4">choose a file</span></span>
          </DropZone>
        ) : (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void upload(); }}>
            <div className="flex items-center gap-3 rounded-md border bg-muted/40 px-3 py-2 text-sm">
              <Badge variant="secondary" className="font-mono uppercase">{file.name.split(".").pop()}</Badge>
              <span className="min-w-0 flex-1 truncate font-medium" title={file.name}>{file.name}</span>
              <span className="text-xs text-muted-foreground">{size(file.size)}</span>
              <Button type="button" size="icon" variant="ghost" className="size-7" onClick={reset} disabled={busy} title="Choose another file"><X /></Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Title" hint="default: file name">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file.name.replace(/\.[^.]+$/, "")} />
              </Field>
              <Field label="Category" hint="optional">
                <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. rates" />
              </Field>
              <Field label="Use">
                <Select value={purpose} onValueChange={setPurpose}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="knowledge">Use for answers</SelectItem>
                    <SelectItem value="data">Store only (never searched)</SelectItem>
                  </SelectContent>
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
              <Button type="submit" disabled={busy}><Upload /> {busy ? "Uploading…" : "Upload"}</Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function Confirm({ trigger, title, text, action, onConfirm }: { trigger: ReactNode; title: string; text: string; action: string; onConfirm: () => void }) {
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
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-white hover:bg-destructive/90">{action}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DocumentView({ id, act, onClose }: { id: string; act: Act; onClose: () => void }) {
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [filter, setFilter] = useState("");
  const [title, setTitle] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const ref = useRef<HTMLDivElement>(null);

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

  if (!doc) return <Card><CardContent className="text-sm text-muted-foreground">Loading…</CardContent></Card>;
  const shown = filter ? chunks.filter((c) => `${c.section_path} ${c.text}`.toLowerCase().includes(filter.toLowerCase())) : chunks;
  const run = async (label: string, fn: () => Promise<unknown>) => { await act(label, fn); await load(); };
  const replace = (f: File) => {
    const why = rejectReason(f);
    if (why) return void act("", () => Promise.reject(new Error(why)));
    void run(`Uploaded a new version of ${doc.title}`, () => kb.replace(id, f));
  };

  return (
    <Card ref={ref} className="scroll-mt-20 gap-0 py-0">
      <div className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <h2 className="font-semibold">{doc.title}</h2>
        <Badge variant="outline" className={doc.searchable ? "border-success/40 text-success" : "text-muted-foreground"}>
          {doc.searchable ? "Used for answers" : "Not used for answers"}
        </Badge>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}><X /> Close</Button>
      </div>

      <div className="grid gap-6 p-6 lg:grid-cols-2">
        <section className="space-y-3">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Details</h3>
          <div className="flex items-end gap-2">
            <Field label="Title" className="flex-1"><Input value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
            <Button variant="outline" disabled={title === doc.title || !title.trim()} onClick={() => void run("Renamed", () => kb.update(id, { title }))}>Save</Button>
          </div>
          <div className="flex items-end gap-2">
            <Field label="Valid from" hint="shown to the AI with this source" className="flex-1">
              <Input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
            </Field>
            <Button variant="outline" disabled={!validFrom || validFrom === (doc.effective_from ?? "")}
                    onClick={() => void run(`Valid from ${validFrom}`, () => kb.update(id, { effective_from: validFrom }))}>Save</Button>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">New version</h3>
          <DropZone onFile={replace} compact>
            <span>Drop a new file here, or <span className="underline underline-offset-4">choose one</span></span>
            <span className="text-xs text-muted-foreground">The old version stays in use until the new one is indexed.</span>
          </DropZone>
          <div className="flex flex-wrap gap-2">
            {doc.purpose === "knowledge" && (
              <Button variant="outline" onClick={() => void run(`${doc.enabled ? "Disabled" : "Enabled"} ${doc.title}`, () => kb.update(id, { enabled: !doc.enabled }))}>
                {doc.enabled ? "Disable" : "Enable"}
              </Button>
            )}
            <Button variant="outline" onClick={() => void run("Re-indexing", () => kb.reindex(id))}><RefreshCw /> Re-index</Button>
            <Confirm title={`Delete "${doc.title}"?`} action="Delete"
                     text="It stops being used for answers at once. The files are kept for audit."
                     onConfirm={() => void act(`Deleted ${doc.title}`, () => kb.remove(id, false)).then(onClose)}
                     trigger={<Button variant="outline"><Trash2 /> Delete</Button>} />
            <Confirm title={`Permanently delete "${doc.title}"?`} action="Delete permanently"
                     text="Its files, versions and passages are removed. This cannot be undone."
                     onConfirm={() => void act(`Permanently deleted ${doc.title}`, () => kb.remove(id, true)).then(onClose)}
                     trigger={<Button variant="ghost" className="text-destructive hover:text-destructive">Delete permanently</Button>} />
          </div>
        </section>
      </div>

      <section className="border-t px-6 py-4">
        <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Versions</h3>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Version</TableHead><TableHead>File</TableHead><TableHead>Status</TableHead>
              <TableHead className="text-right">Passages</TableHead><TableHead>Uploaded</TableHead><TableHead>Parser</TableHead><TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {doc.versions.map((v) => {
              const r = v.report as Record<string, unknown> | null;
              const sheets = v.options?.sheets;
              return (
                <TableRow key={v.version_no} className="align-top text-xs">
                  <TableCell>v{v.version_no}{v.version_no === doc.current_version && <span className="text-muted-foreground"> current</span>}</TableCell>
                  <TableCell className="whitespace-normal">{v.filename}<span className="text-muted-foreground"> · {size(v.size_bytes)}</span>
                    {sheets?.length ? <span className="text-muted-foreground"> · only {sheets.join(", ")}</span> : null}</TableCell>
                  <TableCell className="whitespace-normal">{v.status}{v.error && <span className="text-destructive"> · {v.error}</span>}</TableCell>
                  <TableCell className="text-right tabular-nums">{v.chunks}</TableCell>
                  <TableCell>{new Date(v.created_at).toLocaleString()}</TableCell>
                  <TableCell className="whitespace-normal">
                    {r ? (
                      <details>
                        <summary className="cursor-pointer">{String(r.parser ?? "report")}{r.total_ms != null && ` · ${String(r.total_ms)} ms`}</summary>
                        <pre className="mt-1 font-mono text-[11px] whitespace-pre-wrap text-muted-foreground">{JSON.stringify(r, null, 1)}</pre>
                      </details>
                    ) : "-"}
                  </TableCell>
                  <TableCell className="text-right">
                    {v.version_no !== doc.current_version && v.chunks > 0 && (
                      <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={() => void run(`Rolled back to v${v.version_no}`, () => kb.activate(id, v.version_no))}>
                        Make current
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </section>

      <section className="border-t px-6 py-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Passages <span className="normal-case tracking-normal">({shown.length}{filter && ` of ${chunks.length}`})</span>
          </h3>
          <Input className="ml-auto h-8 w-64" placeholder="Filter passages…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
          {shown.map((c) => (
            <div key={c.id} className="rounded-md border p-3 text-xs">
              <p className="mb-1.5 text-muted-foreground">#{c.ordinal} · {c.kind} · {c.word_count} words · {c.section_path}</p>
              <p className="leading-relaxed whitespace-pre-wrap">{c.text}</p>
            </div>
          ))}
          {shown.length === 0 && <p className="text-xs text-muted-foreground">No passages{filter && " match the filter"}.</p>}
        </div>
      </section>
    </Card>
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
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="text-sm">Which passages would a question use?</CardTitle>
        <CardDescription>Runs the same search the agent uses, without asking the AI for an answer.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void search(); }}>
          <Input placeholder="e.g. senior citizen FD rate for 13 months" value={query} onChange={(e) => setQuery(e.target.value)} />
          <Button type="submit" disabled={busy || !query.trim()}><Search /> {busy ? "Searching…" : "Search"}</Button>
        </form>
        {hits && (
          <>
            <div className="flex items-center text-xs text-muted-foreground">
              <span className="mr-auto">The top 5 above the relevance threshold are what the AI answers from.</span>
              <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={() => setHits(null)}>Clear</Button>
            </div>
            <div className="max-h-[26rem] space-y-2 overflow-y-auto pr-1">
              {hits.slice(0, 8).map((h, i) => (
                <div key={h.chunk_id} className="rounded-md border p-2.5 text-xs">
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
          </>
        )}
      </CardContent>
    </Card>
  );
}
