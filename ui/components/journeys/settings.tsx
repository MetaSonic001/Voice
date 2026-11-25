"use client";

// Settings tab: the business settings the journeys use. Teams (name, priority, open 24x7, what they handle), the
// turnaround promised on each kind of ticket, and which team each kind of problem goes to. Which teams and problem
// types exist is decided in code; their details are edited here.
import { Loader2, Plus, Save, Sparkles, Square, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Confirm, human, Problems } from "@/components/journeys/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { type Department, type DraftChanges, type Editor, journeys, type Language } from "@/lib/api";
import { cn } from "@/lib/utils";

type Save = (c: DraftChanges, ok?: string) => Promise<boolean>;

export function SettingsTab({ editor, onSave, author, onEditor }: { editor: Editor; onSave: Save; author: string; onEditor: (e: Editor) => void }) {
  const problems = editor.problems.filter((p) => p.area === "setting");
  return (
    <div className="space-y-4">
      <Problems items={problems} />
      <Languages editor={editor} onSave={onSave} author={author} onEditor={onEditor} />
      <Teams editor={editor} onSave={onSave} />
      <Turnaround editor={editor} onSave={onSave} />
      <IssueTeams editor={editor} onSave={onSave} />
    </div>
  );
}

function Teams({ editor, onSave }: { editor: Editor; onSave: Save }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Teams</CardTitle>
        <CardDescription>
          Where the agent hands callers over. Priority 1 is picked up first. Teams not open 24x7 follow the desk hours; outside
          them the caller gets a callback. A new name is said to callers: its Hindi is written when you prepare translations.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader><TableRow><TableHead className="min-w-44">Team</TableHead><TableHead>Priority</TableHead><TableHead>Open 24x7</TableHead>
            <TableHead className="min-w-64">What they handle</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>
            {Object.entries(editor.settings.departments).map(([key, d]) => (
              <TeamRow key={key} id={key} name={editor.team_names[key] ?? key} dept={d} live={editor.live_settings.departments[key]} onSave={onSave} />
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function TeamRow({ id, name, dept, live, onSave }: { id: string; name: string; dept: Department; live: Department; onSave: Save }) {
  const [row, setRow] = useState({ name, ...dept });
  useEffect(() => setRow({ name, ...dept }), [name, dept]);
  const dirty = row.name !== name || row.priority !== dept.priority || row.always_open !== dept.always_open || row.handles !== dept.handles;
  const changed = JSON.stringify(dept) !== JSON.stringify(live);
  async function save() {
    const changes: DraftChanges = { settings: { departments: { [id]: { priority: row.priority, always_open: row.always_open, handles: row.handles } } } };
    if (row.name !== name) changes.messages = { [`dept.${id}`]: { en: row.name } };
    await onSave(changes, "Team saved to the draft.");
  }
  return (
    <TableRow className={cn(changed && "bg-warning/5")}>
      <TableCell><Input value={row.name} onChange={(e) => setRow({ ...row, name: e.target.value })} aria-label={`Name of ${id}`} /></TableCell>
      <TableCell>
        <Select value={String(row.priority)} onValueChange={(v) => setRow({ ...row, priority: Number(v) })}>
          <SelectTrigger className="w-20"><SelectValue /></SelectTrigger>
          <SelectContent>{[1, 2, 3].map((p) => <SelectItem key={p} value={String(p)}>{p}</SelectItem>)}</SelectContent>
        </Select>
      </TableCell>
      <TableCell><input type="checkbox" checked={row.always_open} onChange={(e) => setRow({ ...row, always_open: e.target.checked })} aria-label="Open 24x7" /></TableCell>
      <TableCell><Input value={row.handles} onChange={(e) => setRow({ ...row, handles: e.target.value })} aria-label={`What ${id} handles`} /></TableCell>
      <TableCell>{dirty && <Button size="sm" onClick={() => void save()}><Save /> Save</Button>}</TableCell>
    </TableRow>
  );
}

function Turnaround({ editor, onSave }: { editor: Editor; onSave: Save }) {
  const days = editor.settings.tat_days;
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => setValues(Object.fromEntries(Object.entries(days).map(([k, v]) => [k, String(v)]))), [days]);
  const dirty = Object.keys(days).filter((k) => values[k] !== undefined && Number(values[k]) !== days[k]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Promised turnaround</CardTitle>
        <CardDescription>Working days the agent promises when it raises each kind of ticket (&quot;they&apos;ll get back to you by …&quot;).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(days).map(([key, v]) => (
            <label key={key} className={cn("flex items-center justify-between gap-3 rounded-md border p-2 text-sm",
                                           editor.live_settings.tat_days[key] !== v && "border-warning/60")}>
              <span className="min-w-0 break-words">{human(key)}</span>
              <Input type="number" min={1} max={30} value={values[key] ?? ""} className="w-20"
                     onChange={(e) => setValues({ ...values, [key]: e.target.value })} />
            </label>
          ))}
        </div>
        <Button size="sm" disabled={!dirty.length}
                onClick={() => void onSave({ settings: { tat_days: Object.fromEntries(dirty.map((k) => [k, Number(values[k])])) } }, "Saved to the draft.")}>
          <Save /> Save
        </Button>
      </CardContent>
    </Card>
  );
}

function IssueTeams({ editor, onSave }: { editor: Editor; onSave: Save }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Problems and their team</CardTitle>
        <CardDescription>Which team gets a complaint or dispute of each kind. The kind of ticket and how it is searched stay in code.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        {Object.entries(editor.settings.issues).map(([key, issue]) => (
          <label key={key} className={cn("flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-sm",
                                         editor.live_settings.issues[key].department !== issue.department && "border-warning/60")}>
            <span className="min-w-0 break-words">{human(key)} <span className="text-xs text-muted-foreground">({issue.ticket.replace("_", " ")})</span></span>
            <Select value={issue.department} onValueChange={(v) => void onSave({ settings: { issues: { [key]: { department: v } } } }, "Saved to the draft.")}>
              <SelectTrigger className="w-full sm:w-48"><SelectValue /></SelectTrigger>
              <SelectContent>{Object.keys(editor.settings.departments).map((d) => <SelectItem key={d} value={d}>{editor.team_names[d] ?? d}</SelectItem>)}</SelectContent>
            </Select>
          </label>
        ))}
      </CardContent>
    </Card>
  );
}

function Languages({ editor, onSave, author, onEditor }: { editor: Editor; onSave: Save; author: string; onEditor: (e: Editor) => void }) {
  const [name, setName] = useState("");
  const [style, setStyle] = useState("");
  const code = name.trim().toLowerCase().replace(/[^a-z]+/g, "_").replace(/^_|_$/g, "");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Languages</CardTitle>
        <CardDescription>
          English is the source: every sentence is written in it. The AI writes each other language following its writing
          style below. A language you add here gets translations ready, but callers can only use it once a developer adds
          speech and understanding for it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {editor.languages.map((l) => <LanguageRow key={l.code} lang={l} onSave={onSave} author={author} onEditor={onEditor} />)}
        <div className="space-y-2 rounded-md border border-dashed p-3">
          <p className="text-sm font-medium">Add a language</p>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Marathi" maxLength={40} className="sm:max-w-xs" />
          <Textarea value={style} onChange={(e) => setStyle(e.target.value)} rows={2} maxLength={600}
                    placeholder="How it should be written, e.g. everyday spoken Marathi in Devanagari, banking words in English as people say them" />
          <Button size="sm" variant="outline" disabled={code.length < 2 || style.trim().length < 10 || editor.languages.some((l) => l.code === code)}
                  onClick={async () => {
                    if (await onSave({ languages: { [code]: { name: name.trim(), style: style.trim() } } }, `${name.trim()} added to the draft.`)) {
                      setName(""); setStyle("");
                    }
                  }}>
            <Plus /> Add language
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function LanguageRow({ lang, onSave, author, onEditor }: { lang: Language; onSave: Save; author: string; onEditor: (e: Editor) => void }) {
  const [style, setStyle] = useState(lang.style);
  const [progress, setProgress] = useState<{ done: number; left: number } | null>(null);
  const stop = useRef(false);
  /** Every sentence with no text yet in this language, 50 per call, until none is left (or a round makes no progress). */
  async function fillAll() {
    stop.current = false;
    let done = 0;
    setProgress({ done, left: lang.missing ?? 0 });
    try {
      for (;;) {
        const out = await journeys.translate(author, { languages: [lang.code], fill_missing: true });
        onEditor(out.editor);
        done += out.translated;
        const left = out.editor.languages.find((l) => l.code === lang.code)?.missing ?? 0;
        setProgress({ done, left });
        if (!left || !out.translated || stop.current) {
          if (out.failed.length) toast.warning(`${out.failed.length} couldn't be translated, e.g. ${out.failed[0].id}: ${out.failed[0].text}`);
          else if (!left) toast.success(`Every sentence now has ${lang.name}. Read them before publishing.`);
          break;
        }
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setProgress(null);
    }
  }
  useEffect(() => setStyle(lang.style), [lang.style]);
  return (
    <div className={cn("space-y-2 rounded-md border p-3", lang.changed && "border-warning/60")}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{lang.name}</p>
        <code className="text-xs text-muted-foreground">{lang.code}</code>
        {lang.source ? <Badge variant="secondary">Source</Badge>
          : lang.spoken ? <Badge variant="outline" className="border-success/50 text-success">Spoken by the agent</Badge>
          : <Badge variant="outline">Kept ready, not spoken yet</Badge>}
        {lang.changed && <Badge variant="outline" className="border-warning text-warning">Changed in draft</Badge>}
        {lang.added && (
          <span className="ml-auto">
            <Confirm trigger={<Button size="sm" variant="ghost"><Trash2 /> Remove</Button>} title={`Remove ${lang.name}?`} destructive
                     text="Every sentence written in it is removed from the draft too." action="Remove"
                     onConfirm={() => void onSave({ languages: { [lang.code]: null } }, `${lang.name} removed.`)} />
          </span>
        )}
      </div>
      {!lang.source && (lang.missing ?? 0) > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 p-2 text-xs">
          <span className="min-w-0 flex-1">
            {progress ? `Writing… ${progress.done} done, ${progress.left} left` : `${lang.missing} sentence${lang.missing === 1 ? "" : "s"} not written in ${lang.name} yet`}
            {!progress && lang.spoken && " (callers hear the English for these)"}
          </span>
          {progress ? (
            <Button size="sm" variant="outline" onClick={() => { stop.current = true; }}><Loader2 className="animate-spin" /> <Square /> Stop</Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => void fillAll()}><Sparkles /> Write them all</Button>
          )}
        </div>
      )}
      {lang.source ? <p className="text-xs text-muted-foreground">{lang.style}</p> : (
        <>
          <Textarea value={style} onChange={(e) => setStyle(e.target.value)} rows={2} maxLength={600} className="text-sm"
                    aria-label={`How ${lang.name} is written`} />
          {style.trim() !== lang.style && (
            <Button size="sm" disabled={style.trim().length < 10}
                    onClick={() => void onSave({ languages: { [lang.code]: { style: style.trim() } } }, "Writing style saved to the draft.")}>
              <Save /> Save
            </Button>
          )}
        </>
      )}
    </div>
  );
}
