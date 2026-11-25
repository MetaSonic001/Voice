"use client";

// Journeys: everything the agent says that isn't written by the AI on the spot, organised domain > journey > step,
// plus the prompts, the business settings, the guardrails and the tests. A product manager edits a draft here, then
// publishes it to a share of calls (canary) or to all. Nothing reaches a caller before it is published.
import { FolderTree, Route } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { JourneyView, SearchResults } from "@/components/journeys/messages";
import { PromptsTab } from "@/components/journeys/prompts";
import { ReferenceTab } from "@/components/journeys/reference";
import { ReleasesTab } from "@/components/journeys/releases";
import { SettingsTab } from "@/components/journeys/settings";
import { TreePanel } from "@/components/journeys/tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { journeys, type Domain, type DraftChanges, type Editor } from "@/lib/api";

const NAME_KEY = "au-journeys-editor";

export default function JourneysPage() {
  const [editor, setEditor] = useState<Editor | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("journeys");
  const [selected, setSelected] = useState("");
  const [organise, setOrganise] = useState(false);
  const [query, setQuery] = useState("");
  const [author, setAuthor] = useState("");

  const load = useCallback(async () => {
    try {
      const e = await journeys.get();
      setEditor(e);
      setSelected((s) => s || e.tree[0]?.journeys[0]?.id || "");
      setError("");
    } catch (e) {
      setError(`Cannot reach the brain API: ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => {
    setAuthor(localStorage.getItem(NAME_KEY) ?? "");
    void load();
  }, [load]);

  const save = useCallback(async (changes: DraftChanges, ok?: string) => {
    if (!editor) return false;
    try {
      setEditor(await journeys.save(editor.draft.rev, author, changes));
      if (ok) toast.success(ok);
      return true;
    } catch (e) {
      const message = (e as Error).message;
      toast.error(message, message.includes("Someone else") ? { action: { label: "Reload", onClick: () => void load() }, duration: Infinity } : undefined);
      return false;
    }
  }, [editor, author, load]);
  const saveTree = useCallback((tree: Domain[], ok: string) => void save({ tree }, ok), [save]);
  const translate = useCallback(async (ids: string[], languages: string[], hint: string) => {
    try {
      const out = await journeys.translate(author, { ids, languages, hint });
      setEditor(out.editor);
      if (out.failed.length) toast.warning(`Not translated: ${out.failed[0].text}`);
      else toast.success(out.translated ? "Translated. Read it before publishing." : "Nothing to translate.");
      return !out.failed.length;
    } catch (e) {
      toast.error((e as Error).message);
      return false;
    }
  }, [author]);

  const flagged = useMemo(() => new Set([
    ...Object.entries(editor?.messages ?? {}).filter(([, m]) => m.live).map(([id]) => id),
    ...(editor?.problems ?? []).filter((p) => p.area === "message").map((p) => p.id),
  ]), [editor]);

  function openMessage(mid: string) {
    const home = editor?.tree.flatMap((d) => d.journeys).find((j) => j.steps.some((s) => s.messages.includes(mid)));
    if (home) setSelected(home.id);
    setQuery("");
    setTab("journeys");
  }

  const errors = editor?.problems.filter((p) => p.level === "error").length ?? 0;
  const r = editor?.rollout;
  return (
    <main className="mx-auto max-w-[1400px] space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Route className="size-5" /> Journeys</CardTitle>
          <CardDescription>
            Every fixed sentence the agent says, by domain, journey and step, with the prompts and settings behind them. Write
            in English: the AI writes the other languages when you press Translate now, and you can check and correct them. Changes stay in a draft until you
            publish them, first to a small share of calls.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2 text-sm">
          {r && (
            <>
              <Badge variant="secondary">Live: {r.stable ? `Release ${r.stable}` : "as shipped"}</Badge>
              {r.canary && <Badge variant="outline" className="border-warning text-warning">Canary: Release {r.canary} on {r.share}%</Badge>}
              <Badge variant="outline">{editor.changes.length} unpublished change{editor.changes.length === 1 ? "" : "s"}</Badge>
              {errors > 0 && <Badge variant="destructive">{errors} problem{errors === 1 ? "" : "s"}</Badge>}
              {editor.changes.length > 0 && <Button size="sm" variant="outline" onClick={() => setTab("releases")}>Review and publish</Button>}
            </>
          )}
          <label className="ml-auto flex w-full items-center gap-2 sm:w-auto">
            <span className="shrink-0 text-muted-foreground">Your name</span>
            <Input value={author} onChange={(e) => { setAuthor(e.target.value); localStorage.setItem(NAME_KEY, e.target.value); }}
                   placeholder="Shown in the history" className="sm:w-48" />
          </label>
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {!editor && !error && <Skeleton className="h-96 w-full" />}
      {editor && (
        <Tabs value={tab} onValueChange={setTab}>
          <div className="max-w-full overflow-x-auto">
            <TabsList>
              <TabsTrigger value="journeys">Journeys</TabsTrigger>
              <TabsTrigger value="prompts">Prompts</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
              <TabsTrigger value="reference">Guardrails and tests</TabsTrigger>
              <TabsTrigger value="releases">Releases{editor.changes.length ? ` (${editor.changes.length})` : ""}</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="journeys" className="mt-3">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
              <Card className="h-fit lg:sticky lg:top-18">
                <CardContent className="space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium">{editor.tree.length} domains</p>
                    <Button size="sm" variant={organise ? "default" : "outline"} onClick={() => setOrganise((v) => !v)}>
                      <FolderTree /> {organise ? "Done organising" : "Organise"}
                    </Button>
                  </div>
                  {organise && (
                    <p className="text-xs text-muted-foreground">
                      Add, rename, reorder, move or delete domains, journeys and steps. Deleting always moves what it holds first,
                      so no sentence is lost. The agent behaves the same: this only changes how sentences are grouped.
                    </p>
                  )}
                  <div className="max-h-[60vh] overflow-y-auto pr-1 lg:max-h-[calc(100vh-16rem)]">
                    <TreePanel tree={editor.tree} selected={query ? "" : selected} onSelect={(id) => { setSelected(id); setQuery(""); }}
                               organise={organise} onTree={saveTree} flagged={flagged} query={query} onQuery={setQuery} />
                  </div>
                </CardContent>
              </Card>
              <div className="min-w-0">
                {query.trim()
                  ? <SearchResults query={query} editor={editor} organise={organise} onSave={save} onTree={saveTree} onTranslate={translate} />
                  : <JourneyView journeyId={selected} editor={editor} organise={organise} onSave={save} onTree={saveTree} onTranslate={translate} />}
              </div>
            </div>
          </TabsContent>
          <TabsContent value="prompts" className="mt-3"><PromptsTab editor={editor} onSave={save} /></TabsContent>
          <TabsContent value="settings" className="mt-3"><SettingsTab editor={editor} onSave={save} author={author} onEditor={setEditor} /></TabsContent>
          <TabsContent value="reference" className="mt-3"><ReferenceTab /></TabsContent>
          <TabsContent value="releases" className="mt-3">
            <ReleasesTab editor={editor} author={author} onEditor={setEditor} onOpen={openMessage} />
          </TabsContent>
        </Tabs>
      )}
    </main>
  );
}
