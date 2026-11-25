"use client";

// The right panel of /journeys: one journey's steps and the sentences in each, or every sentence that matches a search.
// People write English only: the other languages are written by the AI (Translate now on a sentence, or Prepare
// translations for every changed one), then read and corrected or re-done with a note on what to change.
import { Bot, Languages, Loader2, Lock, Pencil, Plus, Save, Sparkles, Trash2, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { human, langName, LanguagePicker, Problems, targets } from "@/components/journeys/common";
import * as ops from "@/components/journeys/tree-ops";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Domain, DraftChanges, Editor, Message, Problem, Texts } from "@/lib/api";
import { cn } from "@/lib/utils";

type Save = (changes: DraftChanges, ok?: string) => Promise<boolean>;

/** Translate sentences now: (ids, languages, what to change) -> true when done. */
export type Translate = (ids: string[], languages: string[], hint: string) => Promise<boolean>;

type Shared = { editor: Editor; organise: boolean; onSave: Save; onTree: (tree: Domain[], ok: string) => void; onTranslate: Translate };

export function JourneyView({ journeyId, ...shared }: Shared & { journeyId: string }) {
  const { editor, organise, onTree } = shared;
  const found = ops.findJourney(editor.tree, journeyId);
  const [newStep, setNewStep] = useState("");
  if (!found) return <p className="p-6 text-sm text-muted-foreground">Pick a journey on the left.</p>;
  const { domain, journey } = found;
  const others = editor.tree.flatMap((d) => d.journeys.filter((j) => j.id !== journey.id).map((j) => ({ d, j })));
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardDescription>{domain.name}</CardDescription>
          <CardTitle className="break-words">{journey.name}</CardTitle>
        </CardHeader>
        {organise && (
          <CardContent className="flex flex-wrap items-center gap-2 text-sm">
            <Select value="" onValueChange={(to) => onTree(ops.moveJourney(editor.tree, journey.id, to), "Journey moved.")}>
              <SelectTrigger className="w-full sm:w-64"><SelectValue placeholder="Move this journey to a domain…" /></SelectTrigger>
              <SelectContent>{editor.tree.filter((d) => d.id !== domain.id).map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
            </Select>
            {others.length > 0 && (
              <Select value="" onValueChange={(to) => onTree(ops.deleteJourney(editor.tree, journey.id, to), "Journey deleted; its steps moved.")}>
                <SelectTrigger className="w-full sm:w-72"><SelectValue placeholder="Delete it, moving its steps to…" /></SelectTrigger>
                <SelectContent>
                  {editor.tree.map((d) => d.journeys.some((j) => j.id !== journey.id) && (
                    <SelectGroup key={d.id}>
                      <SelectLabel>{d.name}</SelectLabel>
                      {d.journeys.filter((j) => j.id !== journey.id).map((j) => <SelectItem key={j.id} value={j.id}>{j.name}</SelectItem>)}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
            )}
          </CardContent>
        )}
      </Card>
      {journey.steps.map((step, i) => (
        <section key={i} className="space-y-2">
          <StepHeader name={step.name} index={i} organise={organise} canDelete={journey.steps.length > 1}
                      onRename={(name) => onTree(ops.renameStep(editor.tree, journey.id, i, name), "Step renamed.")}
                      onDelete={() => onTree(ops.deleteStep(editor.tree, journey.id, i), "Step deleted; its sentences moved to the step above.")} />
          {step.messages.length === 0 && <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No sentences in this step yet. Move some here in Organise mode.</p>}
          {step.messages.map((mid) => <MessageCard key={mid} id={mid} {...shared} />)}
        </section>
      ))}
      {organise && (
        <div className="flex gap-2">
          <Input value={newStep} onChange={(e) => setNewStep(e.target.value)} placeholder="New step name" />
          <Button variant="outline" disabled={!newStep.trim()} onClick={() => { onTree(ops.addStep(editor.tree, journey.id, newStep.trim()), "Step added."); setNewStep(""); }}>
            <Plus /> Add step
          </Button>
        </div>
      )}
    </div>
  );
}

function StepHeader({ name, index, organise, canDelete, onRename, onDelete }: {
  name: string; index: number; organise: boolean; canDelete: boolean; onRename: (n: string) => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  return (
    <div className="flex min-w-0 items-center gap-2 pt-2">
      <span className="grid size-5 shrink-0 place-items-center rounded-full bg-secondary text-[11px] font-medium">{index + 1}</span>
      {editing !== null ? (
        <Input autoFocus value={editing} onChange={(e) => setEditing(e.target.value)} className="h-7 max-w-sm"
               onKeyDown={(e) => { if (e.key === "Enter") { if (editing.trim()) onRename(editing.trim()); setEditing(null); } }}
               onBlur={() => { if (editing.trim() && editing.trim() !== name) onRename(editing.trim()); setEditing(null); }} />
      ) : <h3 className="min-w-0 truncate text-sm font-semibold">{name}</h3>}
      {organise && editing === null && (
        <span className="flex shrink-0">
          <Button size="icon" variant="ghost" className="size-7" title="Rename step" onClick={() => setEditing(name)}><Pencil /></Button>
          {canDelete && <Button size="icon" variant="ghost" className="size-7" title="Delete step (its sentences move up)" onClick={onDelete}><Trash2 /></Button>}
        </span>
      )}
    </div>
  );
}

/** Every sentence whose id or wording matches the search, with where it sits. */
export function SearchResults({ query, ...shared }: Shared & { query: string }) {
  const { editor } = shared;
  const hits = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out: { mid: string; where: string }[] = [];
    for (const d of editor.tree) for (const j of d.journeys) for (const s of j.steps) for (const mid of s.messages) {
      const m = editor.messages[mid];
      const text = [mid, ...Object.values(m?.text ?? {}).map((t) => (typeof t === "string" ? t : ""))].join(" ").toLowerCase();
      if (text.includes(q)) out.push({ mid, where: `${d.name} › ${j.name} › ${s.name}` });
    }
    return out;
  }, [editor, query]);
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{hits.length} sentence{hits.length === 1 ? "" : "s"} match “{query}”.</p>
      {hits.slice(0, 60).map(({ mid, where }) => (
        <div key={mid} className="space-y-1">
          <p className="text-xs text-muted-foreground">{where}</p>
          <MessageCard id={mid} {...shared} />
        </div>
      ))}
      {hits.length > 60 && <p className="text-xs text-muted-foreground">Showing the first 60. Narrow the search to see the rest.</p>}
    </div>
  );
}

const asText = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

function MessageCard({ id, editor, organise, onSave, onTree, onTranslate }: Shared & { id: string }) {
  const msg: Message | undefined = editor.messages[id];
  const problems: Problem[] = editor.problems.filter((p) => p.area === "message" && p.id === id);
  const english = msg ? asText(msg.text.en) : "";
  const [text, setText] = useState(english);
  const [showLangs, setShowLangs] = useState(false);
  useEffect(() => setText(english), [english]);
  if (!msg) return null;
  const changed = Boolean(msg.live);
  const waiting = editor.needs_translation.includes(id);
  const dirty = text.trim() !== english;
  const aliases = Object.entries(editor.aliases).filter(([, to]) => to === id).map(([from]) => from);

  return (
    <Card className={cn("gap-3 py-4", changed && "border-warning/60", problems.some((p) => p.level === "error") && "border-destructive/60")}>
      <CardContent className="space-y-2.5 px-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <code className="text-[11px] break-all text-muted-foreground">{id}</code>
          {aliases.length > 0 && <span className="text-[11px] text-muted-foreground">(also used as {aliases.join(", ")})</span>}
          {msg.locked && <Badge variant="secondary"><Lock /> Locked</Badge>}
          {changed && <Badge variant="outline" className="border-warning text-warning">Changed in draft</Badge>}
          {waiting && <Badge variant="outline" className="border-warning text-warning">Needs translation</Badge>}
          {msg.auto?.length ? <Badge variant="outline"><Bot /> AI-written {msg.auto.map((l) => langName(editor.languages, l)).join(", ")}</Badge> : null}
        </div>
        {msg.note && <p className="text-xs break-words text-muted-foreground">Why: {msg.note}</p>}
        {msg.locked ? (
          <div className="space-y-1">
            <p className="rounded-md bg-muted p-2 font-mono text-xs break-words">{english}</p>
            <p className="text-xs text-muted-foreground">{msg.locked}</p>
          </div>
        ) : (
          <>
            <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={Math.min(6, Math.max(2, Math.ceil(text.length / 90)))}
                      className="text-sm" aria-label={`English text of ${id}`} />
            {msg.placeholders.length > 0 && (
              <p className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                Keep these blanks, the agent fills them in:
                {msg.placeholders.map((p) => <code key={p} className="rounded bg-muted px-1">{`{${p}}`}</code>)}
              </p>
            )}
          </>
        )}
        {changed && msg.live && msg.live.en !== msg.text.en && (
          <p className="text-xs break-words text-muted-foreground"><span className="font-medium">Live now:</span> {asText(msg.live.en)}</p>
        )}
        <Problems items={problems} />
        <div className="flex flex-wrap items-center gap-2">
          {!msg.locked && dirty && (
            <>
              <Button size="sm" onClick={() => void onSave({ messages: { [id]: { en: text } } }, "Saved to the draft.")}><Save /> Save</Button>
              <Button size="sm" variant="ghost" onClick={() => setText(english)}>Cancel</Button>
            </>
          )}
          {!msg.locked && changed && !dirty && (
            <Button size="sm" variant="ghost" onClick={() => void onSave({ messages: { [id]: null } }, "Back to the live wording.")}><Undo2 /> Undo change</Button>
          )}
          {!msg.locked && (
            <Button size="sm" variant={showLangs ? "secondary" : "ghost"} onClick={() => setShowLangs((v) => !v)}>
              <Languages /> {showLangs ? "Hide languages" : "Other languages"}
            </Button>
          )}
          {organise && <MoveTo id={id} editor={editor} onTree={onTree} />}
        </div>
        {showLangs && <Translations id={id} msg={msg} editor={editor} dirty={dirty} onSave={onSave} onTranslate={onTranslate} />}
      </CardContent>
    </Card>
  );
}

function Translations({ id, msg, editor, dirty, onSave, onTranslate }: {
  id: string; msg: Message; editor: Editor; dirty: boolean; onSave: Save; onTranslate: Translate;
}) {
  const all = targets(editor.languages);
  const [pick, setPick] = useState(all.map((l) => l.code));
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<Texts | null>(null);
  async function translate() {
    setBusy(true);
    if (await onTranslate([id], pick, hint)) setHint("");
    setBusy(false);
  }
  return (
    <div className="space-y-3 rounded-lg border bg-muted/40 p-3">
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Write the English; the AI writes the rest. Pick the languages, add a note if the last try wasn&apos;t right, then read
          what comes back. Correct one by hand only if you read that language well.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <LanguagePicker languages={editor.languages} value={pick} onChange={setPick} />
          <Input value={hint} onChange={(e) => setHint(e.target.value)} maxLength={300} className="h-8 min-w-0 flex-1 basis-48 text-sm"
                 placeholder="What should change? (optional, e.g. more polite)" aria-label="What should change in the translation" />
          <Button size="sm" disabled={busy || !pick.length || dirty} onClick={() => void translate()}
                  title={dirty ? "Save the English first" : undefined}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />} Translate now
          </Button>
        </div>
        {dirty && <p className="text-xs text-warning">Save the English first: the AI translates the saved sentence.</p>}
      </div>
      <div className="space-y-2.5">
        {all.map((l) => {
          const text = msg.text[l.code];
          return (
            <div key={l.code} className="space-y-1">
              <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
                {l.name}
                {msg.auto?.includes(l.code) && <Badge variant="outline" className="h-5 text-[10px]"><Bot /> AI-written</Badge>}
                {!l.spoken && <Badge variant="secondary" className="h-5 text-[10px]">not spoken yet</Badge>}
                {msg.live && msg.live[l.code] !== text && text && <Badge variant="outline" className="h-5 border-warning text-[10px] text-warning">changed</Badge>}
                {l.added && text && !edit && (
                  <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-[11px]" title={`Remove the ${l.name} text of this sentence`}
                          onClick={() => void onSave({ messages: { [id]: { [l.code]: null } } }, `${l.name} text removed.`)}>
                    <Trash2 /> Remove
                  </Button>
                )}
              </p>
              {edit ? (
                <Textarea value={edit[l.code] ?? ""} rows={2} className="text-sm" onChange={(e) => setEdit({ ...edit, [l.code]: e.target.value })} />
              ) : text ? <p className="text-sm break-words">{asText(text)}</p>
                : <p className="text-sm text-muted-foreground">Not written yet{l.spoken ? ": callers hear the English" : ""}.</p>}
            </div>
          );
        })}
      </div>
      {edit ? (
        <div className="flex gap-2">
          <Button size="sm" onClick={async () => {
            const changed = Object.fromEntries(Object.entries(edit).filter(([code, t]) => t.trim() && t !== (msg.text[code] ?? "")));
            if (!Object.keys(changed).length || await onSave({ messages: { [id]: changed } }, "Translation saved.")) setEdit(null);
          }}><Save /> Save</Button>
          <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button>
        </div>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setEdit(Object.fromEntries(all.map((l) => [l.code, asText(msg.text[l.code] ?? "")])))}>
          <Pencil /> Correct by hand
        </Button>
      )}
    </div>
  );
}

function MoveTo({ id, editor, onTree }: { id: string; editor: Editor; onTree: Shared["onTree"] }) {
  return (
    <Select value="" onValueChange={(to) => onTree(ops.moveMessage(editor.tree, id, to), "Sentence moved.")}>
      <SelectTrigger size="sm" className="ml-auto w-full sm:w-56"><SelectValue placeholder="Move to step…" /></SelectTrigger>
      <SelectContent>
        {editor.tree.flatMap((d) => d.journeys.map((j) => (
          <SelectGroup key={j.id}>
            <SelectLabel>{d.name} › {j.name}</SelectLabel>
            {j.steps.map((s, i) => <SelectItem key={i} value={`${j.id}:${i}`}>{s.name || human(`step ${i + 1}`)}</SelectItem>)}
          </SelectGroup>
        )))}
      </SelectContent>
    </Select>
  );
}
