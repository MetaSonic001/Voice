"use client";

// Prompts tab: every instruction the AI models get, with what each does. Some can be edited (how answers sound, the
// strategy lines, the judge, the handover note); the ones code depends on are shown but locked.
import { Lock, Save, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { human, Problems } from "@/components/journeys/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import type { DraftChanges, Editor, Prompt } from "@/lib/api";
import { cn } from "@/lib/utils";

const TITLES: Record<string, string> = {
  answer_style: "Answer: style", strategy: "Answer: how much to say", answer_rules: "Answer: rules", language: "Languages",
  understand: "Understanding each turn", judge: "Call review (judge)", summary: "Handover note", translate: "Translation",
};

export function PromptsTab({ editor, onSave }: { editor: Editor; onSave: (c: DraftChanges, ok?: string) => Promise<boolean> }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        These are the instructions the AI models get. Editing one changes how the AI behaves in a way code can&apos;t check, so
        publish prompt changes to a small share of calls first and compare. Locked prompts are what code reads word by word:
        a developer changes those.
      </p>
      {Object.entries(editor.prompts).map(([name, p]) => (
        <PromptCard key={name} name={name} prompt={p} onSave={onSave}
                    problems={editor.problems.filter((x) => x.area === "prompt" && x.id === name)} />
      ))}
    </div>
  );
}

function PromptCard({ name, prompt, onSave, problems }: {
  name: string; prompt: Prompt; onSave: (c: DraftChanges, ok?: string) => Promise<boolean>; problems: Editor["problems"];
}) {
  const [open, setOpen] = useState(false);
  const changed = prompt.live !== undefined;
  return (
    <Card className={cn(changed && "border-warning/60")}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {TITLES[name] ?? human(name)}
          {prompt.locked && <Badge variant="secondary"><Lock /> Locked</Badge>}
          {changed && <Badge variant="outline" className="border-warning text-warning">Changed in draft</Badge>}
        </CardTitle>
        <CardDescription>{prompt.about}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problems items={problems} />
        <Button size="sm" variant="outline" onClick={() => setOpen((v) => !v)}>{open ? "Hide" : prompt.locked ? "Read" : "Read and edit"}</Button>
        {open && (typeof prompt.text === "string"
          ? <TextPrompt name={name} prompt={prompt} onSave={onSave} />
          : <LinesPrompt name={name} prompt={prompt} onSave={onSave} />)}
      </CardContent>
    </Card>
  );
}

function TextPrompt({ name, prompt, onSave }: { name: string; prompt: Prompt; onSave: (c: DraftChanges, ok?: string) => Promise<boolean> }) {
  const text = prompt.text as string;
  const [value, setValue] = useState(text);
  useEffect(() => setValue(text), [text]);
  if (prompt.locked) return <pre className="max-h-[480px] overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-words">{text}</pre>;
  return (
    <div className="space-y-2">
      <Textarea value={value} onChange={(e) => setValue(e.target.value)} rows={16} className="font-mono text-xs" />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={value === text} onClick={() => void onSave({ prompts: { [name]: value } }, "Prompt saved to the draft.")}><Save /> Save</Button>
        {value !== text && <Button size="sm" variant="ghost" onClick={() => setValue(text)}>Cancel</Button>}
        {prompt.live !== undefined && value === text && (
          <Button size="sm" variant="ghost" onClick={() => void onSave({ prompts: { [name]: null } }, "Back to the live prompt.")}><Undo2 /> Undo change</Button>
        )}
      </div>
    </div>
  );
}

function LinesPrompt({ name, prompt, onSave }: { name: string; prompt: Prompt; onSave: (c: DraftChanges, ok?: string) => Promise<boolean> }) {
  const lines = prompt.text as Record<string, string>;
  const [values, setValues] = useState(lines);
  useEffect(() => setValues(lines), [lines]);
  const dirty = Object.keys(lines).filter((k) => values[k] !== lines[k]);
  return (
    <div className="space-y-3">
      {Object.entries(lines).map(([key, line]) => (
        <div key={key} className="space-y-1">
          <p className="text-xs font-medium">{human(key)}</p>
          {prompt.locked ? <p className="rounded-md bg-muted p-2 text-xs break-words">{line}</p>
            : <Textarea value={values[key] ?? ""} rows={2} className="text-sm" onChange={(e) => setValues({ ...values, [key]: e.target.value })} />}
        </div>
      ))}
      {!prompt.locked && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={!dirty.length} onClick={() => void onSave({ prompts: { [name]: Object.fromEntries(dirty.map((k) => [k, values[k]])) } }, "Saved to the draft.")}>
            <Save /> Save
          </Button>
          {prompt.live !== undefined && !dirty.length && (
            <Button size="sm" variant="ghost" onClick={() => void onSave({ prompts: { [name]: null } }, "Back to the live lines.")}><Undo2 /> Undo change</Button>
          )}
        </div>
      )}
    </div>
  );
}
